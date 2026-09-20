"""
Verified Subject Portraits
==========================
For a headline that names a real, notable person, fetch that person's
lead portrait from Wikimedia Commons -- automatically, with no human in
the loop -- and only when every check below passes. Anything that fails
any check returns None, and the caller falls back to a text-only card:
NO IMAGE beats WRONG IMAGE, same as the rest of this project.

WHAT THIS IS, AND IS NOT
------------------------
This is a FILE PHOTO of the person named in the headline. It is not a
photo of the event. It is always labelled "FILE PHOTO" on the image and
credited in the caption. Event photographs (a fire, a flood, a raid) are
still owned by whoever shot them; this module does not touch those --
see incident_photos.py for that, and for why they need a licence.

WHY WIKIMEDIA COMMONS, NOT "THE INTERNET"
-----------------------------------------
Google Images / agency sites return whatever is indexed, copyrighted or
not, with no reliable licence data. Commons is the one large source
where every file carries machine-readable licence + author metadata, and
Wikidata gives an authoritative "this entity is this human" link. Both
have free, keyless, documented APIs (Wikimedia asks for a descriptive
User-Agent, which _UA supplies).

CHECKS (all must pass)
----------------------
 1. The full name appears in the headline. Single names ("Modi") only
    resolve through a small explicit alias list -- never guessed.
 2. Sensitive headlines (rape, murder, accused, arrested...) never get a
    person's photo: putting a face next to an allegation is a
    reputational/legal risk that an automated name match cannot clear.
 3. Wikidata has an entity whose label/alias matches the name EXACTLY and
    which is a human (instance of Q5).
 4. That person is notable (>= MIN_SITELINKS Wikipedia-language pages),
    which is what separates the Chief Minister from a namesake.
 5. If more than one notable human shares the name, the top one must be
    clearly dominant (2x the runner-up) -- otherwise ambiguous -> skip.
 6. The person has a lead image (P18), and the file is big enough.
 7. The file's licence is on the allowlist: CC0, public domain, CC BY,
    GODL-India (and CC BY-SA only if explicitly enabled). Never NC/ND.
 8. Author + licence URL are captured so attribution can be printed.
"""

import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from config import settings

WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
_UA = ("AravindNews24/1.0 (news page; "
       "https://github.com/araviaravind333-sketch/Aravind24new)")

# Headlines containing these never get a person's photo -- see check 2.
_SENSITIVE = re.compile(
    r"\b(rape[sd]?|raped|gang[- ]?rape[sd]?|molest\w*|murder\w*|kill(?:s|ed|ers?|ing)?|"
    r"stab(?:bed|bing)?|shot dead|accused|arrest(?:s|ed)?|abduct\w*|assault\w*|"
    r"suicide|lynch\w*|trafficking|sexual\w*|harass\w*|victim|fraud|scam|"
    r"convict\w*|sentenced|custody|chargesheet\w*)\b", re.I)

# Single-name references that are unambiguous in Indian/world news. Kept
# short on purpose: every entry is a claim that this surname/first name
# means exactly one person in a headline, so it must be true.
_ALIASES = {
    "modi": "Narendra Modi",
    "mamata": "Mamata Banerjee",
    "kejriwal": "Arvind Kejriwal",
    "trump": "Donald Trump",
    "putin": "Vladimir Putin",
    "zelensky": "Volodymyr Zelenskyy",
    "zelenskyy": "Volodymyr Zelenskyy",
    "netanyahu": "Benjamin Netanyahu",
}

_NOT_NAME_WORDS = {
    "the", "and", "for", "with", "from", "after", "over", "amid", "says",
    "said", "will", "not", "has", "have", "into", "than", "that", "this",
    "police", "court", "minister", "chief", "govt", "government", "cm",
    "pm", "mla", "mp", "party", "congress", "delhi", "india", "indian",
    "state", "union", "supreme", "high", "election", "commission", "bank",
    "team", "group", "sons", "cop", "army", "soldier", "students", "fans",
    "news", "live", "breaking", "world", "asian", "games", "day", "first",
    "season", "cyclone", "sea", "punjab", "bengal", "tamil", "nadu",
    "kerala", "karnataka", "gujarat", "bihar", "odisha", "assam", "andhra",
    "bengaluru", "mumbai", "chennai", "kolkata", "hyderabad", "pune",
    "rs", "crore", "lakh", "per", "cent", "over", "under", "new", "old",
}

_CACHE = {}
_UNUSABLE = object()   # cache marker: a real person whose photo can't be used


def _get_json(base, params, tries=3):
    url = base + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 or e.code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except Exception as e:
            last = e
            time.sleep(1)
    print("subject_photos: request failed:", last)
    return None


