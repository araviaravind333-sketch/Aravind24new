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


def voice_tests(tmp):
    print("\nOWNER'S VOICE")
    import math
    from src import carousel_review as cr

    def tone(path, secs, rate=44100):
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            frames = bytearray()
            import struct
            for n in range(int(rate * secs)):
                v = int(12000 * math.sin(2 * math.pi * 440 * n / rate))
                frames += struct.pack("<hh", v, v)
            w.writeframes(bytes(frames))
        return path

    def slide(i, **kw):
        s = {"index": i, "status": "pending", "is_cover": False}
        s.update(kw)
        return s

    a = tone(os.path.join(tmp, "a.wav"), 2.0)
    b = tone(os.path.join(tmp, "b.wav"), 3.0)
    state = {"slides": [slide(0, is_cover=True), slide(1, voice_path=a), slide(2, voice_path=b)]}
    plan = cr.voice_plan(state)
    check("every story has a recording -> your voice is used", plan and plan["stories"] == [a, b])
    check("the cover recording is optional (no intro)", plan and plan["intro"] is None)
    state["slides"][0]["voice_path"] = a
    check("a cover recording becomes the intro", cr.voice_plan(state)["intro"] == a)

    state["slides"][2].pop("voice_path")
    check("one story missing a recording -> standard voice for the WHOLE reel (no mixing)",
          cr.voice_plan(state) is None)
    state["slides"][2]["status"] = "rejected"
    check("a dropped slide's missing recording does not matter", cr.voice_plan(state) is not None)
    state["slides"][1]["voice_path"] = os.path.join(tmp, "gone.wav")
    check("a recording whose file is missing counts as missing", cr.voice_plan(state) is None)

    if not rr.ffmpeg_exe():
        SKIP.append("voice assembly")
        print("  SKIP  ffmpeg not available here")
        return
    from PIL import Image
    slides = []
    for i in range(3):
        sp = os.path.join(tmp, f"vslide{i}.jpg")
        Image.new("RGB", (1080, 1350), (20 * i, 60, 90)).save(sp)
        slides.append(sp)
    out = os.path.join(tmp, "voice_reel.mp4")
    res = rr.build_reel(slides, ["One", "Two"], out, "20 SEP",
                        voice_clips={"intro": None, "stories": [a, b]}, work_dir=os.path.join(tmp, "vw"))
    dur, has_v, has_a, right_size = _probe(out)
    check("a reel is built from the owner's recordings", os.path.exists(out) and has_v and has_a and right_size)
    check("it is marked as using the owner's voice", res["voice"] == "owner")
    check("segments = intro + stories + end card", res["segments"] == 1 + 2 + 1)
    check("length covers both recordings plus intro/outro padding", 5.0 < dur < 20.0, f"{dur:.1f}s")
    try:
        rr.build_reel(slides, ["One", "Two"], os.path.join(tmp, "x.mp4"), "20 SEP",
                      voice_clips={"intro": None, "stories": [a]})
        check("wrong number of recordings is rejected", False)
    except AssertionError:
        check("wrong number of recordings is rejected", True)

    # the Telegram side: a voice note replied to a slide is saved on that slide
    saved = {}
    real_dl, real_reply, real_save = cr.telegram_bot.download_file, cr.telegram_bot.reply_to_message, cr._save_state
    cr.telegram_bot.download_file = lambda fid, dest: saved.setdefault("p", dest + ".oga")
    replies = []
    cr.telegram_bot.reply_to_message = lambda mid, text, *a_, **k: replies.append(text)
    cr._save_state = lambda s: None
    try:
        st = {"slides": [slide(0, is_cover=True, message_id=10), slide(1, message_id=11), slide(2, message_id=12)]}
        msg = {"message_id": 99, "reply_to_message": {"message_id": 11}, "voice": {"file_id": "F1"}}
        ok = cr.handle_reply(msg, st)
        check("a voice note replied to a slide is stored on that slide",
              ok and st["slides"][1].get("voice_path", "").endswith("voice_01.oga"))
        check("owner is told how many slides are recorded", replies and "1 of 2" in replies[-1], replies)
    finally:
        cr.telegram_bot.download_file, cr.telegram_bot.reply_to_message, cr._save_state = real_dl, real_reply, real_save


