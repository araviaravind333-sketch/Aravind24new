"""
Feed post (1080x1350, 4:5) -- adaptive "broadcast" layout
=========================================================
One design that is sized from the picture, so there is no dead space:

    picture      top-aligned, full width; its height is whatever the headline
                 leaves (640-900px), fading softly into the panel
    panel        near-black; a short category-colour bar, the headline (as large
                 as fits, up to 112px / 5 lines, key phrase highlighted), and a
                 FOLLOW button anchored to the bottom edge

A picture that suits the window (<= 30% cropped) fills it. A much wider one
is shown whole -- its window shrinks to its own height and the headline grows
into the freed space. A tall one is shown whole on a blurred, darkened copy
of itself. Nothing is stretched, and nothing is invented.

Same look as the carousel slides and the Reels (src/clip_reel.py).
"""

import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from src import carousel
from src.template import (ANTON, ARCHIVO, ACCENT, CATEGORY_COLORS, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb)

W, H = 1080, 1350
MARGIN = 70
CROP_LIMIT = 0.30
WIN_MIN, WIN_MAX = 640, 900
BAR_H, BAR_GAP = 10, 24
CTA_H, BOTTOM_PAD, TOP_PAD, CTA_GAP = 64, 40, 36, 30
FIXED = TOP_PAD + BAR_H + BAR_GAP + CTA_GAP + CTA_H + BOTTOM_PAD
_PANEL = _hex_to_rgb(NEAR_BLACK)
BRAND = "ARAVIND NEWS 24"


def _crop_loss(aspect, win_aspect):
    return 1 - win_aspect / aspect if aspect >= win_aspect else 1 - aspect / win_aspect


def layout_for(aspect, headline):
    probe = ImageDraw.Draw(Image.new("RGB", (W, H)))
    _, _, _, h0 = carousel._fit_headline(probe, headline, W - MARGIN * 2, 4, 96, 52, 300)
    target = max(WIN_MIN, min(WIN_MAX, H - FIXED - h0))
    win_aspect = W / target
    if aspect and _crop_loss(aspect, win_aspect) > CROP_LIMIT:
        mode = "contain"
        win_h = max(min(target, round(W / aspect)) if aspect > win_aspect else target, 520)
    else:
        mode, win_h = "cover", target
    head_avail = H - win_h - FIXED
    return {"mode": mode, "win_h": win_h, "head_avail": head_avail, "aspect": aspect}


def _fit(draw, headline, avail):
    return carousel._fit_headline(draw, headline, W - MARGIN * 2, 5, 112, 52, max(avail, 120))


def _photo_layer(photo, lay):
    """Full-canvas RGB with the picture placed in its window."""
    canvas = Image.new("RGB", (W, H), _PANEL)
    wh = lay["win_h"]
    if lay["mode"] == "cover":
        is_portrait = photo.height >= photo.width * 0.9
        win = carousel.cover_crop_biased(photo, W, wh, 0.18 if is_portrait else 0.4)
    else:
        scale = max(W / photo.width, wh / photo.height)
        bg = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1), Image.BILINEAR)
        left, top = (bg.width - W) // 2, (bg.height - wh) // 2
        bg = bg.crop((left, top, left + W, top + wh)).filter(ImageFilter.GaussianBlur(36))
        win = ImageEnhance.Brightness(bg).enhance(0.45)
        s = min(W / photo.width, wh / photo.height)
        w, h = int(photo.width * s), int(photo.height * s)
        win.paste(photo.resize((w, h), Image.LANCZOS), ((W - w) // 2, (wh - h) // 2))
    win = ImageEnhance.Contrast(win).enhance(1.05)
    win = ImageEnhance.Color(win).enhance(1.05)
    canvas.paste(win, (0, 0))
    return canvas


def render_post(photo_path, category, headline, accent_word, out_path,
                footer="For the latest news", handle="@aravindnews24", logo_path=None,
                file_photo=False, **_ignored):
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    photo = Image.open(photo_path).convert("RGB")
    lay = layout_for(photo.width / photo.height, headline)
    wh = lay["win_h"]

    canvas = _photo_layer(photo, lay).convert("RGBA")

    # soft fade from the picture into the panel + a scrim behind the chips
    fade_h = 120
    fade = Image.new("RGBA", (W, fade_h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for y in range(fade_h):
        fd.line([(0, y), (W, y)], fill=_PANEL + (int(255 * (y / fade_h) ** 1.8),))
    canvas.alpha_composite(fade, (0, wh - fade_h))
    scrim = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(160):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(150 * (1 - y / 160))))
    canvas.alpha_composite(scrim, (0, 0))

    draw = ImageDraw.Draw(canvas)

    # brand chip (left) + category pill (right)
    f_brand = _font(ARCHIVO, 28)
    bw = draw.textlength(BRAND, font=f_brand)
    y0 = 44
    draw.rounded_rectangle([MARGIN, y0, MARGIN + bw + 60, y0 + 54], radius=8, fill=(0, 0, 0, 200))
    draw.rectangle([MARGIN + 16, y0 + 14, MARGIN + 24, y0 + 40], fill=carousel.BADGE_COLOR)
    draw.text((MARGIN + 38, y0 + 10), BRAND, font=f_brand, fill=WHITE)
    if category:
        cw = draw.textlength(category, font=f_brand)
        x1 = W - MARGIN
        draw.rounded_rectangle([x1 - cw - 50, y0, x1, y0 + 54], radius=8, fill=color)
        draw.text((x1 - cw - 25, y0 + 10), category, font=f_brand, fill=WHITE)

    if file_photo:
        f_tag = _font(ARCHIVO, 24)
        tw = draw.textlength("FILE PHOTO", font=f_tag)
        ty = wh - fade_h + 20 - 54
        draw.rounded_rectangle([MARGIN, ty, MARGIN + tw + 30, ty + 46], radius=6, fill=(0, 0, 0, 175))
        draw.text((MARGIN + 15, ty + 10), "FILE PHOTO", font=f_tag, fill=WHITE)

    # accent bar + headline
    draw.rectangle([MARGIN, wh + TOP_PAD, MARGIN + 130, wh + TOP_PAD + BAR_H], fill=color)
    top = wh + TOP_PAD + BAR_H + BAR_GAP
    lines, f_head, size, h = _fit(draw, headline, lay["head_avail"])
    a = (accent_word or "").upper().strip()
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top,
                                        a if a and a in headline.upper() else "")

    # FOLLOW button, anchored to the bottom edge
    f_cta = _font(ANTON, 34)
    label = f"FOLLOW  {handle.upper()}"
    lw = draw.textlength(label, font=f_cta)
    cy = H - BOTTOM_PAD - CTA_H
    draw.rounded_rectangle([MARGIN, cy, MARGIN + lw + 60, cy + CTA_H], radius=12, fill=color)
    draw.text((MARGIN + 30, cy + 12), label, font=f_cta, fill=WHITE)
    f_small = _font(ARCHIVO, 26)
    tag = "Daily India + world news"
    tw = draw.textlength(tag, font=f_small)
    draw.text((W - MARGIN - tw, cy + 18), tag, font=f_small, fill=MUTED)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
