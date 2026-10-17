"""Repost Tool GUI: a local browser page on top of render.py. Start with run.bat."""
import functools, io, os, re, subprocess, uuid
import gradio as gr
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import captions, render

OUT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Repost Tool")
FONT = {k: os.path.join(render.HERE, "fonts", f"InterDisplay-{k}.ttf") for k in ("Black", "ExtraBold", "Medium")}
EM = 1 / 1.21  # libass font size = Inter's ascender+descender = 1.21 em
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
SUB_BAND = (0.86, 0.94)  # where Dreamina burns its subtitles, as a share of the source height


# ---------- live preview (drawn with Pillow, so it updates instantly) ----------

@functools.lru_cache(maxsize=48)
def frame_at(video, t):
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB")


@functools.lru_cache(maxsize=8)
def font(name, size):
    return ImageFont.truetype(FONT[name], max(1, round(size * EM)))


def wrap(text, f, width):
    """Like libass smart wrapping: as few lines as fit `width`, balanced, lower lines wider."""
    def fill(words, w):  # greedy from the end, so the leftover short line ends up on top
        lines, cur = [], ""
        for word in reversed(words):
            if cur and f.getlength(word + " " + cur) > w:
                lines.insert(0, cur)
                cur = word
            else:
                cur = (word + " " + cur).strip()
        return [cur] + lines if cur else lines
    words = text.split()
    n, lo, hi = len(fill(words, width)), 1, width
    while hi - lo > 2:  # narrowest width that still needs only n lines
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if len(fill(words, mid)) <= n else (mid, hi)
    return fill(words, hi)


def source_view(img, g):
    sw, sh = img.size
    cw, ch, cx, cy = g["crop"]
    lw = max(2, sw // 300)
    o = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(o)
    d.rectangle([0, 0, sw, sh], fill=(0, 0, 0, 150))
    d.rectangle([cx, cy, cx + cw - 1, cy + ch - 1], fill=(0, 0, 0, 0))
    d.rectangle([0, SUB_BAND[0] * sh, sw, SUB_BAND[1] * sh], fill=(255, 40, 40, 60), outline=(255, 60, 60, 255), width=lw)
    d.rectangle([cx, cy, cx + cw - 1, cy + ch - 1], outline=(255, 200, 77, 255), width=lw * 2)
    out = Image.alpha_composite(img.convert("RGBA"), o).convert("RGB")
    return out.resize((720, round(720 * sh / sw)))


def result_view(img, g, o, guides):
    W, H, s = g["W"], g["H"], g["s"]
    (cw, ch, cx, cy), (vw, vh, vx, vy) = g["crop"], g["video"]
    clip = img.crop((cx, cy, cx + cw, cy + ch)).resize((vw, vh), Image.LANCZOS)
    if o["background"] == "blur":
        r = max(W / vw, H / vh)
        bg = clip.resize((round(vw * r), round(vh * r)))
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width + W) // 2, (bg.height + H) // 2))
        canvas = bg.filter(ImageFilter.BoxBlur(20)).point(lambda p: p * .6)
    else:
        canvas = Image.new("RGB", (W, H))
    canvas.paste(clip, (vx, vy))
    canvas = canvas.convert("RGBA")
    over = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(over)
    hook = [(t, (255, 255, 255)) for t in [o["hook1"]] if t] + [(t, (255, 200, 77)) for t in [o["hook2"]] if t]
    if g["fit"] and hook:
        y0, y1 = render.hook_band(g, bool(o["credit"]))
        d.rectangle([0, y0, W, y1], fill=(0, 0, 0, 140))
    canvas = Image.alpha_composite(canvas, over)
    d = ImageDraw.Draw(canvas)

    def block(items, style, size, cy, width, top=False, **kw):
        f = font(style, size * s)
        rows = [(line, fill) for text, fill in items for line in wrap(text, f, width)]
        lh = size * s
        y = cy if top else cy - lh * len(rows) / 2
        for i, (line, fill) in enumerate(rows):
            d.text((W / 2, y + lh * (i + .5)), line, font=f, fill=fill, anchor="mm", **kw)

    block(hook, "Black", 78, g["hook_y"], W - 120 * s)
    if o["credit"]:
        block([(o["credit"], (160, 160, 160))], "Medium", 34, g["credit_y"], W - 120 * s)
    if o["caps_on"]:
        sample = next((t for *_, t in parse_caps(o["cap_text"])), "") or "Sample caption goes here"
        size = o["cap_size"]
        block([(sample, (255, 255, 255))], "ExtraBold", size, g["cap_y"], W - 140 * s, top=True,
              stroke_width=round(5 * size * s / 66), stroke_fill=(0, 0, 0))

    if guides:
        k = H / 1920
        o2 = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(o2)
        shade = (80, 160, 255, 70)
        d.rectangle([0, 0, W, 150 * k], fill=shade)
        d.rectangle([0, H - 350 * k, W, H], fill=shade)
        d.rectangle([W - 120 * k, 150 * k, W, H - 350 * k], fill=shade)
        d.line([W / 2, 0, W / 2, H], fill=(120, 220, 255, 160), width=2)
        d.line([0, H / 2, W, H / 2], fill=(120, 220, 255, 160), width=2)
        sh = o["src_h"]
        y0, y1 = (vy + (b * sh - cy) * vh / ch for b in SUB_BAND)
        y0, y1 = max(y0, vy), min(y1, vy + vh)
        if y1 - y0 > 3:  # burned-subtitle band is inside the crop: it will show
            d.rectangle([vx, y0, vx + vw, y1], fill=(255, 40, 40, 70), outline=(255, 60, 60, 255), width=4)
        canvas = Image.alpha_composite(canvas, o2)
    canvas = canvas.convert("RGB")
    r = 900 / max(W, H)
    return canvas.resize((round(W * r), round(H * r)), Image.LANCZOS)


