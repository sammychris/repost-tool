"""Clip Desk: the video engine behind the web page and the Claude skill.

Usage (from any folder):  clipdesk.bat <command> ...   or   .venv\\Scripts\\python clipdesk.py <command> ...
Commands: probe, subs-detect, coldopen-suggest, preview, run, presets, inspect, render-edl, check-seams.
See TOOL_GUIDE.md for every command and job field.
"""
import argparse, copy, difflib, io, json, os, re, subprocess, sys, uuid
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import captions, render

VERSION = "1.0.0"
HERE = render.HERE
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
SUB_BAND = (0.86, 0.94)  # where burned-in subtitles sit, as a share of the source height
# Subtitle colours as ASS &HAABBGGRR
COLOURS = {"white": "&H00FFFFFF", "orange": "&H001F9AFF", "yellow": "&H004DE1FF", "black_box": "&H00FFFFFF"}
HQ = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]
SMALL = ["-c:v", "libx264", "-crf", "23", "-preset", "slow", "-pix_fmt", "yuv420p"]
LOSSLESS = ["-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]


class Fail(Exception):
    """A plain-English problem to report to the user (exit code 1)."""


# ---------- helpers ----------

def ff(cmd, what):
    """Run ffmpeg/ffprobe; on failure raise Fail with the last lines of its error output."""
    r = subprocess.run(cmd, capture_output=True, cwd=HERE)
    if r.returncode:
        tail = r.stderr.decode(errors="replace").strip().splitlines()[-4:]
        raise Fail(f"{what} failed:\n  " + "\n  ".join(tail))
    return r


def secs(t):
    """'1:02.5' / '0:12' / 12 / '12' -> seconds."""
    if t is None or isinstance(t, (int, float)):
        return t
    parts = [float(p) for p in str(t).strip().split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def stamp(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def mb(path):
    return os.path.getsize(path) / 1e6


def font(size, name="ExtraBold"):
    return ImageFont.truetype(os.path.join(HERE, "fonts", f"InterDisplay-{name}.ttf"), size)


def is_image(path):
    return path.lower().endswith(IMAGE_EXT)


def loudness(path):
    """Integrated loudness (LUFS) of the first audio stream, or None if there is no usable audio."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af",
                        "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True)
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr.decode(errors="replace"))
    return float(m[-1]) if m and m[-1] != "-inf" and float(m[-1]) > -70 else None


def info(path):
    if not os.path.isfile(path):
        raise Fail(f"File not found: {path}")
    if is_image(path):
        w, h = Image.open(path).size
        return {"file": path, "image": True, "width": w, "height": h, "size_mb": round(mb(path), 2)}
    out = ff(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path], f"Reading {path}").stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    d = {"file": path, "image": False, "duration": round(float(j["format"].get("duration", 0)), 3),
         "size_mb": round(mb(path), 2), "bitrate_kbps": round(int(j["format"].get("bit_rate", 0)) / 1000)}
    if v:
        num, den = (int(x) for x in v["r_frame_rate"].split("/"))
        d.update(width=v["width"], height=v["height"], fps=round(num / den, 3), fps_exact=v["r_frame_rate"],
                 vcodec=v["codec_name"], pix_fmt=v.get("pix_fmt"))
    if a:
        d.update(acodec=a["codec_name"], sample_rate=int(a["sample_rate"]), channels=a["channels"])
    return d


def same_format(infos):
    keys = ("width", "height", "fps_exact", "vcodec", "pix_fmt", "acodec", "sample_rate", "channels")
    return all(not i["image"] for i in infos) and len({tuple(i.get(k) for k in keys) for i in infos}) == 1


def workdir(video):
    d = os.path.splitext(os.path.abspath(video))[0] + "_clipdesk"
    os.makedirs(d, exist_ok=True)
    return d


def tmp(ext):
    os.makedirs(os.path.join(HERE, "tmp"), exist_ok=True)
    return os.path.join(HERE, "tmp", uuid.uuid4().hex[:8] + ext)


# ---------- burned-in subtitle detection ----------

def band_frames(video, fps=None, half=True):
    """Grey pixels of the subtitle band (86-94% height, centre 60% width) for every frame (or `fps` per second)."""
    i = info(video)
    w, h = i["width"], i["height"]
    bw, bh = int(w * .6) // 2 * 2, int(h * (SUB_BAND[1] - SUB_BAND[0])) // 2 * 2
    vf = (f"fps={fps}," if fps else "") + f"crop={bw}:{bh}:{int(w * .2)}:{int(h * SUB_BAND[0])}"
    if half:
        bw, bh = bw // 2, bh // 2
        vf += f",scale={bw}:{bh}:flags=neighbor"
    raw = ff(["ffmpeg", "-v", "error", "-i", video, "-vf", vf + ",format=gray", "-f", "rawvideo", "-"],
             "Scanning the subtitle band").stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, bh, bw), (fps or i["fps"])


def text_masks(frames):
    """Text = bright (>235) pixels with a dark outline (<90) within 2 px. Plain white objects don't count."""
    dark = frames < 90
    near = dark.copy()
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            near |= np.roll(np.roll(dark, dy, 1), dx, 2)
    masks = (frames > 235) & near
    return masks, masks.sum((1, 2)) > max(15, masks[0].size * 0.004)


def find_cues(frames, fps, min_frames=4):
    """A cue starts when text appears or the text mask changes by more than 60%."""
    masks, on = text_masks(frames)
    cues, start = [], None
    for n in range(len(masks) + 1):
        if start is not None and (n == len(masks) or not on[n] or changed(masks[n - 1], masks[n])):
            if n - start >= min_frames:
                cues.append([start / fps, n / fps, (start + n) // 2])
            start = None
        if start is None and n < len(masks) and on[n]:
            start = n
    for a, b in zip(cues, cues[1:]):  # overlapping cues make lines jump: end each just before the next
        a[1] = min(a[1], b[0] - 0.01)
    return cues


def changed(a, b):
    union = np.logical_or(a, b).sum()
    return union > 0 and np.logical_xor(a, b).sum() / union > 0.6


def has_burned_subs(video):
    frames, _ = band_frames(video, fps=2)
    return bool(text_masks(frames)[1].sum() >= 4)  # 2+ seconds of text


def cmd_subs_detect(video):
    video = os.path.abspath(video)
    frames, fps = band_frames(video)
    cues = find_cues(frames, fps)
    if not cues:
        raise Fail("No burned-in subtitles found in the band at 86-94% of the height.")
    d = workdir(video)
    i = info(video)
    w, h = i["width"], i["height"]
    bw, bh = int(w * .6) // 2 * 2, int(h * (SUB_BAND[1] - SUB_BAND[0])) // 2 * 2
    # One full-quality strip per cue, taken from the middle frame of the cue.
    sel = "+".join(f"eq(n\\,{mid})" for *_, mid in cues)
    raw = ff(["ffmpeg", "-v", "error", "-i", video, "-vf",
              f"select='{sel}',crop={bw}:{bh}:{int(w * .2)}:{int(h * SUB_BAND[0])}",
              "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "Grabbing subtitle strips").stdout
    strips = np.frombuffer(raw, np.uint8).reshape(-1, bh, bw, 3)
    sw = 900
    sh = round(bh * sw / bw)
    sheet = Image.new("RGB", (sw + 260, (sh + 8) * len(strips)), "black")
    dr = ImageDraw.Draw(sheet)
    f = font(22, "Medium")
    for n, (strip, (a, b, _)) in enumerate(zip(strips, cues)):
        y = n * (sh + 8)
        sheet.paste(Image.fromarray(strip).resize((sw, sh)), (260, y))
        dr.text((10, y + sh / 2), f"{n + 1:>3}  {stamp(a)}–{stamp(b)}", font=f, fill=(255, 200, 77), anchor="lm")
    json_path, png_path = os.path.join(d, "cues.json"), os.path.join(d, "cues_strips.png")
    if os.path.exists(json_path) and any(c.get("text") for c in json.load(open(json_path, encoding="utf-8-sig"))["cues"]):
        json_path = os.path.join(d, f"cues_{uuid.uuid4().hex[:4]}.json")  # never overwrite words someone typed in
    json.dump({"video": video, "cues": [{"n": n + 1, "start": round(a, 3), "end": round(b, 3), "text": ""}
                                        for n, (a, b, _) in enumerate(cues)]},
              open(json_path, "w", encoding="utf-8"), indent=1)
    sheet.save(png_path)
    print(f"{len(cues)} subtitle cues found.\ncues:   {json_path}\nstrips: {png_path}\n"
          "Fill in each cue's \"text\" from the strips, then use \"subtitles\": {\"source\": \"<cues.json path>\"}.")


# ---------- cold open ----------

def coldopen_candidates(video, length=3.0):
    """Score 3s windows by loudness, movement and 'right after a hard cut'. Skips the last 20% (no endings/punchlines)."""
    i = info(video)
    dur = i["duration"]
    step = 0.25
    n = int(dur / step)
    loud = np.zeros(n)
    if "acodec" in i:
        pcm = np.frombuffer(ff(["ffmpeg", "-v", "error", "-i", video, "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                               "Reading audio").stdout, np.float32)
        hop = int(8000 * step)
        loud = np.array([np.sqrt(np.mean(pcm[k * hop:(k + 1) * hop] ** 2) + 1e-12) for k in range(n)])
    raw = ff(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps={1 / step},scale=64:36,format=gray",
              "-f", "rawvideo", "-"], "Reading frames").stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 36, 64).astype(np.float32)
    diff = np.concatenate([[0], np.abs(np.diff(fr, axis=0)).mean((1, 2))])[:n]
    diff = np.pad(diff, (0, n - len(diff)))
    cut = diff > max(25, diff.mean() + 3 * diff.std())
    z = lambda x: (x - x.mean()) / (x.std() + 1e-9)
    lz, mz = z(loud), z(np.where(cut, np.median(diff), diff))  # a cut is not "movement"
    w = int(length / step)
    scores = []
    for k in range(int(1 / step), max(int(1 / step) + 1, int(dur * 0.8 / step) - w)):
        after_cut = cut[max(0, k - 2):k + 1].any()
        s = lz[k:k + w].mean() + mz[k:k + w].mean() + (1.0 if after_cut else 0)
        why = [x for x, ok in (("loud", lz[k:k + w].mean() > .5), ("big movement", mz[k:k + w].mean() > .5),
                               ("right after a cut", after_cut)) if ok]
        scores.append((s, k * step, why))
    picks = []
    for s, t, why in sorted(scores, reverse=True):
        if all(abs(t - p["start"]) >= length for p in picks):
            picks.append({"n": len(picks) + 1, "start": round(t, 2), "end": round(min(t + length, dur), 2),
                          "score": round(float(s), 2), "why": ", ".join(why) or "best overall"})
        if len(picks) == 3:
            break
    if not picks:
        raise Fail("The clip is too short to suggest a cold open.")
    return picks


def cmd_coldopen_suggest(video):
    video = os.path.abspath(video)
    picks = coldopen_candidates(video)
    d = workdir(video)
    tiles = []
    for p in picks:
        png = ff(["ffmpeg", "-v", "error", "-ss", str(p["start"] + 1), "-i", video, "-frames:v", "1",
                  "-vf", "scale=480:-2", "-f", "image2pipe", "-vcodec", "png", "-"], "Grabbing a still").stdout
        im = Image.open(io.BytesIO(png)).convert("RGB")
        ImageDraw.Draw(im).text((10, 10), f"{p['n']}  {stamp(p['start'])}–{stamp(p['end'])}", font=font(28),
                                fill=(255, 200, 77), stroke_width=3, stroke_fill="black")
        tiles.append(im)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), max(t.height for t in tiles)))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 10
    jp, ip = os.path.join(d, "coldopen.json"), os.path.join(d, "coldopen_candidates.jpg")
    json.dump({"video": video, "candidates": picks}, open(jp, "w", encoding="utf-8"), indent=1)
    sheet.save(ip, quality=88)
    for p in picks:
        print(f"{p['n']}. {stamp(p['start'])}–{stamp(p['end'])}  ({p['why']})")
    print(f"json:   {jp}\nstills: {ip}")


# ---------- jobs ----------

def load_presets():
    p = os.path.join(HERE, "presets.json")
    return json.load(open(p, encoding="utf-8-sig")) if os.path.exists(p) else {}


def load_job(path):
    try:
        job = json.load(open(path, encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise Fail(f"Can't read the job file {path}: {e}")
    base = os.path.dirname(os.path.abspath(path))
    presets = load_presets()
    name = job.get("preset") or (job.get("stack") or {}).get("preset")
    if job.get("preset") and name not in presets:
        raise Fail(f"Unknown preset '{name}'. Run `clipdesk presets` to see them.")
    for k, v in (presets.get(name) or {}).items():  # preset fills only what the job left out
        if k == "description":
            continue
        if k not in job:
            job[k] = copy.deepcopy(v)
        elif isinstance(job[k], dict) and isinstance(v, dict):
            job[k] = {**v, **job[k]}
    rel = lambda p: p if not p or os.path.isabs(p) else os.path.join(base, p)
    if not job.get("inputs"):
        raise Fail("The job has no \"inputs\".")
    job["inputs"] = [rel(p) for p in job["inputs"]]
    for im in job.get("images") or []:
        im["file"] = rel(im["file"])
    if job.get("music"):
        job["music"]["file"] = rel(job["music"]["file"])
    sub = job.get("subtitles")
    if sub and sub.get("source") not in (None, "whisper"):
        src = rel(sub["source"])
        fallback = os.path.join(workdir(job["inputs"][0]), os.path.basename(sub["source"]))
        sub["source"] = src if os.path.exists(src) else fallback
    job["_base"] = base
    return job


def segments(job):
    """The timeline: video pieces (with trims) and still images, in order."""
    trims = job.get("trim") or []
    segs = []
    for n, f in enumerate(job["inputs"]):
        if is_image(f):
            segs.append({"file": f, "image": True, "dur": 3.0, "input": n})
            continue
        mine = [t for t in trims if t.get("input", 0) == n]
        for t in mine or [{}]:
            segs.append({"file": f, "start": secs(t.get("start")) or 0, "end": secs(t.get("end")), "input": n})
    for im in job.get("images") or []:
        seg = {"file": im["file"], "image": True, "dur": float(im.get("duration", 3))}
        pos = im.get("position", "end")
        at = 0 if pos == "start" else len(segs) if pos == "end" else \
            next((k for k, s in enumerate(segs) if s.get("input") == int(pos)), len(segs))
        segs.insert(at, seg)
    return segs


def assemble(segs, out, codec, transition="cut"):
    """Join pieces into one file, re-encoding (normalised to the first video's size and fps)."""
    first = info(next(s["file"] for s in segs if not s.get("image")))
    W, H, FPS = first["width"], first["height"], first["fps_exact"]
    cmd, fc = ["ffmpeg", "-y", "-v", "error"], []
    durs = []
    for n, s in enumerate(segs):
        if s.get("image"):
            cmd += ["-loop", "1", "-framerate", FPS, "-t", str(s["dur"]), "-i", s["file"]]
            d, has_audio = s["dur"], False
        else:
            i = info(s["file"])
            end = s["end"] if s.get("end") is not None else i["duration"]
            if end <= s["start"]:
                raise Fail(f"Trim of {os.path.basename(s['file'])}: end {stamp(end)} is not after start {stamp(s['start'])}.")
            cmd += ["-ss", str(s["start"]), "-to", str(end), "-i", s["file"]]
            d, has_audio = end - s["start"], "acodec" in i
        durs.append(d)
        flash = ",fade=t=in:st=0:d=0.25:color=white" if s.get("flash") else ""
        fc.append(f"[{n}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                  f"setsar=1,fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop=-1,trim=duration={d:.3f},"
                  f"setpts=PTS-STARTPTS{flash}[v{n}]")
        src = f"[{n}:a]" if has_audio else f"anullsrc=r=48000:cl=stereo,"
        fc.append(f"{src}aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{d:.3f},asetpts=PTS-STARTPTS[a{n}]")
    N = len(segs)
    if transition == "fade" and N > 1:
        T = 0.5
        if min(durs) <= T * 2:
            raise Fail("A fade needs every piece to be longer than 1 second.")
        v, a, t = "[v0]", "[a0]", durs[0]
        for n in range(1, N):
            t -= T
            fc.append(f"{v}[v{n}]xfade=transition=fade:duration={T}:offset={t:.3f}[x{n}]")
            fc.append(f"{a}[a{n}]acrossfade=d={T}[y{n}]")
            v, a, t = f"[x{n}]", f"[y{n}]", t + durs[n]
        fc += [f"{v}null[v]", f"{a}anull[a]"]
    else:
        fc.append("".join(f"[v{n}][a{n}]" for n in range(N)) + f"concat=n={N}:v=1:a=1[v][a]")
    ff(cmd + ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", *codec, out], "Joining the clips")
    return out


def concat_copy(files, out):
    lst = tmp(".txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{p.replace(chr(92), '/')}'\n" for p in files)
    try:
        ff(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
            "-movflags", "+faststart", out], "Joining the clips (direct copy)")
    finally:
        os.remove(lst)
    return out


def drama_lines(stack):
    line2 = stack.get("line2") or (f"Part {stack['part']}" if stack.get("part") else "")
    return stack.get("line1", ""), line2


def sub_lines(sub, video, job):
    src = sub.get("source", "whisper")
    if src == "whisper":
        print("Transcribing with Whisper (local)...")
        return captions.group(captions.transcribe(video, sub.get("model", "small")))
    if not os.path.exists(src):
        raise Fail(f"Subtitle source not found: {src}")
    if src.lower().endswith(".json"):
        cues = json.load(open(src, encoding="utf-8-sig"))["cues"]
        empty = [c["n"] for c in cues if not c.get("text", "").strip()]
        if empty:
            print(f"Warning: cues {empty} have no text and are skipped.")
        return [(c["start"], c["end"], c["text"]) for c in cues if c.get("text", "").strip()]
    text = open(src, encoding="utf-8-sig").read()
    try:
        timed = captions.parse(text)
        if timed:
            return timed
    except ValueError:
        pass
    # Plain script (no times): match its words to Whisper's words; words Whisper missed are interpolated.
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    words = captions.transcribe(video, sub.get("model", "small"))
    norm = lambda w: re.sub(r"[^\w']", "", w.lower())
    script = [(n, norm(w)) for n, r in enumerate(rows) for w in r.split()]
    sm = difflib.SequenceMatcher(None, [w for _, w in script], [norm(w) for *_, w in words], autojunk=False)
    hit = {a + k: b + k for a, b, size in sm.get_matching_blocks() for k in range(size)}
    if not hit:
        raise Fail("The script has no times and none of its words match what Whisper heard.")
    xs = sorted(hit)
    starts, ends = [words[hit[x]][0] for x in xs], [words[hit[x]][1] for x in xs]
    out = []
    for n, r in enumerate(rows):
        idx = [i for i, (m, _) in enumerate(script) if m == n]
        out.append([float(np.interp(idx[0], xs, starts)), float(np.interp(idx[-1], xs, ends)), r])
    for a, b in zip(out, out[1:]):
        a[1] = min(a[1], b[0] - 0.01)
    return [tuple(x) for x in out]


def sub_style(sub, g, house):
    colour = COLOURS.get(sub.get("style", "white"))
    if not colour:
        raise Fail(f"Unknown subtitle style '{sub.get('style')}'. Use white, orange, yellow or black_box.")
    box = sub.get("style") == "black_box"
    border, outline_col = ("3", "&H40000000") if box else ("1", "&H00000000")
    if house:  # original-format video: Inter Medium 44 (at 1080p), off-white, soft outline, bottom centre
        k = min(g["W"], g["H"]) / 1080
        size = round(sub.get("size", 44) * k)
        colour = "&H00F0F0F0" if sub.get("style", "white") == "white" else colour
        outline = round((12 if box else 2.5) * k)
        return (f"Style: Sub,Inter Display Medium,{size},{colour},{colour},{outline_col},&H80000000,0,0,0,0,100,100,0,0,"
                f"{border},{outline},{0 if box else 1},2,{round(80 * k)},{round(80 * k)},{round(g['H'] * .06)},1")
    size = round(sub.get("size", 66) * g["s"])
    outline = round((14 if box else 5) * size / 66)
    return (f"Style: Sub,Inter Display ExtraBold,{size},{colour},{colour},{outline_col},&H64000000,0,0,0,0,100,100,0,0,"
            f"{border},{outline},0,8,{round(70 * g['s'])},{round(70 * g['s'])},{g['cap_y']},1")


def visual_filter(job, src, src_info, ass_path):
    """Filter graph for framing, Stack and subtitles. Returns None when nothing visual changes."""
    frame = job.get("frame") or {}
    stack = job.get("stack")
    sub = job.get("subtitles")
    mode = "9:16" if stack else frame.get("mode", "original")
    burned = bool(sub and sub.get("remove_burned")) or bool(frame.get("remove_burned"))
    if mode == "original" and not sub and not burned:
        return None
    sw, sh = src_info["width"], src_info["height"]
    if mode == "original":
        g = {"W": sw, "H": sh, "s": 1, "cap_y": round(sh * .8), "fit": True}
        if burned:  # crop out the band and zoom back up to the original size
            cw, ch = int(sw * .86) // 2 * 2, int(sh * .86) // 2 * 2
            cx = int((sw - cw) / 2 * (1 + float(frame.get("offset_x", 0)))) // 2 * 2
            f = [f"[0:v]crop={cw}:{ch}:{cx}:0,scale={sw}:{sh}:flags=lanczos,setsar=1[vv]"]
        else:
            f = ["[0:v]null[vv]"]
    else:
        if mode not in render.SIZES:
            raise Fail(f"Unknown frame mode '{mode}'. Use original, 9:16, 4:5, 1:1 or 16:9.")
        g = render.geometry(sw, sh, mode, frame.get("zoom"), float(frame.get("offset_x", 0)), burned, stack=bool(stack))
        W, H = g["W"], g["H"]
        (cw, ch, cx, cy), (vw, vh, vx, vy) = g["crop"], g["video"]
        f = [f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={vw}:{vh}:flags=lanczos,setsar=1[v]"]
        if frame.get("background", "black") == "blur":
            f[0] = f[0][:-3] + ",split[v][b]"
            f.append(f"[b]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     "boxblur=20,colorchannelmixer=rr=.6:gg=.6:bb=.6[bg]")
        else:
            f.append(f"color=c=black:s={W}x{H}:r={src_info['fps_exact']}[bg]")
        band = ""
        if stack and g["fit"]:
            y0, y1 = render.hook_band(g, bool(stack.get("credit")))
            band = f",drawbox=y={y0}:w=iw:h={y1 - y0}:color=black@0.55:t=fill"
        f.append(f"[bg][v]overlay={vx}:{vy}:shortest=1{band}[vv]")
    line1, line2 = drama_lines(stack) if stack else ("", "")
    for t in (line1, line2):
        if t and re.search("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]", t):
            print("Warning: emojis in the Stack lines render as flat outlines in the text colour. "
                  "Put them in the post caption instead.")
    lines = sub_lines(sub, src, job) if sub else []
    style = sub_style(sub, g, house=mode == "original") if sub else None
    if mode == "original":
        g.update(hook_y=0, credit_y=0)
    render.write_ass(os.path.join(HERE, ass_path), g, line1, line2, (stack or {}).get("credit", ""), lines,
                     (sub or {}).get("size", 66), style)
    f.append(f"[vv]ass={ass_path}:fontsdir=fonts,format=yuv420p[out]")
    return f


def music_filter(job, base, base_info, dur, f):
    """Append the music graph to `f`. Returns the extra input args."""
    m = job["music"]
    if not os.path.exists(m["file"]):
        raise Fail(f"Music file not found: {m['file']}")
    mode = m.get("mode", "duck")
    if mode not in ("duck", "replace", "mix"):
        raise Fail(f"Unknown music mode '{mode}'. Use duck, replace or mix.")
    dlg = loudness(base) if "acodec" in base_info else None
    if dlg is None:
        mode = "replace"
    level = m.get("level", "auto")
    if level == "auto":
        mus = loudness(m["file"]) or -14
        gain = ((dlg - 12.5) if mode != "replace" else -14) - mus
    else:
        gain = float(level)
    fo = float(m.get("fade_out", 2.5))
    mus_chain = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},volume={gain:.1f}dB,"
                 f"afade=t=in:d={m.get('fade_in', 0.8)},afade=t=out:st={max(dur - fo, 0):.3f}:d={fo}")
    if mode == "duck":
        f += ["[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[dlg][key]", mus_chain + "[mus]",
              "[mus][key]sidechaincompress=threshold=0.03:ratio=5:attack=30:release=500[duck]",
              "[dlg][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"]
    elif mode == "mix":
        f += ["[0:a]aresample=48000,aformat=channel_layouts=stereo[dlg]", mus_chain + "[mus]",
              "[dlg][mus]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"]
    else:
        f.append(mus_chain + "[aout]")
    print(f"Music: {mode}, {gain:+.1f} dB" + (f" (dialogue {dlg:.1f} LUFS)" if dlg is not None else ""))
    return ["-stream_loop", "-1", "-ss", str(secs(m.get("start_at", 0)) or 0), "-i", m["file"]]


def job_tag(job):
    if job.get("stack"):
        return "stack"
    mode = (job.get("frame") or {}).get("mode", "original")
    if mode != "original":
        return mode.replace(":", "x")
    for key, tag in (("cold_open", "coldopen"), ("subtitles", "subs"), ("images", "edit"), ("trim", "trim"),
                     ("music", "music")):
        if job.get(key):
            return "merged" if len(job["inputs"]) > 1 and tag != "music" else tag
    return "merged" if len(job["inputs"]) > 1 else "copy"


def output_path(job, ext=".mp4"):
    first = job["inputs"][0]
    folder = os.path.dirname(os.path.abspath(first))
    for cand in (os.path.join(folder, "3 Final"), os.path.join(os.path.dirname(folder), "3 Final")):
        if os.path.isdir(cand):
            folder = cand
            break
    name = (job.get("output") or {}).get("name")
    if not name:
        stem = os.path.splitext(os.path.basename(first))[0]
        if len(job["inputs"]) > 1:
            last = os.path.splitext(os.path.basename(job["inputs"][-1]))[0]
            if last != stem:  # scene1 + scene4 -> scene1-4
                stem = f"{stem}-{last[len(os.path.commonprefix([stem, last])):]}"
        name = f"{stem}_{job_tag(job)}"
    path = os.path.join(folder, name + ext)
    n = 2
    while os.path.exists(path) or os.path.abspath(path) in map(os.path.abspath, job["inputs"]):
        path = os.path.join(folder, f"{name}_{n}{ext}")
        n += 1
    return path


def check_sheet(video, out):
    d = info(video)["duration"]
    tiles = []
    for k in range(6):
        t = d * (k + .5) / 6
        png = ff(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=-2:360",
                  "-f", "image2pipe", "-vcodec", "png", "-"], "Making the check image").stdout
        im = Image.open(io.BytesIO(png)).convert("RGB")
        ImageDraw.Draw(im).text((8, 8), stamp(t), font=font(22), fill=(255, 200, 77), stroke_width=2, stroke_fill="black")
        tiles.append(im)
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * 3 + 20, h * 2 + 10), "white")
    for k, im in enumerate(tiles):
        sheet.paste(im, ((k % 3) * (w + 10), (k // 3) * (h + 10)))
    sheet.save(out, quality=85)
    return out


def cmd_run(job_path, preview_at=None):
    job = load_job(job_path)
    quality = (job.get("output") or {}).get("quality", "high")
    if quality not in ("high", "copy", "small"):
        raise Fail(f"Unknown output quality '{quality}'. Use high, copy or small.")
    final_codec = SMALL if quality == "small" else HQ
    infos = [info(f) for f in job["inputs"]]
    out = output_path(job)
    temps = []

    def need_reencode(why):
        if quality == "copy":
            raise Fail(f"This job needs a re-encode ({why}), but output.quality is \"copy\".")

    try:
        # 1) the timeline: one file to work on
        segs = segments(job)
        transition = (job.get("merge") or {}).get("transition", "cut")
        plain = all(not s.get("image") and not s["start"] and s["end"] is None for s in segs)
        co = job.get("cold_open")
        visual_or_music = bool(job.get("stack") or job.get("subtitles") or job.get("music")
                               or (job.get("frame") or {}).get("mode", "original") != "original"
                               or (job.get("frame") or {}).get("remove_burned") or co)
        if preview_at is not None:
            base, base_reencoded = job["inputs"][0], False
        elif len(segs) == 1 and plain:
            base, base_reencoded = segs[0]["file"], False
        elif plain and transition == "cut" and same_format(infos):
            target = tmp(".mp4") if visual_or_music else out
            base, base_reencoded = concat_copy([s["file"] for s in segs], target), False
            if target != out:
                temps.append(target)
            print("Joined directly (no re-encode): the clips match exactly.")
        else:
            need_reencode("trims, images, fades or clips that don't match")
            if not visual_or_music:
                base = assemble(segs, out, final_codec + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"], transition)
            else:
                base = assemble(segs, tmp(".mkv"), LOSSLESS, transition)
                temps.append(base)
            base_reencoded = True
        # 2) cold open: a 2-4 s tease, a flash or cut, then the video from the start
        if co and preview_at is None:
            need_reencode("cold open")
            if co.get("auto"):
                pick = coldopen_candidates(base)[0]
                a, b = pick["start"], pick["end"]
                print(f"Cold open: picked {stamp(a)}–{stamp(b)} ({pick['why']}). Ask for a different moment if you like.")
            else:
                a, b = secs(co["start"]), secs(co["end"])
            flash = co.get("transition", "flash") == "flash"
            cut = assemble([{"file": base, "start": a, "end": b},
                            {"file": base, "start": 0, "end": None, "flash": flash}], tmp(".mkv"), LOSSLESS)
            temps.append(cut)
            base, base_reencoded = cut, True
        if base == out:
            return finish(job, out, infos)

        # 3) one final pass: framing / Stack / subtitles / music
        bi = info(base)
        ass = f"tmp/{uuid.uuid4().hex[:8]}.ass"  # relative: a drive letter (C:) breaks ffmpeg filter syntax
        temps.append(os.path.join(HERE, ass))
        f = visual_filter(job, base, bi, ass)
        if preview_at is not None:
            jpg = os.path.splitext(out)[0] + "_preview.jpg"
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", base]
            maps = ["-filter_complex", ";".join(f), "-map", "[out]"] if f else []
            ff(cmd + maps + ["-ss", str(preview_at), "-frames:v", "1", "-q:v", "2", jpg], "Rendering the preview")
            print(f"preview: {jpg}")
            return jpg
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", base]
        f = f or []
        if job.get("music"):
            cmd += music_filter(job, base, bi, bi["duration"], f)
        vmap = ["-map", "[out]"] if any("[out]" in x for x in f) else ["-map", "0:v"]
        if vmap[1] == "0:v" and not base_reencoded:
            vcodec = ["-c:v", "copy"]  # nothing visual changed: keep the picture untouched
        else:
            need_reencode("framing, Stack or subtitles")
            vcodec = final_codec
        if job.get("music"):
            amap, acodec = ["-map", "[aout]"], ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        elif "acodec" in bi and not base_reencoded and bi["acodec"] in ("aac", "mp3"):
            amap, acodec = ["-map", "0:a"], ["-c:a", "copy"]
        else:
            amap, acodec = ["-map", "0:a?"], ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        fc = ["-filter_complex", ";".join(f)] if f else []
        ff(cmd + fc + vmap + amap + vcodec + acodec + ["-movflags", "+faststart", out], "Exporting")
        return finish(job, out, infos)
    finally:
        for t in temps:
            if os.path.exists(t):
                os.remove(t)


def finish(job, out, infos):
    before = sum(i["size_mb"] for i in infos)
    o = info(out)
    check = check_sheet(out, os.path.splitext(out)[0] + "_check.jpg")
    print(f"output:   {out}\nsize:     {before:.1f} MB -> {o['size_mb']:.1f} MB\n"
          f"duration: {o['duration']:.2f} s   {o.get('width')}x{o.get('height')} @ {o.get('fps')} fps\ncheck:    {check}")
    return out


def cmd_probe(files):
    out = []
    for f in files:
        d = info(os.path.abspath(f))
        if not d["image"]:
            d["loudness_lufs"] = loudness(f) if "acodec" in d else None
            d["burned_subtitles"] = has_burned_subs(f) if "width" in d else False
        out.append(d)
    print(json.dumps(out, indent=1))


def cmd_presets():
    for name, p in load_presets().items():
        print(f"{name}: {p.get('description', '')}")


def story(script, args):
    """Run one of the story-cut scripts with this Python."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "story", "scripts", script), *args])
    if r.returncode:
        raise Fail(f"{script} stopped with an error (see above).")


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipdesk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"Clip Desk {VERSION}")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("probe", help="facts about files, as JSON").add_argument("files", nargs="+")
    sp.add_parser("subs-detect", help="find burned-in subtitle timings").add_argument("video")
    sp.add_parser("coldopen-suggest", help="3 candidate cold-open moments").add_argument("video")
    pv = sp.add_parser("preview", help="one still of the final layout")
    pv.add_argument("job")
    pv.add_argument("--at", help="time in the first input (default: preview_at in the job, else 1/3 in)")
    sp.add_parser("run", help="do the job and export").add_argument("job")
    sp.add_parser("presets", help="list saved presets")
    for name, help_ in (("inspect", "story: log a clip (contact sheet, cuts, freezes, loudness)"),
                        ("render-edl", "story: render a cut from an EDL json"),
                        ("check-seams", "story: check every cut of a rendered EDL")):
        sp.add_parser(name, help=help_, add_help=False).add_argument("args", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    try:
        if a.cmd == "probe":
            cmd_probe(a.files)
        elif a.cmd == "subs-detect":
            cmd_subs_detect(a.video)
        elif a.cmd == "coldopen-suggest":
            cmd_coldopen_suggest(a.video)
        elif a.cmd == "preview":
            job = load_job(a.job)
            at = secs(a.at or job.get("preview_at"))
            if at is None:
                at = info(job["inputs"][0]).get("duration", 3) / 3
            cmd_run(a.job, preview_at=at)
        elif a.cmd == "run":
            cmd_run(a.job)
        elif a.cmd == "presets":
            cmd_presets()
        else:
            story({"inspect": "inspect_clip.py", "render-edl": "render_edl.py",
                   "check-seams": "check_seams.py"}[a.cmd], a.args)
    except Fail as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        if "ffmpeg" in str(e) or "ffprobe" in str(e):
            print("Error: FFmpeg isn't installed or isn't on PATH. Install it with: winget install Gyan.FFmpeg", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
