"""
Owner-supplied Reels (video clip or photo) -- the "window" layout
=================================================================
Replaces the older full-bleed layouts, where the headline was printed over
the footage and a clip that did not match 9:16 was cropped or boxed with
black bars. Same design language as the carousel slides:

    0 .. 230      blurred backdrop only (Instagram draws its own controls here)
    230 .. 1170   the footage, in its own window, nothing written over it
                  (brand chip + category pill sit on a soft scrim at its top)
    1170 .. 1570  dark headline panel: category-coloured rule, headline with
                  the accent phrase highlighted, brand line
    1570 .. 1920  backdrop again (Instagram's caption / buttons live here)

The footage is never stretched: near-square/landscape-ish clips fill the
window (small crop); everything else (tall phone video, wide 16:9) is shown
whole on a blurred, darkened copy of itself.
"""

import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from src import carousel
from src.template import (ANTON, ARCHIVO, ACCENT, CATEGORY_COLORS, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb)

W, H = 1080, 1920
WIN_Y, WIN_H = 230, 940
PANEL_Y = WIN_Y + WIN_H            # 1170
PANEL_H = 400
MARGIN = 70
_PANEL = _hex_to_rgb(NEAR_BLACK)
BRAND = "ARAVIND NEWS 24"


def fit_mode(aspect):
    """'cover' when the footage is close to the window's own shape (crop <~20%),
    otherwise 'contain' (shown whole over the blurred copy)."""
    return "cover" if 1.0 <= aspect <= 1.4 else "contain"


def _accent(headline, accent_word):
    a = (accent_word or "").upper().strip()
    return a if a and a in headline.upper() else ""


def render_frame_png(category, headline, accent_word, out_path,
                     footer="For the latest news", handle="@aravindnews24", logo_path=None):
    """Transparent 1080x1920 overlay: everything except the footage window."""
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # soft scrim at the top of the footage window, behind the chip + pill
    scrim = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(170):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(150 * (1 - y / 170))))
    canvas.alpha_composite(scrim, (0, WIN_Y))

    # the headline panel, fading out over its last 70px into the backdrop
    panel = Image.new("RGBA", (W, PANEL_H), _PANEL + (246,))
    pd = ImageDraw.Draw(panel)
    for i in range(70):
        pd.line([(0, PANEL_H - 1 - i), (W, PANEL_H - 1 - i)], fill=_PANEL + (int(246 * i / 70),))
    canvas.alpha_composite(panel, (0, PANEL_Y))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, PANEL_Y, W, PANEL_Y + 8], fill=color)

    # brand chip (left) and category pill (right), over the top of the footage
    f_brand = _font(ARCHIVO, 28)
    bw = draw.textlength(BRAND, font=f_brand)
    y0 = WIN_Y + 34
    draw.rounded_rectangle([MARGIN, y0, MARGIN + bw + 60, y0 + 54], radius=8, fill=(0, 0, 0, 200))
    draw.rectangle([MARGIN + 16, y0 + 14, MARGIN + 24, y0 + 40], fill=carousel.BADGE_COLOR)
    draw.text((MARGIN + 38, y0 + 10), BRAND, font=f_brand, fill=WHITE)
    if category:
        f_cat = _font(ARCHIVO, 28)
        cw = draw.textlength(category, font=f_cat)
        x1 = W - MARGIN
        draw.rounded_rectangle([x1 - cw - 50, y0, x1, y0 + 54], radius=8, fill=color)
        draw.text((x1 - cw - 25, y0 + 10), category, font=f_cat, fill=WHITE)

    # headline, sized to the panel
    top = PANEL_Y + 40
    avail = 250
    lines, f_head, size, h = carousel._fit_headline(draw, headline, W - MARGIN * 2, 4, 88, 48, avail)
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top, _accent(headline, accent_word))

    # brand line, anchored to the panel bottom
    f_foot = _font(ARCHIVO, 30)
    fy = top + h + 30          # directly under the headline, never over it
    if handle and handle in footer:
        pre, _, post = footer.partition(handle)
        x = MARGIN
        for txt, fill in ((pre, MUTED), (handle, WHITE), (post, MUTED)):
            draw.text((x, fy), txt, font=f_foot, fill=fill)
            x += draw.textlength(txt, font=f_foot)
    else:
        draw.text((MARGIN, fy), f"{footer}  →  {handle}", font=f_foot, fill=MUTED)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _backdrop(img):
    scale = max(W / img.width, H / img.height)
    bg = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.BILINEAR)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    return ImageEnhance.Brightness(bg).enhance(0.45)


