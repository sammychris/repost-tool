#!/usr/bin/env python
"""Render a finished cut from a JSON edit decision list (EDL).

usage: python render_edl.py edl.json [--preview] [--native-size]

--native-size: when every video source has the same size and fps, keep that size/fps instead of the EDL's
width/height/fps. Without the flag the EDL values are used (old behaviour). Quality stays CRF 17 either way.

EDL format (see references/edl-example.json):
{
  "output": "final.mp4",
  "width": 1920, "height": 1080, "fps": 30,
  "loudness": -16, "true_peak": -1.5,
  "sources": {"T2": "/path/take2.mp4", "FA": "/path/fixA.mp4"},
  "segments": [
    {"note": "wide entry", "v": "T2", "in": 0.0, "out": 4.208},          # audio defaults to same source/range
    {"note": "bell insert", "v": "FA", "in": 0.20, "out": 1.57,
     "audio": [{"src": "T2", "in": 16.45, "out": 16.95}, {"src": "ROOMTONE", "dur": 0.87}]},
    {"note": "blast", "v": "T2", "in": 16.95, "out": 18.75,
     "shake": {"at": 0.25, "frames": 5, "px": 14}}
  ],
  "subtitles": "subs.ass"          # optional, burned LAST
}
Audio entries: {"src", "in", "out", "gain"(dB, optional)} or {"src": "ROOMTONE", "dur"} or {"src":"SILENCE","dur"}.
Every audio piece gets 20 ms fades; the segment's audio is padded/trimmed to the picture length.
"""
import json, subprocess, sys, os, re, tempfile
from fractions import Fraction
from collections import Counter

FADE = 0.02

def load(path):
    with open(path, encoding="utf-8-sig") as f:
        e = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    rel = lambda p: p if os.path.isabs(p) else os.path.join(base, p)
    e["sources"] = {k: rel(v) for k, v in e["sources"].items()}
    e["output"] = rel(e.get("output", "final.mp4"))
    if e.get("subtitles"):
        e["subtitles"] = rel(e["subtitles"])
    return e

def build(e, audio_only=False, loud=None):
    W, H, FPS = e.get("width", 1920), e.get("height", 1080), e.get("fps", 30)
    segs = e["segments"]
    for s in segs:
        if "audio" not in s:
            s["audio"] = [{"src": s["v"], "in": s["in"], "out": s["out"]}]
    srcs, idx = [], {}
    def inp(k):
        if k not in idx:
            idx[k] = len(srcs); srcs.append(e["sources"][k])
        return idx[k]
    vuse, ause = Counter(), Counter()
    for s in segs:
        if not audio_only:
            inp(s["v"]); vuse[s["v"]] += 1
        for a in s["audio"]:
            if a["src"] in ("ROOMTONE", "SILENCE"):
                continue
            inp(a["src"]); ause[a["src"]] += 1
    fc = []
    for k, i in idx.items():
        if vuse[k]:
            fc.append(f"[{i}:v]split={vuse[k]}" + "".join(f"[vs{i}_{n}]" for n in range(vuse[k])))
        if ause[k]:
            fc.append(f"[{i}:a]asplit={ause[k]}" + "".join(f"[as{i}_{n}]" for n in range(ause[k])))
    vc, ac = Counter(), Counter()
    for n, s in enumerate(segs):
        nf = max(1, round((s["out"] - s["in"]) * FPS))   # snap every segment to whole output frames
        d = nf / FPS                                        # so picture and sound stay locked across all cuts
        if not audio_only:
            i = idx[s["v"]]; k = vc[s["v"]]; vc[s["v"]] += 1
            chain = (f"[vs{i}_{k}]trim={s['in']}:{s['out']},setpts=PTS-STARTPTS,"
                     f"scale={W}:{H}:flags=lanczos,fps={FPS},tpad=stop_mode=clone:stop=2,"
                     f"trim=end_frame={nf},setpts=PTS-STARTPTS,format=yuv420p,setsar=1")
            sh = s.get("shake")
            if sh:  # short decaying post shake (impact recoil), done in post, never generated
                a0 = sh["at"]; dur = sh.get("frames", 5) / FPS; px = sh.get("px", 12)
                pad = 2 * px
                chain += (f",scale={W+2*pad}:{H+2*pad},crop={W}:{H}:"
                          f"x='{pad}+if(between(t,{a0},{a0+dur}),{px}*sin(t*90)*({a0+dur}-t)/{dur},0)':"
                          f"y='{pad}+if(between(t,{a0},{a0+dur}),{px}*cos(t*77)*({a0+dur}-t)/{dur},0)',setsar=1")
            fc.append(chain + f"[v{n}]")
        parts = []
        for m, a in enumerate(s["audio"]):
            lab = f"[a{n}_{m}]"
            if a["src"] in ("ROOMTONE", "SILENCE"):
                dd = a["dur"]
                amp = 0.004 if a["src"] == "ROOMTONE" else 0
                fc.append(f"anoisesrc=color=brown:amplitude={amp}:sample_rate=48000:duration={dd},"
                          f"aformat=channel_layouts=stereo{lab}")
            else:
                j = idx[a["src"]]; kk = ac[a["src"]]; ac[a["src"]] += 1
                dd = a["out"] - a["in"]
                fc.append(f"[as{j}_{kk}]atrim={a['in']}:{a['out']},asetpts=PTS-STARTPTS,aresample=48000,"
                          f"aformat=channel_layouts=stereo,volume={a.get('gain', 0)}dB,"
                          f"afade=t=in:d={FADE},afade=t=out:st={max(dd - FADE, 0):.3f}:d={FADE}{lab}")
            parts.append(lab)
        if len(parts) > 1:
            fc.append("".join(parts) + f"concat=n={len(parts)}:v=0:a=1[ac{n}]")
        else:
            fc.append(f"{parts[0]}anull[ac{n}]")
        fc.append(f"[ac{n}]apad,atrim=0:{d:.4f},asetpts=PTS-STARTPTS[a{n}]")
    N = len(segs)
    I, TP = e.get("loudness", -16), e.get("true_peak", -1.5)
    if audio_only:
        fc.append("".join(f"[a{n}]" for n in range(N)) + f"concat=n={N}:v=0:a=1[amix]")
        fc.append(f"[amix]loudnorm=I={I}:TP={TP}:LRA=11:print_format=json[a]")
    else:
        fc.append("".join(f"[v{n}][a{n}]" for n in range(N)) + f"concat=n={N}:v=1:a=1[vc][amix]")
        if loud:
            ln = (f"loudnorm=I={I}:TP={TP}:LRA=11:measured_I={loud['input_i']}:measured_TP={loud['input_tp']}:"
                  f"measured_LRA={loud['input_lra']}:measured_thresh={loud['input_thresh']}:"
                  f"offset={loud['target_offset']}:linear=true")
        else:
            ln = f"loudnorm=I={I}:TP={TP}:LRA=11"
        fc.append(f"[amix]{ln},alimiter=limit=0.9:latency=1,aresample=48000[a]")
        if e.get("subtitles"):  # subtitles burned LAST, after every other video filter
            sub = e["subtitles"].replace("\\", "/").replace(":", "\\:")
            fc.append(f"[vc]ass='{sub}'[v]")
        else:
            fc.append("[vc]null[v]")
    return srcs, ";".join(fc)

