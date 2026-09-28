"""
UGC permission ledger -- Tests
==============================
The rule being tested is the one that protects the account: a video
filmed by someone else is only ever postable after a HUMAN has recorded
that its creator said yes. Credit alone is not permission, and a page
that reposts without it collects Instagram strikes until it is deleted.

    python -m tests.test_ugc
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import ugc

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def link_tests():
    print("\nLINK DETECTION")
    f = ugc.find_video_link
    check("an x.com post is a permission request",
          f("look at this https://x.com/someone/status/123") == "https://x.com/someone/status/123")
    check("twitter.com too", f("https://twitter.com/a/status/9") == "https://twitter.com/a/status/9")
    check("instagram reel too", bool(f("https://www.instagram.com/reel/ABC123/")))
    check("youtube shorts too", bool(f("https://youtube.com/shorts/xyz")))
    check("a plain news article link is NOT treated as one (the urgent path owns those)",
          f("https://www.thehindu.com/news/national/article123.ece") is None)
    check("a message with no link at all", f("post this please") is None)
    check("trailing punctuation is trimmed",
          f("(see https://x.com/a/status/1).") == "https://x.com/a/status/1")

    check("the creator handle is read from an x.com URL",
          ugc.creator_handle("https://x.com/RealName_1/status/55") == "@RealName_1")
    check("no handle is invented when the URL does not contain one",
          ugc.creator_handle("https://www.instagram.com/reel/ABC/") is None)

    # Regression: a real report -- a shared link produced no response at
    # all. Cause: the link had no "https://" (very common when pasted from
    # a phone's share sheet), and the old pattern required it.
    check("a link with NO scheme still works (the #1 real cause of the bug)",
          f("x.com/someone/status/123") == "https://x.com/someone/status/123")
    check("a scheme-less link is still detected inside other text",
          f("look at this x.com/someone/status/123 wow") == "https://x.com/someone/status/123")
    check("m.twitter.com with no scheme also works",
          f("m.twitter.com/a/status/9") == "https://m.twitter.com/a/status/9")
    check("a scheme-less link keeps a query string",
          f("x.com/a/status/1?s=46&t=xyz") == "https://x.com/a/status/1?s=46&t=xyz")
    for mirror in ("vxtwitter.com", "fxtwitter.com"):
        check(f"the {mirror} mirror is recognised",
              f(f"https://{mirror}/a/status/1") == f"https://{mirror}/a/status/1")
    check("a domain that merely CONTAINS 'x.com' does not false-positive",
          f("https://box.com/somefile") is None)
    check("every find_video_link result carries a real scheme",
          f("x.com/a/status/1").startswith(("http://", "https://")))


def permission_text_tests():
    print("\nREQUEST MESSAGE")
    txt = ugc.permission_text("https://x.com/a/status/1", "@a")
    check("names the page so the creator knows who is asking", "@aravindnews24" in txt)
    check("asks, rather than announcing", "Could I share" in txt)
    check("promises credit", "credit" in txt.lower())
    check("offers takedown, which is what makes it a fair ask", "take it down" in txt.lower())
    check("includes the video link", "https://x.com/a/status/1" in txt)
    generic = ugc.permission_text("https://www.instagram.com/reel/ABC/")
    check("works with no known handle", "Hi there" in generic)


def gate_tests(tmp):
    print("\nTHE GATE (this is the one that matters)")
    ugc.LEDGER_PATH = os.path.join(tmp, "ledger.json")

    check("nothing is releasable before anything is recorded", not ugc.releasable(None))

    rec = ugc.open_request("https://x.com/filmer/status/77", prompt_message_id=None)
    check("a fresh request starts as awaiting, never granted", rec["status"] == ugc.AWAITING)
    check("a merely-requested video is NOT releasable", not ugc.releasable(rec))
    check("the creator was captured", rec["creator"] == "@filmer")
    check("nothing was downloaded just by asking", rec["video_path"] is None)

    declined = ugc.open_request("https://x.com/nope/status/1")
    ugc.mark_declined(declined["id"])
    check("a video the creator refused is NOT releasable",
          not ugc.releasable(ugc.get(declined["id"])))

    granted = ugc.mark_granted(rec["id"], prompt_message_id=4242, credit_mode=ugc.CREDIT_NAMED)
    check("only after a human records a yes does it become releasable",
          ugc.releasable(granted))
    check("the grant is timestamped", bool(granted.get("granted_at")))
    check("the reply prompt is remembered, so the file ties to THIS grant",
          ugc.by_prompt_message(4242)["id"] == rec["id"])
    check("an unrelated reply matches no grant", ugc.by_prompt_message(9999) is None)
    check("a reply to nothing matches no grant", ugc.by_prompt_message(None) is None)

    ugc.attach_video(rec["id"], os.path.join(tmp, "v.mp4"))
    check("the video is only attached after the grant",
          ugc.get(rec["id"])["video_path"].endswith("v.mp4"))
    ugc.mark_posted(rec["id"])
    check("a posted video stays releasable (it was already agreed)",
          ugc.releasable(ugc.get(rec["id"])))

    check("the ledger survives a reload", ugc.get(rec["id"])["creator"] == "@filmer")
    check("a corrupt/missing ledger reads as empty rather than crashing",
          (setattr(ugc, "LEDGER_PATH", os.path.join(tmp, "nope.json")) or ugc.get("1")) is None)


def credit_tests():
    print("\nCREDIT")
    c = ugc.credit_line({"creator": "@filmer"})
    check("names the creator", "@filmer" in c)
    check("states it was used with permission", "with permission" in c)
    check("falls back when the handle is unknown",
          "original creator" in ugc.credit_line({"creator": None}))
    check("no record -> no credit string", ugc.credit_line(None) == "")


def credit_mode_tests(tmp):
    """Some creators say "use it but don't name me". That is their call."""
    print("\nCREDIT PREFERENCE")
    ugc.LEDGER_PATH = os.path.join(tmp, "cm.json")
    named = ugc.open_request("https://x.com/a/status/1")
    ugc.mark_granted(named["id"], credit_mode=ugc.CREDIT_NAMED)
    check("default is to name the creator", "@a" in ugc.credit_line(ugc.get(named["id"])))

    anon = ugc.open_request("https://x.com/shy/status/2")
    ugc.mark_granted(anon["id"], credit_mode=ugc.CREDIT_NONE)
    rec = ugc.get(anon["id"])
    check("a creator who asked for no credit gets none", ugc.credit_line(rec) == "")
    check("but it is still a granted, postable video", ugc.releasable(rec))
    check("their handle is not leaked anywhere in the caption line",
          "shy" not in ugc.credit_line(rec))


def download_gate_tests(tmp):
    """The download itself refuses ungranted videos -- a second gate, so a
    mistake upstream still cannot pull a file nobody agreed to."""
    print("\nDOWNLOAD GATE")
    ugc.LEDGER_PATH = os.path.join(tmp, "dl.json")
    rec = ugc.open_request("https://x.com/a/status/1")
    try:
        ugc.download(rec, tmp)
        check("downloading an un-granted video is refused", False, "it downloaded!")
    except PermissionError:
        check("downloading an un-granted video is refused", True)
    except Exception as e:
        check("downloading an un-granted video is refused",
              isinstance(e, PermissionError), type(e).__name__)

    ugc.mark_declined(rec["id"])
    try:
        ugc.download(ugc.get(rec["id"]), tmp)
        check("downloading a REFUSED video is refused", False, "it downloaded!")
    except PermissionError:
        check("downloading a REFUSED video is refused", True)


def headline_tests(tmp):
    print("\nHEADLINE CARRIED FROM THE LINK MESSAGE")
    ugc.LEDGER_PATH = os.path.join(tmp, "hl.json")
    rec = ugc.open_request("https://x.com/a/status/1", headline="Bank strike in Chennai today")
    check("the headline typed with the link is kept",
          ugc.get(rec["id"])["headline"] == "Bank strike in Chennai today")
    bare = ugc.open_request("https://x.com/b/status/2")
    check("no headline is fine (the video's own title is used instead)",
          ugc.get(bare["id"])["headline"] == "")

    check("open_request stores an article_url when given one",
          ugc.get(ugc.open_request("https://x.com/c/status/3", article_url="https://news.example/a")["id"])
              ["article_url"] == "https://news.example/a")
    check("article_url defaults to None", ugc.get(bare["id"])["article_url"] is None)


def article_link_tests(tmp):
    """Regression: a real report -- the owner pasted the video link AND
    the matching news article link in one message, and the raw article
    URL ended up stored as the headline verbatim (it would have posted
    with a URL as the headline text). The article's own title must be
    fetched and used instead, and the article kept as the story's real
    source link."""
    print("\nVIDEO LINK + ARTICLE LINK TOGETHER")
    from src import main
    ugc.LEDGER_PATH = os.path.join(tmp, "al.json")
    sent = []
    real = (main.telegram_bot.send_message, main.news_engine.fetch_article_metadata, main.ugc.probe)
    main.telegram_bot.send_message = lambda text, buttons=None, reply_to=None: (sent.append(text) or 1)
    main.news_engine.fetch_article_metadata = lambda url: {
        "title": "Tamil Nadu cop clings to car roof for 30km to nab gutkha gang", "summary": ""}
    main.ugc.probe = lambda u: {"title": "raw video title", "duration": 42,
                                "uploader": "Wilson Thomas", "uploader_id": "wilson__thomas"}
    try:
        link = "https://x.com/wilson__thomas/status/2104167814877806848/video/1?s=46"
        article = ("https://www.newindianexpress.com/amp/story/states/tamil-nadu/2026/Sep/28/"
                   "tamil-nadu-cop-clings-to-car-roof-for-30km-to-nab-gutkha-gang")
        msg = {"message_id": 1, "text": f"{link}\n\n{article}"}
        main._open_ugc_request(ugc.find_video_link(msg["text"]), msg)
        rec = ugc._load()[-1]
        check("the article's REAL title is stored as the headline, not the raw URL",
              rec["headline"] == "Tamil Nadu cop clings to car roof for 30km to nab gutkha gang", rec["headline"])
        check("the raw article URL never ends up as the headline",
              "http" not in rec["headline"])
        check("the article link is kept separately as the source link",
              rec["article_url"] == article)
        check("the owner is shown the real headline, not a raw link",
              "Tamil Nadu cop clings" in sent[-1] and "newindianexpress.com" not in sent[-1].split("Headline")[-1])

        # typed words alongside the two links win over the fetched title
        sent.clear()
        msg2 = {"message_id": 2, "text": f"{link}\n\nCop drags gutkha gang for 30km\n\n{article}"}
        main._open_ugc_request(ugc.find_video_link(msg2["text"]), msg2)
        rec2 = ugc._load()[-1]
        check("the owner's own typed words win over the article's fetched title",
              rec2["headline"] == "Cop drags gutkha gang for 30km", rec2["headline"])
        check("the article link is still captured even when words were typed too",
              rec2["article_url"] == article)

        # a video link with no companion link at all still behaves as before
        sent.clear()
        msg3 = {"message_id": 3, "text": link}
        main._open_ugc_request(ugc.find_video_link(msg3["text"]), msg3)
        rec3 = ugc._load()[-1]
        check("a bare video link with nothing else has no headline or article link",
              rec3["headline"] == "" and rec3["article_url"] is None)
    finally:
        main.telegram_bot.send_message, main.news_engine.fetch_article_metadata, main.ugc.probe = real


def id_collision_tests(tmp):
    """Regression: a real CI failure -- open_request() ids were a millisecond
    timestamp, and two calls close enough together got the SAME id, so
    ugc.get() on the second one silently returned the first record instead
    (wrong headline, and -- since ids are the callback-button payload --
    a tap could have targeted the wrong video)."""
    print("\nID COLLISION")
    ugc.LEDGER_PATH = os.path.join(tmp, "ids.json")
    same_moment = dt.datetime.utcnow()
    a = ugc.open_request("https://x.com/a/status/1", headline="First", now=same_moment)
    b = ugc.open_request("https://x.com/b/status/2", headline="Second", now=same_moment)
    check("two requests opened at the exact same moment get different ids", a["id"] != b["id"], (a["id"], b["id"]))
    check("the first record's headline is unaffected", ugc.get(a["id"])["headline"] == "First")
    check("the second record's headline is looked up correctly, not the first's",
          ugc.get(b["id"])["headline"] == "Second")


def byline_tests():
    """Regression: yt-dlp's X/Twitter extractor formats a raw title as
    "<display name> - <tweet text>". A real report -- the creator's own
    name ended up in a post's headline this way even though "no credit
    wanted" had been chosen, because the name was in the TITLE text
    itself, not the separate credit line that honours anonymity."""
    print("\nBYLINE STRIPPING")
    strip = ugc._strip_byline
    check("a leading display-name byline is stripped",
          strip("Wilson Thomas - Headconstable Velmurugan of Kovilpalayam police station",
                "Wilson Thomas", "wilson__thomas")
          == "Headconstable Velmurugan of Kovilpalayam police station")
    check("a leading @handle byline is stripped when no display name matches",
          strip("wilson__thomas - some raw caption", None, "wilson__thomas")
          == "some raw caption")
    check("a title with no byline prefix is left untouched",
          strip("Headconstable Velmurugan saves a child from a fire", "Wilson Thomas", "wilson__thomas")
          == "Headconstable Velmurugan saves a child from a fire")
    check("a name that only appears mid-title (not as a byline prefix) is left alone",
          strip("Report: Wilson Thomas filmed the incident", "Wilson Thomas", "wilson__thomas")
          == "Report: Wilson Thomas filmed the incident")
    check("empty title stays empty", strip("", "Wilson Thomas", "wilson__thomas") == "")


def repost_guard_tests(tmp):
    """Regression: no visible reply after the first tap on 'Approved -- no
    credit wanted' led to the SAME button being pressed several more
    times, and each tap re-downloaded and re-posted the identical video
    (five separate posts of one clip on the live page). Once a decision
    is on file, a later tap on any of its buttons must be a no-op."""
    print("\nRE-TAP GUARD")
    from src import main
    ugc.LEDGER_PATH = os.path.join(tmp, "repost.json")
    rec = ugc.open_request("https://x.com/a/status/1", headline="Some headline")
    ugc.mark_posted(rec["id"])

    answers, sent, posts = [], [], []
    real_answer, real_send, real_post = (main.telegram_bot.answer_callback,
                                         main.telegram_bot.send_message, main._post_permitted_video)
    main.telegram_bot.answer_callback = lambda cb_id, text="": answers.append(text)
    main.telegram_bot.send_message = lambda text, buttons=None, reply_to=None: (sent.append(text) or 1)
    main._post_permitted_video = lambda r, now: posts.append(r["id"])
    try:
        main._handle_ugc_decision({"id": "cb1"}, f"UGCANON|{rec['id']}")
        check("re-tapping an already-posted request does not post again", posts == [])
        check("re-tapping an already-posted request tells the owner it's already done",
              answers and "already" in answers[-1].lower(), answers)
        check("...and says so in a real chat message, not just the toast",
              sent and "already" in sent[-1].lower(), sent)

        rec2 = ugc.open_request("https://x.com/a/status/2", headline="Another headline")
        ugc.mark_declined(rec2["id"])
        answers.clear(); sent.clear()
        main._handle_ugc_decision({"id": "cb2"}, f"UGCOK|{rec2['id']}")
        check("re-tapping an already-declined request does not post it after all", posts == [])
        check("re-tapping an already-declined request tells the owner it's already recorded",
              answers and "declined" in answers[-1].lower(), answers)
        check("...and says so in a real chat message, not just the toast",
              sent and "declined" in sent[-1].lower(), sent)
    finally:
        main.telegram_bot.answer_callback, main.telegram_bot.send_message, main._post_permitted_video = (
            real_answer, real_send, real_post)


if __name__ == "__main__":
    link_tests()
    permission_text_tests()
    with tempfile.TemporaryDirectory() as t:
        gate_tests(t)
    credit_tests()
    with tempfile.TemporaryDirectory() as t:
        credit_mode_tests(t)
    with tempfile.TemporaryDirectory() as t:
        download_gate_tests(t)
    with tempfile.TemporaryDirectory() as t:
        headline_tests(t)
    with tempfile.TemporaryDirectory() as t:
        article_link_tests(t)
    with tempfile.TemporaryDirectory() as t:
        id_collision_tests(t)
    byline_tests()
    with tempfile.TemporaryDirectory() as t:
        repost_guard_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
