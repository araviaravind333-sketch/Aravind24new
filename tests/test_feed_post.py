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
    print("\nRENDER")
    for name, size in (("tall", (600, 1000)), ("wide", (1600, 900)), ("sq", (1000, 1000)), ("ultra", (3000, 1000))):
        src = os.path.join(tmp, f"{name}.jpg")
        Image.new("RGB", size, (200, 40, 40)).save(src)
        out = os.path.join(tmp, f"post_{name}.jpg")
        fp.render_post(src, "INDIA NEWS", HEAD, "KERALA RAINS", out, footer="", handle="@aravindnews24")
        im = Image.open(out)
        lay = fp.layout_for(size[0] / size[1], HEAD)
        px = im.getpixel((540, lay["win_h"] // 2))
        check(f"{name}: 1080x1350 post with the picture in the window",
              im.size == (1080, 1350) and px[0] > 120 and px[1] < 90, px)
        # the FOLLOW button (category colour) sits at the bottom edge, below the headline
        btn = im.getpixel((100, fp.H - fp.BOTTOM_PAD - fp.CTA_H // 2))
        check(f"{name}: FOLLOW button is drawn in the category colour", btn[0] > 200 and btn[2] < 90, btn)
        # nothing bright (headline text) in the strip between the button and the bottom edge
        check(f"{name}: clean margin under the button",
              all(sum(im.getpixel((x, fp.H - 10))) < 120 for x in range(60, 1020, 60)))

    src = os.path.join(tmp, "sq.jpg")
    a = fp.render_post(src, "WORLD NEWS", "SHORT ONE", "", os.path.join(tmp, "a.jpg"), file_photo=False)
    b = fp.render_post(src, "WORLD NEWS", "SHORT ONE", "", os.path.join(tmp, "b.jpg"), file_photo=True)
    ia, ib = Image.open(a).convert("L"), Image.open(b).convert("L")
    wh = fp.layout_for(1.0, "SHORT ONE")["win_h"]
    strip = (fp.MARGIN, wh - 150, fp.MARGIN + 200, wh - 112)      # where the tag is drawn
    mean = lambda im: sum(im.crop(strip).getdata()) / max(1, (strip[2] - strip[0]) * (strip[3] - strip[1]))
    check("the FILE PHOTO tag is drawn only when requested", mean(ib) < mean(ia) - 5, (mean(ia), mean(ib)))

    long_head = ("GOVERNMENT ANNOUNCES A VERY LONG RELIEF PACKAGE FOR FLOOD AFFECTED FAMILIES ACROSS "
                 "SEVERAL DISTRICTS OF ASSAM AND BIHAR TODAY AS WATERS KEEP RISING")
    o = fp.render_post(src, "INDIA NEWS", long_head, "", os.path.join(tmp, "long.jpg"))
    im = Image.open(o)
    btn = im.getpixel((100, fp.H - fp.BOTTOM_PAD - fp.CTA_H // 2))
    check("and the button is still intact under it", btn[0] > 200 and btn[2] < 90, btn)


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
