"""
AravindNews24 — Post Template Renderer
Produces a 1080x1350 (Instagram 4:5) high-quality news post using one of
3 professional layouts (rotated/selected in src/main.py based on category
and — once real data exists — which variant actually gets more reach):

  1. full_bleed    — classic breaking-news look: full photo, bottom gradient,
                      pill + headline overlaid at the bottom
  2. split_banner  — photo on top, solid brand panel with headline below —
                      cleaner separation, good for business/data stories
  3. framed_card   — photo inset in a colored frame, headline in a boxed
                      card near the bottom — more polished/editorial feel
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import os

# ---- paths ----
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(BASE, "assets", "fonts")
ANTON = os.path.join(FONTS, "Anton-Regular.ttf")
ARCHIVO = os.path.join(FONTS, "Archivo.ttf")

# ---- brand tokens ----
W, H = 1080, 1350
ACCENT = "#F5871F"        # orange accent (from your reference)
WHITE = "#FFFFFF"
NEAR_BLACK = "#0A0A0A"
MUTED = "#B8B8B8"

VARIANTS = ("full_bleed", "split_banner", "framed_card")

# category -> pill color
CATEGORY_COLORS = {
    "BREAKING NEWS": "#E01E1E",
    "INDIA NEWS":    "#F5871F",
    "WORLD NEWS":    "#1E6FE0",
    "SPORTS NEWS":   "#12A150",
    "BUSINESS NEWS": "#8B5CF6",
    "HOT TOPIC":     "#F5871F",
}


def _font(path, size):
    return ImageFont.truetype(path, size)


def _fit_cover(img, w, h):
    """Scale + center-crop image to exactly fill w x h (no distortion)."""
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = int(h * src_ratio)
    else:
        new_w = w
        new_h = int(w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _bottom_gradient(size, height):
    """Transparent -> solid black gradient for the lower portion."""
    w, h = size
    grad = Image.new("L", (1, h), 0)
    for y in range(h):
        if y < h - height:
            grad.putpixel((0, y), 0)
        else:
            t = (y - (h - height)) / height
            grad.putpixel((0, y), int(255 * (t ** 1.3)))
    grad = grad.resize((w, h))
    black = Image.new("RGBA", (w, h), (5, 5, 5, 255))
    black.putalpha(grad)
    return black


def _load_photo(photo_path, w, h):
    photo = Image.open(photo_path).convert("RGB")
    photo = _fit_cover(photo, w, h)
    photo = ImageEnhance.Contrast(photo).enhance(1.06)
    photo = ImageEnhance.Color(photo).enhance(1.08)
    return photo


def _draw_logo(canvas, draw, logo_path, x, y):
    if logo_path and os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((300, 120), Image.LANCZOS)
        canvas.alpha_composite(logo, (x, y))
    else:
        f_brand1 = _font(ANTON, 46)
        draw.text((x, y), "ARAVIND", font=f_brand1, fill=WHITE)
        bw = draw.textlength("ARAVIND", font=f_brand1)
        draw.text((x + bw + 12, y), "NEWS24", font=f_brand1, fill=ACCENT)
        draw.text((x + 3, y + 57), "LAST 24 HOURS  •  INDIA & WORLD",
                  font=_font(ARCHIVO, 20), fill=MUTED)


def _headline_lines(draw, headline, max_w, max_lines=4, start_size=118, min_size=58):
    size = start_size
    while size > min_size:
        f_head = _font(ANTON, size)
        lines = _wrap(draw, headline.upper(), f_head, max_w)
        if len(lines) <= max_lines:
            break
        size -= 4
    f_head = _font(ANTON, size)
    lines = _wrap(draw, headline.upper(), f_head, max_w)
    return lines, f_head, size


def _draw_headline_block(draw, lines, f_head, size, x, y, accent_up, color=WHITE):
    line_h = int(size * 0.98)
    for line in lines:
        cx = x
        if accent_up in line:
            before, _, after = line.partition(accent_up)
            if before:
                draw.text((cx, y), before, font=f_head, fill=color)
                cx += draw.textlength(before, font=f_head)
            draw.text((cx, y), accent_up, font=f_head, fill=ACCENT)
            cx += draw.textlength(accent_up, font=f_head)
            if after:
                draw.text((cx, y), after, font=f_head, fill=color)
        else:
            draw.text((cx, y), line, font=f_head, fill=color)
        y += line_h
    return y


# ============================================================
# Variant 1 — full_bleed (original layout)
# ============================================================
def _render_full_bleed(photo_path, category, headline, accent_word, out_path,
                        footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    canvas = _load_photo(photo_path, W, H).convert("RGBA")

    grad = _bottom_gradient((W, H), int(H * 0.62))
    canvas = Image.alpha_composite(canvas, grad)

    top_grad = Image.new("L", (1, H), 0)
    for y in range(H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (H * 0.22)))))
    top_grad = top_grad.resize((W, H))
    top_black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas = Image.alpha_composite(canvas, top_black)

    draw = ImageDraw.Draw(canvas)
    MARGIN = 70
    HEADLINE_BOTTOM = H - 150

    _draw_logo(canvas, draw, logo_path, MARGIN, 55)

    lines, f_head, size = _headline_lines(draw, headline, W - MARGIN * 2)
    line_h = int(size * 0.98)
    total_h = line_h * len(lines)
    headline_top = HEADLINE_BOTTOM - total_h

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

    f_foot = _font(ARCHIVO, 34)
    fy = H - 90
    draw.text((MARGIN, fy), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, fy), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ============================================================
# Variant 2 — split_banner (photo top, solid panel bottom)
# ============================================================
def _render_split_banner(photo_path, category, headline, accent_word, out_path,
                          footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    PHOTO_H = int(H * 0.58)
    MARGIN = 70

    photo = _load_photo(photo_path, W, PHOTO_H)
    canvas = Image.new("RGB", (W, H), NEAR_BLACK).convert("RGBA")
    canvas.paste(photo, (0, 0))

    # subtle top gradient over the photo for the logo
    top_grad = Image.new("L", (1, PHOTO_H), 0)
    for y in range(PHOTO_H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (PHOTO_H * 0.4)))))
    top_grad = top_grad.resize((W, PHOTO_H))
    top_black = Image.new("RGBA", (W, PHOTO_H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas.alpha_composite(top_black, (0, 0))

    # accent-colored divider line between photo and panel
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, PHOTO_H, W, PHOTO_H + 6], fill=pill_color)

    _draw_logo(canvas, draw, logo_path, MARGIN, 55)

    panel_top = PHOTO_H + 6
    panel_pad_top = 40
    cat_y = panel_top + panel_pad_top

    f_cat = _font(ARCHIVO, 28)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    pill_h = 54
    draw.rounded_rectangle([MARGIN, cat_y, MARGIN + tw + 52, cat_y + pill_h],
                            radius=8, fill=pill_color)
    draw.text((MARGIN + 26, cat_y + 11), cat_text, font=f_cat, fill=WHITE)

    headline_top = cat_y + pill_h + 24
    max_headline_h = H - 90 - headline_top
    lines, f_head, size = _headline_lines(
        draw, headline, W - MARGIN * 2, max_lines=4, start_size=88, min_size=44)
    line_h = int(size * 0.98)
    # shrink further if it would overflow into the footer
    while line_h * len(lines) > max_headline_h and size > 40:
        size -= 4
        f_head = _font(ANTON, size)
        lines = _wrap(draw, headline.upper(), f_head, W - MARGIN * 2)
        line_h = int(size * 0.98)

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 32)
    fy = H - 68
    draw.text((MARGIN, fy), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, fy), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


# ============================================================
# Variant 3 — framed_card (photo inset in a colored frame,
# headline in a boxed card)
# ============================================================
def _render_framed_card(photo_path, category, headline, accent_word, out_path,
                         footer, handle, logo_path):
    pill_color = CATEGORY_COLORS.get(category, ACCENT)
    FRAME = 22
    MARGIN = 70

    canvas = Image.new("RGB", (W, H), pill_color).convert("RGBA")
    photo = _load_photo(photo_path, W - FRAME * 2, H - FRAME * 2)
    canvas.paste(photo, (FRAME, FRAME))

    # dark gradient at the bottom of the inset photo for card legibility
    inset_w, inset_h = W - FRAME * 2, H - FRAME * 2
    grad = _bottom_gradient((inset_w, inset_h), int(inset_h * 0.5))
    canvas.alpha_composite(grad, (FRAME, FRAME))

    top_grad = Image.new("L", (1, inset_h), 0)
    for y in range(inset_h):
        top_grad.putpixel((0, y), int(140 * max(0, (1 - y / (inset_h * 0.2)))))
    top_grad = top_grad.resize((inset_w, inset_h))
    top_black = Image.new("RGBA", (inset_w, inset_h), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas.alpha_composite(top_black, (FRAME, FRAME))

    draw = ImageDraw.Draw(canvas)
    _draw_logo(canvas, draw, logo_path, MARGIN, 55)

    # category ribbon, top-left corner of the frame
    f_cat = _font(ARCHIVO, 26)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    draw.rectangle([0, 210, tw + 60, 210 + 50], fill=NEAR_BLACK)
    draw.text((24, 224), cat_text, font=f_cat, fill=pill_color)

    HEADLINE_BOTTOM = H - FRAME - 40
    lines, f_head, size = _headline_lines(
        draw, headline, W - MARGIN * 2, max_lines=4, start_size=100, min_size=52)
    line_h = int(size * 0.98)
    total_h = line_h * len(lines)
    headline_top = HEADLINE_BOTTOM - total_h - 60  # leave room for footer line

    _draw_headline_block(draw, lines, f_head, size, MARGIN, headline_top, accent_word.upper())

    f_foot = _font(ARCHIVO, 30)
    fy = HEADLINE_BOTTOM - 10
    draw.text((MARGIN, fy), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, fy), "→  " + handle, font=f_foot, fill=WHITE)

    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


_RENDERERS = {
    "full_bleed": _render_full_bleed,
    "split_banner": _render_split_banner,
    "framed_card": _render_framed_card,
}


def render_post(photo_path, category, headline, accent_word, out_path,
                 footer="For the latest news", handle="@aravindnews24",
                 logo_path=None, variant="full_bleed"):
    category = category.upper()
    fn = _RENDERERS.get(variant, _render_full_bleed)
    return fn(photo_path, category, headline, accent_word, out_path,
              footer, handle, logo_path)


if __name__ == "__main__":
    demo_photo = os.path.join(BASE, "assets", "demo.jpg")
    for v in VARIANTS:
        render_post(
            photo_path=demo_photo,
            category="INDIA NEWS",
            headline="RBI cuts repo rate to boost economy",
            accent_word="repo rate",
            out_path=os.path.join(BASE, f"demo_output_{v}.jpg"),
            variant=v,
        )
    print("rendered demo for all variants")
