"""
AravindNews24 — Post Template Renderer
Produces a 1080x1350 (Instagram 4:5) high-quality news post.
Matches reference style: full-bleed photo, dark gradient bottom, category pill,
heavy condensed headline with one accent word, brand logo, footer strip.
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


def render_post(
    photo_path,
    category,          # e.g. "INDIA NEWS"
    headline,          # full headline text
    accent_word,       # the word/phrase to color orange
    out_path,
    footer="For the latest news",
    handle="@aravindnews24",
    logo_path=None,
):
    category = category.upper()
    pill_color = CATEGORY_COLORS.get(category, ACCENT)

    # base photo
    photo = Image.open(photo_path).convert("RGB")
    photo = _fit_cover(photo, W, H)
    # slight punch so photos always look premium
    photo = ImageEnhance.Contrast(photo).enhance(1.06)
    photo = ImageEnhance.Color(photo).enhance(1.08)
    canvas = photo.convert("RGBA")

    # bottom gradient for text legibility
    grad = _bottom_gradient((W, H), int(H * 0.62))
    canvas = Image.alpha_composite(canvas, grad)

    # subtle top gradient for the logo
    top_grad = Image.new("L", (1, H), 0)
    for y in range(H):
        top_grad.putpixel((0, y), int(150 * max(0, (1 - y / (H * 0.22)))))
    top_grad = top_grad.resize((W, H))
    top_black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    top_black.putalpha(top_grad)
    canvas = Image.alpha_composite(canvas, top_black)

    draw = ImageDraw.Draw(canvas)
    MARGIN = 70
    FOOTER_TOP = H - 90          # footer strip baseline
    HEADLINE_BOTTOM = H - 150    # headline block ends here (above footer)

    # ---- brand logo (text lockup, top-left) ----
    if logo_path and os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((300, 120), Image.LANCZOS)
        canvas.alpha_composite(logo, (MARGIN, 55))
    else:
        f_brand1 = _font(ANTON, 46)
        draw.text((MARGIN, 55), "ARAVIND", font=f_brand1, fill=WHITE)
        bw = draw.textlength("ARAVIND", font=f_brand1)
        draw.text((MARGIN + bw + 12, 55), "NEWS24", font=f_brand1, fill=ACCENT)
        draw.text((MARGIN + 3, 112), "LAST 24 HOURS  •  INDIA & WORLD",
                  font=_font(ARCHIVO, 20), fill=MUTED)

    # ---- headline: auto-size to fit width across up to 4 lines ----
    size = 118
    while size > 58:
        f_head = _font(ANTON, size)
        lines = _wrap(draw, headline.upper(), f_head, W - MARGIN * 2)
        if len(lines) <= 4:
            break
        size -= 4
    f_head = _font(ANTON, size)
    lines = _wrap(draw, headline.upper(), f_head, W - MARGIN * 2)

    line_h = int(size * 0.98)
    total_h = line_h * len(lines)

    # headline block is bottom-anchored above the footer
    headline_top = HEADLINE_BOTTOM - total_h

    # ---- category pill: placed ABOVE the headline block (no overlap) ----
    f_cat = _font(ARCHIVO, 30)
    cat_text = category
    tw = draw.textlength(cat_text, font=f_cat)
    pill_h = 58
    pill_w = tw + 56
    pill_y = headline_top - pill_h - 26   # 26px gap above headline
    draw.rounded_rectangle(
        [MARGIN, pill_y, MARGIN + pill_w, pill_y + pill_h],
        radius=8, fill=pill_color,
    )
    draw.text((MARGIN + 28, pill_y + 12), cat_text, font=f_cat, fill=WHITE)

    # ---- draw headline lines ----
    y = headline_top
    accent_up = accent_word.upper()

    for line in lines:
        x = MARGIN
        # color the accent word wherever it appears in this line
        if accent_up in line:
            before, _, after = line.partition(accent_up)
            if before:
                draw.text((x, y), before, font=f_head, fill=WHITE)
                x += draw.textlength(before, font=f_head)
            draw.text((x, y), accent_up, font=f_head, fill=ACCENT)
            x += draw.textlength(accent_up, font=f_head)
            if after:
                draw.text((x, y), after, font=f_head, fill=WHITE)
        else:
            draw.text((x, y), line, font=f_head, fill=WHITE)
        y += line_h

    # ---- footer strip ----
    f_foot = _font(ARCHIVO, 34)
    fy = H - 90
    draw.text((MARGIN, fy), footer, font=f_foot, fill=MUTED)
    fw = draw.textlength(footer + "  ", font=f_foot)
    draw.text((MARGIN + fw, fy), "→  " + handle, font=f_foot, fill=WHITE)

    # ---- export high quality ----
    canvas.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path


if __name__ == "__main__":
    # demo render
    demo_photo = os.path.join(BASE, "assets", "demo.jpg")
    render_post(
        photo_path=demo_photo,
        category="INDIA NEWS",
        headline="RBI cuts repo rate to boost economy",
        accent_word="repo rate",
        out_path=os.path.join(BASE, "demo_output.jpg"),
    )
    print("rendered")
