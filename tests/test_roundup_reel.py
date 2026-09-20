"""
Daily Roundup Reel -- Tests
===========================
Offline. The real voice (a 110 MB download) is replaced by a stand-in that
writes silence proportional to the word count, so what is tested is
everything AROUND the voice: text cleaning, timing, frame layout, the
ffmpeg assembly, and the publish / retry / give-up state machine.

    python -m tests.test_roundup_reel
"""

import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import roundup_reel as rr

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def fake_synth(text, wav_path, rate=22050):
    secs = max(0.8, len(text.split()) * 0.32)
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * secs))


def text_tests():
    print("\nSPOKEN TEXT")
    s = rr.speakable
    check("rupee symbol becomes words", s("Firm proposes ₹25,000 crore stake sale") ==
          "Firm proposes 25,000 crore rupees stake sale", s("Firm proposes ₹25,000 crore stake sale"))
    check("'Rs 1 crore' becomes '1 crore rupees'", "1 crore rupees" in s("CM announces Rs 1 crore aid"))
    check("percent sign is spoken", "5 percent" in s("GDP up 5% this year"))
    check("'vs' is read as 'versus'", "versus" in s("India vs Australia final"))
    check("a broken ending is never read aloud",
          not s("Saudi Arabia confirms Yemen's Houthi rebels tried").endswith("tried"))
    check("a normal ending is left alone", s("ISRO launches new satellite").endswith("satellite"))
    check("very long headlines are capped", len(s(" ".join(["word"] * 60)).split()) <= 20)
    check("ellipsis / stray symbols are stripped", "..." not in s("Big news... #breaking @someone"))
    check("empty input is safe", s("") == "" and s(None) == "")
    check("a short headline is never stripped to nothing", len(s("Modi to").split()) >= 1)

    intro, stories, outro = rr.script_for(["First story here", "Second story here"])
    check("script has one spoken line per story", len(stories) == 2)
    check("stories are announced by number", stories[0].startswith("Number one.") and stories[1].startswith("Number two."))
    check("outro asks for the follow", "Follow" in outro)


def frame_tests(tmp):
    print("\nFRAMES")
    from PIL import Image
    slide = os.path.join(tmp, "s.jpg")
    Image.new("RGB", (1080, 1350), (10, 90, 40)).save(slide)
    f = rr.render_frame(slide, 0, 9, "20 SEP")
    check("frame is exactly 1080x1920", f.size == (1080, 1920))
    px = f.getpixel((540, 900))
    check("the slide sits in the middle of the frame (JPEG-tolerant colour match)",
          all(abs(a - b) <= 4 for a, b in zip(px, (10, 90, 40))), px)
    check("progress bar: current segment is lit", f.getpixel((100, 204))[0] > 200)
    check("progress bar: later segments are dim", f.getpixel((1000, 204))[0] < 120)
    last = rr.render_frame(slide, 8, 9, "20 SEP")
    check("progress bar advances with the story", last.getpixel((1000, 204))[0] > 200)
    e = rr.render_end_frame(9, "20 SEP")
    check("end card is exactly 1080x1920", e.size == (1080, 1920))
    check("end card bar is full", e.getpixel((100, 204))[0] > 200 and e.getpixel((1000, 204))[0] > 200)


def timing_tests(tmp):
    print("\nAUDIO / TIMING")
    a, b = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
    fake_synth("one two three four", a)
    fake_synth("one two", b)
    out = os.path.join(tmp, "joined.wav")
    durs = rr._concat_wavs([(a, 0.25), (b, 0.25)], out)
    with wave.open(out, "rb") as r:
        total = r.getnframes() / r.getframerate()
    check("per-line durations add up to the joined audio length", abs(sum(durs) - total) < 0.01,
          f"{sum(durs):.3f} vs {total:.3f}")
    check("each line's duration includes its trailing gap", durs[0] > 4 * 0.32 + 0.2)


