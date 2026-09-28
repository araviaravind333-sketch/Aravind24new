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

    granted = ugc.mark_granted(rec["id"], prompt_message_id=4242)
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


if __name__ == "__main__":
    link_tests()
    permission_text_tests()
    with tempfile.TemporaryDirectory() as t:
        gate_tests(t)
    credit_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