def voice_hold_tests(tmp):
    print("\nREEL WAITS FOR YOUR VOICE")
    from config import settings
    from src import carousel_review as cr, roundup_reel

    sent = []
    real = (cr.telegram_bot.send_message, cr._save_state, roundup_reel.build_reel)
    cr.telegram_bot.send_message = lambda text, *a, **k: sent.append(text)
    cr._save_state = lambda s: None
    built = []
    def fake_build(slides, heads, out, label, **k):
        built.append(k.get("voice_clips"))
        open(out, "wb").write(b"x" * 100)
        return {"duration": 30.0, "voice": "x"}
    roundup_reel.build_reel = fake_build
    old_flag = settings.REEL_REQUIRE_OWNER_VOICE
    old_clone = settings.REEL_CLONE_ENABLED
    settings.REEL_CLONE_ENABLED = False      # the repo now holds a real voice profile; these tests are about its absence
    now = dt.datetime(2026, 9, 20, 21, 0)

    def st():
        slides = []
        for i in range(3):
            path = os.path.join(tmp, f"s{i}.jpg")
            open(path, "wb").write(b"x")
            slides.append({"index": i, "final_index": i, "status": "pending", "is_cover": i == 0,
                           "rendered_image_path": path, "headline": f"H{i}", "media_kind": "image"})
        return {"date": "2026-09-20", "status": "published", "slides": slides}

    try:
        settings.REEL_REQUIRE_OWNER_VOICE = True
        state = st()
        res = cr.build_roundup_reel(state, now)
        check("no voice notes -> no reel is built", res is None and not built and state.get("reel_status") is None)
        check("owner is sent the script to read (intro, numbered lines, closing)",
              len(sent) == 1 and "voice" in sent[0].lower() and "Number one. H1" in sent[0]
              and "Number two. H2" in sent[0] and "Follow Aravind News" in sent[0], sent)
        cr.build_roundup_reel(state, now + dt.timedelta(minutes=30))
        check("no reminder spam inside 3 hours", len(sent) == 1)
        cr.build_roundup_reel(state, now + dt.timedelta(hours=3, minutes=1))
        check("reminded again after 3 hours", len(sent) == 2)

        for s in state["slides"][1:]:
            v = os.path.join(tmp, f"v{s['index']}.wav")
            open(v, "wb").write(b"x")
            s["voice_path"] = v
        cr.build_roundup_reel(state, now + dt.timedelta(hours=4), synth=None)
        check("once every story has a voice note, the reel is built from them",
              built and built[-1] and len(built[-1]["stories"]) == 2 and state.get("reel_status") == "built")

        settings.REEL_REQUIRE_OWNER_VOICE = False
        built.clear()
        state2 = st()
        cr.build_roundup_reel(state2, now, synth=lambda *a: None)
        check("with the requirement off it falls back to the standard voice", built and built[-1] is None)
    finally:
        settings.REEL_REQUIRE_OWNER_VOICE = old_flag
        settings.REEL_CLONE_ENABLED = old_clone
        cr.telegram_bot.send_message, cr._save_state, roundup_reel.build_reel = real


