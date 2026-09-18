"""
Incident Photo Engine -- Tests
==============================
Two groups, and the first one is the important one.

CRITICAL FAILURE TESTS (1-6) are the named cases this system exists to
get right. They are all about the same thing: refusing a photograph that
looks plausible but is not this event. Every one of them must keep
passing; if one ever fails, the engine is publishing wrong pictures of
real events to a real audience, which is the exact harm that started
this work. Two of them genuinely failed when first written and exposed
real bugs (generic keyword overlap being accepted as an event match, and
ISO timestamps with fractional seconds silently failing to parse).

The rest cover the deep-search, clustering, rights, retry, database and
dashboard layers added on top. None of these tests touch the network:
candidates are constructed directly, so the suite is deterministic and
runs offline.

    python -m tests.test_incident_photos
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import incident_photos as ip

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def _cand(caption, tier="gdelt", published_at=None, url="https://x.test/a.jpg",
          source="https://x.test/story", license=None):
    return {"image_url": url, "source_url": source, "source_name": "x.test",
            "caption": caption, "published_at": published_at, "tier": tier,
            "explicit_license": license}


NOW = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.timezone.utc)


# ============================================================
# CRITICAL FAILURE TESTS
# ============================================================

def critical_tests():
    print("\nCRITICAL FAILURE TESTS")

    # 1. A different fire is not this fire. Same event type, same country,
    #    different incident -- the single most likely way to publish a
    #    wrong photo, because the picture "looks right".
    e = ip.extract_entities("Massive fire guts Chennai godown, three dead", "", NOW)
    v = ip.verify(_cand("Fire breaks out at Mumbai chemical plant", published_at=NOW.isoformat()), e, NOW)
    check("1. different fire in a different city is rejected",
          v["decision"] == "REJECT", v["reason"])

    # 2. Same name, different person. "Rajesh Kumar arrested" must not
    #    attach a photo of some other Rajesh Kumar.
    e = ip.extract_entities("Businessman Suresh Nair arrested in Kochi fraud case", "", NOW)
    v = ip.verify(_cand("Cricketer Arjun Menon at a felicitation event", published_at=NOW.isoformat()), e, NOW)
    check("2. story about a named person + photo of someone else is rejected",
          v["decision"] == "REJECT", v["reason"])

    # 3. Generic overlap is not a match. This is the bug that shipped:
    #    "World Championship" shared two whole words with an archery
    #    story and a logo graphic was accepted as the incident photo.
    e = ip.extract_entities("Indian archer wins World Championship gold in Nimes", "", NOW)
    v = ip.verify(_cand("World Championship official logo", published_at=NOW.isoformat()), e, NOW)
    check("3. only-generic word overlap is not an event match",
          v["decision"] == "REJECT" and not v["event_match"], v["reason"])

    # 4. A file photo is detected and never treated as today's event,
    #    even though the publisher attached it to this exact article.
    e = ip.extract_entities("Cyclone alert issued for Odisha coast", "", NOW)
    v = ip.verify(_cand("Cyclone damage in Puri. File photo", tier="article_og_image",
                        published_at=NOW.isoformat()), e, NOW)
    check("4. publisher-labelled file photo is flagged, not published",
          v["is_file_photo"] and v["decision"] == "FILE_PHOTO", str(v))

    # 5. An old photograph of the same place is not a photo of today's
    #    event. Time is a verification axis, not a nice-to-have.
    e = ip.extract_entities("Bridge collapses in Bihar, rescue under way", "", NOW)
    old = (NOW - dt.timedelta(days=400)).isoformat()
    v = ip.verify(_cand("Bihar bridge collapse rescue operation", published_at=old), e, NOW)
    check("5. year-old photo of the same subject is not APPROVEd",
          v["decision"] != "APPROVE" and not v["time_match"], str(v))

    # 6. Unknown provenance never auto-publishes, however good the photo
    #    looks. Authenticity and rights are separate gates and BOTH must
    #    pass -- this is the test that keeps them separate.
    lic = ip.classify_license("https://randomblog.example/pic.jpg",
                              "https://randomblog.example/post")
    check("6. unknown-provenance image is never auto-publishable",
          lic["license_status"] == ip.UNKNOWN_LICENSE and not ip.is_reusable(lic["license_status"]),
          str(lic))


# ============================================================
# RIGHTS / LICENSING
# ============================================================

def licensing_tests():
    print("\nLICENSING")
    pub = ip.classify_license("https://akm-img-a-in.tosshub.com/x.jpg",
                              "https://www.indiatoday.in/story/raid")
    check("publisher photo -> LICENSE_REQUIRED, not reusable",
          pub["license_status"] == ip.LICENSE_REQUIRED and not ip.is_reusable(pub["license_status"]))

    gov = ip.classify_license("https://cdn.pib.gov.in/x.jpg", "https://pib.gov.in/release")
    check("government photo -> OFFICIAL_REUSE_ALLOWED, not blanket public domain",
          gov["license_status"] == ip.OFFICIAL_REUSE_ALLOWED
          and gov["license_status"] != ip.PUBLIC_DOMAIN
          and gov["attribution_required"] is True, str(gov))

    cc = ip.classify_license("https://upload.wikimedia.org/x.jpg",
                             "https://commons.wikimedia.org/x", explicit_license="by-sa")
    check("explicit CC licence -> CREATIVE_COMMONS + attribution required",
          cc["license_status"] == ip.CREATIVE_COMMONS and cc["attribution_required"])

    check("reusable set is exactly the permissive licences",
          all(ip.is_reusable(s) for s in (ip.FREE_REUSE, ip.PUBLIC_DOMAIN,
                                          ip.CREATIVE_COMMONS, ip.OFFICIAL_REUSE_ALLOWED,
                                          ip.OWNER_CONTROLLED))
          and not any(ip.is_reusable(s) for s in (ip.LICENSE_REQUIRED, ip.UNKNOWN_LICENSE,
                                                  ip.PERMISSION_REQUIRED, ip.DO_NOT_USE)))


# ============================================================
# IMAGE AGE
# ============================================================

def age_tests():
    print("\nIMAGE AGE")
    cases = [(0.2, "BREAKING"), (3, "VERY_RECENT"), (12, "SAME_DAY"),
             (48, "RECENT"), (240, "OLD")]
    ok = True
    for hours, expect in cases:
        got = ip.age_bucket((NOW - dt.timedelta(hours=hours)).isoformat(), NOW)
        ok = check(f"{hours}h old -> {expect}", got == expect, f"got {got}") and ok
    check("missing timestamp -> UNKNOWN", ip.age_bucket(None, NOW) == "UNKNOWN")
    check("ISO with fractional seconds parses (regression: silently failed before)",
          ip.age_bucket("2026-09-16T11:30:00.123456Z", NOW) == "BREAKING")


# ============================================================
# SAME-EVENT CLUSTERING
# ============================================================

def clustering_tests():
    print("\nSAME-EVENT CLUSTERING")
    a = ip.extract_entities("Massive fire guts Chennai godown, three dead", "", NOW)
    b = ip.extract_entities("Three dead as fire guts godown in Chennai", "", NOW)
    c = ip.extract_entities("Two arrested in Chennai raid on jewellery shop", "", NOW)
    ha = "Massive fire guts Chennai godown, three dead"
    ida = ip.make_event_id(a)
    known = [(ida, ha)]
    idb = ip.make_event_id(b, known)
    idc = ip.make_event_id(c, known)
    check("two outlets wording the same fire differently share an event_id",
          ida == idb, f"{ida} vs {idb}")
    check("a different event that day gets a different event_id", ida != idc, f"{ida} vs {idc}")
    check("event_id is human-readable, dated and sequenced",
          ida == "IN-CHENNAI-FIRE-2026-09-16-001", ida)
    check("keyword overlap decides sameness, not identical wording",
          ip.same_event(ip.event_keywords(a), ip.event_keywords(b))
          and not ip.same_event(ip.event_keywords(a), ip.event_keywords(c)))


# ============================================================
# RIGHTS-TARGETED ALTERNATIVE SEARCH
# ============================================================

def deep_search_tests():
    print("\nALTERNATIVE SOURCE SEARCH")
    e = ip.extract_entities("Massive fire guts Chennai godown, three dead", "", NOW)
    qs = ip.build_rights_queries(e)
    check("alternative queries target officially-sourced photos",
          any("official" in q for q in qs) and any("police" in q for q in qs), str(qs))
    check("alternative queries stay on the same event",
          all("chennai" in q or "fire" in q for q in qs), str(qs))
    check("official domains sort ahead of publishers",
          ip._domain_class("https://tn.gov.in/x") == "official"
          and ip._domain_class("https://www.thehindu.com/x") == "publisher"
          and ip._domain_class("https://blog.example/x") == "other")


# ============================================================
# STATE MACHINE  (authenticity x rights)
# ============================================================

def state_tests():
    print("\nDECISION STATES")

    def rec(authentic=True, lic=ip.LICENSE_REQUIRED, file_photo=False, event=True):
        return {"authentic": authentic, "license_status": lic, "is_file_photo": file_photo,
                "event_match": event, "reason": "r", "license_name": "n",
                "reusable": ip.is_reusable(lic), "confidence": 0.9}

    check("authentic + free -> VERIFIED_FREE_IMAGE / AUTO_PUBLISH",
          ip._image_status(rec(lic=ip.CREATIVE_COMMONS)) == ip.VERIFIED_FREE_IMAGE
          and ip._decide(rec(lic=ip.CREATIVE_COMMONS))[0] == ip.AUTO_PUBLISH)
    check("authentic + copyrighted -> VERIFIED_COPYRIGHTED_IMAGE / MANUAL_REVIEW",
          ip._image_status(rec()) == ip.VERIFIED_COPYRIGHTED_IMAGE
          and ip._decide(rec())[0] == ip.MANUAL_REVIEW)
    check("official source -> VERIFIED_LICENSED_IMAGE / AUTO_PUBLISH",
          ip._image_status(rec(lic=ip.OFFICIAL_REUSE_ALLOWED)) == ip.VERIFIED_LICENSED_IMAGE)
    check("file photo -> VERIFIED_FILE_PHOTO, never AUTO_PUBLISH",
          ip._image_status(rec(authentic=False, file_photo=True)) == ip.VERIFIED_FILE_PHOTO
          and ip._decide(rec(authentic=False, file_photo=True))[0] != ip.AUTO_PUBLISH)
    check("nothing found -> NO_VERIFIED_IMAGE / TEXT_ONLY",
          ip._image_status(None) == ip.NO_VERIFIED_IMAGE
          and ip._decide(None)[0] == ip.TEXT_ONLY)

    # The central invariant, asserted directly across the whole matrix.
    bad = []
    for authentic in (True, False):
        for lic in (ip.FREE_REUSE, ip.PUBLIC_DOMAIN, ip.CREATIVE_COMMONS,
                    ip.OFFICIAL_REUSE_ALLOWED, ip.LICENSE_REQUIRED,
                    ip.UNKNOWN_LICENSE, ip.PERMISSION_REQUIRED):
            d = ip._decide(rec(authentic=authentic, lic=lic))[0]
            if d == ip.AUTO_PUBLISH and not (authentic and ip.is_reusable(lic)):
                bad.append((authentic, lic))
    check("AUTO_PUBLISH requires authentic AND reusable, with no exceptions",
          not bad, str(bad))

    own = ip.owner_media_result({"id": "x", "title": "t"}, "/tmp/pic.jpg")
    check("own media -> OWNER_MEDIA / AUTO_PUBLISH",
          own["image_status"] == ip.OWNER_MEDIA and own["decision"] == ip.AUTO_PUBLISH
          and ip.is_reusable(own["license_status"]))


# ============================================================
# NO AI SUBSTITUTES
# ============================================================

def no_ai_tests():
    print("\nNO AI SUBSTITUTION")
    check("real-images-only is the default", ip.settings.PHOTO_REAL_IMAGES_ONLY is True)
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "incident_photos.py"), encoding="utf-8").read().lower()
    check("engine has no image-generation call path",
          not any(t in src for t in ("imagen", "dall-e", "stable-diffusion",
                                     "generate_image", "text2im")))
    check("no state exists for a generated image",
          "AI_IMAGE" not in [ip.VERIFIED_FREE_IMAGE, ip.VERIFIED_LICENSED_IMAGE,
                             ip.VERIFIED_COPYRIGHTED_IMAGE, ip.VERIFIED_FILE_PHOTO,
                             ip.NO_VERIFIED_IMAGE, ip.UNCERTAIN_IMAGE, ip.OWNER_MEDIA])


# ============================================================
# DATABASE / RETRY / METRICS / DASHBOARD
# ============================================================

def storage_tests():
    print("\nSTORAGE, RETRY, METRICS, DASHBOARD")
    tmp = tempfile.mkdtemp()
    from src import photo_db
    photo_db.DB_PATH = os.path.join(tmp, "photos.db")
    photo_db.LOG_PATH = os.path.join(tmp, "photo_log.jsonl")
    from src import photo_review, dashboard
    dashboard.OUT_PATH = os.path.join(tmp, "dashboard", "index.html")

    story = {"id": "s1", "title": "Massive fire guts Chennai godown, three dead",
             "summary": "", "link": "https://www.indiatoday.in/story/fire",
             "category": "INDIA NEWS", "score": 88}
    result = {
        "article_id": "s1", "headline": story["title"], "event_id": "IN-CHENNAI-FIRE-2026-09-16-abcd",
        "image_status": ip.VERIFIED_COPYRIGHTED_IMAGE, "decision": ip.MANUAL_REVIEW,
        "chosen_candidate_id": "cand0001", "source_name": "indiatoday.in",
        "source_url": story["link"], "image_url": "https://x/1.jpg",
        "copyright_owner": "indiatoday.in", "photographer": "",
        "candidates": [
            {"candidate_id": "cand0001", "image_url": "https://x/1.jpg",
             "thumbnail_url": "https://x/1.jpg", "source_url": story["link"],
             "source_name": "indiatoday.in", "caption": "fire at godown",
             "published_at": NOW.isoformat(), "discovered_at": NOW.isoformat(),
             "tier": "article_og_image", "photographer": "", "rank_in_story": 1,
             "age_bucket": "BREAKING", "event_match": True, "location_match": True,
             "person_match": True, "time_match": True, "is_file_photo": False,
             "confidence": 0.95, "image_age": "0h", "reason": "publisher article",
             "decision": "APPROVE", "license_status": ip.LICENSE_REQUIRED,
             "license_name": "All rights reserved", "license_url": story["link"],
             "copyright_owner": "indiatoday.in", "attribution_required": True,
             "permission_required": True, "reusable": False},
            # A rejected candidate: it MUST still be stored.
            {"candidate_id": "cand0002", "image_url": "https://y/2.jpg",
             "thumbnail_url": "https://y/2.jpg", "source_url": "https://y/p",
             "source_name": "y.test", "caption": "unrelated stock fire",
             "published_at": NOW.isoformat(), "discovered_at": NOW.isoformat(),
             "tier": "openverse_cc", "photographer": "someone", "rank_in_story": 2,
             "age_bucket": "BREAKING", "event_match": False, "location_match": False,
             "person_match": True, "time_match": True, "is_file_photo": False,
             "confidence": 0.2, "image_age": "0h", "reason": "no distinctive overlap",
             "decision": "REJECT", "license_status": ip.CREATIVE_COMMONS,
             "license_name": "by", "license_url": "", "copyright_owner": "",
             "attribution_required": True, "permission_required": False, "reusable": True},
        ],
    }
    photo_review.persist(result, story)

    cands = photo_db.candidates_for("s1")
    check("every candidate is stored, rejects included", len(cands) == 2, str(len(cands)))
    check("the rejected candidate keeps its reason",
          any(c["verdict"] == "REJECT" and "distinctive" in (c["reason"] or "") for c in cands))
    check("licence rows are stored per candidate",
          {c["license_status"] for c in cands} == {ip.LICENSE_REQUIRED, ip.CREATIVE_COMMONS})

    art = photo_db.get_article("s1")
    check("copyrighted story stays unresolved so the ladder keeps hunting",
          art["resolved"] == 0)
    check("a permission record is opened at NOT_REQUESTED, not sent",
          photo_db.query("SELECT status FROM permission_requests WHERE candidate_id='cand0001'")
          [0]["status"] == "NOT_REQUESTED")

    first = photo_review.schedule_retry("s1")
    check("retry is scheduled on the first ladder rung", first is not None)
    check("retry_index advances", photo_db.get_article("s1")["retry_index"] == 1)

    # Ladder exhaustion must terminate rather than retry forever.
    for _ in range(len(ip.settings.PHOTO_RETRY_LADDER_MIN) + 2):
        photo_review.schedule_retry("s1")
    check("ladder stops when exhausted", photo_db.get_article("s1")["next_retry_at"] is None)

    m = photo_review.compute_daily_metrics(day=photo_db.now_iso()[:10])
    check("metrics count stories from the DB", m["stories"] == 1, str(m))
    check("metrics count the copyrighted story", m["copyrighted"] == 1, str(m))
    check("metrics report zero free images (none were)", m["free_images"] == 0, str(m))
    check("wrong-image count starts at zero (nothing rejected by a human yet)",
          m["wrong_images"] == 0, str(m))

    # Replay: the DB is derived, so deleting it must lose nothing.
    os.remove(photo_db.DB_PATH)
    check("DB rebuilds from the JSONL log with no data loss",
          len(photo_db.candidates_for("s1")) == 2)

    path = dashboard.build(day=photo_db.now_iso()[:10])
    doc = open(path, encoding="utf-8").read()
    check("dashboard renders the story", "Chennai godown" in doc)
    check("dashboard shows the rights status", "LICENSE_REQUIRED" in doc)
    check("dashboard states the no-photo-beats-wrong-photo rule",
          "No photo" in doc and "wrong photo" in doc)

    # Telegram card wording, for all three shapes the spec calls out.
    copy_card = photo_review.format_card({
        "image_status": ip.VERIFIED_COPYRIGHTED_IMAGE, "decision": ip.MANUAL_REVIEW,
        "headline": "Massive fire guts Chennai godown", "authenticity_confidence": 0.96,
        "source_name": "India Today", "published_at": NOW.isoformat(), "age_bucket": "BREAKING",
        "event_match": True, "location_match": True, "time_match": True,
        "license_status": ip.LICENSE_REQUIRED, "copyright_owner": "indiatoday.in",
        "reason": "Exact incident photo found, but publisher copyright applies."})
    check("copyrighted card says DO NOT AUTO-PUBLISH",
          "DO NOT AUTO-PUBLISH" in copy_card and "96%" in copy_card)
    free_card = photo_review.format_card({
        "image_status": ip.VERIFIED_FREE_IMAGE, "decision": ip.AUTO_PUBLISH,
        "headline": "h", "authenticity_confidence": 0.9, "source_name": "Openverse",
        "published_at": NOW.isoformat(), "age_bucket": "SAME_DAY", "event_match": True,
        "location_match": True, "time_match": True, "license_name": "CC BY",
        "attribution": "Photo: someone"})
    check("free card says AUTO-PUBLISH ELIGIBLE and names the attribution",
          "AUTO-PUBLISH ELIGIBLE" in free_card and "Photo: someone" in free_card)
    none_card = photo_review.format_card(
        {"image_status": ip.NO_VERIFIED_IMAGE, "headline": "h"})
    check("no-image card says TEXT ONLY", "TEXT ONLY" in none_card)

    buttons = photo_review.build_buttons({
        "chosen_candidate_id": "cand0001", "decision": ip.MANUAL_REVIEW,
        "image_url": "https://x/1.jpg", "source_url": "https://s/1"})
    labels = [b["text"] for row in buttons for b in row]
    check("review buttons are offered", len(labels) >= 4, str(labels))
    check("NO button republishes copyrighted material",
          not any(w in " ".join(labels).upper() for w in ("USE THIS", "POST IMAGE", "PUBLISH IMAGE", "DOWNLOAD")),
          str(labels))
    check("callback data fits Telegram's 64-byte limit",
          all(len(b.get("callback_data", "")) <= 64 for row in buttons for b in row))


if __name__ == "__main__":
    critical_tests()
    licensing_tests()
    age_tests()
    clustering_tests()
    deep_search_tests()
    state_tests()
    no_ai_tests()
    storage_tests()
    print(f"\n{'='*52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
