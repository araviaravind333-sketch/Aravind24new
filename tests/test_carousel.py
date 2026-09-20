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


def cover_slide_tests(tmp):
    print("\nCOVER SLIDE")
    demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "demo.jpg")

    p = carousel.render_cover_slide(
        "BREAKING", "Have a look at what happened in the world in the last 24 hours",
        "18 SEP", [], os.path.join(tmp, "cover_gradient.jpg"),
        footer="For the latest news", handle="@aravindnews24")
    check("cover with no real photos falls back to a gradient, still renders",
          os.path.exists(p) and os.path.getsize(p) > 1000)

    for n in (1, 2, 3, 4):
        p = carousel.render_cover_slide(
            "BREAKING", "Have a look at what happened in the world in the last 24 hours",
            "18 SEP", [demo] * n, os.path.join(tmp, f"cover_{n}.jpg"),
            footer="For the latest news", handle="@aravindnews24")
        check(f"cover collage with {n} photo(s) renders", os.path.exists(p) and os.path.getsize(p) > 1000)

    from PIL import Image
    collage = carousel._build_collage([demo, demo, demo], 1080, 800)
    check("3-photo collage has no unfilled (solid black) region",
          collage.getpixel((1080 - 5, 800 - 5)) != (0, 0, 0))
    check("collage returns None (not a blank canvas) when given no photos at all",
          carousel._build_collage([], 1080, 800) is None)

    # Regression: a real live run had zero rights-cleared photos across
    # all 8 stories that day, so the cover fell back to one flat colour
    # block -- not a bug (there was genuinely no real photo to show), but
    # a worse-looking fallback than necessary. A multi-category mosaic
    # replaces the single block without ever pretending to be a photo.
    mosaic = carousel._build_color_mosaic(
        ["#F5871F", "#1E6FE0", "#12A150", "#8B5CF6", "#E01E1E"], 1080, 800)
    check("mosaic has distinct colours across the frame, not one flat block",
          len({mosaic.getpixel((x, 400)) for x in (50, 400, 750, 1000)}) > 1)
    check("mosaic has no unfilled region either",
          mosaic.getpixel((1080 - 5, 800 - 5)) != (0, 0, 0))
    single = carousel._build_color_mosaic(["#F5871F"], 1080, 800)
    check("a single category still renders (as a gradient, not a crash)",
          single.size == (1080, 800))
    empty = carousel._build_color_mosaic([], 1080, 800)
    check("no categories at all still renders something rather than crashing",
          empty.size == (1080, 800))

    p_no_photo = carousel.render_cover_slide(
        "BREAKING", "sub", "18 SEP", [], os.path.join(tmp, "cover_mosaic.jpg"),
        footer="For the latest news", handle="@aravindnews24",
        fallback_colors=["#F5871F", "#1E6FE0", "#12A150"])
    check("cover slide actually uses the mosaic fallback end-to-end",
          os.path.exists(p_no_photo) and os.path.getsize(p_no_photo) > 1000)


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


def cover_regeneration_tests(tmp):
    """Found from a live review: the cover started as a plain gradient
    because no rights-cleared photo existed at build time, and stayed
    that way even after the reviewer supplied real photos for other
    slides -- it should rebuild from those photos instead."""
    print("\nCOVER REGENERATION")
    from src import carousel_review as cr
    demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "demo.jpg")

    def _slide(index, is_cover=False, raw_photo_path=None, status="pending", media_source="text_only"):
        return {
            "index": index, "is_cover": is_cover, "status": status,
            "media_source": media_source, "media_kind": "image",
            "raw_photo_path": raw_photo_path,
            "rendered_image_path": os.path.join(tmp, f"slide_{index:02d}.jpg"),
        }

    cover = _slide(0, is_cover=True)
    cover["rendered_image_path"] = os.path.join(tmp, "cover.jpg")
    carousel.render_cover_slide("BREAKING", "sub", "18 SEP", [], cover["rendered_image_path"],
                                 footer="f", handle="h")
    before_size = os.path.getsize(cover["rendered_image_path"])

    state = {"slides": [cover, _slide(1, raw_photo_path=demo, media_source="owner"),
                         _slide(2)]}
    cr._regenerate_cover(state)
    after_size = os.path.getsize(cover["rendered_image_path"])
    check("cover file changes once a real photo becomes available",
          after_size != before_size)

    # A cover the reviewer explicitly replaced themselves must never be
    # silently overwritten by an automatic regeneration afterwards.
    owner_cover = _slide(0, is_cover=True, media_source="owner")
    owner_cover["rendered_image_path"] = os.path.join(tmp, "owner_cover.jpg")
    carousel.render_cover_slide("BREAKING", "sub", "18 SEP", [demo], owner_cover["rendered_image_path"],
                                 footer="f", handle="h")
    locked_size = os.path.getsize(owner_cover["rendered_image_path"])
    state2 = {"slides": [owner_cover, _slide(1, raw_photo_path=demo, media_source="owner")]}
    cr._regenerate_cover(state2)
    check("a reviewer-replaced cover is never auto-overwritten",
          os.path.getsize(owner_cover["rendered_image_path"]) == locked_size)

    # A rejected slide's photo should not appear in the cover collage.
    rejected = _slide(3, raw_photo_path=demo, media_source="owner", status="rejected")
    cover2 = _slide(0, is_cover=True)
    cover2["rendered_image_path"] = os.path.join(tmp, "cover2.jpg")
    carousel.render_cover_slide("BREAKING", "sub", "18 SEP", [], cover2["rendered_image_path"],
                                 footer="f", handle="h")
    unchanged_size = os.path.getsize(cover2["rendered_image_path"])
    state3 = {"slides": [cover2, rejected]}
    cr._regenerate_cover(state3)
    check("a dropped slide's photo is never pulled into the cover collage",
          os.path.getsize(cover2["rendered_image_path"]) == unchanged_size)


