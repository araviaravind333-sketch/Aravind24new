"""
Owner-supplied Reels (video clip or photo) -- the "window" layout
=================================================================
Replaces the older full-bleed layouts, where the headline was printed over
the footage and a clip that did not match 9:16 was cropped or boxed with
black bars. Same design language as the carousel slides:

    the picture, in its own window (brand chip + category pill on a soft scrim
    at its top; nothing else written over it), then a solid dark headline
    panel that runs to the bottom edge. The window's height is whatever the
    headline leaves, so a picture that suits it fills it with no gap above or
    below; only a much wider/taller picture is shown whole (never stretched)
    on a blurred, darkened copy of itself. Nothing important sits in
    Instagram's top ~200px or bottom ~360px.
"""

import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from src import carousel
from src.template import (ANTON, ARCHIVO, ACCENT, CATEGORY_COLORS, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb)

W, H = 1080, 1920
ZONE_TOP, ZONE_BOTTOM = 210, 1560   # content lives here; Instagram's controls own the rest
MAX_WIN_H = 1100
CROP_LIMIT = 0.30                   # fill the window if at most this much of the picture is cropped
MARGIN = 70
_PANEL = _hex_to_rgb(NEAR_BLACK)
BRAND = "ARAVIND NEWS 24"


def _crop_loss(aspect, win_aspect):
    """Fraction of the picture lost when it is cover-cropped into the window."""
    return 1 - win_aspect / aspect if aspect >= win_aspect else 1 - aspect / win_aspect


HEAD_PAD = 40 + 30 + 34 + 30        # above headline + gap to brand line + brand line + below


def _fit_head(probe, headline, avail):
    """Largest headline (<=120px, <=5 lines) that fits `avail` px of height."""
    return carousel._fit_headline(probe, headline, W - MARGIN * 2, 5, 120, 48, max(avail, 120))