def parse_caps(text):
    try:
        return captions.parse(text or "")
    except ValueError:
        return []


def emoji_warning(*texts):
    if any(EMOJI.search(t or "") for t in texts):
        return ("**Heads up:** the hook or credit has emojis. In the video they come out as flat outlines "
                "in the text colour, not colour emojis. Put them in the post caption instead.")
    return ""


def draw(video, t, size, zoom, pan, burned, background, hook1, hook2, credit, caps_on, cap_size, cap_text, guides):
    warn = emoji_warning(hook1, hook2, credit)
    if not video:
        return None, None, warn
    img = frame_at(video, round(t or 0, 1))
    g = render.geometry(img.width, img.height, size, zoom, pan, burned)
    o = dict(background=background, hook1=hook1, hook2=hook2, credit=credit, caps_on=caps_on,
             cap_size=cap_size, cap_text=cap_text, src_h=img.height)
    return source_view(img, g), result_view(img, g, o, guides), warn


def loaded(video):
    if not video:
        return gr.Slider(), gr.Slider(), gr.Slider()
    info = render.probe(video)
    return (gr.Slider(maximum=round(info["dur"], 1), value=round(info["dur"] / 3, 1)),
            gr.Slider(value=1 if info["h"] >= info["w"] else 1.35), gr.Slider(value=0))


def drag_to_pan(video, x, size, zoom, burned):
    """Browser drag gives x as 0..1 across the source image; turn it into pan -1..1 for the crop centre."""
    if not video or x is None:
        return gr.Slider()
    info = render.probe(video)
    cw = render.geometry(info["w"], info["h"], size, zoom, 0, burned)["crop"][0]
    free = (info["w"] - cw) / 2
    return round(max(-1, min(1, (x * info["w"] - info["w"] / 2) / free)), 3) if free > 0 else 0


# ---------- export ----------

