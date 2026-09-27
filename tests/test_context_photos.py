"""
Context photos (places / institutions) -- Tests
===============================================
All offline: the Wikidata / Commons calls are replaced with fixtures. What
is being tested is the DECISION logic -- every way a lookup must refuse --
because the failure that matters is not "no photo", it is "a photo that
implies it shows the event".

    python -m tests.test_context_photos
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

from src import context_photos as cp

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def entity_tests():
    print("\nENTITY EXTRACTION")
    c = cp.candidate_entities("Tamil Nadu announces new scheme for farmers in Chennai")
    check("a two-word state name stays together", "Tamil Nadu" in c, c)
    check("a city in the same headline is also a candidate", "Chennai" in c, c)
    check("'RBI' expands to the full institution name",
          "Reserve Bank of India" in cp.candidate_entities("RBI cuts repo rate"))
    check("'Supreme Court' resolves to the Indian court",
          "Supreme Court of India" in cp.candidate_entities("Supreme Court hears the plea"))
    generic = cp.candidate_entities("Police say the man and the woman were from the family")
    check("generic words are never treated as places", generic == [], generic)
    check("candidate list is bounded",
          len(cp.candidate_entities(" ".join(f"Aaa{i} Bbb{i}" for i in range(40)))) <= 8)


def wrong_match_tests():
    """Regression: a live run matched "DMK" (a Tamil party) to Don Mueang
    airport in Bangkok, and "Change" to a foreign settlement."""
    print("\nWRONG-MATCH GUARDS")
    c = cp.candidate_entities("DMK MP Moves Election Commission Against TVK Leaders")
    check("bare abbreviations are never looked up", "DMK" not in c and "TVK" not in c and "MP" not in c, c)
    check("but the vetted full name still is", "Election Commission of India" in c, c)
    check("'AI' in an Air India headline is not looked up",
          "AI" not in cp.candidate_entities("AI 171 crash probe continues"))

    # the India requirement, at the Wikidata-filter level
    def entity(country_qid):
        return {"entities": {"Q9": {
            "claims": {"P31": [], "P625": [{"mainsnak": {}}],
                       "P17": ([{"mainsnak": {"datavalue": {"value": {"id": country_qid}}}}]
                               if country_qid else [])},
            "sitelinks": {f"w{i}": 1 for i in range(80)},
            "descriptions": {"en": {"value": "airport"}},
            "labels": {"en": {"value": "Somewhere"}}}}}
    real = cp.sp._get_json
    try:
        for qid, want, label in ((_IN := "Q668", True, "in India"),
                                 ("Q869", False, "in Thailand"),
                                 (None, False, "with no country")):
            cp.sp._get_json = lambda base, params, tries=3, _q=qid: (
                {"search": [{"id": "Q9", "label": "Somewhere"}]}
                if params.get("action") == "wbsearchentities" else entity(_q))
            got = cp._search_entities("Somewhere")
            check(f"a place {label} is {'accepted' if want else 'refused'}", bool(got) == want, got)
    finally:
        cp.sp._get_json = real


def safety_tests():
    print("\nSAFETY (the rule that matters)")
    called = []
    real = cp._search_entities
    cp._search_entities = lambda n: called.append(n) or []
    try:
        for h in ["Police shoot dead UP man who killed four people and took hostages",
                  "Heavy Kerala rains kill five people and trigger landslides",
                  "Man arrested in Mumbai over alleged fraud",
                  "Woman dies after accident on Delhi highway",
                  "Three held in Chennai rape case",
                  "Fire breaks out at Mumbai market",
                  "Building collapse in Gujarat leaves several injured",
                  "Cyclone approaches Odisha coast",
                  "Blast reported near Delhi court",
                  "Two dead in Bengal clashes"]:
            cp.find_context_photo(h)
        check("crime / death / victim headlines never even trigger a lookup", called == [], called)
    finally:
        cp._search_entities = real

    from config import settings
    old = settings.CONTEXT_PHOTOS_ENABLED
    settings.CONTEXT_PHOTOS_ENABLED = False
    try:
        check("disabled -> never returns a photo",
              cp.find_context_photo("Parliament passes new bill") is None)
    finally:
        settings.CONTEXT_PHOTOS_ENABLED = old


def photo_detection_tests(tmp):
    print("\nPHOTOGRAPH vs DOCUMENT")
    # a scanned table/report: near-white, colourless, thin dark text
    doc = Image.new("RGB", (900, 1200), (252, 252, 250))
    d = ImageDraw.Draw(doc)
    for row in range(24):
        d.line([(80, 90 + row * 44), (820, 90 + row * 44)], fill=(40, 40, 40), width=3)
    doc_p = os.path.join(tmp, "doc.jpg")
    doc.save(doc_p)
    check("a scanned document/table is rejected", not cp.looks_like_photograph(doc_p))

    # a photograph: colourful, mid-tone (sky over a building)
    photo = Image.new("RGB", (900, 600))
    px = photo.load()
    for y in range(600):
        for x in range(900):
            px[x, y] = ((70, 130, 200) if y < 260 else (150, 110, 70))
    photo_p = os.path.join(tmp, "photo.jpg")
    photo.save(photo_p)
    check("a real photograph is accepted", cp.looks_like_photograph(photo_p))

    blank = Image.new("RGB", (900, 900), (255, 255, 255))
    blank_p = os.path.join(tmp, "blank.jpg")
    blank.save(blank_p)
    check("a blank/white page is rejected", not cp.looks_like_photograph(blank_p))
    check("an unreadable file is rejected, not crashed on",
          not cp.looks_like_photograph(os.path.join(tmp, "nope.jpg")))


def filename_filter_tests():
    print("\nFILE FILTERING")
    names = ["Reserve Bank of India building.jpg", "RBI annual report 2019.pdf",
             "Table 2 composition of GDP.jpg", "India locator map.png",
             "BJP logo.svg", "Parliament House aerial view.jpg",
             "Flag of Tamil Nadu.png", "Chennai Marina beach.jpeg"]
    real = cp.sp._get_json
    cp.sp._get_json = lambda base, params, tries=3: {
        "query": {"categorymembers": [{"title": "File:" + n} for n in names]}}
    try:
        got = cp.category_photos("Whatever")
    finally:
        cp.sp._get_json = real
    check("keeps real photographs", "Reserve Bank of India building.jpg" in got and
          "Parliament House aerial view.jpg" in got, got)
    check("drops scanned reports and tables",
          not any("report" in g.lower() or "Table" in g for g in got), got)
    check("drops maps, logos and flags",
          not any(k in g.lower() for g in got for k in ("map", "logo", "flag")), got)
    check("drops non-bitmap files", not any(g.endswith((".svg", ".pdf")) for g in got), got)


def resolution_tests():
    print("\nRESOLUTION")
    def fake(name):
        # (qid, sitelinks, p18, description, label, commons_cat)
        return {
            "Chennai": [("Q1", 120, "a.jpg", "city in Tamil Nadu", "Chennai", "Chennai")],
            "Springfield": [("Q2", 40, "b.jpg", "city", "Springfield", "S1"),
                            ("Q3", 38, "c.jpg", "city", "Springfield", "S2")],
            "Tinyplace": [("Q4", 3, "d.jpg", "hamlet", "Tinyplace", None)],
            "Nopic": [("Q5", 90, None, "region", "Nopic", None)],
        }.get(name, [])
    real = cp._search_entities
    cp._search_entities = fake
    try:
        qid, p18, label, desc, cat, why = cp.resolve_entity("Chennai")
        check("a clearly notable place resolves", qid == "Q1" and p18 == "a.jpg", why)
        qid, *_ , why = cp.resolve_entity("Springfield")
        check("two similarly notable same-named places -> refused", qid is None, why)
        qid, *_, why = cp.resolve_entity("Tinyplace")
        check("an obscure place is refused", qid is None, why)
        qid, *_, why = cp.resolve_entity("Nopic")
        check("no image and no category -> refused", qid is None, why)
        qid, *_, why = cp.resolve_entity("Unknown")
        check("an unknown name is refused", qid is None, why)
    finally:
        cp._search_entities = real


if __name__ == "__main__":
    entity_tests()
    wrong_match_tests()
    safety_tests()
    with tempfile.TemporaryDirectory() as t:
        photo_detection_tests(t)
    filename_filter_tests()
    resolution_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
