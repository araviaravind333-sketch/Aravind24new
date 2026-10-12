"""
Posting-window scheduling -- Tests
==================================
    python -m tests.test_scheduling
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from src import main

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def _held(hour):
    """The same comparison telegram_cycle() makes, against one IST hour."""
    return settings.DEAD_HOURS_IST[0] <= hour < settings.DEAD_HOURS_IST[1]


def dead_window_tests():
    """This account's own measured numbers (99 mature posts, category and
    age controlled): 15:00-20:59 IST had a MEDIAN of zero views and was
    taking more slots than any other window, while 21:00-05:00 had
    medians of 104-163. Routine posts are held through the dead window so
    those slots land where they actually reach people."""
    print("\nDEAD WINDOW")
    for h in (15, 17, 20):
        check(f"{h:02d}:00 IST is held (measured median 0 views)", _held(h))
    for h in (0, 5, 9, 14, 21, 23):
        check(f"{h:02d}:00 IST still posts", not _held(h))
    check("the window is a half-open range -- 21:00 is the first hour back on",
          _held(20) and not _held(21))
    check("the window never swallows the whole day",
          sum(1 for h in range(24) if _held(h)) < 12,
          sum(1 for h in range(24) if _held(h)))


def urgent_not_gated_tests():
    """Breaking/urgent/owner-submitted posts must NEVER be held by the
    dead window -- they are time-sensitive and human-chosen, and delaying
    them by hours would defeat the point of having a fast path at all.
    Only the routine automated queue is paced this way."""
    print("\nURGENT PATH IS EXEMPT")
    for fn_name in ("_post_permitted_video", "_poll_telegram_replies"):
        src = inspect.getsource(getattr(main, fn_name))
        check(f"{fn_name} does not consult the dead window",
              "DEAD_HOURS" not in src)
    check("the routine queue DOES consult it",
          "DEAD_HOURS_IST" in inspect.getsource(main.telegram_cycle))


if __name__ == "__main__":
    dead_window_tests()
    urgent_not_gated_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
