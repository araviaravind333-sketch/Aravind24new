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
from src.template import (ARCHIVO, PT_SERIF, ACCENT, CATEGORY_COLORS, MUTED, NEAR_BLACK, WHITE,
                          _font, _hex_to_rgb, _wrap)

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


def _fit(draw, headline, avail):
    return carousel._fit_headline(draw, headline, W - MARGIN * 2, 5, 112, 52, max(avail, 120))


# Effectively unbounded: passed as `avail` so _fit_headline is never
# artificially shrunk by a guessed vertical budget. It always returns the
# largest font that satisfies the WIDTH/line-count limit alone -- the
# headline's true natural height, which everything else is then sized to.
_UNBOUNDED = 5000


def layout_for(aspect, headline):
    """The picture window is sized from the headline's REAL height, computed
    first -- not from an estimate made before the headline was actually
    fitted. A prior version guessed the height with a different (max_lines,
    start_size) than the real fit used, so the real headline routinely
    needed less room than guessed, and the unused room became a gap between
    the headline and the credit line (which sits at a fixed position)
    instead of being given back to the picture."""
    probe = ImageDraw.Draw(Image.new("RGB", (W, H)))
    _, _, _, natural_h = _fit(probe, headline, _UNBOUNDED)
    content_h = TOP_PAD + BAR_H + BAR_GAP + natural_h + FOOT_GAP + FOOT_H + BOTTOM_PAD
    win_h = max(WIN_MIN, min(WIN_MAX, H - content_h))
    win_aspect = W / win_h
    if aspect and _crop_loss(aspect, win_aspect) > CROP_LIMIT:
        mode = "contain"
        win_h = max(min(win_h, round(W / aspect)) if aspect > win_aspect else win_h, 520)
    else:
        mode = "cover"
    return {"mode": mode, "win_h": win_h, "aspect": aspect}