def clone_tests(tmp):
    print("\nCLONED VOICE SELECTION")
    from config import settings
    from src import carousel_review as cr, roundup_reel, voice_clone

    sent = []
    real = (cr.telegram_bot.send_message, cr._save_state, roundup_reel.build_reel,
            voice_clone.make_clone_synth, voice_clone.PROFILE_PATH, settings.REEL_CLONE_ENABLED,
            settings.REEL_REQUIRE_OWNER_VOICE)
    cr.telegram_bot.send_message = lambda text, *a, **k: sent.append(text)
    cr._save_state = lambda s: None
    got = {}

    def fake_build(slides, heads, out, label, **k):
        got.update(k)
        open(out, "wb").write(b"x" * 100)
        return {"duration": 30.0, "voice": "x"}
    roundup_reel.build_reel = fake_build
    marker = lambda text, path: None
    voice_clone.make_clone_synth = lambda *a, **k: marker
    settings.REEL_REQUIRE_OWNER_VOICE = True

    def st():
        slides = []
        for i in range(3):
            path = os.path.join(tmp, f"c{i}.jpg")
            open(path, "wb").write(b"x")
            slides.append({"index": i, "final_index": i, "status": "pending", "is_cover": i == 0,
                           "rendered_image_path": path, "headline": f"H{i}", "media_kind": "image"})
        return {"date": "2026-09-20", "status": "published", "slides": slides,
                "publish_results": {"instagram": {"id": "1"}}}

    now = dt.datetime(2026, 9, 20, 21, 0)
    try:
        profile = os.path.join(tmp, "owner_voice.pt")
        voice_clone.PROFILE_PATH = profile
        settings.REEL_CLONE_ENABLED = True
        check("no profile -> clone not available", not voice_clone.clone_available())
        check("no profile, no notes -> reel waits (not needs_clone)", not cr.reel_needs_clone(st(), now))

        open(profile, "wb").write(b"profile")
        check("profile present -> clone available", voice_clone.clone_available())
        check("a due reel with a profile needs the clone model", cr.reel_needs_clone(st(), now))
        check("a reel that is not due does not install the model",
              not cr.reel_needs_clone(dict(st(), reel_status="published"), now))

        s = st()
        cr.build_roundup_reel(s, now)
        check("the reel is narrated with the cloned voice", got.get("synth") is marker and not got.get("voice_clips"),
              got)
        check("no 'send me your voice' nag when a profile exists", not sent)

        settings.REEL_CLONE_ENABLED = False
        got.clear()
        s = st()
        res = cr.build_roundup_reel(s, now)
        check("clone switched off + no notes -> waits instead of using the standard voice",
              res is None and not got)

        settings.REEL_CLONE_ENABLED = True
        s = st()
        for sl in s["slides"][1:]:
            v = os.path.join(tmp, f"vv{sl['index']}.wav")
            open(v, "wb").write(b"x")
            sl["voice_path"] = v
        got.clear()
        cr.build_roundup_reel(s, now)
        check("the owner's own recordings beat the clone", got.get("voice_clips") and got.get("synth") is None, got)
    finally:
        (cr.telegram_bot.send_message, cr._save_state, roundup_reel.build_reel,
         voice_clone.make_clone_synth, voice_clone.PROFILE_PATH, settings.REEL_CLONE_ENABLED,
         settings.REEL_REQUIRE_OWNER_VOICE) = real


