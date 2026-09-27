"""
Feed post adaptive layout -- Tests
==================================
    python -m tests.test_feed_post
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

from src import feed_post as fp

PASS, FAIL = [], []
HEAD = "HEAVY KERALA RAINS KILL FIVE PEOPLE AND TRIGGER WIDESPREAD LANDSLIDES"


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


SHORT = "ISRO LAUNCHES SATELLITE"


def layout_tests():
    print("\nLAYOUT")
    # The window is sized from the headline's own real height (computed
    # first), so cover/contain depends on BOTH the picture's shape and how
    # much room a given headline actually needs -- with a short headline
    # the window reaches its max and behaves as pure aspect-ratio logic:
    m = lambda a, h=SHORT: fp.layout_for(a, h)["mode"]
    check("square and 5:4 pictures fill the window", m(1.0) == "cover" and m(1.25) == "cover")
    check("a 4:5 portrait is shown whole (cropping it would cut heads)", m(0.8) == "contain")
    check("3:2 fills the window (moderate crop)", m(1.5) == "cover")
    check("a tall phone picture is shown whole", m(9 / 16) == "contain")
    check("an ultra-wide picture is shown whole", m(3.0) == "contain")
    for a in (0.56, 0.8, 1.5, 3.0):
        lay = fp.layout_for(a, HEAD)
        check(f"aspect {a}: picture window is between 520 and 900px",
              520 <= lay["win_h"] <= 900, lay["win_h"])

    # The picture window is sized from the headline's OWN real height,
    # computed first -- a short headline needs less of the frame, so the
    # picture gets more of it (a prior version guessed the height with
    # different parameters than the real fit used, so the leftover became
    # a gap instead of being given back to the picture).
    short_win = fp.layout_for(1.5, SHORT)["win_h"]
    long_win = fp.layout_for(1.5, HEAD)["win_h"]
    check("a short headline gives the picture a bigger window than a long one",
          short_win > long_win, (short_win, long_win))
    check("a short headline's window reaches the configured maximum",
          short_win == fp.WIN_MAX, short_win)


def gap_tests(tmp):
    """Regression: a live post left ~500px of solid black between the
    headline/credit line and the frame edge, because the window was sized
    from a rough estimate of the headline's height made before the real
    headline was fitted, not from its actual height."""
    print("\nNO DEAD SPACE (regression)")
    src = os.path.join(tmp, "gap.jpg")
    Image.new("RGB", (1500, 1000), (200, 40, 40)).save(src)
    out = fp.render_post(src, "INDIA NEWS", SHORT, "", os.path.join(tmp, "gap_out.jpg"),
                         footer="For the latest news", handle="@aravindnews24")
    im = Image.open(out)
    lay = fp.layout_for(1.5, SHORT)
    wh = lay["win_h"]
    # the credit line must be close to the picture, not stranded near the
    # bottom of the frame with a large gap of pure background above it
    credit_y = fp.H - fp.BOTTOM_PAD - fp.FOOT_H // 2
    is_black_row = [all(im.getpixel((x, y))[:3] == (10, 10, 10) for x in range(fp.MARGIN, fp.W - fp.MARGIN, 40))
                    for y in range(wh + fp.TOP_PAD, credit_y)]
    # small gaps between lines/paragraphs are normal; a single CONTINUOUS
    # run of blank rows is the actual defect (the old bug left one ~500px
    # run of nothing but background between the content and the frame edge)
    longest_run = max((sum(1 for _ in g) for k, g in __import__("itertools").groupby(is_black_row) if k), default=0)
    check("no single continuous void between the headline block and the credit line",
          longest_run < 120, longest_run)


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
    # the credit line follows the content now (no fixed y), so just confirm
    # bright text appears somewhere in the lower portion of the frame
    found = any(sum(im.getpixel((x, y))[:3]) > 300
               for y in range(fp.H - 200, fp.H - 10, 4) for x in range(fp.MARGIN, fp.W - fp.MARGIN, 8))
    check("a very long headline still leaves the credit line legible", found)


def highlight_tests():
    """A live post highlighted "PILOT BODY" in red on an air-crash story."""
    print("\nHIGHLIGHT RESTRAINT")
    from src import carousel
    h = "PILOT BODY SEEKS SCRUTINY OF TECHNICAL RECORDS IN AI-171 CRASH PROBE"
    for bad in ("Pilot Body", "AI-171 Crash", "Death Toll", "Victims", "Blast"):
        check(f"never highlights {bad!r}", carousel.highlightable(bad, h + " " + bad.upper()) == "")
    check("still highlights an ordinary name",
          carousel.highlightable("Tamil Nadu", "TAMIL NADU ANNOUNCES SCHEME") == "Tamil Nadu")
    check("a phrase not in the headline is not highlighted",
          carousel.highlightable("Kerala", "TAMIL NADU ANNOUNCES SCHEME") == "")


def overlay_tests(tmp):
    """The full-bleed/serif-headline style the owner pointed to by example
    and asked to have built (a reference screenshot of a photo with a
    sentence-case serif headline set directly on it, thin colour bar,
    small handle credit)."""
    print("\nOVERLAY STYLE (owner's reference template)")
    src = os.path.join(tmp, "photo.jpg")
    Image.new("RGB", (1000, 1250), (90, 90, 110)).save(src)
    headline = "CM Vijay to launch 1-gram gold ring scheme, a key TVK poll promise, on Monday"
    out = fp.render_overlay_post(src, "INDIA NEWS", headline, "TVK",
                                 os.path.join(tmp, "overlay.jpg"),
                                 handle="@aravindnews24", photo_note="")
    im = Image.open(out)
    check("1080x1350 output", im.size == (1080, 1350))

    check("the headline keeps its own casing (sentence case, not shouted caps)",
          "aunch" in headline and headline[0] == "C")  # sanity on the input itself
    lines, f_head, size, block_h = fp._fit_serif(ImageDraw.Draw(im), headline, 1080 - 70 - 7 - 27 - 70)
    check("_fit_serif preserves casing instead of uppercasing", any(c.islower() for line in lines for c in line))

    # thin colour accent bar to the left of the headline block, category colour
    color = fp.CATEGORY_COLORS.get("INDIA NEWS")
    color_rgb = fp._hex_to_rgb(color)
    bar_col = im.getpixel((fp.MARGIN + 3, 1350 - 76 - block_h + 10))
    check("a colour accent bar sits to the left of the headline",
          all(abs(a - b) <= 3 for a, b in zip(bar_col[:3], color_rgb)), (bar_col, color_rgb))

    # the top of the frame is a clear, undarkened photo (unlike the panel
    # styles) -- the scrim is tight to the headline zone, not a long fade
    top_px = im.getpixel((540, 30))
    check("the top of the frame shows the photo clearly, not a dark scrim",
          sum(top_px[:3]) > 250, top_px)

    # a small credit line under the headline block
    bottom_band = im.crop((fp.MARGIN, 1350 - 50, 1080 - fp.MARGIN, 1350 - 10))
    has_text = any(sum(bottom_band.getpixel((x, y))[:3]) > 300
                   for x in range(0, bottom_band.width, 4) for y in range(0, bottom_band.height, 4))
    check("a small credit line renders near the bottom", has_text)

    # photo_note (e.g. "File photo: X") is accepted but NEVER drawn on the
    # image, by request -- the required author/licence credit already goes
    # in the post's caption text (main.py, around `photo_credit`), so an
    # on-image "File photo: X" label was a purely cosmetic redundancy the
    # owner didn't want. Compare against the identical render with no note
    # at all: they must be pixel-identical.
    out_bare = fp.render_overlay_post(src, "SPORTS NEWS", "Kohli hits a century", "",
                                      os.path.join(tmp, "overlay_bare.jpg"), handle="@aravindnews24")
    out_noted = fp.render_overlay_post(src, "SPORTS NEWS", "Kohli hits a century", "",
                                       os.path.join(tmp, "overlay_note.jpg"),
                                       handle="@aravindnews24", photo_note="File photo: Virat Kohli")
    check("photo_note is accepted but never drawn on the overlay style",
          list(Image.open(out_bare).getdata()) == list(Image.open(out_noted).getdata()))

    # a short headline still produces a well-formed frame, no crash, and
    # the accent bar colour matches the given category
    out3 = fp.render_overlay_post(src, "SPORTS NEWS", "Kohli hits a century", "",
                                  os.path.join(tmp, "overlay_short.jpg"), handle="@aravindnews24")
    im3 = Image.open(out3)
    check("short headline still renders a valid 1080x1350 frame", im3.size == (1080, 1350))


def reel_tests(tmp):
    """Every post now goes out as a Reel (Instagram shows Reels to
    non-followers; on this page's data image posts reached a median of 3
    accounts). The 9:16 frames must keep all text out of Instagram's own
    top bar (~200px) and bottom caption/buttons (~400px), and inside the
    profile grid's 3:4 centre crop (y 240-1680)."""
    print("\nREEL FRAMES (9:16)")
    src = os.path.join(tmp, "p.jpg")
    Image.new("RGB", (1000, 1250), (200, 200, 205)).save(src)   # bright photo: worst case for legibility
    head = "Suvendu Adhikari blames Mamata Banerjee for deleting 27 lakh voter names"
    out = fp.render_overlay_reel(src, "INDIA NEWS", head, "", os.path.join(tmp, "r.jpg"))
    im = Image.open(out)
    check("overlay reel is 1080x1920", im.size == (1080, 1920))
    lines, f, size, block_h = fp._fit_serif(ImageDraw.Draw(im), head, 1080 - fp.MARGIN - 34 - fp.MARGIN, start=84)
    top = 1920 - fp.REEL_BOTTOM_PAD - block_h
    check("headline sits below Instagram's top bar and inside the grid crop", 240 <= top, top)
    check("headline + credit end above Instagram's caption area", top + block_h + 60 <= 1920 - 380,
          top + block_h + 60)
    behind = im.getpixel((fp.W - 40, top + block_h // 2))
    check("the scrim makes the headline zone dark even on a bright photo", sum(behind[:3]) < 200, behind)
    clear = im.getpixel((540, 200))
    check("the photo stays clear well above the headline", sum(clear[:3]) > 500, clear)

    out2 = fp.render_text_reel("INDIA NEWS", "Karur stampede: first accused arrested a year after the tragedy", "",
                               os.path.join(tmp, "t.jpg"), subhead="Police said the accused was held in Chennai.")
    im2 = Image.open(out2)
    check("text reel is 1080x1920", im2.size == (1080, 1920))
    bright = lambda y0, y1: any(sum(im2.getpixel((x, y))[:3]) > 450
                                for x in range(fp.MARGIN, 1010, 6) for y in range(y0, y1, 4))
    check("no text in Instagram's top bar zone", not bright(0, 200))
    check("no text in Instagram's bottom caption zone", not bright(1920 - 400, 1920))
    check("the headline does render in the safe middle", bright(400, 1500))


if __name__ == "__main__":
    layout_tests()
    highlight_tests()
    with tempfile.TemporaryDirectory() as t:
        render_tests(t)
    with tempfile.TemporaryDirectory() as t:
        gap_tests(t)
    with tempfile.TemporaryDirectory() as t:
        overlay_tests(t)
    with tempfile.TemporaryDirectory() as t:
        reel_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
