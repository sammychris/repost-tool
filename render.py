"""Turn a finished AI clip into a ready-to-post social video. Usage: python render.py --help"""
import argparse, json, os, subprocess, uuid
import captions

HERE = os.path.dirname(os.path.abspath(__file__))
# W, H, text scale, hook centre y, credit centre y, video box top (None = fit the whole frame), caption top
SIZES = {
    "9:16": (1080, 1920, 1, 340, 500, 565, 1290),
    "4:5": (1080, 1350, 1, 190, 320, 360, 1060),
    "1:1": (1080, 1080, .75, 95, 185, 212, 900),
    "16:9": (1920, 1080, 1, 130, 235, None, 860),
}
BOX_H = 675  # height of the video box for a 16:10 crop
GOLD = "&H4DC8FF&"  # ASS colours are BGR: this is #FFC84D


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return {"w": int(v["width"]), "h": int(v["height"]), "fps": v["r_frame_rate"],
            "dur": float(info["format"]["duration"]),
            "audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def geometry(sw, sh, size="9:16", zoom=None, pan=0.0, burned=False, stack=True):
    """Where everything goes. Shared by the export and the live preview so they always agree.

    Box mode (stack layout, landscape clip, not 16:9): a centred crop ~1/zoom of the source width,
    at most 16:10, placed full-width in the video box. Fit mode (no stack, 16:9, or portrait clip):
    zoom 1 = the source width fits the frame width; higher zoom crops in and fills more of the frame.
    Portrait clips are never cropped. Burned subs: keep 2.5%-86% of the height. pan -1..1 slides the crop.
    """
    W, H, s, hook_y, credit_y, box_y, cap_y = SIZES[size]
    portrait = sh >= sw
    fit = box_y is None or portrait or not stack
    top, bottom = (0.025 * sh, 0.86 * sh) if burned else (0, sh)
    if fit:
        k = min(W / sw, H / (bottom - top)) if portrait else W / sw * (zoom or 1)
        cw, ch = min(sw, W / k), min(bottom - top, H / k)
    else:
        cw = sw / (zoom or 1.35)
        ch = min(cw / 1.6, bottom - top)
    even = lambda x: int(round(x)) // 2 * 2
    cw, ch = even(cw), even(ch)
    cx = even((sw - cw) / 2 * (1 + max(-1, min(1, pan))))
    cy = even(top if burned else top + (bottom - top - ch) / 2)
    if fit:
        r = min(W / cw, H / ch)
        vw, vh = even(cw * r), even(ch * r)
        vx, vy = (W - vw) // 2, (H - vh) // 2
    else:
        vw, vh = W, even(W * ch / cw)
        vx, vy = 0, box_y + (BOX_H - vh) // 2
    return dict(W=W, H=H, s=s, hook_y=hook_y, credit_y=credit_y, cap_y=cap_y, fit=fit,
                crop=(cw, ch, cx, cy), video=(vw, vh, vx, vy))


def hook_band(g, has_credit):
    """Dark band behind the hook in fit mode, where the text sits on top of the picture."""
    s = g["s"]
    return max(0, int(g["hook_y"] - 140 * s)), int((g["credit_y"] if has_credit else g["hook_y"] + 100 * s) + 40 * s)


def ass_time(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{t % 60:05.2f}"


def clean(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").strip()


def write_ass(path, g, hook1, hook2, credit, lines, cap_size, sub_style=None):
    W, H, s = g["W"], g["H"], g["s"]
    px = lambda v: round(v * s)
    cap = px(cap_size)
    ev = []
    if hook1 or hook2:
        text = "\\N".join(filter(None, [clean(hook1), hook2 and "{\\c" + GOLD + "}" + clean(hook2)]))
        ev.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,Hook,,0,0,0,,{{\\pos({W // 2},{g['hook_y']})}}{text}")
    if credit:
        ev.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,Credit,,0,0,0,,{{\\pos({W // 2},{g['credit_y']})}}{clean(credit)}")
    ev += [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Sub,,0,0,0,,{clean(t)}" for a, b, t in lines]
    # Built outside the big f-string: a nested f-string reusing its quotes needs Python 3.12.
    sub_style = sub_style or (f"Style: Sub,Inter Display ExtraBold,{cap},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
                              f"0,0,0,0,100,100,0,0,1,{round(5 * cap / 66)},0,8,{px(70)},{px(70)},{g['cap_y']},1")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Inter Display Black,{px(78)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,{px(60)},{px(60)},0,1
Style: Credit,Inter Display Medium,{px(34)},&H00A0A0A0,&H00A0A0A0,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,{px(60)},{px(60)},0,1
{sub_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n")


def build(a):
    """Return the ffmpeg command for options `a` (argparse namespace or anything with the same fields)."""
    src = os.path.abspath(a.video)
    info = probe(src)
    g = geometry(info["w"], info["h"], a.size, a.zoom, a.pan, a.burned_subs)
    W, H = g["W"], g["H"]
    lines = []
    if a.captions == "auto":
        lines = captions.group(captions.transcribe(src, a.whisper_model))
        txt = os.path.splitext(a.out)[0] + ".captions.txt"
        captions.save(lines, txt)
        print(f"Captions saved to {txt} - edit it and re-run with --captions \"{txt}\" to fix mishearings.")
    elif a.captions != "none":
        lines = captions.load(a.captions)

    os.makedirs(os.path.join(HERE, "tmp"), exist_ok=True)
    ass = f"tmp/{uuid.uuid4().hex[:8]}.ass"  # relative: a drive letter (C:) breaks ffmpeg filter syntax
    write_ass(os.path.join(HERE, ass), g, a.hook1, a.hook2, a.credit, lines, a.caption_size)

    (cw, ch, cx, cy), (vw, vh, vx, vy) = g["crop"], g["video"]
    sharpen = "" if g["fit"] else ",unsharp=5:5:0.4"
    f = [f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={vw}:{vh}:flags=lanczos{sharpen},setsar=1[v]"]
    if a.background == "blur":
        f[0] = f[0][:-3] + ",split[v][b]"
        f.append(f"[b]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 "boxblur=20,colorchannelmixer=rr=.6:gg=.6:bb=.6[bg]")
    else:
        f.append(f"color=c=black:s={W}x{H}:r={info['fps']}[bg]")
    band = ""
    if g["fit"] and (a.hook1 or a.hook2):
        y0, y1 = hook_band(g, bool(a.credit))
        band = f",drawbox=y={y0}:w=iw:h={y1 - y0}:color=black@0.55:t=fill"
    f.append(f"[bg][v]overlay={vx}:{vy}:shortest=1{band},ass={ass}:fontsdir=fonts,format=yuv420p[out]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats", "-i", src]
    if a.preview is not None:
        return cmd + ["-filter_complex", ";".join(f), "-map", "[out]", "-ss", str(a.preview),
                      "-frames:v", "1", a.out], ass

    audio = ["-map", "0:a?"]
    if a.music:
        dur = info["dur"]
        cmd += ["-stream_loop", "-1", "-ss", str(a.music_start), "-i", os.path.abspath(a.music)]
        mus = (f"[1:a]aresample=48000,atrim=0:{dur:.3f},volume={a.music_volume}dB,"
               f"afade=t=in:d={a.fade_in},afade=t=out:st={max(dur - a.fade_out, 0):.3f}:d={a.fade_out}")
        if a.music_mode == "duck" and info["audio"]:
            f += ["[0:a]aresample=48000,asplit=2[dlg][key]", mus + "[mus]",
                  "[mus][key]sidechaincompress=threshold=0.03:ratio=5:attack=30:release=500[duck]",
                  "[dlg][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"]
        else:  # replace mode, or a clip with no sound to duck under
            f.append(mus + "[aout]")
        audio = ["-map", "[aout]"]
    return cmd + ["-filter_complex", ";".join(f), "-map", "[out]", *audio,
                  "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high",
                  "-maxrate", "12M", "-bufsize", "24M", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                  "-movflags", "+faststart", a.out], ass


def out_suffix(size):
    return "_vertical" if size == "9:16" else "_" + size.replace(":", "x")


def render(a):
    if not a.out:
        base = os.path.splitext(os.path.abspath(a.video))[0]
        a.out = base + ("_preview.png" if a.preview is not None else out_suffix(a.size) + ".mp4")
    a.out = os.path.abspath(a.out)
    cmd, ass = build(a)
    try:
        subprocess.run(cmd, cwd=HERE, check=True)
    finally:
        remove_quietly(os.path.join(HERE, ass))
    return a.out


def remove_quietly(path):
    """Delete a temp file; if deleting isn't allowed (some remote shells block it), leave it in tmp/."""
    try:
        os.remove(path)
    except OSError:
        pass


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video")
    p.add_argument("-o", "--out", help="default: <name>_vertical.mp4 (or _4x5, _1x1, _16x9) next to the input")
    p.add_argument("--size", choices=list(SIZES), default="9:16")
    p.add_argument("--hook1", default="", help="hook line 1 (white)")
    p.add_argument("--hook2", default="", help="hook line 2 (gold)")
    p.add_argument("--credit", default="", help='e.g. "Inspired by Pulp Fiction (1994)"')
    p.add_argument("--captions", default="none", help="none | auto | path to an edited .captions.txt")
    p.add_argument("--caption-size", type=int, default=66)
    p.add_argument("--whisper-model", default="small", help="small, or base.en for speed")
    p.add_argument("--burned-subs", action="store_true", help="crop out a subtitle band burned into the source")
    p.add_argument("--zoom", type=float, help="1 = whole width fits; default 1.35 (1 for portrait clips and 16:9)")
    p.add_argument("--pan", type=float, default=0, help="-1 (left edge) .. 1 (right edge)")
    p.add_argument("--background", choices=["black", "blur"], default="black")
    p.add_argument("--music", help="MP3/WAV/M4A")
    p.add_argument("--music-mode", choices=["duck", "replace"], default="duck")
    p.add_argument("--music-volume", type=float, default=-12, help="dB")
    p.add_argument("--music-start", type=float, default=0, help="seconds into the song")
    p.add_argument("--fade-in", type=float, default=0.8)
    p.add_argument("--fade-out", type=float, default=2.5)
    p.add_argument("--preview", type=float, help="render one still at this second instead of the video")
    return p


if __name__ == "__main__":
    print(render(parser().parse_args()))
