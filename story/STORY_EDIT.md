# Story edit workflow (Clip Desk)

> Moved here from the edit-director skill on 2026-10-13. The edit-director skill sends **story cuts** here. Quick jobs (music, Stack, subtitles only, plain merges, trims, cold open) do NOT use this workflow.
> Paths below are relative to the Clip Desk tool folder. On Windows use `python` instead of `python3`.

You are the editor. The shots are already generated. Your job is to turn takes, fix clips and upscales into one finished cut that tells the story in cause-and-effect order, sounds continuous, and has no visible mistakes. Work in ffmpeg through the scripts in `story/scripts/`. Never ship a render you have not checked with your own eyes and the seam tool.

**Core law: the story decides every cut.** A shot stays if it fits the story. A shot goes only if it is broken, repeated, or adds nothing. Longer is fine. Sammy's standing rule is "Don't cut anything as long as it fits the story."

## Where this sits

- **Upstream:** blocking-director planned the shots, and the Seedance prompt generated them. shot-repair-director decides whether a broken shot needs a fix clip. sound-director plans the sound.
- **This skill** assembles the takes, fix clips and sound into the delivered video.
- If a needed shot doesn't exist in any take (for example, the cause of an action was never filmed), stop. Tell Sammy which shot is missing and offer a short fix-clip prompt. Don't fake it with a zoom or a freeze.

## Priorities when a cut has to give something up (Murch, *Rule of Six*)

| Rank | Criterion | Weight | Question |
|---|---|---|---|
| 1 | Emotion | 51% | Does the cut carry the feeling of the moment? |
| 2 | Story | 23% | Does it move the story forward, in the right order? |
| 3 | Rhythm | 10% | Does it land at the right beat, neither early nor late? |
| 4 | Eye-trace | 7% | Is the eye already where the new shot's subject is? |
| 5 | 2D plane | 5% | Does screen direction hold (left stays left)? |
| 6 | 3D space | 4% | Do positions in the room stay true? |

Give up criteria from the bottom of the table first. Our series adds a hard floor on top of Murch: **nobody swaps sides**. The Nervous One stays frame-left of the Deadpan One in every cut. A cut that flips them is rejected, whatever it gains elsewhere.

## Workflow

### 1. Inventory and log every source
For each file:
```
python story/scripts/inspect_clip.py CLIP.mp4 OUTDIR
```
This writes a contact sheet (one frame every 0.5 s), a spectrogram, and a log with size, fps, length, the clip's own cuts, frozen spans, silences and loudness.
- Read the contact sheet and the spectrogram yourself.
- Name each source with a short key: T1/T2 for takes, FA/FB for fix clips.
- Then write a **shot log**:

| Shot | Story beat | T1 | T2 | Fix | Pick + reason |
|---|---|---|---|---|---|

When several takes cover the same beat, compare them in this table before picking. Judge each take on:
- whether the performance reads right (Sammy flagged "relaxing" sweat as wrong);
- face and identity;
- props;
- glitches.

### 2. Map the sound
- Use the spectrogram to find every story sound: dialogue lines, bells, shots, doors, the radio.
  - A voice shows up as a stacked band.
  - A bell is a thin bright line that holds.
  - A blast is a full-height column.
- Write down the exact in/out time of each sound in its source file.
- Choose one **master audio source** per beat. Usually that's the big takes, whose sound was generated alongside the whole scene.
- **Mute fix clips** unless Sammy says otherwise. Their sound was generated without the scene around it.

### 3. Build the EDL (plain words first)
- When Sammy has already said what to use, go ahead.
- When picking takes is a story judgment he hasn't made, show him the shot-log table first.

Write the EDL as JSON (see `story/edl-example.json`):
- each segment has a video source with in/out times, an audio list, and a `note` giving its story job;
- the order follows cause and effect.
For any beat where something sets off an action, the order is:

> **CAUSE (insert) → REACTION (face) → EFFECT (the action and its result)**

Example: the bell rings → he jumps → the gun goes off into the ceiling. Each of the three gets its own shot. If the cause is buried inside a wide shot, it doesn't count. Give it its own insert.

### 4. Render
```
python story/scripts/render_edl.py edl.json --preview    # fast look
python story/scripts/render_edl.py edl.json --native-size    # final at the sources' own size/fps, high quality (default for Sammy)
python story/scripts/render_edl.py edl.json              # final 1080p, only when he wants a fixed size
```
The renderer handles the mechanics below. Don't hand-roll a new filtergraph each time.
- Conforms every source to one size and fps (lanczos).
- Snaps every segment to whole frames so picture and sound stay locked across all cuts.
- Puts 20 ms fades on every audio piece.
- Generates clean room tone (`ROOMTONE`) when needed.
- Applies an optional post shake for impacts.
- Normalizes loudness in two passes and compensates the limiter's delay.
- Burns subtitles last.