def _norm(name):
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower().replace(".", "")).strip()


def candidate_names(headline):
    """Full-name candidates worth a lookup, most specific first. Title-Case
    headlines make almost every run of words look like a name, so this is
    deliberately loose -- the Wikidata exact-match + human + notability
    checks are what actually decide, this only bounds how many lookups."""
    out, seen = [], set()

    def add(n):
        k = _norm(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)

    for m in re.finditer(r"\b(" + "|".join(re.escape(a) for a in _ALIASES) + r")\b",
                         headline, re.I):
        # only when it is actually capitalised in the headline, not a
        # lowercase common word that happens to match
        if headline[m.start()].isupper():
            add(_ALIASES[m.group(1).lower()])

    for run in re.finditer(r"(?:[A-Z][A-Za-z\.'\-]+)(?:\s+[A-Z][A-Za-z\.'\-]+){1,3}", headline):
        words = run.group(0).split()
        for size in (3, 2):
            for i in range(0, len(words) - size + 1):
                gram = words[i:i + size]
                if any(_norm(w) in _NOT_NAME_WORDS or len(_norm(w)) < 2 for w in gram):
                    continue
                add(" ".join(gram))
    return out[:10]


def _search_humans(name):
    """Wikidata entities whose label/alias matches `name` exactly and are
    human. Returns [(qid, sitelinks, p18_filename_or_None, description)]."""
    data = _get_json(WIKIDATA, {
        "action": "wbsearchentities", "search": name, "language": "en",
        "type": "item", "limit": 8, "format": "json"})
    if not data:
        return []
    ids = []
    for hit in data.get("search", []):
        matched = (hit.get("match") or {}).get("text") or hit.get("label")
        if _norm(matched) == _norm(name):
            ids.append(hit["id"])
    if not ids:
        return []
    ents = _get_json(WIKIDATA, {
        "action": "wbgetentities", "ids": "|".join(ids),
        "props": "claims|sitelinks|descriptions", "languages": "en", "format": "json"})
    out = []
    for qid, e in ((ents or {}).get("entities") or {}).items():
        claims = e.get("claims", {})
        is_human = any(
            (c.get("mainsnak", {}).get("datavalue", {}).get("value") or {}).get("id") == "Q5"
            for c in claims.get("P31", []))
        if not is_human:
            continue
        p18 = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("P18", [])
                    if c.get("mainsnak", {}).get("datavalue")), None)
        out.append((qid, len(e.get("sitelinks", {})), p18,
                    (e.get("descriptions", {}).get("en") or {}).get("value", "")))
    return out


def resolve_person(name, min_sitelinks=None):
    """(qid, p18, description, reason). qid None means rejected; `reason`
    says why, so a skip is explainable instead of silent."""
    min_sl = settings.SUBJECT_PHOTO_MIN_SITELINKS if min_sitelinks is None else min_sitelinks
    humans = _search_humans(name)
    notable = sorted([h for h in humans if h[1] >= min_sl], key=lambda h: -h[1])
    if not notable:
        return None, None, "", "no notable human with that exact name"
    if len(notable) > 1 and notable[0][1] < 2 * notable[1][1]:
        return None, None, "", (f"ambiguous: {len(notable)} notable people named "
                                f"{name!r}, none clearly dominant")
    qid, sl, p18, desc = notable[0]
    if not p18:
        return None, None, desc, "notable, but no lead image on Wikidata"
    return qid, p18, desc, "ok"


_LICENSE_OK = [
    re.compile(r"^CC0", re.I),
    re.compile(r"^(public domain|pd\b|pdm)", re.I),
    re.compile(r"^CC[ -]BY(?![- ]?(SA|NC|ND))[ -]?\d", re.I),
    re.compile(r"^GODL", re.I),
]
_LICENSE_SA = re.compile(r"^CC[ -]BY-SA", re.I)
_LICENSE_BAD = re.compile(r"(^|[ -])(NC|ND)([ -]|$)", re.I)


