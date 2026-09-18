"""
Daily Carousel -- Tests
=======================
Covers the parts of the carousel feature that don't touch the network:
slide rendering (both the photo and text-only paths actually produce a
file), the accent-phrase heuristic, and the review state machine (reject
a slide, replace a slide, decide when it's ready to publish, and
renumber survivors with no gaps).

Does NOT test real Telegram delivery or Graph API publishing -- those
need live credentials and are exercised manually against the real
account, same as the rest of this project's Meta-API-touching code.

    python -m tests.test_carousel
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import carousel

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def rendering_tests(tmp):
    print("\nSLIDE RENDERING")
    demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "demo.jpg")

    p = carousel.render_carousel_slide(
        demo, "BREAKING NEWS", "Fuel Tanker Explodes Outside Baltimore",
        "Officials confirm the risk has passed", 1, 8,
        os.path.join(tmp, "photo_slide.jpg"), footer="For the latest news", handle="@aravindnews24")
    check("photo slide renders a real file", os.path.exists(p) and os.path.getsize(p) > 1000)

    p2 = carousel.render_carousel_slide(
        None, "INDIA NEWS", "Supreme Court Reserves Verdict In Electoral Bonds Case",
        "A ruling is expected within two weeks", 2, 8,
        os.path.join(tmp, "text_slide.jpg"), footer="For the latest news", handle="@aravindnews24")
    check("text-only (no-photo) slide renders a real file", os.path.exists(p2) and os.path.getsize(p2) > 1000)

    png = carousel.render_carousel_overlay_png(
        "WORLD NEWS", "Train Derails In France, 44 Injured", "Sabotage suspected",
        3, 8, os.path.join(tmp, "overlay.png"), footer="For the latest news", handle="@aravindnews24")
    check("transparent overlay PNG renders (for video compositing)", os.path.exists(png))
    from PIL import Image
    im = Image.open(png)
    check("overlay PNG actually has an alpha channel", im.mode == "RGBA")


def accent_phrase_tests():
    print("\nACCENT PHRASE HEURISTIC")
    check("picks a proper-noun run over nothing",
          carousel._pick_accent_phrase("India Welcomes UAE Crown Prince Sheikh Khaled") != "")
    check("prefers the longer proper-noun phrase",
          len(carousel._pick_accent_phrase("Modi And Xi Jinping Share A Handshake")) > len("Modi"))
    check("falls back to a number+word phrase when no name is present",
          carousel._pick_accent_phrase("Train Derails In France, 44 Injured") != "")
    check("leading sentence-starter words are not mistaken for names",
          "The" not in carousel._pick_accent_phrase("The Government Announces New Policy Today"))
    check("a plain lowercase headline has no accent phrase",
          carousel._pick_accent_phrase("a quiet day for markets") == "")


def _fake_slide(index, status="pending", kind="image"):
    return {
        "index": index, "status": status, "media_kind": kind,
        "message_id": 1000 + index, "video_path": None,
        "rendered_image_path": f"public/carousel/2026-09-18/slide_{index:02d}.jpg",
        "category": "INDIA NEWS", "headline": f"Story {index}", "subhead": "sub",
        "story": {"id": f"s{index}"}, "media_source": "auto",
    }


def review_state_tests():
    print("\nREVIEW STATE MACHINE")
    from src import carousel_review as cr

    state = {"slides": [_fake_slide(i) for i in range(1, 6)]}
    state["slides"][2]["status"] = "rejected"
    kept = cr.surviving_slides(state)
    check("rejected slide is excluded from survivors", len(kept) == 4)
    check("survivors are renumbered with no gaps",
          [s["final_index"] for s in kept] == [1, 2, 3, 4])
    check("original order is preserved among survivors",
          [s["index"] for s in kept] == [1, 2, 4, 5])

    all_rejected = {"slides": [_fake_slide(i, status="rejected") for i in range(1, 4)]}
    check("every slide rejected -> zero survivors", cr.surviving_slides(all_rejected) == [])

    now = dt.datetime(2026, 9, 18, 21, 30)
    fresh = {"status": "preview_sent", "preview_sent_at": "2026-09-18 21:00"}
    stale = {"status": "preview_sent", "preview_sent_at": "2026-09-18 19:00"}
    unsent = {"status": "collecting", "preview_sent_at": None}
    check("not ready before the grace period elapses", cr.ready_to_publish(fresh, now) is False)
    check("ready once the grace period has elapsed", cr.ready_to_publish(stale, now) is True)
    check("never ready before a preview has even been sent", cr.ready_to_publish(unsent, now) is False)

    # due_for_preview reads STATE_PATH off disk -- redirect it to an
    # isolated temp file so this is deterministic and doesn't depend on
    # (or clobber) whatever real carousel state exists locally.
    with tempfile.TemporaryDirectory() as tmp:
        real_path = cr.STATE_PATH
        cr.STATE_PATH = os.path.join(tmp, "carousel_pending.json")
        try:
            before_time = dt.datetime(2026, 9, 18, 19, 0)
            after_time = dt.datetime(2026, 9, 18, 20, 45)
            check("not due before the configured preview hour",
                  cr.due_for_preview(before_time) is False)
            check("due at/after the configured preview hour with nothing built yet",
                  cr.due_for_preview(after_time) is True)

            cr._save_state({"date": "2026-09-18", "slides": []})
            check("not due again once today's carousel already exists",
                  cr.due_for_preview(after_time) is False)

            next_day = dt.datetime(2026, 9, 19, 20, 45)
            check("due again on a new day even with yesterday's state present",
                  cr.due_for_preview(next_day) is True)
        finally:
            cr.STATE_PATH = real_path


def caption_tests():
    print("\nCAPTION BUILDING")
    from src import carousel_review as cr
    slides = [_fake_slide(1), _fake_slide(2), _fake_slide(3)]
    for i, s in enumerate(slides, start=1):
        s["headline"] = f"Headline number {i}"
    now = dt.datetime(2026, 9, 18, 20, 30)
    caption = cr._build_carousel_caption(slides, now)
    check("caption numbers every slide", all(f"{i:02d}." in caption for i in range(1, 4)))
    check("caption includes every headline", all(s["headline"] in caption for s in slides))
    check("caption ends with the brand handle", caption.strip().endswith("@aravindnews24"))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        rendering_tests(tmp)
    accent_phrase_tests()
    review_state_tests()
    caption_tests()
    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
