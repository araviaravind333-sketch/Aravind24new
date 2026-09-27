"""
Feed post (1080x1350, 4:5) -- adaptive "broadcast" layout
=========================================================
One design that is sized from the picture, so there is no dead space:

    picture      top-aligned, full width; its height is whatever the headline
                 leaves (640-900px), fading softly into the panel
    panel        near-black; a short category-colour bar, the headline (as large
                 as fits, up to 112px / 5 lines, key phrase highlighted), and a
                 plain credit line anchored to the bottom edge

Deliberately quiet, by request: no coloured category label, no boxed
"FOLLOW" button, no tagline -- just the brand chip and a plain text credit
line, the same understated style a real newsroom account uses. The only
colour accents left are the category bar above the headline and the
highlighted key phrase within it.

A picture that suits the window (<= 30% cropped) fills it. A much wider one
is shown whole -- its window shrinks to its own height and the headline grows
into the freed space. A tall one is shown whole on a blurred, darkened copy
of itself. Nothing is stretched, and nothing is invented.

Same look as the carousel slides and the Reels (src/clip_reel.py).
"""

import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from src import carousel
from src.template import (ARCHIVO, ACCENT, CATEGORY_COLORS, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb)

W, H = 1080, 1350
MARGIN = 70
CROP_LIMIT = 0.30
WIN_MIN, WIN_MAX = 640, 900
BAR_H, BAR_GAP = 10, 24
FOOT_H, FOOT_GAP, TOP_PAD, BOTTOM_PAD = 40, 30, 36, 44
FIXED = TOP_PAD + BAR_H + BAR_GAP + FOOT_GAP + FOOT_H + BOTTOM_PAD
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


def render_text_post(category, headline, accent_word, out_path, subhead="",
                     footer="For the latest news", handle="@aravindnews24", **_ignored):
    """No legitimately usable photograph exists for this story, so the card
    is typographic rather than illustrated. A designed text card is an
    honest visual -- unlike a stock photo, it never implies it is a picture
    of the event. Deliberately bold: the headline is the whole design."""
    color = CATEGORY_COLORS.get((category or "").upper(), ACCENT)
    canvas = Image.new("RGB", (W, H), _PANEL)
    draw = ImageDraw.Draw(canvas)

    # a broad, very low-contrast wash of the category colour so the card
    # still reads as "this brand" rather than a plain black square
    wash = Image.new("RGB", (W, H), _PANEL)
    wd = ImageDraw.Draw(wash)
    color_rgb = _hex_to_rgb(color) if isinstance(color, str) else color
    for y in range(H):
        t = (1 - y / H) ** 2
        wd.line([(0, y), (W, y)], fill=tuple(int(p + (c - p) * 0.13 * t)
                                             for p, c in zip(_PANEL, color_rgb)))
    canvas = wash
    draw = ImageDraw.Draw(canvas)

    # brand chip, top-left -- same as the photo card
    f_brand = _font(ARCHIVO, 28)
    bw = draw.textlength(BRAND, font=f_brand)
    y0 = 52
    draw.rounded_rectangle([MARGIN, y0, MARGIN + bw + 60, y0 + 54], radius=8, fill=(0, 0, 0))
    draw.rectangle([MARGIN + 16, y0 + 14, MARGIN + 24, y0 + 40], fill=carousel.BADGE_COLOR)
    draw.text((MARGIN + 38, y0 + 10), BRAND, font=f_brand, fill=WHITE)

    f_sub = _font(ARCHIVO, 32)
    sub_lines = carousel._wrap(draw, subhead, f_sub, W - MARGIN * 2)[:3] if subhead else []
    sub_h = len(sub_lines) * 44

    # the headline owns the card: as large as fits the whole middle band
    bar_y = 240
    foot_y = H - BOTTOM_PAD - FOOT_H
    avail = foot_y - 40 - (sub_h + 40 if sub_lines else 0) - (bar_y + BAR_H + BAR_GAP)
    lines, f_head, size, h = carousel._fit_headline(
        draw, headline, W - MARGIN * 2, 6, 150, 56, max(avail, 200))

    block = BAR_H + BAR_GAP + h + (40 + sub_h if sub_lines else 0)
    top = bar_y + max(0, ((foot_y - 40 - bar_y) - block) // 2)
    draw.rectangle([MARGIN, top, MARGIN + 130, top + BAR_H], fill=color)
    y = top + BAR_H + BAR_GAP
    a = (accent_word or "").upper().strip()
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, y,
                                        a if a and a in headline.upper() else "")
    y += h + 40
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=MUTED)
        y += 44

    _credit_line(draw, footer, handle)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


def _credit_line(draw, footer, handle):
    """Plain credit line at the bottom edge -- no box, no colour."""
    f_foot = _font(ARCHIVO, 30)
    fy = H - BOTTOM_PAD - FOOT_H
    if handle and handle in footer:
        pre, _, post = footer.partition(handle)
        x = MARGIN
        for txt, fill in ((pre, MUTED), (handle, WHITE), (post, MUTED)):
            draw.text((x, fy), txt, font=f_foot, fill=fill)
            x += draw.textlength(txt, font=f_foot)
    else:
        draw.text((MARGIN, fy), f"{footer}  →  {handle}", font=f_foot, fill=MUTED)


def render_post(photo_path, category, headline, accent_word, out_path,
                footer="For the latest news", handle="@aravindnews24", logo_path=None,
                file_photo=False, photo_note="", **_ignored):
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    photo = Image.open(photo_path).convert("RGB")
    lay = layout_for(photo.width / photo.height, headline)
    wh = lay["win_h"]

    canvas = _photo_layer(photo, lay).convert("RGBA")

    # soft fade from the picture into the panel + a scrim behind the brand chip
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

    # brand chip, top-left -- the only thing over the picture
    f_brand = _font(ARCHIVO, 28)
    bw = draw.textlength(BRAND, font=f_brand)
    y0 = 44
    draw.rounded_rectangle([MARGIN, y0, MARGIN + bw + 60, y0 + 54], radius=8, fill=(0, 0, 0, 200))
    draw.rectangle([MARGIN + 16, y0 + 14, MARGIN + 24, y0 + 40], fill=carousel.BADGE_COLOR)
    draw.text((MARGIN + 38, y0 + 10), BRAND, font=f_brand, fill=WHITE)

    # Photo caption, sitting in the fade at the foot of the picture. Small
    # and quiet, but NOT optional: when the photo is a file shot of a place
    # rather than a picture of the event, the post has to say so.
    if photo_note:
        f_note = _font(ARCHIVO, 24)
        draw.text((MARGIN, wh - 42), photo_note, font=f_note, fill=(190, 190, 190))

    # accent bar + headline
    draw.rectangle([MARGIN, wh + TOP_PAD, MARGIN + 130, wh + TOP_PAD + BAR_H], fill=color)
    top = wh + TOP_PAD + BAR_H + BAR_GAP
    lines, f_head, size, h = _fit(draw, headline, lay["head_avail"])
    a = (accent_word or "").upper().strip()
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top,
                                        a if a and a in headline.upper() else "")

    _credit_line(draw, footer, handle)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
