# Repost Tool

Turns a finished AI clip into a ready-to-post social video: vertical reframe (9:16, 4:5, 1:1 or 16:9), hook text, credit line, big auto-captions and optional background music. Runs fully offline on Windows.

## Setup (Windows, once)

1. Install **Python 3.10 or newer** from python.org (tick "Add python.exe to PATH").
2. Install **FFmpeg**: open PowerShell and run `winget install Gyan.FFmpeg`, then close and reopen PowerShell.
3. Download this repo (Code → Download ZIP) and unzip it anywhere.
4. Double-click `run.bat`. The first run installs everything (a few minutes); after that it opens the tool in your browser at http://127.0.0.1:7860.

The first time you transcribe, the speech model (~500 MB) downloads once. After that, no internet is needed.

## Use

1. Drop in a clip (and an MP3 if you want music).
2. Type the hook and credit, pick a size, and drag the gold box to frame the shot.
3. Press **Transcribe**, fix any misheard words in the caption box, then **Export**.

Exports go to `Videos\Repost Tool` in your user folder. **Open output folder** takes you there.

## Command line

```
.venv\Scripts\python render.py clip.mp4 --hook1 "..." --hook2 "..." --credit "Inspired by ..." --captions auto --music song.mp3
.venv\Scripts\python render.py clip.mp4 --preview 3     # one still frame
clipdesk.bat --help                                      # Clip Desk jobs (see TOOL_GUIDE.md)
```

`--captions auto` writes `<name>_vertical.captions.txt` (`1.20-2.85 | text` per line). Edit it and re-run with `--captions <that file>`.

## Fonts

Inter Display, included under the SIL Open Font License (`fonts/LICENSE.txt`).
