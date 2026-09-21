"""
Daily Carousel Renderer
========================
Renders one flagship, competitor-style multi-slide carousel post per day
(the "@worldinlast24hrs" format the user pointed at): several of the
day's strongest stories, each its own numbered slide within ONE
Instagram post, instead of several separate posts.

This module only renders. src/carousel_review.py owns selection,
Telegram approval, and orchestration; src/publisher.py owns the actual
Graph API carousel-container calls.

Slide layout deliberately mirrors this project's existing feed-card style
(src/template.py) rather than inventing a new visual language: full-bleed
photo, bottom gradient, category pill, headline block, brand footer. The
one new element is the numbered corner badge (01, 02, 03...) that ties
each slide to its position, matching the reference competitor post.
"""

import os
import re

from PIL import Image, ImageDraw, ImageEnhance

from src.template import (
    W, H, ANTON, ARCHIVO, ACCENT, WHITE, NEAR_BLACK, MUTED,
    CATEGORY_COLORS, _font, _load_photo, _bottom_gradient,
    _headline_lines, _wrap, _vertical_gradient, _mix, _hex_to_rgb,
)

from src.subject_photos import cover_crop_biased

_ACCENT_NOISE = {"The", "This", "That", "After", "Over", "With", "From",
                 "Amid", "For", "And", "Says", "Will"}


def _pick_accent_phrase(headline):
    """Which phrase gets the solid highlight box, matching the reference
    post's style (a highlighter-style block behind a key name/number/
    place, not just a color change). Heuristic, not exact -- prefers a
    proper-noun run (a name or place), falling back to a number+word
    phrase ("44 Injured"), since those are what the reference consistently
    highlights."""
    proper = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", headline)
    proper = [p for p in proper if p.split()[0] not in _ACCENT_NOISE]
    if proper:
        return max(proper, key=len)
    numeric = re.search(r"\b\d+[%,\d]*\s+[A-Za-z]+\b", headline)
    if numeric:
        return numeric.group(0)
    return ""

# 4:5 matches this project's existing feed cards and is Instagram's
# recommended carousel ratio -- kept identical to W, H rather than a new
# constant so a slide looks like a natural extension of the regular feed,
# not a different product bolted on.
SLIDE_W, SLIDE_H = W, H

BADGE_COLOR = "#E01E1E"


def _draw_slide_badge(canvas, draw, index, total):
    """Top-right numbered stamp -- '01', '02'... -- the one visual
    element in the reference post that isn't already part of this
    project's existing card style."""
    text = f"{index:02d}"
    f = _font(ANTON, 44)
    tw = draw.textlength(text, font=f)
    pad = 22
    box_w, box_h = tw + pad * 2, 70
    x0, y0 = SLIDE_W - box_w - 50, 50
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=10, fill=BADGE_COLOR)
    draw.text((x0 + pad, y0 + 10), text, font=f, fill=WHITE)


def _draw_highlighted_headline(draw, lines, f_head, size, x, y, accent_phrase):
    """Draws each headline line, and if `accent_phrase` appears in a line,
    paints a solid red block behind just that substring with white text
    on top -- a highlighter mark, matching the reference post's style,
    rather than this project's other cards which only recolor the text."""
    line_h = int(size * 0.98)
    pad_y = 6
    for line in lines:
        if accent_phrase and accent_phrase in line:
            before, _, after = line.partition(accent_phrase)
            cx = x
            if before:
                draw.text((cx, y), before, font=f_head, fill=WHITE)
                cx += draw.textlength(before, font=f_head)
            aw = draw.textlength(accent_phrase, font=f_head)
            # sized from the real glyph bounds, so it hugs the letters and
            # never reaches into the line above or below
            bb = draw.textbbox((cx, y), accent_phrase, font=f_head)
            draw.rectangle([cx - 8, bb[1] - 6, cx + aw + 8, bb[3] + 6], fill=BADGE_COLOR)
            draw.text((cx, y), accent_phrase, font=f_head, fill=WHITE)
            cx += aw
            if after:
                draw.text((cx, y), after, font=f_head, fill=WHITE)
        else:
            draw.text((x, y), line, font=f_head, fill=WHITE)
        y += line_h