def render_photo_card(photo_path, category, headline, accent_word, out_path,
                      footer="For the latest news", handle="@aravindnews24", logo_path=None, **_ignored):
    """Still 1080x1920 card for a photo Reel, in the same layout."""
    photo = Image.open(photo_path).convert("RGB")
    canvas = _backdrop(photo)
    aspect = photo.width / photo.height
    if fit_mode(aspect) == "cover":
        win = carousel.cover_crop_biased(photo, W, WIN_H, 0.25)
        canvas.paste(win, (0, WIN_Y))
    else:
        scale = min(W / photo.width, WIN_H / photo.height)
        w, h = int(photo.width * scale), int(photo.height * scale)
        canvas.paste(photo.resize((w, h), Image.LANCZOS), ((W - w) // 2, WIN_Y + (WIN_H - h) // 2))
    frame = os.path.splitext(out_path)[0] + "_frame.png"
    render_frame_png(category, headline, accent_word, frame, footer, handle)
    canvas = canvas.convert("RGBA")
    canvas.alpha_composite(Image.open(frame))
    os.remove(frame)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ---------------------------------------------------------------- video
def _ffmpeg():
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    from src import roundup_reel
    return roundup_reel.ffmpeg_exe() or "ffmpeg"


def probe_aspect(clip_path):
    """width/height as displayed (rotation applied); None if it cannot be read.
    Uses ffprobe when present, otherwise reads it from `ffmpeg -i`."""
    import re
    import shutil
    try:
        if shutil.which("ffprobe"):
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
                   "-of", "json", clip_path]
            st = json.loads(subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout)["streams"][0]
            w, h = int(st["width"]), int(st["height"])
            rot = 0
            for sd in st.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(float(sd["rotation"]))
            rot = rot or int(float((st.get("tags") or {}).get("rotate", 0) or 0))
        else:
            log = subprocess.run([_ffmpeg(), "-i", clip_path], capture_output=True, text=True, timeout=30).stderr
            m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", log)
            w, h = int(m.group(1)), int(m.group(2))
            r = re.search(r"rotation of (-?[\d.]+) degrees", log)
            rot = int(float(r.group(1))) if r else 0
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w / h
    except Exception:
        return None


def build_filter(aspect):
    """ffmpeg filter graph: [0] = the clip, [1] = the frame PNG."""
    bg = (f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=30:6,eq=brightness=-0.2[bg];")
    if aspect is not None and fit_mode(aspect) == "cover":
        win = (f"[b]scale={W}:{WIN_H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{WIN_H}:(iw-{W})/2:(ih-{WIN_H})*0.25[win];")
        place = f"overlay=0:{WIN_Y}"
    else:
        win = f"[b]scale={W}:{WIN_H}:force_original_aspect_ratio=decrease[win];"
        place = f"overlay=(main_w-overlay_w)/2:{WIN_Y}+({WIN_H}-overlay_h)/2"
    return ("[0:v]split=2[a][b];" + bg + win + f"[bg][win]{place}[v1];[v1][1:v]overlay=0:0[outv]")


def render_reel_from_clip(clip_path, frame_png, out_path, max_duration_sec):
    """Muted (by the owner's choice) Reel: clip in the window, frame on top."""
    fc = build_filter(probe_aspect(clip_path))
    cmd = [_ffmpeg(), "-y", "-i", clip_path, "-loop", "1", "-i", frame_png,
           "-t", str(max_duration_sec), "-filter_complex", fc, "-map", "[outv]", "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {res.stderr[-2000:]}")
    return out_path