def _probe(path):
    exe = rr.ffmpeg_exe()
    txt = subprocess.run([exe, "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d\.]+)", txt)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    return dur, ("Video:" in txt), ("Audio:" in txt), ("1080x1920" in txt)


def assembly_tests(tmp):
    print("\nFFMPEG ASSEMBLY")
    if not rr.ffmpeg_exe():
        SKIP.append("assembly")
        print("  SKIP  ffmpeg not available here (the daily-carousel workflow installs it)")
        return
    from PIL import Image
    slides = []
    for i in range(4):
        p = os.path.join(tmp, f"slide{i}.jpg")
        Image.new("RGB", (1080, 1350), (20 * i, 60, 90)).save(p)
        slides.append(p)
    heads = ["First headline about a thing", "Second headline about another thing",
             "Third headline about one more"]
    out = os.path.join(tmp, "reel.mp4")
    res = rr.build_reel(slides, heads, out, "20 SEP", synth=fake_synth, work_dir=os.path.join(tmp, "w"))
    dur, has_v, has_a, right_size = _probe(out)
    check("an mp4 is produced", os.path.exists(out) and os.path.getsize(out) > 5000)
    check("it has both a video and an audio stream", has_v and has_a)
    check("it is 1080x1920 (9:16)", right_size)
    check("its length matches the planned timeline", abs(dur - res["duration"]) < 1.0,
          f"{dur:.1f}s vs {res['duration']:.1f}s")
    check("it has one segment per spoken line (intro + stories + outro)", res["segments"] == 1 + 3 + 1)
    check("it stays under Instagram's 90s Reel limit", dur < 90)
    try:
        rr.build_reel(slides, heads[:2], os.path.join(tmp, "bad.mp4"), "20 SEP", synth=fake_synth)
        check("mismatched slides/headlines are rejected", False)
    except AssertionError:
        check("mismatched slides/headlines are rejected", True)


def caption_tests():
    print("\nREEL CAPTION")
    c = rr.reel_caption(8, 54.3)
    check("caption states the story count and length", "8 stories" in c and "54 seconds" in c)
    check("caption asks for the follow, naming the handle", "Follow @aravindnews24" in c)
    check("caption ends on hashtags", c.strip().splitlines()[-1].startswith("#"))


def state_tests(tmp):
    print("\nPUBLISH / RETRY STATE MACHINE")
    from src import carousel_review as cr, publisher

    check("a published carousel with results is due for a reel",
          cr.reel_due({"status": "published", "publish_results": {"instagram": {"id": "1"}}}))
    check("not due before the carousel is published",
          not cr.reel_due({"status": "preview_sent", "publish_results": {}}))
    check("not due when nothing actually posted (all slides dropped)",
          not cr.reel_due({"status": "published", "publish_results": None}))
    # Regression: the gating shipped without a date check, so the 20:00 and
    # 20:15 ticks (before tonight's carousel exists) would have found
    # YESTERDAY's published carousel and posted a reel of yesterday's news.
    yday = {"status": "published", "date": "2026-09-19", "publish_results": {"instagram": {"id": "1"}}}
    today = dt.datetime(2026, 9, 20, 20, 15)
    check("yesterday's published carousel is NOT due for a reel today",
          not cr.reel_due(yday, today))
    check("today's published carousel is due",
          cr.reel_due(dict(yday, date="2026-09-20"), today))
    os.environ["CAROUSEL_FORCE_REEL"] = "true"
    try:
        check("force_reel deliberately waives the date check", cr.reel_due(yday, today))
    finally:
        os.environ.pop("CAROUSEL_FORCE_REEL", None)
    check("never built twice",
          not cr.reel_due({"status": "published", "publish_results": {"instagram": {}}, "reel_status": "built"}))
    check("a failed reel is not retried forever",
          not cr.reel_due({"status": "published", "publish_results": {"instagram": {}}, "reel_status": "failed"}))

    real = (cr.STATE_PATH, cr._wait_until_public, cr.telegram_bot.send_message,
            publisher.publish_instagram_reel, os.environ.get("GH_PAGES_BASE"))
    sent = []
    cr.STATE_PATH = os.path.join(tmp, "state.json")
    cr._wait_until_public = lambda *a, **k: True
    cr.telegram_bot.send_message = lambda *a, **k: sent.append(a[0] if a else "")
    os.environ["GH_PAGES_BASE"] = "https://example.test/public"
    reel_file = os.path.join(cr._ROOT, "public", "carousel", "2026-09-20", "roundup.mp4")
    try:
        def fresh():
            return {"status": "published", "publish_results": {"instagram": {"id": "1"}},
                    "reel_status": "built", "reel_path": reel_file,
                    "reel_caption": "cap", "reel_attempts": 0}

        publisher.publish_instagram_reel = lambda url, cap, place=None: {"id": "REEL1", "url": url}
        st = fresh()
        cr.publish_roundup_reel(st)
        check("a successful publish marks the reel published", st["reel_status"] == "published")
        check("it published the public URL of the file",
              st["reel_result"]["url"] == "https://example.test/public/carousel/2026-09-20/roundup.mp4",
              st.get("reel_result"))
        check("a reel already published is never published again",
              cr.publish_roundup_reel(st) is None)

        def boom(*a, **k):
            raise RuntimeError("meta said no")
        publisher.publish_instagram_reel = boom
        st = fresh()
        cr.publish_roundup_reel(st)
        check("a first failure keeps it 'built' for the next tick",
              st["reel_status"] == "built" and st["reel_attempts"] == 1)
        cr.publish_roundup_reel(st)
        cr.publish_roundup_reel(st)
        check("after 3 failures it gives up ('failed') instead of retrying forever",
              st["reel_status"] == "failed")
        check("and tells you why", any("gave up" in m for m in sent), sent)

        cr._wait_until_public = lambda *a, **k: False
        st = fresh()
        cr.publish_roundup_reel(st)
        check("a file that never became public counts as a failed attempt, not a post",
              st["reel_status"] == "built" and st["reel_attempts"] == 1)
    finally:
        (cr.STATE_PATH, cr._wait_until_public, cr.telegram_bot.send_message,
         publisher.publish_instagram_reel) = real[:4]
        if real[4] is None:
            os.environ.pop("GH_PAGES_BASE", None)
        else:
            os.environ["GH_PAGES_BASE"] = real[4]


def build_gate_tests(tmp):
    print("\nBUILD GATES")
    from src import carousel_review as cr
    now = dt.datetime(2026, 9, 20, 21, 0)
    slide = lambda i, cover=False, status="pending": {
        "index": i, "is_cover": cover, "status": status, "headline": f"H{i}",
        "rendered_image_path": os.path.join(tmp, f"s{i}.jpg")}
    only_one_story = {"slides": [slide(0, True), slide(1)]}
    check("fewer than 2 surviving stories -> no reel", cr.build_roundup_reel(only_one_story, now) is None)
    all_dropped = {"slides": [slide(0, True), slide(1, status="rejected"), slide(2, status="rejected")]}
    check("every story dropped -> no reel", cr.build_roundup_reel(all_dropped, now) is None)


if __name__ == "__main__":
    text_tests()
    with tempfile.TemporaryDirectory() as t:
        frame_tests(t)
    with tempfile.TemporaryDirectory() as t:
        timing_tests(t)
    with tempfile.TemporaryDirectory() as t:
        assembly_tests(t)
    caption_tests()
    with tempfile.TemporaryDirectory() as t:
        state_tests(t)
    with tempfile.TemporaryDirectory() as t:
        build_gate_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
