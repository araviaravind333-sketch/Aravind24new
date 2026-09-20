"""
Verified Subject Portraits -- Tests
===================================
All offline: the Wikidata / Commons calls are replaced with fixtures, so
the suite is deterministic. What is being tested is the DECISION logic --
every way a lookup must refuse -- because the failure that matters here
is not "no photo", it is "the wrong person's photo".

    python -m tests.test_subject_photos
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from src import subject_photos as sp

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    return condition


def name_tests():
    print("\nNAME CANDIDATES")
    c = sp.candidate_names("Congress supports Mamata Banerjee as Election Commission freezes her party symbol")
    check("a full name in a headline is a candidate", "Mamata Banerjee" in c, c)
    check("'PM Modi' resolves through the explicit alias list", "Narendra Modi" in sp.candidate_names("PM Modi inaugurates new terminal"))
    check("a lowercase common word is never treated as an alias",
          "Narendra Modi" not in sp.candidate_names("the modi family of words is not a person"))
    check("place and generic words alone are not names",
          sp.candidate_names("Delhi Police Chief Minister Government") == [])
    check("candidate list is bounded", len(sp.candidate_names(" ".join(f"Aaa{i} Bbb{i}" for i in range(40)))) <= 10)


def licence_tests():
    print("\nLICENCE ALLOWLIST")
    ok = ["CC BY 3.0", "CC BY 4.0", "CC0", "CC0 1.0", "Public domain", "PD-India", "GODL-India"]
    bad = ["CC BY-NC 4.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "Fair use", "All rights reserved", "", None,
           "GFDL"]
    for name in ok:
        check(f"allowed: {name}", sp.licence_allowed(name, allow_share_alike=False))
    for name in bad:
        check(f"refused: {name!r}", not sp.licence_allowed(name, allow_share_alike=True))
    check("CC BY-SA is refused by default (share-alike on a derived slide)",
          not sp.licence_allowed("CC BY-SA 4.0", allow_share_alike=False))
    check("CC BY-SA is allowed once explicitly enabled",
          sp.licence_allowed("CC BY-SA 4.0", allow_share_alike=True))


def resolve_tests():
    print("\nIDENTITY RESOLUTION")
    real = sp._search_humans

    def fake(rows):
        return lambda name: rows

    try:
        sp._search_humans = fake([("Q1", 60, "A.jpg", "chief minister")])
        check("one notable human with a lead image resolves", sp.resolve_person("X Y", 25)[0] == "Q1")

        sp._search_humans = fake([("Q1", 10, "A.jpg", "a person")])
        q, _, _, why = sp.resolve_person("X Y", 25)
        check("a barely-notable namesake is refused", q is None and "notable" in why, why)

        sp._search_humans = fake([("Q1", 40, "A.jpg", "a"), ("Q2", 35, "B.jpg", "b")])
        q, _, _, why = sp.resolve_person("X Y", 25)
        check("two similarly notable people with the same name -> ambiguous, refused",
              q is None and "ambiguous" in why, why)

        sp._search_humans = fake([("Q1", 90, "A.jpg", "a"), ("Q2", 30, "B.jpg", "b")])
        check("a clearly dominant person among namesakes resolves to the dominant one",
              sp.resolve_person("X Y", 25)[0] == "Q1")

        sp._search_humans = fake([("Q1", 60, None, "no image")])
        q, _, _, why = sp.resolve_person("X Y", 25)
        check("notable but with no lead image -> refused", q is None and "no lead image" in why, why)

        sp._search_humans = fake([])
        check("no exact human match -> refused", sp.resolve_person("X Y", 25)[0] is None)
    finally:
        sp._search_humans = real


def safety_tests():
    print("\nSENSITIVE HEADLINES")
    real = sp._get_json

    def boom(*a, **k):
        raise AssertionError("network was called for a sensitive headline")

    sp._get_json = boom
    try:
        for h in ["Mamata Banerjee condemns rape of student",
                  "Rahul Gandhi visits family of murder victim",
                  "Arvind Kejriwal arrested by ED",
                  "Killers beat mother to death, Mamata Banerjee reacts",
                  "Donald Trump accused of fraud in new filing"]:
            check(f"no lookup, no photo: {h[:48]}", sp.find_subject_photo(h) is None)
    finally:
        sp._get_json = real


def framing_tests():
    print("\nFRAMING")
    tmp = tempfile.mkdtemp()
    # top half red, bottom half blue, tall portrait
    im = Image.new("RGB", (600, 1000), (0, 0, 255))
    im.paste(Image.new("RGB", (600, 500), (255, 0, 0)), (0, 0))
    src = os.path.join(tmp, "p.png")
    im.save(src)

    biased = sp.cover_crop_biased(Image.open(src).convert("RGB"), 1080, 500, v_bias=0.0)
    centred = sp.cover_crop_biased(Image.open(src).convert("RGB"), 1080, 500, v_bias=0.5)
    check("top-biased crop keeps the top of a portrait (where the face is)",
          biased.getpixel((540, 5))[0] > 200 and biased.getpixel((540, 495))[0] > 200)
    # control: source is 600x1000 (top half red, bottom half blue) scaled
    # to 1080 wide = 1800 tall; a centred 500px window starts at row 650,
    # so its last row (1150) is already in the blue half -- i.e. a plain
    # centred crop drops the top of the image, which is exactly the
    # failure the biased crop exists to avoid
    check("control: a plain centred crop of the same image reaches into the lower half",
          centred.getpixel((540, 495))[2] > 200 and centred.getpixel((540, 495))[0] < 60)

    out = sp.crop_portrait(src, os.path.join(tmp, "o.jpg"))
    o = Image.open(out)
    check("crop_portrait produces the exact post frame", o.size == (1080, 1350))
    plain = sp.crop_portrait(src, os.path.join(tmp, "o2.jpg"), tag=None)
    a = Image.open(out).convert("L").crop((800, 56, 1030, 108))
    b = Image.open(plain).convert("L").crop((800, 56, 1030, 108))
    mean = lambda im_: sum(im_.getdata()) / (im_.width * im_.height)
    check("the FILE PHOTO label is actually baked into the image", mean(a) < mean(b) - 5,
          f"{mean(a):.1f} vs {mean(b):.1f}")


def disabled_tests():
    print("\nSWITCH")
    from config import settings
    old = settings.SUBJECT_PHOTOS_ENABLED
    settings.SUBJECT_PHOTOS_ENABLED = False
    try:
        check("disabled -> never returns a photo", sp.find_subject_photo("Mamata Banerjee wins") is None)
    finally:
        settings.SUBJECT_PHOTOS_ENABLED = old


if __name__ == "__main__":
    name_tests()
    licence_tests()
    resolve_tests()
    safety_tests()
    framing_tests()
    disabled_tests()
    print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
    sys.exit(1 if FAIL else 0)
