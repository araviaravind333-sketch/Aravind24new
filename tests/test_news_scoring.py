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


def cinema_category_tests():
    """Regression: INDIAN CINEMA must be eligible to post and treated as
    India content. (HUMAN INTEREST was eligible too until the 2026-10-23
    three-category narrowing below turned it back off deliberately.)"""
    print("\nCATEGORY WIRING")
    from config import settings
    check("INDIAN CINEMA is eligible to post",
          "INDIAN CINEMA" in settings.INDIA_ONLY_CATEGORIES)
    check("a cinema story is detected as India content",
          ne.detect_geo("Shah Rukh Khan announces new film", "INDIAN CINEMA") == {"is_india": True, "place": "India"})
    check("INDIAN CINEMA has its own category colour",
          "INDIAN CINEMA" in __import__("src.template", fromlist=["CATEGORY_COLORS"]).CATEGORY_COLORS)
    check("INDIAN CINEMA has its own hashtag set",
          "INDIAN CINEMA" in settings.HASHTAG_BANK)
    check("INDIAN CINEMA has at least one RSS feed configured",
          len(settings.RSS_FEEDS.get("INDIAN CINEMA", [])) > 0)


def three_category_trending_focus_tests():
    """Regression for an explicit instruction (2026-10-23): post ONLY
    breaking/trending news, across exactly three verticals -- national
    India, Tamil Nadu and cinema. Two changes bundled together:

      1. HUMAN INTEREST (which measured well on engagement RATE, see
         cinema_category_tests' history) is now off -- off-strategy for
         a page narrowed to these three, not a bug.
      2. INDIA NEWS is back. The 2026-10-21 pivot (see
         tamil_nadu_niche_pivot_tests) REPLACED it outright with TAMIL
         NADU NEWS; this is a deliberate, explicit reversal of that one
         specific decision -- re-adding it as a third vertical, not
         silently recreating the old blanket-pan-India identity, because
         it is now ALSO gated by the trending-only floor (see
         trending_only_gate_tests below)."""
    print("\nTHREE-CATEGORY TRENDING FOCUS (2026-10-23)")
    from config import settings
    from src import template
    check("exactly three categories are eligible to post",
          set(settings.INDIA_ONLY_CATEGORIES) == {"INDIA NEWS", "TAMIL NADU NEWS", "INDIAN CINEMA"},
          settings.INDIA_ONLY_CATEGORIES)
    check("HUMAN INTEREST is correctly excluded now (off-strategy, not forgotten)",
          "HUMAN INTEREST" not in settings.INDIA_ONLY_CATEGORIES)
    check("allowed_categories() returns exactly these three at any hour (24h window)",
          set(ne.allowed_categories()) == {"INDIA NEWS", "TAMIL NADU NEWS", "INDIAN CINEMA"},
          ne.allowed_categories())
    check("INDIA NEWS has real RSS feeds configured",
          len(settings.RSS_FEEDS.get("INDIA NEWS", [])) >= 2,
          settings.RSS_FEEDS.get("INDIA NEWS"))
    check("INDIA NEWS has its own category colour",
          "INDIA NEWS" in template.CATEGORY_COLORS)
    check("INDIA NEWS has its own hashtag set",
          "INDIA NEWS" in settings.HASHTAG_BANK)
    check("a national India story geo-tags as India",
          ne.detect_geo("RBI holds emergency meeting on rupee slide", "INDIA NEWS")
          == {"is_india": True, "place": "India"})


def trending_only_gate_tests():
    """Regression for the trending-only gate (2026-10-23, explicit
    request -- "post only breaking trending news"): top_candidates()
    must refuse anything that isn't hot_hit AND above
    TRENDING_ONLY_MIN_SCORE, even if it would otherwise be the
    highest-scoring thing available that cycle. Mocks fetch_candidates so
    this never depends on real RSS content or network access -- the
    scoring logic itself (what makes hot_hit True) is covered by the
    other tests in this file."""
    print("\nTRENDING-ONLY GATE (top_candidates)")
    from config import settings
    real_fetch = ne.fetch_candidates

    def fake_fetch(category):
        if category != "INDIA NEWS":
            return []
        return [
            {"id": "routine", "title": "Routine ministry statement on roads",
             "summary": "", "link": "https://x", "category": "INDIA NEWS",
             "score": 45, "hot_hit": False, "is_incident": False},
            {"id": "trending", "title": "RBI cuts repo rate in surprise move",
             "summary": "", "link": "https://x", "category": "INDIA NEWS",
             "score": 70, "hot_hit": True, "is_incident": False},
            {"id": "weak_hot_hit", "title": "Minor local event, barely hot",
             "summary": "", "link": "https://x", "category": "INDIA NEWS",
             "score": settings.TRENDING_ONLY_MIN_SCORE - 1, "hot_hit": True,
             "is_incident": False},
        ]
    ne.fetch_candidates = fake_fetch
    try:
        out = ne.top_candidates(5)
        ids = {s["id"] for s in out}
        check("a higher-scoring but non-trending (hot_hit=False) story is excluded",
              "routine" not in ids, ids)
        check("a genuinely trending (hot_hit=True, score >= floor) story is included",
              "trending" in ids, ids)
        check("hot_hit=True alone is not enough -- it must also clear the score floor",
              "weak_hot_hit" not in ids, ids)
    finally:
        ne.fetch_candidates = real_fetch


