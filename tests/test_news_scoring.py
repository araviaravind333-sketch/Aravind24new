"""
News Selection Scoring -- Regression Tests
===========================================
This exists because of a real, observed failure, not a hypothetical one.

A live run of this pipeline's own selection logic (see the incident-photo
demo run in this project's history) showed political point-scoring
headlines -- "AIADMK Slams Tamil Nadu CM Vijay Over Secretariat Plans",
"Narendra's Ongoing Trump Appeasement: Congress's swipe" -- and pure court
procedure updates ("High Court Seeks Status Report") dominating a day's
selections ahead of genuine incidents. That happened because "slams" /
"row" / "backlash" / "controversy" were scored as generic shareability
signals, and "high court" / "cbi" as generic national-impact signals,
with nothing distinguishing "a real event described dramatically" from
"one party reacting to another with no event at all".

These tests assert the ordering that actually matters: a real incident
must outscore a political reaction or a procedural update, not just
score positively in isolation. They are the guard against this
regressing back in a future keyword-list edit.

    python -m tests.test_news_scoring
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import news_engine as ne

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


NOW = dt.datetime.now(dt.timezone.utc)


def score(title):
    return ne._virality(title, NOW)


def run():
    print("NEWS SCORING")

    # The two actual headlines observed dominating a live run.
    s1, h1 = score("AIADMK Slams Tamil Nadu CM Vijay Over Secretariat Plans")
    s2, h2 = score("Narendra's Ongoing Trump Appeasement: Congress's swipe")
    check("political point-scoring headline scores negative, not positive",
          s1 < 0, f"score={s1}")
    check("political reaction is never flagged as a hot/breaking hit",
          not h1 and not h2)

    s3, _ = score("Gulzar Singh Suicide Case: High Court Seeks Status Report")
    s4, _ = score("Massive fire guts Chennai godown, three dead")
    check("a procedural court update scores well below a real incident",
          s3 < s4 - 20, f"procedural={s3} incident={s4}")

    # The actual invariant that matters: incidents must outrank both
    # failure patterns, not just clear zero individually.
    incident_score, incident_hot = score("Building collapses in Mumbai, several trapped")
    check("a real incident is flagged as a hot hit", incident_hot)
    check("a real incident outscores political reaction by a wide margin",
          incident_score > s1 + 30, f"incident={incident_score} reaction={s1}")
    check("a real incident outscores routine court procedure by a wide margin",
          incident_score > s3 + 15, f"incident={incident_score} procedural={s3}")

    # Guard against the exact substring bug fixed earlier ("row" matching
    # inside "Narrows") re-entering through the new keyword lists.
    unrelated, _ = score("Company narrows losses in third quarter")
    check("a word merely CONTAINING a penalty keyword is not penalised for it",
          not ne._is_political_reaction("Company narrows losses in third quarter"))

    # A genuinely shareable, non-political curiosity story must still
    # score well -- the fix should not have collapsed shareability
    # scoring generally, only the political-reaction/procedural subset.
    viral_score, viral_hot = score("Viral video shows shocking moment bridge gives way")
    check("genuine viral/shocking language still scores well",
          viral_score > 20 and viral_hot, f"score={viral_score}")

    # A real terror/violent attack must not be caught by the political
    # "attack"-adjacent penalty wording -- POLITICAL_REACTION_KEYWORDS
    # deliberately does not include the bare word "attack".
    attack_score, attack_hot = score("Terror attack near border post leaves soldiers dead")
    check("a real attack story is not penalised as political reaction",
          not ne._is_political_reaction("Terror attack near border post leaves soldiers dead")
          and attack_hot)


if __name__ == "__main__":
    run()
    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
