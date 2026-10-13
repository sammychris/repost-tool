# Clip Desk: tool guide

**Version:** 1.0.0 (2026-10-13)
**Folder:** `C:\Users\SAMMY\Desktop\video tool`
**Rule:** whenever the tool changes, update this guide and bump the version (patch = fix, minor = new command or field).

Clip Desk is one engine with two doors:
- **Web page:** double-click `run.bat`. A local page opens with drag and drop, a live framing preview, captions and export (exports go to `Videos\Repost Tool`).
- **Command mode:** `clipdesk.bat <command> ...` from any folder. This is what Claude uses.

Before a job, read `style-notes.md` (the user's preferences).

## Rules the engine follows
1. **Original by default.** Anything not in the job stays as it was.
2. **Quality never drops.** Re-encodes use `libx264 -crf 17 -preset medium`, at the source resolution and fps. There's no bitrate cap, so a heavier file is fine. Intermediate steps are lossless.
3. **No needless re-processing.**
   - Audio-only jobs (music) copy the picture untouched (`-c:v copy`). Verified bit-identical.
   - Clips that match exactly (codec, size, fps, pixel format, audio format) and need no trims are joined directly (`-c copy`).
   - Anything else is re-encoded, for safety.
4. **Never overwrites.**
   - Output goes next to the first input, or into a `3 Final` folder if one exists there or one level up.
   - It is named `<name>_<job>.mp4`; `_2`, `_3`… are added if the name is taken.
5. Every `run` prints the output path, size before → after, duration and size/fps, and writes `<output>_check.jpg` (6 frames across the video).
6. Errors are plain English on stderr, exit code 1. Success is exit code 0. Command mode never asks questions.

## Commands

| Command | What it does | Writes |
|---|---|---|
| `probe <file...>` | Resolution, fps, duration, codecs, bitrate, size, loudness (LUFS), burned-in subtitles yes/no. Images and audio files work too. | JSON on screen |
| `subs-detect <video>` | Finds when each burned-in subtitle appears and disappears. | `<video>_clipdesk\cues.json` (times, empty `text`) + `cues_strips.png` (one numbered strip per cue, to read the words) |
| `coldopen-suggest <video>` | Scores 3 cold-open candidates (loud, big movement, right after a cut). Skips the last 20% so it never shows the ending or punchline. | `<video>_clipdesk\coldopen.json` + `coldopen_candidates.jpg` |
| `preview <job.json> [--at 0:12]` | One still of the final layout (framing, Stack, subtitles) on the first input. The time comes from `--at`, else `preview_at` in the job, else 1/3 in. | `<output>_preview.jpg` |
| `run <job.json>` | Does the job and exports. | output MP4 + `_check.jpg` + summary |
| `presets` | Lists the presets in `presets.json`. | screen |
| `inspect <clip> [outdir] [--every 0.5]` | Story: logs a clip (cuts, frozen spans, silences, loudness, contact sheet, spectrogram). | `<clip>-log\` |
| `render-edl <edl.json> [--preview] [--native-size]` | Story: renders a cut from an EDL. `--native-size` keeps the sources' own size/fps when they all match. It prints the source size → output size. | the EDL's `output` |
| `check-seams <edl.json> [rendered.mp4] [outdir]` | Story: checks each cut (flash frames, audio pops) and makes a strip per cut. | `<output>-seams\seams.jpg` |

Examples:
```
clipdesk.bat probe "D:\clips\standoff.mp4"
clipdesk.bat subs-detect "D:\clips\who.mp4"
clipdesk.bat preview "D:\clips\job.json" --at 0:12
clipdesk.bat run "D:\clips\job.json"
```

Story cuts follow `story\STORY_EDIT.md` (full workflow) and `story\edit-rules.md`.

## Job file (`job.json`)
Every section is optional except `inputs`. Leave a section out to keep the original. Relative paths are relative to the job file's folder. Times can be seconds (`12.5`) or `m:ss` (`"0:12"`). Save as UTF-8 (with or without BOM).

```json
{
  "preset": "meme",
  "inputs": ["scene1.mp4", "scene2.mp4"],
  "merge": { "transition": "cut" },
  "trim": [{ "input": 0, "start": "0:02", "end": "0:41" }],
  "images": [{ "file": "poster.png", "duration": 3, "position": "end" }],
  "cold_open": { "start": "0:12", "end": "0:15", "transition": "flash" },
  "frame": { "mode": "original", "zoom": 1.0, "offset_x": 0, "background": "black" },
  "stack": { "preset": "meme", "line1": "...", "line2": "...", "credit": "Inspired by Pulp Fiction (1994)", "part": 1 },
  "subtitles": { "source": "cues.json", "style": "white", "size": 66, "remove_burned": true },
  "music": { "file": "Slow Smoky.mp3", "mode": "duck", "start_at": "0:00", "fade_in": 0.8, "fade_out": 2.5, "level": "auto" },
  "output": { "name": "scenes_merged", "quality": "high" },
  "preview_at": "0:12"
}
```

### Fields

**`preset`**: the name of an entry in `presets.json`. It fills in only the sections or fields the job leaves out, and the job always wins. `stack.preset` also pulls in the preset of the same name. A section set to `null` stays off.

**`inputs`** (required): videos and/or images (`.png .jpg .jpeg .webp .bmp`) in order. An image in `inputs` shows for 3 s.

**`merge.transition`**: `cut` (default) or `fade` (0.5 s crossfade of picture and sound, re-encodes). A plain cut of clips that match exactly is a lossless direct join.

**`trim`**: a list of `{ "input": n, "start", "end" }` (either end can be left out). Several entries for the same input keep several pieces in order, which is how to cut a section out:
```json
"trim": [{ "input": 0, "end": "0:10" }, { "input": 0, "start": "0:14" }]
```

**`images`**: `{ "file", "duration" (s, default 3), "position": "start" | "end" | n }`. A number means "before input n". Images are fitted to the first video's size with black bars. They are silent.

**`cold_open`**: `null` = off (the default; only when asked).
- `{ "start", "end" }` uses exactly that part. Keep it 2–4 s.
- `{ "auto": true }` picks the top `coldopen-suggest` candidate itself and prints which one, so the user can ask for another.
- `transition`: `flash` (default, a quick white flash into the start) or `cut`.
- Times are on the timeline after merge and trim.
- Drama: tease the most intense moment, never the ending. Memes: an absurd moment from the build-up, never the punchline.

**`frame`**
- `mode`: `original` (default) | `9:16` | `4:5` | `1:1` | `16:9`.
- `zoom`: 1.0 = the source width fits the output width. Higher crops in and fills more of the frame.
- `offset_x`: −1 (left edge) … 1 (right edge), to slide the crop.
- `background`: `black` | `blur` (blurred, darkened copy of the video behind it).
- `remove_burned`: crop out the burned-in subtitle band (see subtitles).
- Portrait sources are never cropped, only scaled to fit.

**`stack`**: `null` or `{ "preset": "meme" | "drama", "line1", "line2", "credit", "part" }`.
- Stack forces 9:16 with the proven layout: line 1 white and line 2 gold `#FFC84D` (Inter Display Black 78, centre y 340), credit grey (Inter Display Medium 34, y 500), video full width at y≈565, captions top at y 1290.
- Stack zoom default is 1.35 (tight). Use about 1.2 when people sit at the frame edges.
- `drama`: line 1 = title, and line 2 defaults to "Part N" from `part`.
- Emojis print a warning: the renderer draws them as flat outlines. Put them in the post caption instead.

**`subtitles`**: `null` or:
- `source`:
  - `"cues.json"`: from `subs-detect`, with each cue's `text` filled in. Best when the clip has burned-in subtitles. Cues with empty text are skipped with a warning. A bare `cues.json` is found in `<first input>_clipdesk\`.
  - `"whisper"`: local speech-to-text (`"model": "small"` default, or `"base.en"` for speed).
  - `"script.txt"`: either `start-end | text` lines (the web page's format, e.g. `1.20-2.85 | Who's on first?`), or plain lines with no times, which are timed against Whisper's word timings.
- `style`: `white` | `orange` | `yellow` | `black_box`.
- `size`: Stack/frame captions default 66. Original-format videos use house style: Inter Medium 44 (at 1080p), off-white, soft outline, bottom centre.
- `remove_burned: true` crops out the old subtitle band (86–94% of the height). In `original` mode it crops to 86% of the width and height and scales back up to the original size, so the size doesn't change.

**`music`**
- `file`: MP3/WAV/M4A.
- `mode`:
  - `duck` (default): dialogue kept, music dips under speech.
  - `replace`: music only.
  - `mix`: both, no ducking.
- `start_at`: point in the song.
- `fade_in` / `fade_out` in seconds (defaults 0.8 / 2.5).
- `level`: `auto` (default) puts the music 12.5 LU under the dialogue's loudness, or −14 LUFS when replacing. A number = gain in dB.
- The music loops if it's shorter than the video.
- A video without sound always gets `replace`.

**`output`**
- `name`: base name without extension (default `<first input>_<job>`, e.g. `standoff_stack`, `scene1-4_merged`).
- `quality`:
  - `high` (default, never lowers quality).
  - `copy`: fail with a message if a re-encode would be needed.
  - `small`: CRF 23. Only when the user asks to shrink a file.

**`preview_at`**: the time for `preview` (optional).

## Presets (`presets.json`)
`meme`, `drama`, `wide stack`, `orange subs`, `yellow subs`, `boxed subs`, `quiet music`. Run `clipdesk.bat presets` for descriptions. Add a preset by adding an entry with a `description` and any job sections.

## Recipes
Add music, keep everything else:
```json
{ "inputs": ["standoff.mp4"], "music": { "file": "Noir - Slow Smoky.mp3" } }
```
Meme Stack with burned-in subtitles turned into Stack captions:
1. `subs-detect who.mp4`
2. Read `cues_strips.png` and fill in `text` in `cues.json`.
3. Run:
```json
{ "inputs": ["who.mp4"], "stack": { "preset": "meme", "line1": "One name away", "line2": "from a breakdown.",
  "credit": "Inspired by The Naughty Nineties (1945)" },
  "subtitles": { "source": "cues.json", "style": "white", "remove_burned": true } }
```
Merge scenes, then Stack for Shorts, Part 2:
```json
{ "inputs": ["scene1.mp4", "scene2.mp4", "scene3.mp4"], "stack": { "preset": "drama", "line1": "The Standoff", "part": 2 } }
```

## Files
| File | What |
|---|---|
| `clipdesk.py`, `clipdesk.bat` | engine + command mode |
| `render.py`, `captions.py`, `app.py`, `run.bat` | shared layout/caption code and the web page |
| `fonts/` | Inter Display Black / ExtraBold / Medium + OFL licence |
| `presets.json`, `style-notes.md` | presets, the user's preferences |
| `story/` | story-cut workflow and scripts (`inspect`, `render-edl`, `check-seams`) |

Needs: Python 3.11+ with `.venv` (made by `run.bat` on first launch), FFmpeg on PATH (`winget install Gyan.FFmpeg`).

## Changelog
- **1.0.0 (2026-10-13):** command mode, job files, Stack presets, subtitle detection, cold open, merge/trim/images, music modes, story commands, `--native-size`.