def _fake_slide(index, status="pending", kind="image", is_cover=False):
    return {
        "index": index, "status": status, "media_kind": kind,
        "message_id": 1000 + index, "video_path": None,
        "rendered_image_path": f"public/carousel/2026-09-18/slide_{index:02d}.jpg",
        "category": "INDIA NEWS", "headline": f"Story {index}", "subhead": "sub",
        "story": {"id": f"s{index}"}, "media_source": "auto", "is_cover": is_cover,
    }


def _story(id_, title, category="INDIA NEWS", score=90):
    return {"id": id_, "title": title, "summary": "", "score": score,
            "category": category, "link": "", "hot_hit": True}


def dedup_tests():
    """Regression test for a real bug found on the first live test of
    this feature: 3 of 8 slides turned out to be the same TMC symbol/name
    dispute from three different RSS sources, since news_engine's own
    top_candidates() only dedupes by exact story id, not by real-world
    event."""
    print("\nSTORY SELECTION DEDUPLICATION")
    from src import carousel_review as cr

    fake_pool = [
        _story("a1", "Mamata Banerjee faction seeks new name after Election Commission move"),
        _story("a2", "Mamata Banerjee Protests After Election Commission Freezes TMC Symbol"),
        _story("a3", "Election Commission freezes TMC name and symbol ahead of polls"),
        _story("b1", "Massive fire guts Chennai godown, three dead"),
        _story("c1", "Supreme Court reserves verdict in electoral bonds case"),
    ]
    picked = cr._dedupe_by_event(fake_pool, 3)

    check("same-event duplicates collapse to a single slide",
          len(picked) == 3, f"got {len(picked)}: {[p['id'] for p in picked]}")
    check("only one of the three TMC stories survives",
          sum(1 for p in picked if p["id"] in ("a1", "a2", "a3")) == 1,
          [p["id"] for p in picked])
    check("genuinely distinct stories are both kept",
          {"b1", "c1"} <= {p["id"] for p in picked})


