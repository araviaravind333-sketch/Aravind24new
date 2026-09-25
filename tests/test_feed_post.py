"""
Feed post adaptive layout -- Tests
==================================
    python -m tests.test_feed_post
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from src import feed_post as fp

PASS, FAIL = [], []
HEAD = "HEAVY KERALA RAINS KILL FIVE PEOPLE AND TRIGGER WIDESPREAD LANDSLIDES"


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def layout_tests():
    print("\nLAYOUT")
    m = lambda a, h=HEAD: fp.layout_for(a, h)["mode"]
    check("square and 5:4 pictures fill the window", m(1.0) == "cover" and m(1.25) == "cover")
    check("a 4:5 portrait is shown whole (cropping it would cut heads)", m(0.8) == "contain")
    check("3:2 fills the window (moderate crop)", m(1.5) == "cover")
    check("a tall phone picture is shown whole", m(9 / 16) == "contain")
    check("an ultra-wide picture is shown whole", m(3.0) == "contain")
    for a in (0.56, 0.8, 1.5, 3.0):
        lay = fp.layout_for(a, HEAD)
        check(f"aspect {a}: picture window is between 520 and 900px",
              520 <= lay["win_h"] <= 900, lay["win_h"])
    ultra = fp.layout_for(3.0, HEAD)
    normal = fp.layout_for(1.5, HEAD)
    check("a short (ultra-wide) picture leaves more room for the headline",
          ultra["head_avail"] > normal["head_avail"] + 150, (ultra["head_avail"], normal["head_avail"]))


def render_tests(tmp):
    print("\nRENDER (plain, no button/pill/tagline/FILE PHOTO tag)")
    for name, size in (("tall", (600, 1000)), ("wide", (1600, 900)), ("sq", (1000, 1000)), ("ultra", (3000, 1000))):
        src = os.path.join(tmp, f"{name}.jpg")
        Image.new("RGB", size, (200, 40, 40)).save(src)
        out = os.path.join(tmp, f"post_{name}.jpg")
        fp.render_post(src, "INDIA NEWS", HEAD, "KERALA RAINS", out, footer="For the latest news",
                       handle="@aravindnews24", file_photo=True)
        im = Image.open(out)
        lay = fp.layout_for(size[0] / size[1], HEAD)
        px = im.getpixel((540, lay["win_h"] // 2))
        check(f"{name}: 1080x1350 post with the picture in the window",
              im.size == (1080, 1350) and px[0] > 120 and px[1] < 90, px)
        # the old FOLLOW button and category pill were solid, sharp-edged
        # rectangles filled with the exact category colour (245,135,31 for
        # INDIA NEWS) -- confirm neither is anywhere in the frame any more,
        # anti-aliased text edges aside (checked at a tolerance).
        # A small (130x10px) colour-coded accent bar above the headline is
        # intentional design, not a labelled button/pill -- only flag a
        # BIG block of the category colour (the button/pill were hundreds
        # of px wide and tall).
        cat_color = (245, 135, 31)
        matches = sum(1 for x in range(0, fp.W, 4) for y in range(0, fp.H, 4)
                     if all(abs(a - b) < 6 for a, b in zip(im.getpixel((x, y))[:3], cat_color)))
        check(f"{name}: no big category-colour block (button/pill gone, small accent bar ok)",
              matches * 16 < 4000, matches * 16)
        # top-right corner (where the old category pill sat) is just the
        # (darkened) picture / scrim now, not a boxed label
        top_right = im.crop((fp.W - fp.MARGIN - 200, 40, fp.W - fp.MARGIN, 100))
        edges = top_right.filter(__import__("PIL").ImageFilter.FIND_EDGES).convert("L")
        sharp_px = sum(1 for p in edges.getdata() if p > 200)
        check(f"{name}: no sharp rectangle edges top-right (no pill outline)",
              sharp_px < 40, sharp_px)

    src = os.path.join(tmp, "sq.jpg")
    a = fp.render_post(src, "WORLD NEWS", "SHORT ONE", "", os.path.join(tmp, "a.jpg"), file_photo=False)
    b = fp.render_post(src, "WORLD NEWS", "SHORT ONE", "", os.path.join(tmp, "b.jpg"), file_photo=True)
    check("file_photo=True renders identically to False (the tag is gone)",
          list(Image.open(a).getdata()) == list(Image.open(b).getdata()))

    long_head = ("GOVERNMENT ANNOUNCES A VERY LONG RELIEF PACKAGE FOR FLOOD AFFECTED FAMILIES ACROSS "
                 "SEVERAL DISTRICTS OF ASSAM AND BIHAR TODAY AS WATERS KEEP RISING")
    o = fp.render_post(src, "INDIA NEWS", long_head, "", os.path.join(tmp, "long.jpg"),
                       footer="For the latest news", handle="@aravindnews24")
    im = Image.open(o)
    fy = fp.H - fp.BOTTOM_PAD - fp.FOOT_H
    row_has_text = any(sum(im.getpixel((x, fy + 10))[:3]) > 300 for x in range(fp.MARGIN, fp.W - fp.MARGIN, 4))
    check("a very long headline still leaves the credit line legible", row_has_text)


if __name__ == "__main__":
    layout_tests()
    with tempfile.TemporaryDirectory() as t:
        render_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