### 5. Self-check (mandatory, up to 3 fix passes)
```
python story/scripts/check_seams.py edl.json final.mp4 OUTDIR
python story/scripts/inspect_clip.py final.mp4 OUTDIR
```
- The **flash test** must come back clean. A FLASH means one to a few frames of a neighbouring shot slipped in at a segment edge. Move that segment's in or out point by the amount printed.
- Look at `seams.jpg`, one row per cut, and check that:
  - each person is on the same side of the frame;
  - screen direction is kept;
  - there's no duplicate face;
  - hats, masks and props are in the right state;
  - there's no frozen frame;
  - the cut isn't a jump in the same framing.
- Check `frozen:` in the log. Trim any frozen tail longer than about 0.3 s at a segment's end.
- Check that the EDL length matches the file length within 1–2 frames.
- Check that the loudness is near the target.
- Fix, re-render and re-check. After 3 passes, report what's left instead of looping.

### 6. Subtitles (when the cut is final)
- Write an .ass file in the house style:
  - `Inter Medium 44`, PlayRes 1708×960, off-white text, soft black outline and shadow, bottom-centre, MarginV 74.
  - Copy the style line from a previous `*-subtitles.ass`.
- Time each line on the **output** timeline, from the spectrogram:
  - start at the voice onset and end within 0.1 s of the voice ending;
  - minimum 1.0 s on screen;
  - at most 2 lines, about 42 characters per line.
- Spoken words only, in the language spoken. No sound descriptions unless Sammy asks.
- Add `"subtitles": "subs.ass"` to the EDL so it's burned last. Then grab a frame under every line to check it is readable.

### 7. Deliver
- Write the finished video next to the sources (or into the project's `3 Final` folder if there is one) as `<meme>-clip1-final.mp4`, or `<meme>-full.mp4` / `<meme>-subtitled.mp4` for the joined meme. Never overwrite a source.
- Report the file size beside the sources' sizes. A bigger file is fine; quality never drops to save size.
- Tell Sammy in a few lines: what the cut contains, the order of the key beats, where the sound comes from, and anything you cut and why.
- Keep the EDL JSON beside the scratch files so a later fix is a one-line change.

## Cutting rules

1. **Cause before effect, every time.** An action needs its trigger on screen first. A state (a hole in the ceiling, plaster on a face) needs its cause shown earlier.
2. **Cut on action.** Cut in the middle of a movement (a turn, a rise, a jump), not before it starts or after it settles. The movement hides the cut.
3. **No jump cuts.** Don't cut between two near-identical framings of the same subject. Change the size or angle, or put a reaction or insert between them. The one exception is cutting a glitch out of a single held shot. Accept that only when the jump is small, and tell Sammy.
4. **Kill AI artifacts at the edges.**
   - Generated shots often morph for the first 2–4 frames and freeze for the last 0.3–0.6 s. Trim both.
   - Watch for flash frames where a take's own cut is close to your edit point. The flash test catches them.
5. **Reaction holds.** After a hit, a blast or a reveal, hold the reaction long enough to land (1–2 s). Don't cut away on the impact frame.
6. **Keep the room.** After a big moment, keep a beat of quiet before the next line. Don't stack lines back to back.
7. **Takes may be mixed** when identity, wardrobe and set state match. Check face, hat, mask state, carnation and props against the continuity notes before mixing takes.
8. **Contiguous source is not a cut.** Two segments that play one source straight through are just bookkeeping. Mark them so the flash test treats them as continuous.

## Sound rules

1. **One master source per beat.** Story sounds (bell, shot, lines) come from the master takes. Fix clips are muted.
2. **Sound leads picture into a cause (J-cut).** The trigger sound starts under the shot before its picture, e.g. the radio call under the Henchman's look. A sound that lingers into the next shot is an **L-cut**.
3. **A continuous sound stays continuous across cuts.** A bell that rings over an insert and a reaction is one ringing, built from the master clip, never restarted.
4. **Sync hits to the frame.** The blast lands on the muzzle-flash frame, and a door bang on the door frame. Tolerance is ±1 frame, and sound must never be early.
5. **No digital silence.** Fill gaps with generated room tone (`ROOMTONE`). Never reuse a "quiet" stretch from a take without listening for voices in it.
6. **No pops.** Every audio piece has 20 ms fades. In the seam check, a level jump of 10 dB or more at a cut that isn't a story sound means something is wrong.
7. **Loudness.** The house standard is −16 LUFS integrated, −1.5 dBTP, LRA 11, applied to the whole cut at once. If Sammy wants posts louder, use −14 LUFS / −1 dBTP (the usual social-video target). Keep it the same across a series.
8. **No music** unless Sammy asks. When he does, add it afterwards as a Clip Desk quick job on the finished cut.

## Impacts in post
Camera shake, a punch-in or recoil on a blast or slam is added in post, never generated:
- Add `"shake": {"at": seconds-into-segment, "frames": 4-6, "px": 10-16}` to the segment.
- Use one shake per impact, decaying, synced to the sound hit.

## Hard constraints
- Never skip the self-check, and never deliver with a FLASH, a side-swap, a duplicate face or an unsynced hit.
- Never cut a shot that carries the story to save time.
- Never invent a missing cause with zooms or freezes. Ask for a fix clip.
- Never let fix-clip audio into the mix unless Sammy asks for it.
- Never delete Sammy's files. Write new versions beside them.

See `story/edit-rules.md` for sources and the reasoning behind each rule.