def _draw_slide_foreground(canvas, category, headline, subhead, index, total, footer, handle):
    """Everything except the background: gradient, pill, badge, headline,
    subhead, footer. Shared between render_carousel_slide (photo
    underneath) and render_carousel_overlay_png (transparent, composited
    onto real video via ffmpeg) so a video slide gets the exact same
    graphic as a photo slide, not a simplified stand-in."""
    grad = _bottom_gradient((SLIDE_W, SLIDE_H), int(SLIDE_H * 0.58))
    canvas = Image.alpha_composite(canvas, grad)
    draw = ImageDraw.Draw(canvas)
    MARGIN = 70

    _draw_slide_badge(canvas, draw, index, total)

    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    f_cat = _font(ARCHIVO, 28)
    tw = draw.textlength(category, font=f_cat)
    pill_h, pill_w = 54, tw + 50
    draw.rounded_rectangle([MARGIN, 50, MARGIN + pill_w, 50 + pill_h],
                            radius=8, fill=pill_color)
    draw.text((MARGIN + 25, 50 + 11), category, font=f_cat, fill=WHITE)

    # Subhead sits UNDER the headline in the reference post, so headline
    # position is computed leaving room for it beneath.
    f_sub = _font(ARCHIVO, 30)
    sub_lines = _wrap(draw, subhead, f_sub, SLIDE_W - MARGIN * 2)[:2] if subhead else []
    sub_h = len(sub_lines) * 38

    footer_top = SLIDE_H - 90
    sub_bottom = footer_top - 20
    sub_top = sub_bottom - sub_h
    headline_bottom = sub_top - 20 if sub_lines else sub_bottom

    lines, f_head, size = _headline_lines(draw, headline, SLIDE_W - MARGIN * 2,
                                           max_lines=4, start_size=88, min_size=48)
    line_h = int(size * 0.98)
    headline_top = headline_bottom - line_h * len(lines)

    accent_phrase = _pick_accent_phrase(headline)
    _draw_highlighted_headline(draw, lines, f_head, size, MARGIN, headline_top, accent_phrase)

    y = sub_top
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=MUTED)
        y += 38

    f_foot = _font(ARCHIVO, 30)
    draw.text((MARGIN, footer_top), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, footer_top), "→  " + handle, font=f_foot, fill=WHITE)
    return canvas


PHOTO_H = 790          # photo band height on a photo slide (of 1350)
_PANEL = _hex_to_rgb(NEAR_BLACK)


def _fit_headline(draw, headline, max_w, max_lines, start, minimum, avail_h):
    """Largest headline size whose wrapped lines fit BOTH the width and
    the vertical space actually available -- _headline_lines alone only
    guarantees the width/line-count, which is how a long headline used to
    run into the footer."""
    size = start
    while True:
        lines, font, sz = _headline_lines(draw, headline, max_w, max_lines=max_lines,
                                           start_size=size, min_size=minimum)
        h = len(lines) * int(sz * 0.98)
        if h <= avail_h or size <= minimum:
            return lines, font, sz, h
        size -= 6


def _pill_and_badge(canvas, draw, category, index):
    MARGIN = 70
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    f_cat = _font(ARCHIVO, 28)
    tw = draw.textlength(category, font=f_cat)
    draw.rounded_rectangle([MARGIN, 50, MARGIN + tw + 50, 104], radius=8, fill=pill_color)
    draw.text((MARGIN + 25, 61), category, font=f_cat, fill=WHITE)
    _draw_slide_badge(canvas, draw, index, 0)


