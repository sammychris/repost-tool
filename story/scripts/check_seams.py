#!/usr/bin/env python
"""Self-check a rendered cut: one strip per cut (last frame before | first frame after) plus the audio level around it.

usage: python check_seams.py edl.json [rendered.mp4] [outdir]

Writes seams.jpg (one row per cut, labelled) and prints, per cut, the peak level in the 60 ms around it
(a jump of 10 dB or more against the neighbouring 200 ms usually means a pop or a hard audio cut).
"""
import json, subprocess, sys, os, re


FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "fonts", "InterDisplay-ExtraBold.ttf")

def ff_path(p):
    """A file path inside an ffmpeg filter: forward slashes and an escaped drive colon (C\\:/...)."""
    return os.path.abspath(p).replace("\\", "/").replace(":", "\\:")

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def peak(f, t, d):
    r = run(["ffmpeg", "-hide_banner", "-ss", f"{max(t,0):.3f}", "-t", f"{d:.3f}", "-i", f,
             "-af", "astats=metadata=0", "-vn", "-f", "null", "-"])
    m = re.findall(r"Peak level dB:\s*(-?[\d.inf]+)", r.stderr)
    return m[-1] if m else "?"

def main():
    e = json.load(open(sys.argv[1], encoding="utf-8-sig"))
    base = os.path.dirname(os.path.abspath(sys.argv[1]))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, e.get("output", "final.mp4"))
    od = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(out)[0] + "-seams"
    os.makedirs(od, exist_ok=True)
    t, cuts = 0.0, []
    for s in e["segments"][:-1]:
        t += max(1, round((s["out"] - s["in"]) * e.get("fps", 30))) / e.get("fps", 30); cuts.append((t, s.get("note", "")))
    # 1) flash test: every real picture change in the render must sit on an EDL cut
    r = run(["ffmpeg", "-hide_banner", "-i", out, "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"])
    found = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    fps = e.get("fps", 30); bounds = [c for c, _ in cuts]
    starts = [0.0] + bounds
    problems = 0
    for f in found:
        if any(abs(f - b) <= 1.5 / fps for b in bounds):
            continue
        seg = max(i for i, st in enumerate(starts) if st <= f + 1e-6)
        end = bounds[seg] if seg < len(bounds) else None
        segs = e["segments"]
        cont = seg + 1 < len(segs) and segs[seg]["v"] == segs[seg+1]["v"] and abs(segs[seg]["out"] - segs[seg+1]["in"]) < 0.01
        if end is not None and end - f <= 0.25 and cont:
            print(f"NOTE   source cuts at {f:.3f}s inside a continuous run (segments {seg+1}-{seg+2} play the source straight through) — fine")
        elif end is not None and end - f <= 0.25:
            print(f"FLASH  picture changes at {f:.3f}s, {round((end-f)*fps)} frame(s) before cut {seg+1} -> pull segment {seg+1} out-point earlier by {end-f+1/fps:.3f}s (unless the source is continuous into the next segment)")
        elif seg > 0 and f - starts[seg] <= 0.25:
            print(f"FLASH  picture changes at {f:.3f}s, just after cut {seg} -> push segment {seg+1} in-point later by {f-starts[seg]+1/fps:.3f}s")
        else:
            print(f"NOTE   source cut inside segment {seg+1} at {f:.3f}s (fine if that shot change is wanted)")
        problems += 1
    if not problems:
        print("flash test: clean — every picture change sits on an EDL cut")
    rows = []
    for n, (c, note) in enumerate(cuts):
        a = os.path.join(od, f"c{n:02d}a.png"); b = os.path.join(od, f"c{n:02d}b.png")
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{c-0.05:.3f}", "-i", out, "-frames:v", "1", "-vf", "scale=400:-2", a])
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{c+0.02:.3f}", "-i", out, "-frames:v", "1", "-vf", "scale=400:-2", b])
        row = os.path.join(od, f"row{n:02d}.png")
        label = f"cut {n+1} at {c:.2f}s".replace(":", "")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", a, "-i", b, "-filter_complex",
             f"[0][1]hstack,pad=iw:ih+30:0:30:black,drawtext=fontfile='{ff_path(FONT)}':"
             f"text='{label}':x=8:y=6:fontsize=20:fontcolor=yellow", row])
        rows.append(row)
        print(f"cut {n+1:2d} {c:7.3f}s  peak@cut {peak(out, c-0.03, 0.06):>6} dB  before {peak(out, c-0.25, 0.2):>6}  after {peak(out, c+0.05, 0.2):>6}  | {note}")
    if rows:
        args = []
        for r in rows: args += ["-i", r]
        run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex",
             "".join(f"[{i}]" for i in range(len(rows))) + f"vstack=inputs={len(rows)}" if len(rows) > 1 else "null",
             os.path.join(od, "seams.jpg")])
    print(f"wrote {od}/seams.jpg — look at every row: same person same side? screen direction kept? no duplicate face? no frozen frame?")

if __name__ == "__main__":
    main()