def selection_scope_tests():
    """The carousel is a 'last 24 hours, world + India' roundup by
    request -- it must NOT be limited to news_engine's India-only
    daytime window like the regular single-story posts, but it must
    always include at least one India story regardless of what the
    world-news mix looks like on merit."""
    print("\nSELECTION SCOPE (all categories + India guaranteed)")
    from src import carousel_review as cr, news_engine as ne

    all_world = {
        "WORLD NEWS": [_story("w1", "Train derails in France, 44 injured", "WORLD NEWS", 95),
                        _story("w2", "Fuel tanker explodes outside Baltimore", "WORLD NEWS", 92)],
        "INDIA NEWS": [_story("i1", "Massive fire guts Chennai godown, three dead", "INDIA NEWS", 60)],
        "BUSINESS NEWS": [], "SPORTS NEWS": [], "HUMAN INTEREST": [],
    }
    real_fetch = ne.fetch_candidates
    ne.fetch_candidates = lambda category: list(all_world.get(category, []))
    try:
        picked = cr._select_distinct_stories(2)
        check("world stories are eligible even though India's score is lower",
              any(p["category"] == "WORLD NEWS" for p in picked), picked)
        check("at least one India story is guaranteed even when it scores lowest",
              any(p["category"] == "INDIA NEWS" for p in picked), picked)

        # And the reverse: if India already made it on merit, nothing is
        # force-swapped in on top of it.
        picked2 = cr._select_distinct_stories(3)
        check("does not duplicate India once it's already included on merit",
              sum(1 for p in picked2 if p["category"] == "INDIA NEWS") == 1, picked2)
    finally:
        ne.fetch_candidates = real_fetch


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

    with_cover = {"slides": [_fake_slide(0, is_cover=True)] + [_fake_slide(i) for i in range(1, 4)]}
    with_cover["slides"][2]["status"] = "rejected"  # drop story slide 2 of 3
    kept_wc = cr.surviving_slides(with_cover)
    check("cover slide is always first among survivors", kept_wc[0]["is_cover"] is True)
    check("cover slide keeps final_index 0 (no number badge)", kept_wc[0]["final_index"] == 0)
    check("story slides after the cover are still renumbered with no gaps",
          [s["final_index"] for s in kept_wc[1:]] == [1, 2])

    cover_reject_attempt = {"slides": [_fake_slide(0, is_cover=True, status="rejected")]}
    check("a cover slide marked rejected some other way still survives (belt and braces)",
          len(cr.surviving_slides(cover_reject_attempt)) == 1)

    now = dt.datetime(2026, 9, 18, 21, 30)
    fresh = {"status": "preview_sent", "preview_sent_at": "2026-09-18 21:00"}
    stale = {"status": "preview_sent", "preview_sent_at": "2026-09-18 19:00", "image_gate": "clear"}
    unsent = {"status": "collecting", "preview_sent_at": None}
    check("not ready before the grace period elapses", cr.ready_to_publish(fresh, now) is False)
    check("ready once the grace period has elapsed", cr.ready_to_publish(stale, now) is True)
    check("never ready before a preview has even been sent", cr.ready_to_publish(unsent, now) is False)
    stale_ungated = {"status": "preview_sent", "preview_sent_at": "2026-09-18 19:00"}
    check("grace elapsed but image gate not cleared -> NOT ready",
          cr.ready_to_publish(stale_ungated, now) is False)

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
    slides = [_fake_slide(0, is_cover=True)] + [_fake_slide(1), _fake_slide(2), _fake_slide(3)]
    for i, s in enumerate(slides[1:], start=1):
        s["headline"] = f"Headline number {i}"
    now = dt.datetime(2026, 9, 18, 20, 30)
    caption = cr._build_carousel_caption(slides, now)
    check("caption numbers every story slide", all(f"{i:02d}." in caption for i in range(1, 4)))
    check("caption includes every story headline", all(s["headline"] in caption for s in slides[1:]))
    check("caption does not list a fake entry for the cover slide", "00." not in caption)
    check("caption asks for the follow, naming the handle", "Follow @aravindnews24" in caption)
    check("caption asks for a comment and a share",
          "Tell us below" in caption and "Send this to" in caption)
    check("caption ends on the hashtag line", caption.strip().splitlines()[-1].startswith("#"))
    check("the hook (first line, all a viewer sees before 'more') states the story count",
          caption.splitlines()[0].startswith("\U0001F4F0 3 stories"))

    # Regression from the first live post: a slide dropped during review
    # was still listed in the published caption.
    slides[2]["status"] = "rejected"
    cr.surviving_slides({"slides": slides})
    after_drop = cr._build_carousel_caption(slides, now)
    check("a dropped slide's headline is not in the caption",
          slides[2]["headline"] not in after_drop)
    check("caption numbering follows the surviving slides, with no gap",
          "01. Headline number 1" in after_drop and "02. Headline number 3" not in after_drop
          or "02." in after_drop and "03." not in after_drop)

    # Licence credit must reach the caption for a portrait slide.
    slides[1]["photo_credit"] = "Biswarup Ganguly / CC BY 3.0 (Wikimedia Commons)"
    slides[1]["photo_subject"] = "Mamata Banerjee"
    with_credit = cr._build_carousel_caption(slides, now)
    check("a licensed portrait's author + licence are printed in the caption",
          "Mamata Banerjee: Biswarup Ganguly / CC BY 3.0" in with_credit)


