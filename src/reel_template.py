"""
Reel Card Templates — native 9:16 (1080x1920)
==============================================
Separate from the 4:5 feed card (src/template.py), which is still used
UNCHANGED for the Facebook photo post (Facebook always gets a static
photo, even for a story published as an Instagram Reel) and for any
non-reel Instagram post. These templates exist because a Reel deserves
real full-screen composition instead of a 4:5 card just squeezed to fit,
and because Instagram's own UI permanently covers part of every Reel —
roughly the top ~150px (profile row) and bottom ~300px (caption, audio
info, like/comment/share/save icons) — so text here is placed to clear
both zones, unlike the feed card's tighter margins.

  1. reel_full_bleed   — full photo, bottom gradient, headline block
  2. reel_split_banner — photo on top ~60%, solid brand panel + headline
  3. reel_framed_card  — photo inset in a colored frame, boxed headline

Also exposes render_overlay_png(): the transparent-background version of
reel_full_bleed's text elements, composited via ffmpeg on top of a real
submitted video clip (src/video.py's render_reel_from_clip) instead of a
held photo.
"""

from PIL import Image, ImageDraw

from src.template import (
    ANTON, ARCHIVO, ACCENT, WHITE, NEAR_BLACK, MUTED, CATEGORY_COLORS,
    _font, _wrap, _bottom_gradient, _load_photo, _draw_logo,
    _headline_lines, _hex_to_rgb, _mix, _vertical_gradient, _draw_headline_block,
)

W, H = 1080, 1920
# Reels' own UI permanently covers roughly this much of every video --
# keep text clear of both bands.
SAFE_TOP = 160
SAFE_BOTTOM = 300

VARIANTS = ("reel_full_bleed", "reel_split_banner", "reel_framed_card")


# ============================================================
# Variant 1 — reel_full_bleed
# ============================================================
def _render_full_bleed(photo_path, category, headline, accent_word, out_path,
                        footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    canvas = _load_photo(photo_path, W, H).convert("RGBA")

    grad = _bottom_gradient((W, H), int(H * 0.5))
    canvas = Image.alpha_composite(canvas, grad)

    top_grad = Image.new("L", (1, H), 0)
    for y in range(H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (H * 0.14)))))
    top_grad = top_grad.resize((W, H))
    top_black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas = Image.alpha_composite(canvas, top_black)

    draw = ImageDraw.Draw(canvas)
    MARGIN = 70
    footer_y = H - SAFE_BOTTOM
    headline_bottom = footer_y - 60

    _draw_logo(canvas, draw, logo_path, MARGIN, SAFE_TOP - 105)

    lines, f_head, size = _headline_lines(draw, headline, W - MARGIN * 2,
                                           max_lines=5, start_size=100, min_size=52)
    line_h = int(size * 0.98)
    total_h = line_h * len(lines)
    headline_top = headline_bottom - total_h

    f_cat = _font(ARCHIVO, 30)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    pill_h = 58
    pill_w = tw + 56
    pill_y = headline_top - pill_h - 26
    draw.rounded_rectangle([MARGIN, pill_y, MARGIN + pill_w, pill_y + pill_h],
                            radius=8, fill=pill_color)
    draw.text((MARGIN + 28, pill_y + 12), cat_text, font=f_cat, fill=WHITE)

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 32)
    draw.text((MARGIN, footer_y), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, footer_y), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ============================================================
# Variant 2 — reel_split_banner
# ============================================================
def _render_split_banner(photo_path, category, headline, accent_word, out_path,
                          footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    PHOTO_H = int(H * 0.6)
    MARGIN = 70

    photo = _load_photo(photo_path, W, PHOTO_H)
    canvas = Image.new("RGB", (W, H), NEAR_BLACK).convert("RGBA")
    canvas.paste(photo, (0, 0))

    top_grad = Image.new("L", (1, PHOTO_H), 0)
    for y in range(PHOTO_H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (PHOTO_H * 0.3)))))
    top_grad = top_grad.resize((W, PHOTO_H))
    top_black = Image.new("RGBA", (W, PHOTO_H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas.alpha_composite(top_black, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, PHOTO_H, W, PHOTO_H + 6], fill=pill_color)

    _draw_logo(canvas, draw, logo_path, MARGIN, SAFE_TOP - 105)

    panel_top = PHOTO_H + 6
    cat_y = panel_top + 50

    f_cat = _font(ARCHIVO, 28)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    pill_h = 54
    draw.rounded_rectangle([MARGIN, cat_y, MARGIN + tw + 52, cat_y + pill_h],
                            radius=8, fill=pill_color)
    draw.text((MARGIN + 26, cat_y + 11), cat_text, font=f_cat, fill=WHITE)

    headline_top = cat_y + pill_h + 30
    footer_y = H - SAFE_BOTTOM
    max_headline_h = footer_y - 40 - headline_top
    lines, f_head, size = _headline_lines(
        draw, headline, W - MARGIN * 2, max_lines=5, start_size=88, min_size=44)
    line_h = int(size * 0.98)
    while line_h * len(lines) > max_headline_h and size > 40:
        size -= 4
        f_head = _font(ANTON, size)
        lines = _wrap(draw, headline.upper(), f_head, W - MARGIN * 2)
        line_h = int(size * 0.98)

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 32)
    draw.text((MARGIN, footer_y), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, footer_y), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ============================================================