def layout_for(aspect, headline):
    """Sized from the picture's shape and the headline, so nothing floats in a
    gap:
      * a normal headline is measured first and the picture window takes the
        rest of the content zone;
      * a picture that fits that window (<= CROP_LIMIT cropped) fills it;
      * a much wider/taller one is shown whole, the window shrinks to it, and
        the headline grows into the space that frees up (up to 120px);
      * any space still left is split evenly above/below the picture+headline
        block, and the panel runs to the bottom edge."""
    probe = ImageDraw.Draw(Image.new("RGB", (W, H)))
    zone = ZONE_BOTTOM - ZONE_TOP
    _, _, _, h0 = carousel._fit_headline(probe, headline, W - MARGIN * 2, 4, 88, 48, 260)
    target = min(MAX_WIN_H, zone - (h0 + HEAD_PAD))
    win_aspect = W / target
    if aspect and _crop_loss(aspect, win_aspect) > CROP_LIMIT:
        mode = "contain"
        win_h = max(min(target, round(W / aspect)) if aspect > win_aspect else target, 560)
    else:
        mode, win_h = "cover", target
    head_avail = zone - win_h - HEAD_PAD
    _, _, _, h = _fit_head(probe, headline, head_avail)
    block = win_h + h + HEAD_PAD
    win_y = ZONE_TOP + max(0, (zone - block) // 2)
    return {"mode": mode, "win_y": win_y, "win_h": win_h, "panel_y": win_y + win_h,
            "panel_h": H - (win_y + win_h), "head_avail": head_avail, "aspect": aspect}


def fit_mode(aspect, headline="HEADLINE"):
    return layout_for(aspect, headline)["mode"]


def _accent(headline, accent_word):
    a = (accent_word or "").upper().strip()
    return a if a and a in headline.upper() else ""


def render_frame_png(category, headline, accent_word, out_path,
                     footer="For the latest news", handle="@aravindnews24", logo_path=None, layout=None):
    """Transparent 1080x1920 overlay: everything except the picture window."""
    lay = layout or layout_for(None, headline)
    win_y, panel_y, panel_h = lay["win_y"], lay["panel_y"], lay["panel_h"]
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # solid dark band above the picture (Instagram's own controls draw here)
    canvas.alpha_composite(Image.new("RGBA", (W, win_y), _PANEL + (255,)), (0, 0))

    # soft scrim at the top of the picture window, behind the chip + pill
    scrim = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(170):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(150 * (1 - y / 170))))
    canvas.alpha_composite(scrim, (0, win_y))

    # the headline panel: solid, running to the bottom edge of the frame
    canvas.alpha_composite(Image.new("RGBA", (W, panel_h), _PANEL + (250,)), (0, panel_y))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, panel_y, W, panel_y + 8], fill=color)

    # brand chip (left) and category pill (right), over the top of the picture
    f_brand = _font(ARCHIVO, 28)
    bw = draw.textlength(BRAND, font=f_brand)
    y0 = win_y + 34
    draw.rounded_rectangle([MARGIN, y0, MARGIN + bw + 60, y0 + 54], radius=8, fill=(0, 0, 0, 200))
    draw.rectangle([MARGIN + 16, y0 + 14, MARGIN + 24, y0 + 40], fill=carousel.BADGE_COLOR)
    draw.text((MARGIN + 38, y0 + 10), BRAND, font=f_brand, fill=WHITE)
    # No boxed category label over the footage, by request -- quieter,
    # matching src/feed_post.py. The category still shows up as the accent
    # bar colour above the headline.
    top = panel_y + 40
    lines, f_head, size, h = _fit_head(draw, headline, lay["head_avail"])
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top, _accent(headline, accent_word))

    f_foot = _font(ARCHIVO, 30)
    fy = top + h + 30
    if handle and handle in footer:
        pre, _, post = footer.partition(handle)
        x = MARGIN
        for txt, fill in ((pre, MUTED), (handle, WHITE), (post, MUTED)):
            draw.text((x, fy), txt, font=f_foot, fill=fill)
            x += draw.textlength(txt, font=f_foot)
    else:
        draw.text((MARGIN, fy), f"{footer}  \u2192  {handle}", font=f_foot, fill=MUTED)

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
    lay = layout_for(photo.width / photo.height, headline)
    canvas = _backdrop(photo)
    if lay["mode"] == "cover":
        canvas.paste(carousel.cover_crop_biased(photo, W, lay["win_h"], 0.25), (0, lay["win_y"]))
    else:
        scale = min(W / photo.width, lay["win_h"] / photo.height)
        w, h = int(photo.width * scale), int(photo.height * scale)
        canvas.paste(photo.resize((w, h), Image.LANCZOS),
                     ((W - w) // 2, lay["win_y"] + (lay["win_h"] - h) // 2))
    frame = os.path.splitext(out_path)[0] + "_frame.png"
    render_frame_png(category, headline, accent_word, frame, footer, handle, layout=lay)
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


def build_filter(lay):
    """ffmpeg filter graph: [0] = the clip, [1] = the frame PNG."""
    ww, wh, wy = W, lay["win_h"], lay["win_y"]
    bg = (f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=30:6,eq=brightness=-0.25[bg];")
    if lay["mode"] == "cover":
        win = (f"[b]scale={ww}:{wh}:force_original_aspect_ratio=increase,"
               f"crop={ww}:{wh}:(iw-{ww})/2:(ih-{wh})*0.25[win];")
        place = f"overlay=0:{wy}"
    else:
        win = f"[b]scale={ww}:{wh}:force_original_aspect_ratio=decrease[win];"
        place = f"overlay=(main_w-overlay_w)/2:{wy}+({wh}-overlay_h)/2"
    return "[0:v]split=2[a][b];" + bg + win + f"[bg][win]{place}[v1];[v1][1:v]overlay=0:0[outv]"


def render_reel_from_clip(clip_path, frame_png, out_path, max_duration_sec, layout=None):
    """Muted (by the owner's choice) Reel: clip in the window, frame on top.
    `layout` must be the one the frame PNG was rendered with."""
    lay = layout or layout_for(probe_aspect(clip_path), "")
    cmd = [_ffmpeg(), "-y", "-i", clip_path, "-loop", "1", "-i", frame_png,
           "-t", str(max_duration_sec), "-filter_complex", build_filter(lay), "-map", "[outv]", "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {res.stderr[-2000:]}")
    return out_path