def image_gate_tests():
    """The owner's rule: an image-less story is never published."""
    print("\nIMAGE GATE")
    from src import carousel_review as cr
    sent = []
    real_send = cr.telegram_bot.send_message
    cr.telegram_bot.send_message = lambda text, *a, **k: sent.append(text)
    real_path = cr.STATE_PATH
    real_render = cr._rerender_slide
    cr._rerender_slide = lambda s, n: None
    now = dt.datetime(2026, 9, 18, 22, 0)

    def st(with_image, without_image):
        slides = [_fake_slide(0, is_cover=True)]
        i = 1
        for _ in range(with_image):
            s = _fake_slide(i); s["raw_photo_path"] = f"p{i}.jpg"; slides.append(s); i += 1
        for _ in range(without_image):
            s = _fake_slide(i); s["media_source"] = "text_only"; s["raw_photo_path"] = None; slides.append(s); i += 1
        return {"status": "preview_sent", "preview_sent_at": "2026-09-18 20:30", "slides": slides}

    with tempfile.TemporaryDirectory() as tmp:
        cr.STATE_PATH = os.path.join(tmp, "carousel_pending.json")
        try:
            s = st(6, 0)
            cr.apply_image_gate(s, now)
            check("all slides have images -> gate clear, ready", s["image_gate"] == "clear"
                  and cr.ready_to_publish(s, now))

            s = st(5, 3)
            sent.clear()
            cr.apply_image_gate(s, now)
            dropped = [x for x in s["slides"] if x.get("drop_reason") == "no_image"]
            check(">=4 with images -> image-less ones left out, gate clear",
                  s["image_gate"] == "clear" and len(dropped) == 3)
            check("the left-out slides are all the image-less ones",
                  all(not cr.slide_has_image(x) for x in dropped))
            check("survivors are renumbered 1..5",
                  [x["final_index"] for x in cr.surviving_slides(s)[1:]] == [1, 2, 3, 4, 5])
            check("owner is told which slides were left out", len(sent) == 1 and "no image" in sent[0])
            check("nothing image-less can survive to publish",
                  all(cr.slide_has_image(x) for x in cr.surviving_slides(s) if not x.get("is_cover")))

            s = st(2, 6)
            sent.clear()
            cr.apply_image_gate(s, now)
            check("<4 images -> HELD", s["image_gate"] == "hold" and not cr.ready_to_publish(s, now))
            check("held carousel drops nothing", not any(x.get("drop_reason") for x in s["slides"]))
            check("owner gets a hold reminder", len(sent) == 1 and "ON HOLD" in sent[0])
            cr.apply_image_gate(s, now + dt.timedelta(minutes=30))
            check("no reminder spam within the reminder interval", len(sent) == 1)
            cr.apply_image_gate(s, now + dt.timedelta(hours=3))
            check("reminder again after the interval", len(sent) == 2)

            for x in s["slides"][3:5]:
                x["raw_photo_path"] = "attached.jpg"
            cr.apply_image_gate(s, now + dt.timedelta(hours=3, minutes=5))
            check("attaching photos while held releases it (4 have images, 4 left out)",
                  s["image_gate"] == "clear" and cr.ready_to_publish(s, now + dt.timedelta(hours=4)))

            s = st(6, 0)
            check("gate does nothing before the review window ends",
                  cr.apply_image_gate(s, dt.datetime(2026, 9, 18, 20, 40)) is None
                  and "image_gate" not in s)

            s = st(2, 1)
            s["slides"][3]["video_path"] = "v.mp4"; s["slides"][3]["media_kind"] = "video"
            check("a video slide counts as having an image", cr.slide_has_image(s["slides"][3]))

            s = st(3, 2)
            s["status"] = "preview_sent"
            check("finalize refuses to publish an image-less slide even if the gate was bypassed",
                  cr.finalize_and_publish(s, now) is None and s["status"] == "preview_sent")
        finally:
            cr.STATE_PATH = real_path
            cr.telegram_bot.send_message = real_send
            cr._rerender_slide = real_render

    # single posts: no verified image -> held, never rendered/published
    import datetime as _dt
    from src import main
    held = []
    real = (main.subject_photos.find_subject_photo, main._hold_for_image, main.ai_writer.rewrite)
    main.subject_photos.find_subject_photo = lambda *a, **k: None
    main._hold_for_image = lambda story, ist: held.append(story["id"])
    def _no_render(*a, **k):
        raise AssertionError("rendered an image-less post")
    main.ai_writer.rewrite = _no_render
    try:
        story = {"id": "z1", "title": "Delhi rain floods roads", "summary": "", "score": 99,
                 "category": "INDIA NEWS", "link": ""}
        r = main._render_story(story, _dt.datetime(2026, 9, 20, 21, 0), is_reel=False, force_no_image=True)
        check("a single post with no verified image is held, not rendered", r is None and held == ["z1"])
        entry = {"story": story}
        check("portrait availability is cached on the queue entry",
              main._portrait_available(entry) is False and entry.get("portrait_ok") is False)
    finally:
        main.subject_photos.find_subject_photo, main._hold_for_image, main.ai_writer.rewrite = real


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        rendering_tests(tmp)
        cover_slide_tests(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        cover_regeneration_tests(tmp)
    accent_phrase_tests()
    dedup_tests()
    selection_scope_tests()
    review_state_tests()
    caption_tests()
    image_gate_tests()
    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