def native_size(e):
    """Use the sources' own size/fps when all video sources match; otherwise keep the EDL's."""
    used = sorted({s["v"] for s in e["segments"]})
    fmt = {}
    for k in used:
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                          "stream=width,height,r_frame_rate", "-of", "json", e["sources"][k]],
                                         capture_output=True, text=True).stdout)["streams"][0]
        fmt[k] = (info["width"], info["height"], info["r_frame_rate"])
    if len(set(fmt.values())) == 1:
        w, h, r = next(iter(fmt.values()))
        e["width"], e["height"], e["fps"] = w, h, float(Fraction(r))
        print(f"native size: {w}x{h} @ {e['fps']:.3f} fps (all sources match)")
    else:
        print("native size: sources differ (" + ", ".join(f"{k} {w}x{h}@{r}" for k, (w, h, r) in fmt.items())
              + f") -> using the EDL's {e.get('width', 1920)}x{e.get('height', 1080)}")

def main():
    e = load(sys.argv[1])
    preview = "--preview" in sys.argv
    if "--native-size" in sys.argv:
        native_size(e)
    # pass 1: measure loudness of the audio mix only (fast)
    srcs, fc = build(json.loads(json.dumps(e)), audio_only=True)
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for f in srcs: cmd += ["-i", f]
    cmd += ["-filter_complex", fc, "-map", "[a]", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    loud = json.loads(m[-1]) if m else None
    # pass 2: full render
    srcs, fc = build(e, loud=loud)
    out = e["output"]
    if preview:
        out = os.path.splitext(out)[0] + "-preview.mp4"
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    for f in srcs: cmd += ["-i", f]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
            "-crf", "26" if preview else "17", "-preset", "veryfast" if preview else "medium",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    FPS = e.get("fps", 30)
    total = sum(max(1, round((s["out"] - s["in"]) * FPS)) / FPS for s in e["segments"])
    got = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    print(f"wrote {out}\nEDL length {total:.3f}s | file length {got:.3f}s | diff {got-total:+.3f}s")
    src_mb = sum(os.path.getsize(e["sources"][k]) for k in {s["v"] for s in e["segments"]}) / 1e6
    print(f"size: sources {src_mb:.1f} MB total -> output {os.path.getsize(out) / 1e6:.1f} MB")
    t = 0
    print("cut points (output timeline):")
    for n, s in enumerate(e["segments"]):
        print(f"  {t:7.3f}s  seg {n+1:2d}  {s['v']} {s['in']}-{s['out']}  {s.get('note','')}")
        t += max(1, round((s["out"] - s["in"]) * FPS)) / FPS
    if loud:
        print(f"loudness in: {loud['input_i']} LUFS, true peak {loud['input_tp']} dBTP")

if __name__ == "__main__":
    main()
