#!/usr/bin/env python
"""Log a generated clip before editing: facts, shot changes, frozen spans, silences, a contact sheet and a spectrogram.

usage: python inspect_clip.py clip.mp4 [outdir] [--every 0.5]

Writes into outdir (default: <clip>-log/):
  contact.jpg      one frame every N seconds, time-stamped, to review shot by shot
  spectrogram.png  audio picture: voices = bands, bells = thin bright lines, blasts = full-height columns
  log.txt          resolution, fps, length, detected cuts, frozen spans, silences, loudness
"""
import subprocess, sys, os, re, json


FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fonts", "InterDisplay-ExtraBold.ttf")

def ff_path(p):
    """A file path inside an ffmpeg filter: forward slashes and an escaped drive colon (C\\:/...)."""
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def main():
    clip = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else os.path.splitext(clip)[0] + "-log"
    every = float(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 0.5
    os.makedirs(out, exist_ok=True)
    info = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", clip]).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_a = any(s["codec_type"] == "audio" for s in info["streams"])
    dur = float(info["format"]["duration"])
    lines = [f"file: {clip}", f"size: {v['width']}x{v['height']}  fps: {v['r_frame_rate']}  length: {dur:.3f}s  audio: {has_a}"]

    # shot changes (hard cuts inside the generated clip)
    r = run(["ffmpeg", "-hide_banner", "-i", clip, "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"])
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    lines.append("cuts at: " + ", ".join(f"{c:.3f}" for c in cuts))
    edges = [0.0] + cuts + [dur]
    for n in range(len(edges) - 1):
        lines.append(f"  shot {n+1}: {edges[n]:.3f} - {edges[n+1]:.3f}  ({edges[n+1]-edges[n]:.2f}s)")

    # frozen spans (AI clips often freeze at the tail or stall mid-shot)
    r = run(["ffmpeg", "-hide_banner", "-i", clip, "-vf", "freezedetect=n=0.003:d=0.4", "-an", "-f", "null", "-"])
    fs = re.findall(r"freeze_start: ([\d.]+)", r.stderr); fe = re.findall(r"freeze_end: ([\d.]+)", r.stderr)
    lines.append("frozen: " + (", ".join(f"{a}-{b}" for a, b in zip(fs, fe + [f'{dur:.3f}'] * len(fs))) or "none"))

    if has_a:
        r = run(["ffmpeg", "-hide_banner", "-i", clip, "-af", "silencedetect=n=-40dB:d=0.3", "-vn", "-f", "null", "-"])
        ss = re.findall(r"silence_start: ([\d.]+)", r.stderr); se = re.findall(r"silence_end: ([\d.]+)", r.stderr)
        lines.append("silences: " + (", ".join(f"{a}-{b}" for a, b in zip(ss, se)) or "none"))
        r = run(["ffmpeg", "-hide_banner", "-i", clip, "-af", "ebur128", "-vn", "-f", "null", "-"])
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)   # last match = the summary
        lines.append(f"loudness: {m[-1] if m else '?'} LUFS integrated")
        w = max(1200, int(dur * 80))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip, "-lavfi",
             f"showspectrumpic=s={w}x400:legend=1:color=intensity:scale=log", os.path.join(out, "spectrogram.png")])

    n = int(dur / every)
    cols = 6
    rows = (n + cols - 1) // cols
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip, "-vf",
         f"fps=1/{every},scale=320:-2,drawtext=fontfile='{ff_path(FONT)}':"
         f"text='%{{pts\\:hms}}':x=6:y=6:fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.6,"
         f"tile={cols}x{rows}", "-frames:v", "1", os.path.join(out, "contact.jpg")])
    with open(os.path.join(out, "log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"wrote {out}/contact.jpg" + (f", {out}/spectrogram.png" if has_a else ""))

if __name__ == "__main__":
    main()