def _footer(draw, footer, handle):
    """Brand line. The handle is picked out in white; when the footer text
    already contains it (it does by default) it is not printed twice."""
    f_foot = _font(ARCHIVO, 30)
    top = SLIDE_H - 90
    if handle and handle in footer:
        pre, _, post = footer.partition(handle)
        x = 70
        draw.text((x, top), pre, font=f_foot, fill=MUTED)
        x += draw.textlength(pre, font=f_foot)
        draw.text((x, top), handle, font=f_foot, fill=WHITE)
        x += draw.textlength(handle, font=f_foot)
        draw.text((x, top), post, font=f_foot, fill=MUTED)
        return
    draw.text((70, top), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((70 + fw, top), "\u2192  " + handle, font=f_foot, fill=WHITE)


PHOTO_H_MIN, PHOTO_H_MAX = 700, 900


def _render_split_slide(photo_path, category, headline, subhead, index, footer, handle,
                         file_photo=False):
    """Photo on top, headline in a solid panel underneath -- the text never
    sits on the subject. The photo band is sized to the text: a short
    headline gives the picture more room, a long one gives the words more,
    so the panel never has a dead gap in it (the old fixed split left ~200px
    of empty black under short headlines). The crop keeps the top of
    portrait photos so faces are not sliced."""
    W, H = SLIDE_W, SLIDE_H
    MARGIN = 70
    pill_color = CATEGORY_COLORS.get(category, ACCENT)

    probe = ImageDraw.Draw(Image.new("RGB", (W, H)))
    f_sub = _font(ARCHIVO, 30)
    sub_lines = _wrap(probe, subhead, f_sub, W - MARGIN * 2)[:2] if subhead else []
    sub_h = len(sub_lines) * 38
    # panel = top pad + headline + gap + subhead + gap above footer
    avail = (H - PHOTO_H_MIN) - 170 - (sub_h + 30 if sub_lines else 0)
    lines, f_head, size, h = _fit_headline(probe, headline, W - MARGIN * 2, 4, 84, 44, max(avail, 200))
    panel_h = 40 + h + (30 + sub_h if sub_lines else 0) + 30 + 100
    photo_h = max(PHOTO_H_MIN, min(PHOTO_H_MAX, H - panel_h))

    photo = Image.open(photo_path).convert("RGB")
    is_portrait = photo.height >= photo.width * 0.9
    photo = cover_crop_biased(photo, W, photo_h, 0.18 if is_portrait else 0.4)
    photo = ImageEnhance.Contrast(photo).enhance(1.05)
    photo = ImageEnhance.Color(photo).enhance(1.05)

    canvas = Image.new("RGB", (W, H), _PANEL)
    canvas.paste(photo, (0, 0))
    canvas = canvas.convert("RGBA")

    fade = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for y in range(170):
        fd.line([(0, y), (W, y)], fill=_PANEL + (int(255 * (y / 170) ** 1.7),))
    canvas.alpha_composite(fade, (0, photo_h - 170))
    scrim = Image.new("RGBA", (W, 150), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(150):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(140 * (1 - y / 150))))
    canvas.alpha_composite(scrim, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, photo_h, W, photo_h + 8], fill=pill_color)
    _pill_and_badge(canvas, draw, category, index)

    if file_photo:
        f_tag = _font(ARCHIVO, 24)
        tw = draw.textlength("FILE PHOTO", font=f_tag)
        draw.rounded_rectangle([70, photo_h - 84, 70 + tw + 30, photo_h - 36], radius=6,
                               fill=(0, 0, 0, 175))
        draw.text((85, photo_h - 74), "FILE PHOTO", font=f_tag, fill=WHITE)

    top = photo_h + 40
    _draw_highlighted_headline(draw, lines, f_head, size, MARGIN, top, _pick_accent_phrase(headline))
    y = top + h + 30
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=MUTED)
        y += 38
    _footer(draw, footer, handle)
    return canvas