def options(video, music, hook1, hook2, credit, size, zoom, pan, burned, background, caps_on, cap_size,
            cap_text, mode, volume, start, fade_in, fade_out, preview=None):
    if not video:
        raise gr.Error("Drop a video first.")
    a = render.parser().parse_args([video])
    a.hook1, a.hook2, a.credit, a.burned_subs, a.background = hook1, hook2, credit, burned, background
    a.size, a.zoom, a.pan = size, zoom, pan
    a.caption_size, a.music, a.music_mode, a.music_volume = int(cap_size), music, mode, volume
    a.music_start, a.fade_in, a.fade_out, a.preview = start, fade_in, fade_out, preview
    if caps_on and cap_text.strip():
        a.captions = os.path.join(render.HERE, "tmp", uuid.uuid4().hex[:8] + ".captions.txt")
        os.makedirs(os.path.dirname(a.captions), exist_ok=True)
        open(a.captions, "w", encoding="utf-8").write(cap_text)
    elif caps_on:
        a.captions = "auto"  # nothing to edit yet: transcribe now (also saves <name>_vertical.captions.txt)
    name = os.path.splitext(os.path.basename(video))[0]
    os.makedirs(OUT_DIR, exist_ok=True)
    a.out = os.path.join(render.HERE, "tmp", name + "_preview.png") if preview is not None \
        else os.path.join(OUT_DIR, name + render.out_suffix(size) + ".mp4")
    return a


def run(a):
    try:
        return render.render(a)
    finally:
        if a.captions not in ("none", "auto"):
            render.remove_quietly(a.captions)


def transcribe(video, model):
    if not video:
        raise gr.Error("Drop a video first.")
    lines = captions.group(captions.transcribe(video, model))
    return "".join(f"{s:.2f}-{e:.2f} | {t}\n" for s, e, t in lines)


CSS = """
#size .wrap { gap: 10px; }
#size label { flex-direction: column; align-items: center; gap: 6px; min-width: 64px; justify-content: flex-end; }
#size input { position: absolute; opacity: 0; pointer-events: none; }
#size label.selected { border-color: var(--color-accent); color: var(--color-accent); }
#size label::before { content: ""; display: block; border: 2px solid currentColor; border-radius: 3px; }
#size label:nth-of-type(1)::before { width: 18px; height: 32px; }
#size label:nth-of-type(2)::before { width: 24px; height: 30px; }
#size label:nth-of-type(3)::before { width: 28px; height: 28px; }
#size label:nth-of-type(4)::before { width: 36px; height: 20px; }
#zoomlabels { position: relative; height: 1.4em; margin: -6px 12px 0; font-size: 13px; opacity: .8; }
#zoomlabels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
#srcview img { cursor: ew-resize; user-select: none; -webkit-user-drag: none; }
"""

# Drag on the source still: send x (0..1 across the image) to the hidden #dragx number.
JS = """
() => {
  let down = false;
  const send = (e) => {
    const img = document.querySelector('#srcview img'), inp = document.querySelector('#dragx input');
    if (!img || !inp) return;
    const r = img.getBoundingClientRect();
    inp.value = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)).toFixed(3);
    inp.dispatchEvent(new Event('input', {bubbles: true}));
  };
  document.addEventListener('pointerdown', (e) => {
    if (e.target.closest('#srcview img')) { down = true; e.preventDefault(); send(e); }
  });
  document.addEventListener('pointermove', (e) => { if (down) send(e); });
  document.addEventListener('pointerup', () => { down = false; });
  document.addEventListener('dragstart', (e) => { if (e.target.closest('#srcview')) e.preventDefault(); });
}
"""

