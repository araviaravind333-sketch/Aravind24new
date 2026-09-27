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


def rhetoric_tests():
    """Regression: 'attacks'/'attack' in Indian political headlines almost
    always means criticises, not violence -- an over-broad block on the
    word alone refused a context photo to most political news. A prior
    version of _RHETORIC also silently compiled with literal backspace
    bytes instead of \\b word-boundary escapes (a string-building bug, not
    a regex design bug) and matched nothing at all."""
    print("\nPOLITICAL RHETORIC vs REAL VIOLENCE")
    for h in ["Congress attacks Maharashtra government for data theft",
              "Explained: Opposition's Attack On Chief Election Commissioner",
              "Rahul Gandhi slams BJP over poll remarks",
              "Parliament passes new data bill"]:
        check(f"criticism is not treated as violence: {h[:44]!r}", not cp.unsafe_for_context(h))
    for h in ["Terror attack on Parliament complex",
              "Attack kills two in Manipur",
              "Mob attacks temple in Bihar",
              "Gunmen attack village in Manipur",
              "2 Naga Civilians Shot Dead In Manipur",
              "Karur stampede anniversary observed"]:
        check(f"real violence is still refused: {h[:44]!r}", cp.unsafe_for_context(h))
    check("_RHETORIC actually contains real backslash-b escapes, not backspace bytes",
          chr(8) not in cp._RHETORIC.pattern)


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


def reuse_tests(tmp):
    print("\nSUBJECT DIVERSITY")
    import json, os as _os, datetime as _dt
    real_path = cp._RECENT_PATH
    cp._RECENT_PATH = _os.path.join(tmp, "recent.json")
    try:
        check("nothing recorded -> nothing is fresh", cp._recent_subjects() == {})
        cp.note_subject_used("Election Commission of India")
        check("a used subject is remembered",
              "Election Commission of India" in cp._recent_subjects())
        stale = {"Old Place": (_dt.datetime.utcnow() - _dt.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")}
        json.dump(stale, open(cp._RECENT_PATH, "w"))
        check("a subject used long ago is free again", cp._recent_subjects() == {}, cp._recent_subjects())
        json.dump({"Broken": "not-a-date"}, open(cp._RECENT_PATH, "w"))
        check("a corrupt timestamp is ignored, not crashed on", cp._recent_subjects() == {})
    finally:
        cp._RECENT_PATH = real_path


def share_alike_tests(tmp):
    print("\nSHARE-ALIKE DISPLAY TERMS")
    from PIL import Image
    from src import feed_post as fp
    # 3:1 is much wider than the picture window, so "shown whole" must
    # letterbox it -- that is what proves it was not cropped to fill
    src = os.path.join(tmp, "wide.jpg")
    Image.new("RGB", (3000, 1000), (30, 160, 60)).save(src)
    head = "PARLIAMENT PASSES NEW BILL ON DIGITAL DATA PROTECTION"
    plain = fp.render_post(src, "INDIA NEWS", head, "", os.path.join(tmp, "p.jpg"))
    sa = fp.render_post(src, "INDIA NEWS", head, "", os.path.join(tmp, "sa.jpg"), share_alike=True)
    sa_im = Image.open(sa)
    green = lambda px: px[1] > px[0] + 30 and px[1] > px[2] + 30
    check("share-alike photo is shown whole, letterboxed rather than cropped",
          not green(sa_im.getpixel((540, 6))), sa_im.getpixel((540, 6)))
    check("the photo itself is still there, centred",
          green(sa_im.getpixel((540, fp.layout_for(3.0, head)["win_h"] // 2))))
    # neutral grey (r==g==b), not a tinted blur of the green photo. The exact
    # value varies because the brand-chip scrim darkens the top of the frame.
    corner = sa_im.getpixel((5, 6))[:3]
    check("share-alike sits on a plain background, not a blurred copy of itself",
          max(corner) - min(corner) <= 2 and max(corner) < 40, corner)
    plain_corner = Image.open(plain).getpixel((5, 6))[:3]
    check("the ordinary (non-share-alike) path may still use a tinted backdrop",
          plain_corner is not None)
    check("the two renders genuinely differ",
          list(Image.open(plain).getdata()) != list(sa_im.getdata()))


if __name__ == "__main__":
    entity_tests()
    wrong_match_tests()
    safety_tests()
    rhetoric_tests()
    with tempfile.TemporaryDirectory() as t:
        photo_detection_tests(t)
    filename_filter_tests()
    resolution_tests()
    with tempfile.TemporaryDirectory() as t:
        reuse_tests(t)
    with tempfile.TemporaryDirectory() as t:
        share_alike_tests(t)
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