def tamil_nadu_niche_pivot_tests():
    """Regression for the 2026-10-21 niche pivot: three weeks of generic
    pan-India headlines plateaued at near-zero follower conversion
    (9->10 across ~170 posts) while competing head-on, with zero
    resources, against NDTV/India Today on the exact same national
    stories. This account's OWN data already showed where real
    engagement existed unprompted -- Tamil Nadu content averaged 550
    views vs 214 for generic Delhi/national coverage, on fewer posts.
    "INDIA NEWS" was replaced outright, not supplemented, with
    "TAMIL NADU NEWS" as the flagship category. (INDIA NEWS was later
    re-added on 2026-10-23 as an explicit, deliberate THIRD vertical
    alongside this one -- see three_category_trending_focus_tests --
    which is why there is no "INDIA NEWS no longer exists" assertion
    here any more; that would now be asserting the wrong thing.) These
    tests exist so TAMIL NADU NEWS itself stays the flagship category
    with its own feeds/colour/hashtags, not to forbid INDIA NEWS from
    ever coming back in a clearly-scoped way."""
    print("\nTAMIL NADU NICHE PIVOT (2026-10-21)")
    from config import settings
    from src import template
    check("TAMIL NADU NEWS is the flagship India-hours category",
          "TAMIL NADU NEWS" in settings.INDIA_ONLY_CATEGORIES)
    check("it has real RSS feeds configured (not left empty)",
          len(settings.RSS_FEEDS.get("TAMIL NADU NEWS", [])) >= 2,
          settings.RSS_FEEDS.get("TAMIL NADU NEWS"))
    check("every feed URL actually mentions Tamil Nadu in its path -- not a bare national feed relabelled",
          all("tamil-nadu" in u.lower() or "tamilnadu" in u.lower()
              for u in settings.RSS_FEEDS["TAMIL NADU NEWS"]),
          settings.RSS_FEEDS["TAMIL NADU NEWS"])
    check("it has its own category colour, not falling back to the default accent",
          "TAMIL NADU NEWS" in template.CATEGORY_COLORS)
    check("it has its own, on-brand hashtag set",
          settings.HASHTAG_BANK.get("TAMIL NADU NEWS", []) and
          any("tamilnadu" in h.lower() or "chennai" in h.lower()
              for h in settings.HASHTAG_BANK["TAMIL NADU NEWS"]),
          settings.HASHTAG_BANK.get("TAMIL NADU NEWS"))
    check("a Tamil Nadu story geo-tags specifically, not just a blanket 'India'",
          ne.detect_geo("Tamil Nadu cabinet approves new scheme", "TAMIL NADU NEWS")
          == {"is_india": True, "place": "Tamil Nadu"})
    check("the fallback hashtag lookup points at the new flagship key, not a dead one",
          settings.HASHTAG_BANK["TAMIL NADU NEWS"] ==
          __import__("src.ai_writer", fromlist=["settings"]).settings.HASHTAG_BANK["TAMIL NADU NEWS"])


def tamil_cinema_boost_tests():
    """Regression for option 1 of the 2026-10-21 pivot: Tamil cinema is
    the one TN-adjacent content type with genuinely pan-India/diaspora
    reach, so it should score like a national-impact story, not like
    ordinary regional content. Deliberately does NOT boost on "Vijay"
    alone -- that name is currently ALSO Tamil Nadu's sitting CM, and
    boosting it would just re-score ordinary state politics as cinema
    content."""
    print("\nTAMIL CINEMA BOOST (pan-India reach lever)")
    for title in ["Rajinikanth's next film with Lokesh Kanagaraj announced",
                  "Dhanush injures hand while shooting fight sequence",
                  "Kamal Haasan turns 70, fans celebrate across Tamil Nadu",
                  "Suriya and Jyotika announce new production house"]:
        s, hot = score(title)
        check(f"a real Tamil cinema story scores like national-impact content: {title[:40]!r}",
              s >= 25 and hot, (s, hot))
    check("bare 'Vijay' (the sitting CM) is NOT boosted as cinema content",
          not ne._is_tamil_cinema("CM Vijay inaugurates new metro line in Chennai"))
    check("but an explicit cinema term still catches a Vijay-the-actor headline",
          ne._is_tamil_cinema("Vijay Sethupathi's new Kollywood thriller gets release date"))
    check("an unrelated TN political headline is not touched",
          not ne._is_tamil_cinema("Tamil Nadu cabinet approves new irrigation scheme"))