with gr.Blocks(title="Repost Tool") as app:
    gr.Markdown("# Repost Tool")
    with gr.Row():
        with gr.Column(scale=1):
            video = gr.Video(label="Video", sources=["upload"])
            music = gr.File(label="Music (optional)", file_types=[".mp3", ".wav", ".m4a"], type="filepath")
            hook1 = gr.Textbox(label="Hook line 1 (white)")
            hook2 = gr.Textbox(label="Hook line 2 (gold)")
            credit = gr.Textbox(label="Credit line", placeholder="Inspired by Pulp Fiction (1994)")
            warning = gr.Markdown()
            burned = gr.Checkbox(label="Source has burned-in subtitles", value=False)
            background = gr.Radio(["black", "blur"], value="black", label="Background")
            with gr.Accordion("Music settings", open=False):
                mode = gr.Radio(["duck", "replace"], value="duck", label="Mode (duck = under the dialogue)")
                volume = gr.Slider(-30, 0, value=-12, step=1, label="Music volume (dB)")
                start = gr.Number(value=0, label="Start point in the song (seconds)")
                fade_in = gr.Slider(0, 5, value=0.8, step=0.1, label="Fade in (s)")
                fade_out = gr.Slider(0, 8, value=2.5, step=0.1, label="Fade out (s)")
        with gr.Column(scale=2):
            size = gr.Radio(list(render.SIZES), value="9:16", label="Size", elem_id="size")
            with gr.Row():
                with gr.Column():
                    src_img = gr.Image(label="Source: the gold box is what's kept (drag to move it)",
                                       interactive=False, elem_id="srcview", buttons=[])
                    scrub = gr.Slider(0, 10, value=0, step=0.1, label="Scrub through the clip (s)")
                    zoom = gr.Slider(1, 1.5, value=1.35, step=0.01, label="Zoom")
                    gr.HTML('<div id="zoomlabels"><span style="left:0;transform:none">Fit width</span>'
                            '<span style="left:40%">1.2×</span><span style="left:100%;transform:translateX(-100%)">1.5×</span></div>')
                    pan = gr.Slider(-1, 1, value=0, step=0.01, label="Left / right")
                    guides = gr.Checkbox(label="Show guides (app buttons, centre lines, subtitle band)", value=True)
                    dragx = gr.Number(visible="hidden", elem_id="dragx")
                with gr.Column():
                    res_img = gr.Image(label="Result (live)", interactive=False, buttons=[])

    with gr.Row():
        with gr.Column():
            caps_on = gr.Checkbox(label="Captions", value=True)
            with gr.Row():
                model = gr.Radio(["small", "base.en"], value="small", label="Whisper model (base.en is faster)")
                cap_size = gr.Slider(40, 90, value=66, step=2, label="Caption size")
            transcribe_btn = gr.Button("Transcribe")
            cap_text = gr.Textbox(label="Captions (edit freely: start-end | text)", lines=12)
        with gr.Column():
            preview_btn = gr.Button("Exact preview at the scrub point (slower)")
            still = gr.Image(label="Exact preview", height=480, buttons=[])
            export_btn = gr.Button("Export", variant="primary")
            result = gr.Video(label="Result")
            status = gr.Markdown()
            gr.Button("Open output folder").click(lambda: (os.startfile(OUT_DIR) if hasattr(os, "startfile") else subprocess.Popen(["xdg-open", OUT_DIR]))
                if os.path.isdir(OUT_DIR) else None)

    look = [video, scrub, size, zoom, pan, burned, background, hook1, hook2, credit, caps_on, cap_size, cap_text, guides]
    for c in look[1:]:
        c.change(draw, look, [src_img, res_img, warning], trigger_mode="always_last", show_progress="hidden")
    video.change(loaded, video, [scrub, zoom, pan]).then(draw, look, [src_img, res_img, warning])
    dragx.change(drag_to_pan, [video, dragx, size, zoom, burned], pan, trigger_mode="always_last", show_progress="hidden")

    settings = [video, music, hook1, hook2, credit, size, zoom, pan, burned, background, caps_on, cap_size,
                cap_text, mode, volume, start, fade_in, fade_out]
    transcribe_btn.click(transcribe, [video, model], cap_text)
    preview_btn.click(lambda *s: run(options(*s[:-1], preview=s[-1])), settings + [scrub], still)
    export_btn.click(lambda *s: (p := run(options(*s)), f"Saved: `{p}`"), settings, [result, status])

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    app.launch(inbrowser=True, allowed_paths=[OUT_DIR], css=CSS, js=JS)