def _photo_layer(photo, lay, plain_bg=False):
    """Full-canvas RGB with the picture placed in its window. `plain_bg`
    fills behind a letterboxed photo with flat panel colour instead of a
    blurred copy of the photo -- a blurred copy is a derivative, which a
    share-alike file must not be turned into."""
    canvas = Image.new("RGB", (W, H), _PANEL)
    wh = lay["win_h"]
    if lay["mode"] == "cover":
        is_portrait = photo.height >= photo.width * 0.9
        win = carousel.cover_crop_biased(photo, W, wh, 0.18 if is_portrait else 0.4)
    else:
        if plain_bg:
            win = Image.new("RGB", (W, wh), _PANEL)
        else:
            scale = max(W / photo.width, wh / photo.height)
            bg = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1), Image.BILINEAR)
            left, top = (bg.width - W) // 2, (bg.height - wh) // 2
            bg = bg.crop((left, top, left + W, top + wh)).filter(ImageFilter.GaussianBlur(36))
            win = ImageEnhance.Brightness(bg).enhance(0.45)
        s = min(W / photo.width, wh / photo.height)
        w, h = int(photo.width * s), int(photo.height * s)
        win.paste(photo.resize((w, h), Image.LANCZOS), ((W - w) // 2, (wh - h) // 2))
    if not plain_bg:
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
    sub_extra = (40 + sub_h) if sub_lines else 0

    # The whole group (bar + headline + subhead + footer) is sized from the
    # headline's REAL height and centred as ONE block, so a short headline
    # gets even, deliberate-looking margins on both sides instead of a
    # large gap that used to appear only above it (a fixed bar position,
    # centred within a zone that started well below the true middle, plus
    # a footer pinned to the very bottom regardless of where the content
    # actually ended).
    TOP_CLEAR, BOTTOM_CLEAR = 150, 60
    zone_h = (H - BOTTOM_CLEAR) - TOP_CLEAR
    head_avail = zone_h - (BAR_H + BAR_GAP) - sub_extra - (FOOT_GAP + FOOT_H)
    lines, f_head, size, h = carousel._fit_headline(
        draw, headline, W - MARGIN * 2, 6, 150, 56, max(head_avail, 200))

    block = BAR_H + BAR_GAP + h + sub_extra + FOOT_GAP + FOOT_H
    top = TOP_CLEAR + max(0, (zone_h - block) // 2)
    draw.rectangle([MARGIN, top, MARGIN + 130, top + BAR_H], fill=color)
    y = top + BAR_H + BAR_GAP
    a = (accent_word or "").upper().strip()
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, y,
                                        a if a and a in headline.upper() else "")
    y += h + (40 if sub_lines else 0)
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=MUTED)
        y += 44

    _credit_line(draw, footer, handle, y=y + FOOT_GAP)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


def _credit_line(draw, footer, handle, y=None):
    """Plain credit line -- no box, no colour. `y` follows the content that
    was actually drawn above it; the fixed-bottom fallback only applies
    when a caller has no dynamic position to give it."""
    f_foot = _font(ARCHIVO, 30)
    fy = H - BOTTOM_PAD - FOOT_H if y is None else y
    if handle and handle in footer:
        pre, _, post = footer.partition(handle)
        x = MARGIN
        for txt, fill in ((pre, MUTED), (handle, WHITE), (post, MUTED)):
            draw.text((x, fy), txt, font=f_foot, fill=fill)
            x += draw.textlength(txt, font=f_foot)
    else:
        draw.text((MARGIN, fy), f"{footer}  →  {handle}", font=f_foot, fill=MUTED)


def _fit_serif(draw, headline, max_w, max_lines=5, start=76, minimum=40):
    """Largest PT Serif Bold size that wraps `headline` (its OWN casing --
    this style is sentence case, not shouted caps) into <= max_lines at
    max_w. Returns (lines, font, size, total_block_height)."""
    size = start
    while size > minimum:
        f = _font(PT_SERIF, size)
        lines = _wrap(draw, headline, f, max_w)
        if len(lines) <= max_lines:
            break
        size -= 4
    f = _font(PT_SERIF, size)
    lines = _wrap(draw, headline, f, max_w)
    line_h = int(size * 1.18)
    return lines, f, size, line_h * len(lines)


def render_overlay_post(photo_path, category, headline, accent_word, out_path,
                        footer="For the latest news", handle="@aravindnews24",
                        file_photo=False, photo_note="", **_ignored):
    """Full-bleed photo with the headline set directly on it in serif type,
    sentence case, with a thin colour-accent bar -- the "premium newsroom"
    look the owner pointed to as a reference and asked for by name.

    Only usable when the photo may be freely cropped to fill the frame.
    NEVER for a share-alike photo, which src/context_photos.py's terms
    require to be shown whole and unmodified -- render_post (whole image,
    plain background) is what those use instead.

    `photo_note` (e.g. "File photo: X") is accepted but not drawn on the
    image, by request -- the required author/licence credit is already in
    the post's caption text (see main.py's build around `photo_credit`),
    so the on-image label was a purely cosmetic redundancy."""
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    color_rgb = _hex_to_rgb(color) if isinstance(color, str) else color
    photo = Image.open(photo_path).convert("RGB")
    is_portrait = photo.height >= photo.width * 0.9
    canvas = carousel.cover_crop_biased(photo, W, H, 0.15 if is_portrait else 0.35).convert("RGBA")

    # A tight scrim just above the headline zone, not a long fade -- the
    # photo should read clearly for most of the frame, darkening only
    # where the text actually sits (matching the reference).
    fade_h = int(H * 0.42)
    grad = Image.new("RGBA", (W, fade_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(fade_h):
        t = y / fade_h
        gd.line([(0, y), (W, y)], fill=(4, 4, 4, int(235 * (t ** 2.2))))
    canvas.alpha_composite(grad, (0, H - fade_h))

    draw = ImageDraw.Draw(canvas)

    BOTTOM_PAD, BAR_W, TEXT_GAP = 76, 7, 27
    tx = MARGIN + BAR_W + TEXT_GAP
    lines, f_head, size, block_h = _fit_serif(draw, headline, W - tx - MARGIN)
    line_h = int(size * 1.18)
    top = H - BOTTOM_PAD - block_h

    # the one colour accent: a thin bar spanning the headline block
    draw.rectangle([MARGIN, top + 6, MARGIN + BAR_W, top + block_h - 6], fill=color_rgb)
    y = top
    for line in lines:
        draw.text((tx, y), line, font=f_head, fill=WHITE)
        y += line_h

    f_credit = _font(ARCHIVO, 26)
    draw.text((tx, top + block_h + 16), handle, font=f_credit, fill=_hex_to_rgb(MUTED))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


def render_post(photo_path, category, headline, accent_word, out_path,
                footer="For the latest news", handle="@aravindnews24", logo_path=None,
                file_photo=False, photo_note="", share_alike=False, **_ignored):
    category = (category or "").upper()
    color = CATEGORY_COLORS.get(category, ACCENT)
    photo = Image.open(photo_path).convert("RGB")
    lay = layout_for(photo.width / photo.height, headline)
    if share_alike:
        # shown whole, never cropped -- see src/context_photos.py
        lay = dict(lay, mode="contain")
    wh = lay["win_h"]

    canvas = _photo_layer(photo, lay, plain_bg=share_alike).convert("RGBA")

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

    # `photo_note` (e.g. "File photo: X") is accepted but not drawn on the
    # image, by request -- the required author/licence credit is already
    # in the post's caption text (main.py, around `photo_credit`), so the
    # on-image label was a purely cosmetic redundancy.

    # accent bar + headline, sized to its own natural height (see layout_for)
    draw.rectangle([MARGIN, wh + TOP_PAD, MARGIN + 130, wh + TOP_PAD + BAR_H], fill=color)
    top = wh + TOP_PAD + BAR_H + BAR_GAP
    lines, f_head, size, h = _fit(draw, headline, _UNBOUNDED)
    a = (accent_word or "").upper().strip()
    carousel._draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top,
                                        a if a and a in headline.upper() else "")

    # the credit line follows directly below the headline, never at a fixed
    # position that leaves a gap when the headline needed less room
    _credit_line(draw, footer, handle, y=top + h + FOOT_GAP)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