def _render_text_slide(category, headline, subhead, index, footer, handle):
    """No photo exists (or is allowed): a typographic card. The headline
    block is centred in the frame rather than pinned to the bottom, which
    left the whole top half empty, and a large ghost numeral gives the
    slide a visual anchor without pretending to be a picture of anything."""
    W, H = SLIDE_W, SLIDE_H
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    top_c = _mix(pill_color, "#000000", 0.55)
    canvas = _vertical_gradient(W, H, top_c, _PANEL).convert("RGBA")

    ghost = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(ghost)
    f_ghost = _font(ANTON, 640)
    txt = f"{index:02d}"
    gd.text((W - gd.textlength(txt, font=f_ghost) - 20, 70), txt, font=f_ghost,
            fill=(255, 255, 255, 24))
    canvas = Image.alpha_composite(canvas, ghost)

    draw = ImageDraw.Draw(canvas)
    _pill_and_badge(canvas, draw, category, index)

    MARGIN = 70
    f_sub = _font(ARCHIVO, 32)
    sub_lines = _wrap(draw, subhead, f_sub, W - MARGIN * 2)[:3] if subhead else []
    sub_h = len(sub_lines) * 42
    region_top, region_bottom = 210, H - 130
    avail_head = (region_bottom - region_top) - (sub_h + 44 if sub_lines else 0) - 40
    lines, f_head, size, h = _fit_headline(draw, headline, W - MARGIN * 2, 5, 100, 52, avail_head)
    block_h = 24 + h + (44 + sub_h if sub_lines else 0)
    y0 = region_top + int(((region_bottom - region_top) - block_h) * 0.42)
    draw.rectangle([MARGIN, y0, MARGIN + 120, y0 + 10], fill=pill_color)
    y = y0 + 34
    _draw_highlighted_headline(draw, lines, f_head, size, MARGIN, y, _pick_accent_phrase(headline))
    y += h + 44
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=MUTED)
        y += 42
    _footer(draw, footer, handle)
    return canvas


def render_carousel_slide(photo_path, category, headline, subhead, index, total,
                            out_path, footer, handle, smart_fit=False, file_photo=False):
    """One slide. With a photo: photo band + headline panel. Without one:
    a centred typographic card. `smart_fit` is accepted for backward
    compatibility and ignored -- the blurred-letterbox fit is gone (it
    read as a rendering glitch); every photo is now cover-cropped with a
    face-safe bias. `file_photo=True` stamps the FILE PHOTO label.

    Same rule as everywhere else in this pipeline: a slide never gets a
    guessed or copyrighted photo. It gets a verified one (yours, a
    rights-cleared discovery, or a licensed portrait of the person named)
    or it stays a text card."""
    if photo_path is None:
        canvas = _render_text_slide(category, headline, subhead, index, footer, handle)
    else:
        canvas = _render_split_slide(photo_path, category, headline, subhead, index,
                                     footer, handle, file_photo=file_photo)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