# Variant 3 — reel_framed_card
# ============================================================
def _render_framed_card(photo_path, category, headline, accent_word, out_path,
                         footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    FRAME = 22
    MARGIN = 70

    canvas = Image.new("RGB", (W, H), pill_color).convert("RGBA")
    photo = _load_photo(photo_path, W - FRAME * 2, H - FRAME * 2)
    canvas.paste(photo, (FRAME, FRAME))

    inset_w, inset_h = W - FRAME * 2, H - FRAME * 2
    grad = _bottom_gradient((inset_w, inset_h), int(inset_h * 0.4))
    canvas.alpha_composite(grad, (FRAME, FRAME))

    top_grad = Image.new("L", (1, inset_h), 0)
    for y in range(inset_h):
        top_grad.putpixel((0, y), int(140 * max(0, (1 - y / (inset_h * 0.14)))))
    top_grad = top_grad.resize((inset_w, inset_h))
    top_black = Image.new("RGBA", (inset_w, inset_h), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas.alpha_composite(top_black, (FRAME, FRAME))

    draw = ImageDraw.Draw(canvas)
    _draw_logo(canvas, draw, logo_path, MARGIN, SAFE_TOP - 105)

    f_cat = _font(ARCHIVO, 26)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    draw.rectangle([0, SAFE_TOP + 50, tw + 60, SAFE_TOP + 50 + 50], fill=NEAR_BLACK)
    draw.text((24, SAFE_TOP + 64), cat_text, font=f_cat, fill=pill_color)

    footer_y = H - SAFE_BOTTOM
    headline_bottom = footer_y - 60
    lines, f_head, size = _headline_lines(
        draw, headline, W - MARGIN * 2, max_lines=5, start_size=96, min_size=48)
    line_h = int(size * 0.98)
    total_h = line_h * len(lines)
    headline_top = headline_bottom - total_h

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 30)
    draw.text((MARGIN, footer_y), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, footer_y), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ============================================================
# Transparent overlay — composited via ffmpeg on top of a real submitted
# video clip (src/video.py's render_reel_from_clip). Reuses reel_full_bleed's
# layout since a bottom-anchored gradient + text block is the one style
# that stays legible sitting on top of arbitrary moving video.
# ============================================================
def render_overlay_png(category, headline, accent_word, out_path,
                        footer="For the latest news", handle="@aravindnews24",
                        logo_path=None):
    category = category.upper()
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    grad = _bottom_gradient((W, H), int(H * 0.5))
    canvas = Image.alpha_composite(canvas, grad)

    top_grad = Image.new("L", (1, H), 0)
    for y in range(H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (H * 0.14)))))
    top_grad = top_grad.resize((W, H))
    top_black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas = Image.alpha_composite(canvas, top_black)

    draw = ImageDraw.Draw(canvas)
    MARGIN = 70
    footer_y = H - SAFE_BOTTOM
    headline_bottom = footer_y - 60

    _draw_logo(canvas, draw, logo_path, MARGIN, SAFE_TOP - 105)

    lines, f_head, size = _headline_lines(draw, headline, W - MARGIN * 2,
                                           max_lines=5, start_size=100, min_size=52)
    line_h = int(size * 0.98)
    total_h = line_h * len(lines)
    headline_top = headline_bottom - total_h

    f_cat = _font(ARCHIVO, 30)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    pill_h = 58
    pill_w = tw + 56
    pill_y = headline_top - pill_h - 26
    draw.rounded_rectangle([MARGIN, pill_y, MARGIN + pill_w, pill_y + pill_h],
                            radius=8, fill=pill_color)
    draw.text((MARGIN + 28, pill_y + 12), cat_text, font=f_cat, fill=WHITE)

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 32)
    draw.text((MARGIN, footer_y), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, footer_y), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.save(out_path, "PNG")
    return out_path


_RENDERERS = {
    "reel_full_bleed": _render_full_bleed,
    "reel_split_banner": _render_split_banner,
    "reel_framed_card": _render_framed_card,
}


def render_reel_card(photo_path, category, headline, accent_word, out_path,
                      footer="For the latest news", handle="@aravindnews24",
                      logo_path=None, variant="reel_full_bleed"):
    category = category.upper()
    fn = _RENDERERS.get(variant, _render_full_bleed)
    return fn(photo_path, category, headline, accent_word, out_path,
              footer, handle, logo_path)


if __name__ == "__main__":
    import os
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo_photo = os.path.join(BASE, "assets", "demo.jpg")
    for v in VARIANTS:
        render_reel_card(
            photo_path=demo_photo,
            category="INDIA NEWS",
            headline="RBI cuts repo rate to boost economy",
            accent_word="repo rate",
            out_path=os.path.join(BASE, f"demo_reel_{v}.jpg"),
            variant=v,
        )
    print("rendered reel demo for all variants")
