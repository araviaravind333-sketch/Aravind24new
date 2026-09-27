"""
Owner Reel "window" layout -- Tests
===================================
    python -m tests.test_clip_reel
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from src import clip_reel as cr

PASS, FAIL, SKIP = [], [], []
HEAD = "KERALA SHOOTER WINS ASIAN GAMES SILVER AFTER PARENTS TAKE LOAN"
PANEL_RGB = (10, 10, 10)


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


SHORT = "ISRO LAUNCHES SATELLITE"


def layout_tests(tmp):
    print("\nLAYOUT")
    # The window is sized from the headline's own real height (computed
    # first), so cover/contain depends on both the picture's shape and how
    # much room the headline needs -- with a short headline the window
    # reaches its natural maximum and behaves as pure aspect-ratio logic:
    m = lambda a: cr.layout_for(a, SHORT)["mode"]
    check("square footage fills the window", m(1.0) == "cover")
    check("3:2 photo fills the window (moderate crop)", m(1.5) == "cover")
    check("tall phone video is shown whole", m(9 / 16) == "contain")
    check("16:9 is shown whole", m(16 / 9) == "contain")

    for name, a in (("3:2", 1.5), ("16:9", 16 / 9), ("9:16", 9 / 16), ("square", 1.0)):
        lay = cr.layout_for(a, HEAD)
        check(f"{name}: window + panel exactly tile the frame down to the bottom edge",
              lay["panel_y"] == lay["win_y"] + lay["win_h"] and lay["panel_h"] == cr.H - lay["panel_y"])
        check(f"{name}: picture starts below Instagram's top controls and ends above its bottom controls",
              lay["win_y"] >= cr.ZONE_TOP and lay["panel_y"] < 1300, (lay["win_y"], lay["panel_y"]))

    # Regression: a live post ("PROTESTS ERUPT AT PUNJAB UNIVERSITY...")
    # left ~500px of solid black panel below the footer, because the
    # window was sized from a rough estimate of the headline's height made
    # BEFORE the real headline was fitted (different max_lines/start_size
    # than the real fit used), so the real headline needed less room and
    # the leftover became a void instead of being given back to the picture.
    short_win = cr.layout_for(1.5, SHORT)["win_h"]
    long_win = cr.layout_for(1.5, HEAD)["win_h"]
    check("a short headline gives the picture a bigger window than a long one",
          short_win > long_win, (short_win, long_win))

    lay = cr.layout_for(1.5, HEAD)
    f = Image.open(cr.render_frame_png("INDIA NEWS", HEAD, "SILVER", os.path.join(tmp, "f.png"),
                                       footer="For the latest news → @aravindnews24",
                                       handle="@aravindnews24", layout=lay))
    mid = lay["win_y"] + lay["win_h"] // 2
    check("frame is exactly 1080x1920 RGBA", f.size == (1080, 1920) and f.mode == "RGBA")
    check("the picture window is transparent (nothing drawn over the footage)",
          all(f.getpixel((x, y))[3] == 0 for x in (100, 540, 1000) for y in (mid - 100, mid, mid + 200)))
    check("solid dark band above the picture", all(f.getpixel((x, y)) == PANEL_RGB + (255,)
          for x in (100, 540, 1000) for y in (10, 120, lay["win_y"] - 4)))
    check("the headline panel is solid", f.getpixel((20, lay["panel_y"] + 150))[3] >= 240)
    check("the panel runs to the bottom edge (no empty band)", f.getpixel((540, 1915))[3] >= 240)
    check("nothing is written in Instagram's bottom 300px", all(f.getpixel((x, y))[:3] == PANEL_RGB
          for x in range(60, 1020, 30) for y in (1640, 1720, 1800, 1900)))

    rows = [y for y in range(lay["panel_y"] + 20, 1600)
            if any(f.getpixel((x, y))[:3] == (255, 255, 255) and f.getpixel((x, y))[3] > 200 for x in range(70, 1000, 4))]
    check("headline and brand line both render inside the content zone", rows and rows[-1] < cr.ZONE_BOTTOM, rows[-3:] if rows else None)

    # Regression: the real "PROTESTS ERUPT..." post had ~500px of solid
    # black panel between the credit line and ZONE_BOTTOM. A modest margin
    # (the safe-zone reserve) is expected; one continuous void is not.
    is_white_text_row = lambda y: any(f.getpixel((x, y))[:3] == (255, 255, 255) and f.getpixel((x, y))[3] > 200
                                      for x in range(70, 1000, 4))
    last_text_row = next(y for y in range(cr.ZONE_BOTTOM, lay["panel_y"], -1) if is_white_text_row(y))
    void_rows = [y for y in range(last_text_row + 5, cr.ZONE_BOTTOM)
                if all(f.getpixel((x, y))[:3] == PANEL_RGB for x in range(60, 1020, 40))]
    check("no large continuous void between the credit line and the safe-zone boundary",
          len(void_rows) < 150, len(void_rows))

    long_head = ("GOVERNMENT ANNOUNCES A VERY LONG RELIEF PACKAGE FOR FLOOD AFFECTED FAMILIES ACROSS "
                 "SEVERAL DISTRICTS OF ASSAM AND BIHAR TODAY")
    for a in (1.5, 16 / 9, 9 / 16):
        lay2 = cr.layout_for(a, long_head)
        g = Image.open(cr.render_frame_png("INDIA NEWS", long_head, "", os.path.join(tmp, "g.png"), layout=lay2))
        check(f"very long headline stays out of the bottom 350px ({a:.2f})",
              all(g.getpixel((x, y))[:3] == PANEL_RGB for x in range(60, 1020, 30) for y in (1600, 1700, 1800)))


def photo_tests(tmp):
    print("\nPHOTO CARD")
    for name, size in (("tall", (600, 900)), ("wide", (1600, 900)), ("sq", (1000, 1000)), ("land", (1500, 1000))):
        src = os.path.join(tmp, f"{name}.jpg")
        Image.new("RGB", size, (200, 40, 40)).save(src)
        out = cr.render_photo_card(src, "WORLD NEWS", "SHORT HEADLINE", "", os.path.join(tmp, f"card_{name}.jpg"))
        lay = cr.layout_for(size[0] / size[1], "SHORT HEADLINE")
        im = Image.open(out)
        px = im.getpixel((540, lay["win_y"] + lay["win_h"] // 2))
        check(f"{name} photo -> 1080x1920 card with the photo in the window",
              im.size == (1080, 1920) and px[0] > 120 and px[1] < 90, px)
    check("no temp frame file left behind", not [f for f in os.listdir(tmp) if f.endswith("_frame.png")])


def filter_tests():
    print("\nFFMPEG FILTER")
    cover = cr.build_filter(cr.layout_for(1.5, HEAD))
    contain = cr.build_filter(cr.layout_for(9 / 16, HEAD))
    check("filling footage uses a cover crop", "crop=1080:" in cover and "decrease" not in cover)
    check("tall footage is scaled DOWN to fit (never stretched or cropped)",
          "force_original_aspect_ratio=decrease" in contain)
    check("the frame PNG is composited last", cover.rstrip().endswith("[v1][1:v]overlay=0:0[outv]"))
    check("the window is placed at the layout's own y",
          f"overlay=0:{cr.layout_for(1.5, HEAD)['win_y']}" in cover)


def render_tests(tmp):
    print("\nVIDEO RENDER")
    ff = cr._ffmpeg()
    ok = subprocess.run([ff, "-version"], capture_output=True).returncode == 0 if ff else False
    if not ok:
        SKIP.append("video render")
        print("  SKIP  ffmpeg not available here")
        return
    for name, size in (("tall", "360x640"), ("wide", "640x360"), ("land", "600x400")):
        clip = os.path.join(tmp, f"{name}.mp4")
        subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=1.5",
                        "-pix_fmt", "yuv420p", clip], capture_output=True)
        a = cr.probe_aspect(clip)
        lay = cr.layout_for(a, HEAD)
        frame = cr.render_frame_png("INDIA NEWS", HEAD, "", os.path.join(tmp, f"fr_{name}.png"), layout=lay)
        out = os.path.join(tmp, f"reel_{name}.mp4")
        cr.render_reel_from_clip(clip, frame, out, 1.5, layout=lay)
        log = subprocess.run([ff, "-i", out], capture_output=True, text=True).stderr
        w, h = size.split("x")
        check(f"{name} clip -> 1080x1920 h264 reel, muted",
              "1080x1920" in log and "Video: h264" in log and "Audio:" not in log)
        check(f"{name} clip's aspect is read correctly", abs(a - int(w) / int(h)) < 0.01, a)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as t:
        layout_tests(t)
    with tempfile.TemporaryDirectory() as t:
        photo_tests(t)
    filter_tests()
    with tempfile.TemporaryDirectory() as t:
        render_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