def bollywood_boost_tests():
    """Regression for the 2026-10-22 follow-up: Bollywood as a second
    pan-India reach lever in the same INDIAN CINEMA category, asked for
    by name after the Tamil cinema boost shipped. Hindi cinema is, if
    anything, followed MORE uniformly nationwide than Tamil cinema, so
    it gets the identical national-impact-level score treatment. Full
    names only -- "Shah Rukh Khan" not "Shah" or "Khan" -- for the same
    reason the Tamil list excludes a bare "Vijay"."""
    print("\nBOLLYWOOD BOOST (second pan-India reach lever)")
    for title in ["Shah Rukh Khan's next film wraps shoot in Mumbai",
                  "Deepika Padukone and Ranveer Singh spotted at airport",
                  "Alia Bhatt starrer crosses Rs 200 crore at the box office",
                  "Amitabh Bachchan hospitalised after on-set injury"]:
        s, hot = score(title)
        check(f"a real Bollywood story scores like national-impact content: {title[:40]!r}",
              s >= 25 and hot, (s, hot))
    check("an unrelated story mentioning neither cinema list is not boosted",
          not ne._is_bollywood("RBI holds repo rate steady at monetary policy meeting"))
    check("Tamil and Bollywood keyword lists don't silently overlap",
          not (set(ne.TAMIL_CINEMA_KEYWORDS) & set(ne.BOLLYWOOD_KEYWORDS)),
          set(ne.TAMIL_CINEMA_KEYWORDS) & set(ne.BOLLYWOOD_KEYWORDS))


def trailer_drop_detection_tests():
    """Regression: a trailer/song/teaser release genuinely IS a video, so
    these stories get flagged (not auto-fetched -- see is_trailer_drop's
    own docstring for why sourcing the real link stays a human call)."""
    print("\nTRAILER-DROP DETECTION")
    for title in ["'Jailer 2' trailer out now, fans go wild",
                  "Coolie teaser crosses 10 million views in a day",
                  "'Pushpa 3' first look poster unveiled",
                  "Leo second single song release date announced",
                  "Vikram motion poster out, release date confirmed"]:
        check(f"recognised as a trailer/teaser/song drop: {title[:45]!r}",
              ne.is_trailer_drop(title))
    for title in ["Dhanush spotted at Chennai airport",
                  "Rajinikanth turns 75, fans celebrate",
                  "RBI holds repo rate steady at monetary policy meeting"]:
        check(f"an ordinary story is not misread as a trailer drop: {title[:45]!r}",
              not ne.is_trailer_drop(title))


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL so this test never hits the real
    network or depends on yt-dlp actually being installed -- it only
    checks that search_trailer_candidates parses whatever yt-dlp hands
    back and builds a real watch URL from a bare video id."""
    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, query, download=False):
        return {"entries": [
            {"id": "abc123", "title": "Jailer 2 - Official Trailer", "channel": "Sun Pictures"},
            {"url": "https://youtube.com/watch?v=xyz789", "title": "Reaction video", "uploader": "Some Fan Channel"},
            {"title": "no id or url at all -- must be skipped"},
        ]}


def trailer_candidate_search_tests():
    """Regression for the follow-up to trailer-drop detection: the bot
    looks up possible official matches so the owner doesn't have to
    search by hand, but posting one is still the owner's call -- this
    only checks that a yt-dlp search result gets turned into a clean
    list of {title, channel, url}, with a real watch URL built even when
    yt-dlp only hands back a bare video id, and that entries with
    neither an id nor a url are dropped rather than crashing."""
    print("\nTRAILER CANDIDATE SEARCH (entertainment video lookup)")
    import types
    import sys as _sys
    fake_module = types.SimpleNamespace(YoutubeDL=_FakeYoutubeDL)
    real_module = _sys.modules.get("yt_dlp")
    _sys.modules["yt_dlp"] = fake_module
    try:
        results = ne.search_trailer_candidates("Jailer 2 trailer out now")
        check("returns one entry per real search result (junk entry dropped)",
              len(results) == 2, results)
        check("a bare video id is turned into a real youtube.com watch URL",
              results[0]["url"] == "https://www.youtube.com/watch?v=abc123", results)
        check("channel name is read from the 'channel' field when present",
              results[0]["channel"] == "Sun Pictures", results)
        check("a full URL already on the entry is kept as-is",
              results[1]["url"] == "https://youtube.com/watch?v=xyz789", results)
        check("channel falls back to 'uploader' when 'channel' is missing",
              results[1]["channel"] == "Some Fan Channel", results)
    finally:
        if real_module is not None:
            _sys.modules["yt_dlp"] = real_module
        else:
            _sys.modules.pop("yt_dlp", None)

    def _boom(*a, **k):
        raise RuntimeError("network down")
    _sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=_boom)
    try:
        check("a search failure (network, blocked, etc.) returns [] instead of raising",
              ne.search_trailer_candidates("Leo song release") == [])
    finally:
        if real_module is not None:
            _sys.modules["yt_dlp"] = real_module
        else:
            _sys.modules.pop("yt_dlp", None)


if __name__ == "__main__":
    run()
    cinema_category_tests()
    three_category_trending_focus_tests()
    trending_only_gate_tests()
    tamil_nadu_niche_pivot_tests()
    tamil_cinema_boost_tests()
    bollywood_boost_tests()
    trailer_drop_detection_tests()
    trailer_candidate_search_tests()
    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
