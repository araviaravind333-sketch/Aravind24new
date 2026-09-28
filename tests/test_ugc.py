"""
UGC permission ledger -- Tests
==============================
The rule being tested is the one that protects the account: a video
filmed by someone else is only ever postable after a HUMAN has recorded
that its creator said yes. Credit alone is not permission, and a page
that reposts without it collects Instagram strikes until it is deleted.

    python -m tests.test_ugc
"""

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
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