def licence_allowed(short_name, allow_share_alike=None):
    """Allowlist, not blocklist: an unrecognised licence is a refusal."""
    allow_sa = settings.SUBJECT_PHOTO_ALLOW_SHARE_ALIKE if allow_share_alike is None else allow_share_alike
    s = (short_name or "").strip()
    if not s or _LICENSE_BAD.search(s):
        return False
    if any(p.search(s) for p in _LICENSE_OK):
        return True
    return bool(allow_sa and _LICENSE_SA.search(s))


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def commons_file_info(filename):
    data = _get_json(COMMONS, {
        "action": "query", "titles": "File:" + filename, "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1200, "format": "json"})
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for p in pages.values():
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        m = ii.get("extmetadata", {})
        val = lambda k: (m.get(k) or {}).get("value")
        return {
            "url": ii.get("thumburl") or ii.get("url"),
            "width": ii.get("width", 0), "height": ii.get("height", 0),
            "mime": ii.get("mime", ""),
            "license": val("LicenseShortName") or "",
            "license_url": val("LicenseUrl") or "",
            "artist": _strip_html(val("Artist")),
            "credit": _strip_html(val("Credit")),
            "page": "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(filename.replace(" ", "_")),
        }
    return None


def _download(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)
    return dest_path


def find_subject_photo(headline, summary="", dest_dir=None, allow_share_alike=None):
    """Full pipeline for one story. Returns a dict describing a verified,
    licensed FILE PHOTO of a person named in the headline, or None."""
    if not settings.SUBJECT_PHOTOS_ENABLED:
        return None
    if _SENSITIVE.search(headline) or _SENSITIVE.search(summary or ""):
        return None

    for name in candidate_names(headline):
        key = _norm(name)
        if key in _CACHE:
            hit = _CACHE[key]
            if hit is _UNUSABLE:
                return None
            if hit is not None:
                return hit
            continue
        qid, p18, desc, reason = resolve_person(name)
        if not qid:
            _CACHE[key] = None      # not a (notable) person -- try the next name
            continue
        # From here the name IS a real person. If their photo can't be used,
        # stop: falling through to the NEXT person named in the headline
        # would put someone else's face under this person's story.
        info = commons_file_info(p18)
        if not info or not info.get("url"):
            _CACHE[key] = _UNUSABLE
            return None
        if not licence_allowed(info["license"], allow_share_alike):
            print(f"subject_photos: {name}: licence {info['license']!r} not allowed -- skipped")
            _CACHE[key] = _UNUSABLE
            return None
        if min(info["width"], info["height"]) < 500 or not info["mime"].startswith("image/"):
            _CACHE[key] = _UNUSABLE
            return None
        dest_dir = dest_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                             "data", "subject_photos")
        ext = ".png" if info["mime"] == "image/png" else ".jpg"
        path = os.path.join(dest_dir, hashlib.sha1(qid.encode()).hexdigest()[:12] + ext)
        try:
            _download(info["url"], path)
        except Exception as e:
            print("subject_photos: download failed:", e)
            _CACHE[key] = _UNUSABLE
            return None
        who = info["artist"] or "Wikimedia Commons contributor"
        result = {
            "path": path, "subject": name, "wikidata_id": qid, "description": desc,
            "license": info["license"], "license_url": info["license_url"],
            "artist": who, "source_url": info["page"], "is_file_photo": True,
            "attribution": f"{who} / {info['license']} (Wikimedia Commons)",
        }
        _CACHE[key] = result
        return result
    return None


# ---------------------------------------------------------------------------
# Framing helpers (shared by the single-post and carousel renderers)
# ---------------------------------------------------------------------------

def cover_crop_biased(img, w, h, v_bias=0.2):
    """Scale + crop to exactly w x h like a normal cover-crop, but keep the
    TOP of the picture instead of the middle. A centred crop of a portrait
    photo into a wider frame slices through the head; faces sit in the top
    third, so the crop window is shifted up (v_bias=0 keeps the very top,
    0.5 is a plain centred crop)."""
    from PIL import Image
    src_ratio, dst_ratio = img.width / img.height, w / h
    if src_ratio > dst_ratio:
        new_h, new_w = h, int(h * src_ratio)
    else:
        new_w, new_h = w, int(w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = int((new_h - h) * v_bias)
    return img.crop((left, top, left + w, top + h))


def crop_portrait(path, out_path, w=1080, h=1350, v_bias=0.15, tag="FILE PHOTO"):
    """Pre-crops a portrait to the post's exact frame (face-safe) and bakes
    in the 'FILE PHOTO' label, so every renderer that later draws its own
    text on top of it shows the honest label without needing to know
    about it."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(path).convert("RGB")
    img = cover_crop_biased(img, w, h, v_bias).convert("RGBA")
    if tag:
        fonts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "fonts", "Archivo.ttf")
        f = ImageFont.truetype(fonts, 28)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        tw = d.textlength(tag, font=f)
        x1, y0 = w - 50, 56
        d.rounded_rectangle([x1 - tw - 36, y0, x1, y0 + 52], radius=8, fill=(0, 0, 0, 170))
        d.text((x1 - tw - 18, y0 + 9), tag, font=f, fill=(255, 255, 255, 255))
        img = Image.alpha_composite(img, layer)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=95, subsampling=0)
    return out_path