def render_carousel_overlay_png(category, headline, subhead, index, total,
                                 out_path, footer, handle):
    """Same graphic as render_carousel_slide but on a transparent
    background, for compositing onto a real video clip (a user-supplied
    video for one carousel slide) via ffmpeg -- see
    render_carousel_video_slide. Keeps a video slide visually identical
    to a photo slide instead of looking like a different kind of post."""
    canvas = Image.new("RGBA", (SLIDE_W, SLIDE_H), (0, 0, 0, 0))
    canvas = _draw_slide_foreground(canvas, category, headline, subhead, index, total, footer, handle)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def render_carousel_video_slide(clip_path, overlay_png_path, out_path, max_duration_sec=12):
    """Crops a real submitted video clip to the same 4:5 frame as every
    other slide and composites the branded overlay on top -- the video
    equivalent of render_carousel_slide, reusing the exact ffmpeg approach
    src/video.py already uses for Reels (src/video.py's
    render_reel_from_clip), just at the carousel's 4:5 frame instead of
    the Reel's 9:16. Muted, same as every other video in this pipeline.
    Capped short (default 12s) since a carousel video auto-plays inline
    among several other slides, not as the sole focus like a Reel."""
    import subprocess
    filter_complex = (
        f"[0:v]scale={SLIDE_W}:{SLIDE_H}:force_original_aspect_ratio=increase,"
        f"crop={SLIDE_W}:{SLIDE_H}[bg];[bg][1:v]overlay=0:0[outv]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-loop", "1", "-i", overlay_png_path,
        "-t", str(max_duration_sec),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {result.stderr[-2000:]}")
    return out_path


# ============================================================
# Cover slide -- the un-numbered first slide, matching the reference
# post's own opening card ("BREAKING -- have a look at what happened in
# the world in the last 24 hours").
# ============================================================

def _build_collage(paths, w, h):
    """Fills a w x h area with up to 4 of the day's own real photos in a
    grid, each cover-cropped to its cell -- these are photographs this
    pipeline already legitimately has (rights-cleared discoveries or your
    own submitted media), never a stock or celebrity photo pulled in just
    for this slide. That is a deliberate limit, not an oversight: a
    composite of well-known public figures (as the reference post uses)
    is someone else's copyrighted photography arranged into a new image,
    which does not make it any more reusable than using one of those
    photos alone -- the same rule this whole project runs on. If none of
    today's slides has a real photo, the caller falls back to a plain
    gradient instead of inventing one."""
    canvas = Image.new("RGB", (w, h))
    n = min(len(paths), 4)
    if n == 0:
        return None
    if n == 1:
        canvas.paste(_load_photo(paths[0], w, h, False).convert("RGB"), (0, 0))
        return canvas
    if n == 2:
        cw = w // 2
        canvas.paste(_load_photo(paths[0], cw, h, False).convert("RGB"), (0, 0))
        canvas.paste(_load_photo(paths[1], w - cw, h, False).convert("RGB"), (cw, 0))
        return canvas
    if n == 3:
        # One large cell + two stacked -- a plain 2x2 grid with only 3
        # photos leaves the 4th cell blank, so 3 gets its own layout
        # rather than falling through the 4-cell one.
        cw, ch = w // 2, h // 2
        canvas.paste(_load_photo(paths[0], cw, h, False).convert("RGB"), (0, 0))
        canvas.paste(_load_photo(paths[1], w - cw, ch, False).convert("RGB"), (cw, 0))
        canvas.paste(_load_photo(paths[2], w - cw, h - ch, False).convert("RGB"), (cw, ch))
        return canvas
    cw, ch = w // 2, h // 2
    cells = [(0, 0, cw, ch), (cw, 0, w - cw, ch), (0, ch, cw, h - ch), (cw, ch, w - cw, h - ch)]
    for i in range(4):
        x, y, cell_w, cell_h = cells[i]
        canvas.paste(_load_photo(paths[i], cell_w, cell_h, False).convert("RGB"), (x, y))
    return canvas


def _build_color_mosaic(colors, w, h):
    """Fallback for the day nothing has a real, rights-cleared photo at
    all (measured: a real live run where all 8 stories came back
    text_only) -- a grid of that day's actual category colours instead
    of one flat gradient. Still entirely abstract graphic design, never
    pretending to be a photo of anything -- this is NOT a substitute for
    a real incident photo, it is only meant to look more like "several
    stories combined" than a single block of colour does, which is what
    was there before. If there is only one colour available, a vertical
    gradient of it is used instead of a pointless single-colour block."""
    colors = colors or [ACCENT]
    if len(colors) == 1:
        return _vertical_gradient(w, h, _mix(colors[0], "#000000", 0.15), _hex_to_rgb(NEAR_BLACK))
    n = min(len(colors), 6)
    cols = 3 if n > 4 else 2
    rows = (n + cols - 1) // cols
    cell_w, cell_h = w // cols, h // rows
    canvas = Image.new("RGB", (w, h), _hex_to_rgb(NEAR_BLACK))
    for i in range(n):
        r, c = divmod(i, cols)
        x0, y0 = c * cell_w, r * cell_h
        cw = w - x0 if c == cols - 1 else cell_w
        ch = h - y0 if r == rows - 1 else cell_h
        top = _mix(colors[i], "#000000", 0.1)
        block = _vertical_gradient(cw, ch, top, _hex_to_rgb(NEAR_BLACK))
        canvas.paste(block, (x0, y0))
    return canvas


COVER_PHOTO_H = 800


def render_cover_slide(top_word, subheadline, date_label, collage_paths, out_path,
                        footer, handle, fallback_colors=None):
    """The un-numbered opening slide, and the one that has to work as a
    thumbnail on its own. Layout matches the story slides so the carousel
    reads as one design: a photo collage of the day's own stories on top,
    fading into the dark panel, then a red tag + date, the big headline
    and the brand line. Nothing overlaps: every element has its own band."""
    W, H = SLIDE_W, SLIDE_H
    MARGIN = 70
    canvas = Image.new("RGB", (W, H), _PANEL)

    collage = _build_collage(collage_paths, W, COVER_PHOTO_H)
    if collage is None:
        collage = _build_color_mosaic(fallback_colors, W, COVER_PHOTO_H)
    canvas.paste(collage, (0, 0))
    canvas = canvas.convert("RGBA")

    # hairline gaps between collage cells so photos read as separate stories
    gd = ImageDraw.Draw(canvas)
    n = min(len(collage_paths or []), 4)
    if n >= 2:
        gd.rectangle([W // 2 - 3, 0, W // 2 + 3, COVER_PHOTO_H], fill=_PANEL)
    if n >= 3:
        gd.rectangle([W // 2 if n == 3 else 0, COVER_PHOTO_H // 2 - 3, W, COVER_PHOTO_H // 2 + 3], fill=_PANEL)

    fade = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for y in range(200):
        fd.line([(0, y), (W, y)], fill=_PANEL + (int(255 * (y / 200) ** 1.6),))
    canvas.alpha_composite(fade, (0, COVER_PHOTO_H - 200))
    scrim = Image.new("RGBA", (W, 150), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(150):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(150 * (1 - y / 150))))
    canvas.alpha_composite(scrim, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, COVER_PHOTO_H, W, COVER_PHOTO_H + 8], fill=BADGE_COLOR)

    # brand chip over the photos, top-left
    f_brand = _font(ARCHIVO, 30)
    brand = (handle or "").upper() or "ARAVIND NEWS 24"
    bw = draw.textlength(brand, font=f_brand)
    draw.rounded_rectangle([MARGIN, 50, MARGIN + bw + 44, 104], radius=8, fill=(0, 0, 0, 190))
    draw.text((MARGIN + 22, 61), brand, font=f_brand, fill=WHITE)

    # red tag + date on one line
    y = COVER_PHOTO_H + 44
    f_tag = _font(ANTON, 46)
    tag = top_word.upper()
    tw = draw.textlength(tag, font=f_tag)
    draw.rounded_rectangle([MARGIN, y, MARGIN + tw + 44, y + 68], radius=8, fill=BADGE_COLOR)
    draw.text((MARGIN + 22, y + 8), tag, font=f_tag, fill=WHITE)
    f_date = _font(ANTON, 46)
    dtw = draw.textlength(date_label, font=f_date)
    dx = MARGIN + tw + 44 + 24
    draw.rounded_rectangle([dx, y, dx + dtw + 44, y + 68], radius=8, outline=WHITE, width=3)
    draw.text((dx + 22, y + 8), date_label, font=f_date, fill=WHITE)

    # headline: what the whole post is
    head_top = y + 68 + 26
    footer_top = H - 90
    lines, f_head, size, h = _fit_headline(draw, subheadline.upper(), W - MARGIN * 2, 4, 84, 48,
                                           footer_top - 20 - head_top)
    _draw_highlighted_headline(draw, lines, f_head, size, MARGIN, head_top,
                               "LAST 24 HOURS" if any("LAST 24 HOURS" in ln for ln in lines) else None)

    _footer(draw, footer, handle)
    canvas = canvas.convert("RGB")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
