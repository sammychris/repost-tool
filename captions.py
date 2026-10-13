"""Auto-captions: faster-whisper words -> short lines -> editable text file -> ASS events."""
import re

MAX_CHARS, MAX_DUR, MAX_GAP = 32, 2.5, 0.6


def transcribe(video, model="small"):
    """Return [(start, end, word)] for every spoken word. The model downloads once, then works offline."""
    import subprocess, numpy as np
    from faster_whisper import WhisperModel
    # Decode with ffmpeg, not faster-whisper's PyAV: newer PyAV breaks its decode_audio.
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    segments, _ = WhisperModel(model, device="cpu", compute_type="int8").transcribe(
        np.frombuffer(pcm, np.float32), word_timestamps=True, vad_filter=True)
    return [(w.start, w.end, w.word.strip()) for s in segments for w in s.words if w.word.strip()]


def group(words):
    """Group words into lines of <= MAX_CHARS and <= MAX_DUR, splitting at punctuation and pauses."""
    lines, cur = [], []
    for start, end, word in words:
        if cur:
            text = " ".join(w for _, _, w in cur)
            if (len(text) + 1 + len(word) > MAX_CHARS or end - cur[0][0] > MAX_DUR
                    or start - cur[-1][1] > MAX_GAP or text[-1] in ".?!,;:"):
                lines.append([cur[0][0], cur[-1][1], text])
                cur = []
        cur.append((start, end, word))
    if cur:
        lines.append([cur[0][0], cur[-1][1], " ".join(w for _, _, w in cur)])
    # Overlapping cues make libass stack them (a line jumps lower), so end each just before the next.
    for a, b in zip(lines, lines[1:]):
        a[1] = min(a[1], b[0] - 0.01)
    return [tuple(l) for l in lines]


def save(lines, path):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(f"{s:.2f}-{e:.2f} | {t}\n" for s, e, t in lines)


def load(path):
    return parse(open(path, encoding="utf-8-sig").read(), path)


def parse(text, where="captions"):
    """Read edited captions. Format per line: `1.20-2.85 | text`."""
    lines = []
    for n, row in enumerate(text.splitlines(), 1):
        if not row.strip():
            continue
        m = re.match(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*\|\s*(.*)", row)
        if not m:
            raise ValueError(f"{where} line {n}: expected `start-end | text`, got: {row.strip()}")
        lines.append((float(m[1]), float(m[2]), m[3].strip()))
    return lines


if __name__ == "__main__":  # self-check
    out = group([(0, .3, "Who's"), (.3, .6, "on"), (.6, 1, "first?"), (1.05, 1.4, "Yes."),
                 (3, 3.5, "after"), (3.5, 4, "a"), (4, 4.4, "pause")])
    assert out == [(0, 1, "Who's on first?"), (1.05, 1.4, "Yes."), (3, 4.4, "after a pause")], out
    assert all(len(t) <= MAX_CHARS for *_, t in group([(i * .2, i * .2 + .15, "word") for i in range(20)]))
    print("captions ok")
