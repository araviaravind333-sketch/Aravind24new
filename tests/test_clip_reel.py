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


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def layout_tests(tmp):
    print("\nLAYOUT")
    check("square and near-square footage fills the window", cr.fit_mode(1.0) == "cover" and cr.fit_mode(1.33) == "cover")
    check("tall phone video is shown whole", cr.fit_mode(9 / 16) == "contain")
    check("16:9 is shown whole", cr.fit_mode(16 / 9) == "contain")
    check("4:5 is shown whole (not cropped)", cr.fit_mode(0.8) == "contain")

    png = cr.render_frame_png("INDIA NEWS", "KERALA SHOOTER WINS ASIAN GAMES SILVER AFTER PARENTS TAKE LOAN",
                              "SILVER", os.path.join(tmp, "f.png"),
                              footer="For the latest news → @aravindnews24", handle="@aravindnews24")
    f = Image.open(png)
    check("frame is exactly 1080x1920 RGBA", f.size == (1080, 1920) and f.mode == "RGBA")
    check("the footage window is transparent (nothing drawn over the video)",
          all(f.getpixel((x, y))[3] == 0 for x in (100, 540, 1000) for y in (cr.WIN_Y + 300, cr.WIN_Y + 500, cr.WIN_Y + 800)))
    check("the top 230px is empty (Instagram's own controls)", all(f.getpixel((x, y))[3] == 0
          for x in (100, 540, 1000) for y in (10, 120, 220)))
    check("the headline panel is opaque", f.getpixel((20, cr.PANEL_Y + 150))[3] > 200)
    check("the bottom 300px is empty (Instagram's caption/buttons)", all(f.getpixel((x, y))[3] == 0
          for x in (100, 540, 1000) for y in (1650, 1750, 1900)))

    # brand line must sit BELOW the headline: find the lowest bright headline row and the footer row
    rows = [y for y in range(cr.PANEL_Y + 20, cr.PANEL_Y + cr.PANEL_H)
            if any(f.getpixel((x, y))[:3] == (255, 255, 255) and f.getpixel((x, y))[3] > 200 for x in range(70, 1000, 4))]
    check("headline and brand line both render inside the panel", rows and rows[-1] < cr.PANEL_Y + cr.PANEL_H - 40, rows[-3:] if rows else None)

    long_head = "GOVERNMENT ANNOUNCES A VERY LONG RELIEF PACKAGE FOR FLOOD AFFECTED FAMILIES ACROSS SEVERAL DISTRICTS OF ASSAM AND BIHAR TODAY"
    g = Image.open(cr.render_frame_png("INDIA NEWS", long_head, "", os.path.join(tmp, "g.png")))
    check("a very long headline still stays inside the panel",
          all(g.getpixel((x, y))[3] == 0 for x in (100, 900) for y in (1650, 1800)))


def photo_tests(tmp):
    print("\nPHOTO CARD")
    for name, size in (("tall", (600, 900)), ("wide", (1600, 900)), ("sq", (1000, 1000))):
        src = os.path.join(tmp, f"{name}.jpg")
        Image.new("RGB", size, (200, 40, 40)).save(src)
        out = cr.render_photo_card(src, "WORLD NEWS", "SHORT HEADLINE", "", os.path.join(tmp, f"card_{name}.jpg"))
        im = Image.open(out)
        px = im.getpixel((540, cr.WIN_Y + 470))
        check(f"{name} photo -> 1080x1920 card with the photo in the window",
              im.size == (1080, 1920) and px[0] > 120 and px[1] < 90, px)
    check("no temp frame file left behind", not [f for f in os.listdir(tmp) if f.endswith("_frame.png")])


def filter_tests():
    print("\nFFMPEG FILTER")
    cover = cr.build_filter(1.0)
    contain = cr.build_filter(9 / 16)
    unknown = cr.build_filter(None)
    check("square footage uses a cover crop", "crop=1080:940" in cover and "decrease" not in cover)
    check("tall footage is scaled DOWN to fit (never stretched or cropped)", "force_original_aspect_ratio=decrease" in contain)
    check("unknown shape falls back to the safe whole-frame fit", "decrease" in unknown)
    check("the frame PNG is composited last", cover.rstrip().endswith("[v1][1:v]overlay=0:0[outv]"))


def render_tests(tmp):
    print("\nVIDEO RENDER")
    ff = cr._ffmpeg()
    ok = subprocess.run([ff, "-version"], capture_output=True).returncode == 0 if ff else False
    if not ok:
        SKIP.append("video render")
        print("  SKIP  ffmpeg not available here")
        return
    frame = cr.render_frame_png("INDIA NEWS", "TEST HEADLINE FOR THE REEL", "", os.path.join(tmp, "fr.png"))
    for name, size in (("tall", "360x640"), ("wide", "640x360")):
        clip = os.path.join(tmp, f"{name}.mp4")
        subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=1.5",
                        "-pix_fmt", "yuv420p", clip], capture_output=True)
        out = os.path.join(tmp, f"reel_{name}.mp4")
        cr.render_reel_from_clip(clip, frame, out, 1.5)
        log = subprocess.run([ff, "-i", out], capture_output=True, text=True).stderr
        check(f"{name} clip -> 1080x1920 h264 reel, muted",
              "1080x1920" in log and "Video: h264" in log and "Audio:" not in log)
        check(f"{name} clip's aspect is read correctly",
              abs(cr.probe_aspect(clip) - (360 / 640 if name == "tall" else 640 / 360)) < 0.01)


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
