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
        check("deleteWebhook is called before getUpdates",
              [m for m, _ in calls] == ["deleteWebhook", "getUpdates"], calls)
        check("getUpdates is still called with the right offset",
              calls[1][1]["offset"] == 123, calls)
        check("deleteWebhook does not drop already-queued updates",
              calls[0][1]["drop_pending_updates"] is False, calls)
    finally:
        tb._call = real_call


if __name__ == "__main__":
    webhook_guard_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
