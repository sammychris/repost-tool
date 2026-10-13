"""Run report (`<name>_report.txt`): one PASS/FAIL line per check, so a render can be verified without looking at it.

Used by `clipdesk run` and story/scripts/render_edl.py. Standard library only.
"""
import json, os, re, subprocess
from fractions import Fraction

TOL_SYNC = 0.04  # seconds


def probe(path):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                                  capture_output=True, text=True, errors="replace").stdout or "{}")
    v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), None)
    a = next((s for s in j.get("streams", []) if s["codec_type"] == "audio"), None)
    return float(j.get("format", {}).get("duration", 0)), v, a


def scan(path, duration, audio, box=None):
    """Frozen spans, black spans (lists of lengths in s) and integrated loudness, in one decode."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:v:0",
           "-vf", (f"crop={box[0]}:{box[1]}:{box[2]}:{box[3]}," if box else "") + "scale=320:-2,freezedetect=d=0.5,blackdetect=d=0.2"]
    if audio:
        cmd += ["-map", "0:a:0", "-af", "ebur128=framelog=quiet"]
    err = subprocess.run(cmd + ["-f", "null", "-"], capture_output=True, text=True, errors="replace").stderr
    starts = [float(x) for x in re.findall(r"freeze_start: (-?[\d.]+)", err)]
    lens = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err)]
    lens += [duration - s for s in starts[len(lens):]]  # a freeze that runs to the end has no duration line
    black = [float(x) for x in re.findall(r"black_duration:([\d.]+)", err)]
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return lens, black, (float(lufs[-1]) if lufs else None)


def av_gaps(v, a, dur):
    """(start offset, duration gap) between the audio and video streams, in seconds."""
    return (abs(float(a.get("start_time", 0)) - float(v.get("start_time", 0))),
            abs(float(a.get("duration", dur)) - float(v.get("duration", dur))))


def unexpected(spans, expected):
    # shortcut: matches spans by length, not by position; a short new freeze passes if the source has a longer one anywhere
    return [s for s in spans if not any(e >= s - 0.1 for e in expected)]


def write(out, videos, planned, path, images=(), loud_target=None, other_sources=(), box=None):
    """Check `out` against its source `videos` (plus still `images` as (file, seconds)). Returns the number of FAILs.
    `box` = (w, h, x, y) of the video inside a taller layout (Stack, 9:16), so text and bars don't hide motion."""
    dur, v, a = probe(out)
    fps = float(Fraction(v["r_frame_rate"])) if v else 25
    lines = []
    add = lambda name, ok, detail: lines.append(f"{name:<9}{ok:<6}{detail}")

    tol = 2 / fps
    add("LENGTH", "PASS" if abs(dur - planned) <= tol else "FAIL",
        f"planned {planned:.2f}s  actual {dur:.2f}s  (tolerance 2 frames)")

    if v and a:
        off, gap = av_gaps(v, a, dur)
        src = (0, 0)  # an output may keep a gap the source already had
        for s in videos:
            sd, sv, sa = probe(s)
            if sv and sa:
                src = tuple(map(max, src, av_gaps(sv, sa, sd)))
        extra = f", source gap {src[1]:.2f}s" if src[1] > 0.005 else ""
        add("SYNC", "PASS" if off <= TOL_SYNC + src[0] and gap <= TOL_SYNC + src[1] else "FAIL",
            f"audio-video start offset {off:.2f}s, duration gap {gap:.2f}s{extra}  (fail above {TOL_SYNC}s more than the source)")
    else:
        add("SYNC", "INFO", "no audio stream" if v else "no video stream")

    frozen, black, lufs = scan(out, dur, bool(a), box)
    src_frozen, src_black = [d for _, d in images], [d for _, d in images]  # stills are meant to be still
    for s in videos:
        sd = probe(s)[0]
        f, b, _ = scan(s, sd, False)
        src_frozen += f
        src_black += b
    for name, spans, exp, rule in (("FROZEN", frozen, src_frozen, "freezedetect, fail on any frozen span over 0.5s not present in the source"),
                                   ("BLACK", black, src_black, "blackdetect, fail on black over 0.2s not present in the source")):
        bad = unexpected(spans, exp)
        detail = ", ".join(f"{s:.2f}s" for s in bad) + " new" if bad else \
            (f"{len(spans)} span(s), all in the source" if spans else "none")
        add(name, "FAIL" if bad else "PASS", f"{detail}  ({rule})")

    if lufs is None:
        add("LOUDNESS", "INFO", "no audio")
    elif loud_target is None:
        add("LOUDNESS", "INFO", f"{lufs:.1f} LUFS  (no target: the original level is kept)")
    else:
        add("LOUDNESS", "PASS" if abs(lufs - loud_target) <= 1 else "FAIL",
            f"{lufs:.1f} LUFS  (target {loud_target:g}, tolerance 1 LU)")

    vs = f"video {v['codec_name']} {v['width']}x{v['height']} {fps:.3g}fps" if v else "no video"
    as_ = f"audio {a['codec_name']} {int(a['sample_rate']) // 1000}kHz {a.get('channel_layout') or str(a['channels']) + 'ch'}" if a else "no audio"
    src_audio = any(probe(s)[2] for s in videos)
    add("STREAMS", "PASS" if v and (a or not src_audio) else "FAIL", f"{vs}, {as_}")

    src_mb = sum(os.path.getsize(s) for s in [*videos, *(f for f, _ in images), *other_sources]) / 1e6
    add("SIZE", "INFO", f"sources {src_mb:.1f} MB -> output {os.path.getsize(out) / 1e6:.1f} MB")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return sum(l[9:13] == "FAIL" for l in lines)


if __name__ == "__main__":  # self-check of the span matching
    assert unexpected([0.6, 2.0], [2.5]) == []
    assert unexpected([0.6, 3.0], [2.5]) == [3.0]
    assert unexpected([1.0], []) == [1.0]
    print("report ok")