def one_take_tests(tmp):
    print("\nONE-TAKE RECORDING")
    import math, struct
    from src import carousel_review as cr

    exe = rr.ffmpeg_exe()
    if not exe:
        SKIP.append("one-take")
        print("  SKIP  ffmpeg not available here")
        return

    def make(path, pieces, gap=1.6, inner=0.25, rate=22050):
        """pieces: list of lists of phrase lengths (s). Phrases inside a piece
        are separated by a short `inner` pause, pieces by a long `gap`."""
        frames = bytearray()
        def tone(sec, hz=300):
            for n in range(int(rate * sec)):
                frames.extend(struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * n / rate))))
        def quiet(sec):
            frames.extend(b"\x00\x00" * int(rate * sec))
        quiet(0.4)
        for i, phrases in enumerate(pieces):
            for j, sec in enumerate(phrases):
                tone(sec)
                if j < len(phrases) - 1:
                    quiet(inner)
            quiet(gap if i < len(pieces) - 1 else 0.6)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(bytes(frames))
        return path

    five = make(os.path.join(tmp, "five.wav"), [[1.5], [2.0, 1.2], [1.8], [2.2, 1.0], [1.4]])
    paths, found = rr.split_recording(exe, five, (5, 3), os.path.join(tmp, "s1"))
    check("intro + 3 stories + closing line -> 5 clips", paths and found == 5 and len(paths) == 5, (found,))
    check("a short pause inside a story does not split it", paths and len(paths) == 5)
    durs = []
    for pth in paths or []:
        with wave.open(pth, "rb") as w:
            durs.append(w.getnframes() / w.getframerate())
    check("the two-part story keeps both of its phrases (longest clip)",
          durs and max(durs) > 3.0, [round(d, 1) for d in durs])

    three = make(os.path.join(tmp, "three.wav"), [[1.5], [1.8], [1.4]])
    paths, found = rr.split_recording(exe, three, (5, 3), os.path.join(tmp, "s2"))
    check("stories only (no intro/closing) -> 3 clips", paths and found == 3)

    four = make(os.path.join(tmp, "four.wav"), [[1.5], [1.8], [1.4], [1.6]])
    paths, found = rr.split_recording(exe, four, (5, 3), os.path.join(tmp, "s3"))
    check("wrong number of pieces -> refused, with what was found", paths is None and 4 in found, found)

    # the state-level flow
    sent = []
    real = (cr.telegram_bot.send_message, cr._save_state)
    cr.telegram_bot.send_message = lambda text, *a, **k: sent.append(text)
    cr._save_state = lambda s: None
    try:
        slides = [{"index": 0, "is_cover": True, "status": "pending", "voice_full_path": five},
                  {"index": 1, "status": "pending"}, {"index": 2, "status": "pending"},
                  {"index": 3, "status": "pending"}]
        state = {"slides": slides}
        plan = cr.one_take_plan(state)
        check("a cover with a full recording is a one-take plan", plan == {"full": five, "stories": 3}, plan)
        clips = cr.resolve_one_take(state, plan)
        check("it resolves to intro + 3 stories + outro",
              clips and clips["intro"] and len(clips["stories"]) == 3 and clips["outro"])
        check("story slides with no per-slide notes are still covered by the one-take",
              cr.voice_plan(state) is None and cr.one_take_plan(state) is not None)

        slides[0]["voice_full_path"] = four
        state2 = {"slides": slides}
        res = cr.resolve_one_take(state2, cr.one_take_plan(state2))
        check("an unsplittable recording is refused, forgotten, and the owner is told",
              res is None and "voice_full_path" not in slides[0] and sent and "expected 5" in sent[-1], sent)
        check("no one-take plan once it was rejected", cr.one_take_plan(state2) is None)

        # long voice note replied to the cover is stored as the one-take
        real_dl, real_reply = cr.telegram_bot.download_file, cr.telegram_bot.reply_to_message
        cr.telegram_bot.download_file = lambda fid, dest: dest + ".oga"
        cr.telegram_bot.reply_to_message = lambda mid, text, *a, **k: sent.append(text)
        try:
            st = {"slides": [{"index": 0, "is_cover": True, "message_id": 10, "status": "pending"},
                             {"index": 1, "message_id": 11, "status": "pending"}]}
            cr.handle_reply({"message_id": 90, "reply_to_message": {"message_id": 10},
                             "voice": {"file_id": "F", "duration": 45}}, st)
            check("a 45s voice note on the cover is the one-take recording",
                  st["slides"][0].get("voice_full_path", "").endswith(".oga")
                  and not st["slides"][0].get("voice_path"))
            st2 = {"slides": [{"index": 0, "is_cover": True, "message_id": 10, "status": "pending"},
                              {"index": 1, "message_id": 11, "status": "pending"}]}
            cr.handle_reply({"message_id": 91, "reply_to_message": {"message_id": 10},
                             "voice": {"file_id": "F", "duration": 6}}, st2)
            check("a short voice note on the cover is just the optional intro",
                  st2["slides"][0].get("voice_path") and not st2["slides"][0].get("voice_full_path"))
        finally:
            cr.telegram_bot.download_file, cr.telegram_bot.reply_to_message = real_dl, real_reply
    finally:
        cr.telegram_bot.send_message, cr._save_state = real


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
    check("last night's carousel is still due the next morning (waiting for voice notes)",
          cr.reel_due(yday, dt.datetime(2026, 9, 20, 9, 0)))
    check("...but not from noon on", not cr.reel_due(yday, dt.datetime(2026, 9, 20, 12, 0)))
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
    with tempfile.TemporaryDirectory() as t:
        voice_tests(t)
    with tempfile.TemporaryDirectory() as t:
        voice_hold_tests(t)
    with tempfile.TemporaryDirectory() as t:
        clone_tests(t)
    with tempfile.TemporaryDirectory() as t:
        one_take_tests(t)
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
