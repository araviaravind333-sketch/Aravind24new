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

from PIL import Image, ImageDraw

from src.template import (
    W, H, ANTON, ARCHIVO, ACCENT, WHITE, NEAR_BLACK, MUTED,
    CATEGORY_COLORS, _font, _load_photo, _bottom_gradient,
    _headline_lines, _wrap, _vertical_gradient, _mix, _hex_to_rgb,
)

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
            draw.rectangle([cx - 8, y - pad_y, cx + aw + 8, y + size + pad_y],
                           fill=BADGE_COLOR)
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


def render_carousel_slide(photo_path, category, headline, subhead, index, total,
                            out_path, footer, handle, smart_fit=False):
    """One slide: full-bleed photo, bottom gradient, category pill,
    numbered badge, headline, one-line subhead, brand footer.

    `smart_fit` should be True for a human-supplied photo (blurred
    letterbox rather than a crop that might cut off the actual subject --
    same reasoning as the main feed cards) and False for a discovered
    incident photo the publisher already framed correctly.

    `photo_path=None` renders a text-only slide (colour-graded gradient
    background, same as this project's existing text_card variant) --
    used when no authentic, rights-cleared photo was found for this
    story. Same "no image beats wrong image" rule as the rest of this
    pipeline: a carousel slide never gets a guessed or copyrighted photo,
    it just goes photo-less until you optionally reply with your own.
    """
    if photo_path is None:
        top = _mix(CATEGORY_COLORS.get(category, ACCENT), "#000000", 0.55)
        canvas = _vertical_gradient(SLIDE_W, SLIDE_H, top, _hex_to_rgb(NEAR_BLACK)).convert("RGBA")
    else:
        canvas = _load_photo(photo_path, SLIDE_W, SLIDE_H, smart_fit).convert("RGBA")
    canvas = _draw_slide_foreground(canvas, category, headline, subhead, index, total, footer, handle)
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


def render_cover_slide(top_word, subheadline, date_label, collage_paths, out_path,
                        footer, handle, fallback_colors=None):
    """The un-numbered opening slide: bold top word + wrapped subheadline
    on a white band (matching the reference's own layout -- text block
    up top, photo below, not text-over-photo like the numbered story
    slides), a real-photo collage (or a gradient if none exist yet) below
    it to give the viewer something worth swiping past, and a red date
    tag. This is the one slide in the carousel meant to work as a
    thumbnail on its own -- it is what a viewer sees before deciding to
    swipe at all."""
    canvas = Image.new("RGB", (SLIDE_W, SLIDE_H), WHITE)
    draw = ImageDraw.Draw(canvas)
    MARGIN = 70

    f_top = _font(ANTON, 140)
    draw.text((MARGIN, 70), top_word.upper(), font=f_top, fill=NEAR_BLACK)

    f_sub = _font(ANTON, 52)
    sub_lines = _wrap(draw, subheadline, f_sub, SLIDE_W - MARGIN * 2)[:3]
    y = 70 + 150
    for line in sub_lines:
        draw.text((MARGIN, y), line, font=f_sub, fill=NEAR_BLACK)
        y += 62

    photo_top = y + 30
    photo_h = SLIDE_H - photo_top
    collage = _build_collage(collage_paths, SLIDE_W, photo_h)
    if collage is None:
        collage = _build_color_mosaic(fallback_colors, SLIDE_W, photo_h)
    canvas.paste(collage, (0, photo_top))

    f_date = _font(ANTON, 38)
    dtw = draw.textlength(date_label, font=f_date)
    pad = 20
    draw.rounded_rectangle([MARGIN, SLIDE_H - 100, MARGIN + dtw + pad * 2, SLIDE_H - 32],
                           radius=8, fill=BADGE_COLOR)
    draw.text((MARGIN + pad, SLIDE_H - 90), date_label, font=f_date, fill=WHITE)

    f_foot = _font(ARCHIVO, 28)
    ftxt = f"{footer}  →  {handle}"
    fw = draw.textlength(ftxt, font=f_foot)
    draw.text((SLIDE_W - MARGIN - fw, SLIDE_H - 68), ftxt, font=f_foot, fill=WHITE)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
