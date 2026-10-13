# Edit rules: sources and reasoning

## Craft sources
- **Walter Murch, *In the Blink of an Eye*: the Rule of Six.** Ranked as emotion 51%, story 23%, rhythm 10%, eye-trace 7%, 2D plane of the screen 5%, 3D space 4%. When a cut can't satisfy all six, give up criteria from the bottom of the list first; emotion is the last to go.
  https://nofilmschool.com/2016/11/6-rules-good-cutting-according-oscar-winning-editor-walter-murch
  https://cloud.wikis.utexas.edu/wiki/spaces/rtf318/pages/80022510/Murch+s+Rule+of+Six
- **Eye-trace.** Place the new shot's subject where the viewer's eye already is at the cut, so the cut feels invisible.
  https://artlist.io/blog/eye-trace-and-rule-of-six-editing/
- **J-cut / L-cut.** In a J-cut the sound of the next shot starts first and pulls the viewer in; that's how we show a cause. In an L-cut the sound of the last shot carries on after the picture changes, to linger. The house sound-director already maps THEREFORE joints to J-cuts.
- **Cut on action, the 30° rule and screen direction.** These are standard continuity editing. Our series adds a hard lock: the Nervous One is always frame-left of the Deadpan One.

## Engineering sources (GitHub)
- **browser-use/video-use** ("Edit videos with Claude Code", MIT). Rules we adopted:
  - 30 ms audio fades at every cut (we use 20 ms).
  - Never a hard audio cut.
  - Subtitles burned last, after every other video filter.
  - Subtitle times computed on the output timeline.
  - The plan is approved before the cut.
  - Self-eval at every cut boundary, with up to 3 fix-and-re-render passes before showing the user.
  - Social loudness target −14 LUFS / −1 dBTP / LRA 11, applied in two passes.
  https://github.com/browser-use/video-use
- **PySceneDetect** (content detector). Finds the cuts inside a generated clip. Our scripts use ffmpeg's built-in scene score (`select=gt(scene,0.30)`), so nothing extra has to be installed. If a clip has soft cuts this misses, `pip install scenedetect[opencv]` and run `scenedetect -i clip.mp4 detect-content list-scenes`.
  https://www.scenedetect.com/
- **auto-editor.** Cuts dead space by audio and motion thresholds. We don't auto-cut, because the story decides every cut, but its margin idea carries over: pad a cut around the sound it protects.
- **ffmpeg filters we rely on:**
  - `loudnorm` (EBU R128, run in two passes);
  - `alimiter` with `latency=1`, so the limiter doesn't push sound late;
  - `freezedetect`, to find frozen tails;
  - `silencedetect`;
  - `showspectrumpic`, to see sounds;
  - `ass`, to burn subtitles.

## Lessons from our own cuts (Curve Studios)
- **Nemesis.** The race bell was buried inside the wide shot, so the gunshot had no visible cause. Fix: a bell insert, then his jump, then the blast. This is the rule: cause → reaction → effect, each in its own shot.
- **Nemesis.** The bell fix clip sounded like a hammer. Fix: mute the fix clips and rebuild one continuous ringing from the master takes. This is the rule: one master source per beat.
- **Nemesis.** A stretch of "quiet" take audio used as filler had a voice in it. Fix: generated room tone.
- **Nemesis.** The seam tool found 2 frames of take 2's "relaxing" sweat shot just before take 1's sweat shot, and 1 frame of take 1's next shot after it. Fix: pull the out-points in. The flash test is now mandatory.
- **Nemesis.** A held henchman shot froze for 0.47 s before the cut. Fix: trim it. Frozen tails over 0.3 s get trimmed.
- **Sync.** Trimming at times that don't fall on whole frames, across sources with different fps (24 and 30), drifted picture against sound by up to 0.19 s over 13 cuts. Fix: snap every segment to whole output frames.
- **Earlier memes.** Sammy wants nothing cut that fits the story, no left/right drift, cuts in cause-and-effect order, and illogical parts fixed in the edit when a fix exists.
