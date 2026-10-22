"""
Telegram polling safety -- Tests
================================
    python -m tests.test_telegram_bot
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import telegram_bot as tb

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def webhook_guard_tests():
    """Regression: a real incident -- a webhook got registered against the
    bot token this polling pipeline uses, and Telegram silently routed
    every update there instead of queuing it for getUpdates. No error,
    just an empty result forever (over a day, in production, while real
    button taps and messages piled up unseen). get_new_updates() must
    call deleteWebhook before every getUpdates so this can't happen
    again even if something re-registers a webhook later."""
    print("\nWEBHOOK GUARD")
    calls = []
    real_call = tb._call
    tb._call = lambda method, params=None: (calls.append((method, params)) or [])
    try:
        tb.get_new_updates(123)
        methods = [m for m, _ in calls]
        check("deleteWebhook is called before getUpdates",
              methods.index("deleteWebhook") < methods.index("getUpdates"), methods)
        check("getUpdates is still called with the right offset",
              calls[1][1]["offset"] == 123, calls)
        check("deleteWebhook does not drop already-queued updates",
              calls[0][1]["drop_pending_updates"] is False, calls)
    finally:
        tb._call = real_call


def allowed_updates_tests():
    """Regression: second outage, same silent shape -- pasted links kept
    arriving but every button tap vanished. Telegram's getUpdates says of
    allowed_updates "If not specified, the previous setting will be
    used", and this pipeline never specified it, so a restriction set
    once (by the webhook registration) silently became permanent and
    dropped every callback_query. It must always be sent explicitly, and
    must always include callback_query -- that IS every review button."""
    print("\nALLOWED UPDATES")
    calls = []
    real_call = tb._call
    tb._call = lambda method, params=None: (calls.append((method, params)) or [])
    try:
        tb.get_new_updates(1)
        params = next(p for m, p in calls if m == "getUpdates")
        check("getUpdates always states allowed_updates explicitly",
              "allowed_updates" in params, params)
        check("button taps (callback_query) are always allowed",
              "callback_query" in params.get("allowed_updates", []), params)
        check("plain messages are still allowed",
              "message" in params.get("allowed_updates", []), params)
        check("an empty poll asks Telegram what it thinks is queued",
              any(m == "getWebhookInfo" for m, _ in calls), [m for m, _ in calls])
    finally:
        tb._call = real_call


def trailer_drop_nudge_tests():
    """Regression for a real, sharp observation (2026-10-22): entertainment
    is the one news category where a text card is an especially flat
    substitute for the real thing, since a trailer/teaser/song release
    genuinely IS a video the studio just put out to be shared. The
    candidate card must nudge the owner to go find the studio's OWN
    official link when (and only when) a story is actually that kind of
    drop -- never auto-fetched, since that would just be scraping at a
    different layer (see news_engine.is_trailer_drop's docstring)."""
    print("\nTRAILER-DROP NUDGE (entertainment candidate cards)")
    calls = []
    real_call = tb._call
    tb._call = lambda method, params=None: (calls.append(params) or {"message_id": 1})
    try:
        tb.send_candidate({"category": "INDIAN CINEMA", "score": 90,
                           "title": "Jailer 2 trailer out now, fans go wild",
                           "summary": "", "link": "https://x"})
        check("a trailer-drop story gets the nudge to find the official link",
              "official" in calls[-1]["text"].lower(), calls[-1]["text"])

        calls.clear()
        tb.send_candidate({"category": "INDIAN CINEMA", "score": 80,
                           "title": "Dhanush spotted at Chennai airport",
                           "summary": "", "link": "https://x"})
        check("an ordinary cinema story (no trailer/teaser) is not nudged",
              "official" not in calls[-1]["text"].lower(), calls[-1]["text"])

        calls.clear()
        tb.send_candidate({"category": "TAMIL NADU NEWS", "score": 70,
                           "title": "Tamil Nadu cabinet approves new scheme",
                           "summary": "", "link": "https://x"})
        check("a non-cinema story is never nudged, even at a high score",
              "official" not in calls[-1]["text"].lower(), calls[-1]["text"])
    finally:
        tb._call = real_call


if __name__ == "__main__":
    webhook_guard_tests()
    allowed_updates_tests()
    trailer_drop_nudge_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
