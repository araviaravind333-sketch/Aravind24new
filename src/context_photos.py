"""
Context photos -- licensed images of PLACES and INSTITUTIONS
============================================================
src/subject_photos.py finds a licensed portrait when a headline names a
notable *person*. That covers only a small share of stories. This module
covers the far more common case in Indian news: a headline that names a
*place* or an *institution* -- Kerala, Mumbai, the Supreme Court, the
Reserve Bank, ISRO, Parliament.

The image is a real, licensed photograph of that place/institution from
Wikimedia Commons. It is never presented as a picture of the event: every
post built this way carries a small caption naming what the photo actually
shows (see feed_post.render_post's `photo_note`), the same way a newsroom
runs a file/context shot.

Two hard limits keep this honest:

  * SENSITIVE HEADLINES GET NOTHING. Crime, death, accidents, victims --
    anything matching subject_photos._SENSITIVE -- never gets a context
    photo. "Police shoot dead man in UP" beside a stock photo of a UP
    landmark reads as a picture of the incident. Those stories wait for
    the owner's own media or run as a text card.
  * ONLY REUSABLE LICENCES. CC0, public domain, CC BY and GODL-India are
    used freely. CC BY-SA is also accepted, but only on terms that keep the
    post a *collection* rather than an adaptation, so no share-alike
    obligation attaches to the post: the photograph is shown WHOLE and
    UNCROPPED on a plain background, never with text over it, and its
    author and licence are printed on the image. Anything unrecognised, and
    anything NonCommercial or NoDerivatives, is refused.

Resolution is deliberately conservative: the entity must not be a person,
must be physically locatable (coordinates, or a headquarters), must clear
a notability bar, and must have a lead image big enough to look good.
"""

import os
import re

from config import settings
from src import subject_photos as sp

WIKIDATA = "https://www.wikidata.org/w/api.php"
_INDIA_QID = "Q668"

# Bare abbreviations are the single most dangerous kind of candidate: "DMK"
# is a Tamil party in an Indian headline but Bangkok's airport on Wikidata,
# "AI" is Air India or artificial intelligence. Only the abbreviations in
# _ALIASES (each one checked by hand) are ever looked up.
_ABBREV = re.compile(r"^[A-Z]{2,5}$")

# Short forms that are unambiguous in Indian news. Each entry is a claim
# that this abbreviation means exactly one institution, so it must be true.
_ALIASES = {
    "rbi": "Reserve Bank of India",
    "reserve bank": "Reserve Bank of India",
    "supreme court": "Supreme Court of India",
    "parliament": "Parliament of India",
    "lok sabha": "Lok Sabha",
    "rajya sabha": "Rajya Sabha",
    "election commission": "Election Commission of India",
    "eci": "Election Commission of India",
    "isro": "Indian Space Research Organisation",
    "cbi": "Central Bureau of Investigation",
    "sebi": "Securities and Exchange Board of India",
    "nia": "National Investigation Agency",
    "bcci": "Board of Control for Cricket in India",
    "drdo": "Defence Research and Development Organisation",
    "niti aayog": "NITI Aayog",
    "rashtrapati bhavan": "Rashtrapati Bhavan",
    "red fort": "Red Fort",
    "taj mahal": "Taj Mahal",
    "india gate": "India Gate",
}

# Words that are never the name of a place/institution on their own.
_STOP = {
    "the", "and", "for", "with", "from", "after", "over", "amid", "says",
    "said", "will", "not", "has", "have", "into", "than", "that", "this",
    "new", "old", "big", "top", "first", "last", "next", "more", "most",
    "news", "live", "breaking", "update", "report", "video", "watch",
    "man", "woman", "men", "women", "people", "family", "girl", "boy",
    "police", "court", "government", "govt", "minister", "chief", "party",
    "president", "prime", "leader", "official", "officials", "case",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

# Entities whose lead image is a logo/flag rather than a photograph, or
# whose marks carry trademark complications, are skipped regardless of the
# file's copyright licence.
_DESC_BLOCK = re.compile(
    r"\b(political party|party in|trade union|television channel|tv channel|"
    r"newspaper|logo|brand|trademark)\b", re.I)

# A context photo needs a STRICTER bar than a portrait. A licensed photo of
# Kerala next to "Kerala floods kill five" reads as a picture of the flood,
# however it is captioned -- so disasters, accidents and violence are
# refused on top of everything subject_photos._SENSITIVE already blocks.
# These stories run as text cards instead.
_UNSAFE_EXTRA = re.compile(
    r"\b(dies|died|die|death|deaths|dead|fatal\w*|toll|"
    r"accident|crash(?:es|ed)?|collision|derail\w*|stampede|collapse[sd]?|"
    r"fire|blaze|blast|explosion|bomb\w*|terror\w*|hostage\w*|encounter|"
    r"shooting|shot|firing|attack(?:s|ed|er|ers)?|clash(?:es|ed)?|riot\w*|"
    r"violence|violent|lynch\w*|"
    r"flood\w*|landslide\w*|earthquake|cyclone|storm|tsunami|drought|"
    r"drown\w*|injur\w*|wounded|hospitalis\w*|hospitaliz\w*|critical|"
    r"missing|bodies|body|funeral|mourn\w*|tragedy|tragic|disaster|"
    r"outbreak|epidemic|pandemic|infection|disease)\b", re.I)


# In Indian political headlines "attacks" almost always means criticises
# ("Congress attacks Maharashtra government"). Treating that as violence
# blocked most of the political news the page exists to cover. Real
# violence is still caught, because a genuinely violent story carries
# another trigger too ("terror attack", "attack kills two").
_RHETORIC = re.compile(
    r"\b(?:attack\w*|slam\w*|hit\s+out|target\w*)\b[^.]{0,40}?"
    r"\b(?:government|govt|bjp|congress|opposition|minister|ministry|commission|"
    r"commissioner|party|leader|centre|center|poll\s+body|mp|mla|mlas|cm|pm|"
    r"parliament|assembly|court|remark\w*|statement\w*|claim\w*)\b", re.I)


def unsafe_for_context(text):
    """True when a place/institution photo must not be attached to this
    story -- see the module docstring."""
    text = text or ""
    if sp._SENSITIVE.search(text):
        return True
    hits = [m.group(0).lower() for m in _UNSAFE_EXTRA.finditer(text)]
    if not hits:
        return False
    # the only triggers are the word "attack" used about a political
    # target: criticism, not violence
    if all(h.startswith("attack") for h in hits) and _RHETORIC.search(text):
        return False
    return True


_CACHE = {}
_UNUSABLE = object()


def candidate_entities(headline):
    """Place/institution names worth a lookup, most specific first."""
    out, seen = [], set()

    def add(n):
        k = sp._norm(n)
        if not k or k in seen or len(k) <= 2:
            return
        if _ABBREV.match(n.strip()):
            return          # unvetted abbreviation -- see _ABBREV
        seen.add(k)
        out.append(n)

    low = headline.lower()
    for alias, full in _ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", low):
            add(full)

    # Runs of capitalised words: "Supreme Court", "Tamil Nadu", "Mumbai".
    # Longest runs first -- "Tamil Nadu" beats "Tamil".
    runs = re.findall(
        r"\b[A-Z][a-zA-Z]+(?:\s+(?:of|and|the)\s+[A-Z][a-zA-Z]+|\s+[A-Z][a-zA-Z]+)*\b", headline)
    cleaned = []
    for run in runs:
        words = [w for w in run.split() if w.lower() not in _STOP]
        while words and words[0].lower() in ("of", "and"):
            words.pop(0)
        while words and words[-1].lower() in ("of", "and"):
            words.pop()
        if words:
            cleaned.append(" ".join(words))
    for run in sorted(cleaned, key=lambda r: -len(r.split())):
        add(run)
        # a two-word run is also worth trying as its first word alone
        # ("Kerala Rains" -> "Kerala"), but never a bare stopword
        parts = run.split()
        if len(parts) > 1 and parts[0].lower() not in _STOP:
            add(parts[0])
    return out[:8]


def _search_entities(name):
    """Wikidata items whose label matches `name` exactly and which are a
    physically locatable non-person. Returns
    [(qid, sitelinks, p18, description, label)]."""
    data = sp._get_json(WIKIDATA, {
        "action": "wbsearchentities", "search": name, "language": "en",
        "type": "item", "limit": 8, "format": "json"})
    if not data:
        return []
    ids = []
    for hit in data.get("search", []):
        matched = (hit.get("match") or {}).get("text") or hit.get("label")
        if sp._norm(matched) == sp._norm(name):
            ids.append(hit["id"])
    if not ids:
        return []
    ents = sp._get_json(WIKIDATA, {
        "action": "wbgetentities", "ids": "|".join(ids),
        "props": "claims|sitelinks|descriptions|labels", "languages": "en", "format": "json"})
    out = []
    for qid, e in ((ents or {}).get("entities") or {}).items():
        claims = e.get("claims", {})
        is_human = any(
            (c.get("mainsnak", {}).get("datavalue", {}).get("value") or {}).get("id") == "Q5"
            for c in claims.get("P31", []))
        if is_human:
            continue          # people are subject_photos' job, not this one
        # Physically locatable: has coordinates, or a headquarters location.
        # This is what separates a real place/institution from an abstract
        # concept, an event or a work -- and everything it admits is
        # something a photograph can honestly depict.
        if not (claims.get("P625") or claims.get("P159")):
            continue
        # MUST BE IN INDIA. Without this, a live run matched "DMK" (a Tamil
        # political party in the headline) to Don Mueang airport in Bangkok,
        # and "Change" to a similarly-named foreign settlement. The page
        # only covers Indian news, so anything outside India is a false
        # match by definition.
        countries = {(c.get("mainsnak", {}).get("datavalue", {}).get("value") or {}).get("id")
                     for c in claims.get("P17", [])}
        if _INDIA_QID not in countries:
            continue
        desc = (e.get("descriptions", {}).get("en") or {}).get("value", "")
        if _DESC_BLOCK.search(desc):
            continue
        p18 = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("P18", [])
                    if c.get("mainsnak", {}).get("datavalue")), None)
        commons_cat = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("P373", [])
                            if c.get("mainsnak", {}).get("datavalue")), None)
        label = (e.get("labels", {}).get("en") or {}).get("value", name)
        out.append((qid, len(e.get("sitelinks", {})), p18, desc, label, commons_cat))
    return out


COMMONS = "https://commons.wikimedia.org/w/api.php"

# Files that are not photographs of the place, or that carry trademark
# complications even when the file licence is free.
_FILE_BLOCK = re.compile(
    r"(map|logo|seal|flag|coat[_ ]of[_ ]arms|emblem|diagram|chart|graph|"
    r"locator|location|plan|sketch|drawing|icon|symbol|signature|"
    # scanned paperwork: a Commons category for an institution is full of
    # annual reports, tables and notices, which are not pictures of it
    r"table|report|document|scan|page|statistics|data|census|letter|"
    r"notice|certificate|stamp|coin|banknote|currency|note|cover|"
    r"title|book|poster|leaflet|form|circular|gazette|act|bill)", re.I)
_PHOTO_EXT = re.compile(r"\.(jpe?g|png)$", re.I)


def search_photos(query, limit=30):
    """Photographs on Commons matching a free-text search. An entity's own
    category is often only a handful of files (the Election Commission's
    holds three), so the search index is used as well to widen the pool of
    freely-licensed candidates."""
    data = sp._get_json(COMMONS, {
        "action": "query", "list": "search", "srsearch": f'{query} filemime:image',
        "srnamespace": 6, "srlimit": limit, "format": "json"})
    names = [m["title"].split(":", 1)[-1]
             for m in ((data or {}).get("query") or {}).get("search", [])]
    return [n for n in names if _PHOTO_EXT.search(n) and not _FILE_BLOCK.search(n)]


def category_photos(commons_cat, limit=40):
    """Filenames of photographs in an entity's Commons category. Wikidata's
    single lead image (P18) is often share-alike, while the same category
    usually also holds public-domain or CC BY photographs of the same
    place -- so the category is searched before giving up on a story."""
    if not commons_cat:
        return []
    data = sp._get_json(COMMONS, {
        "action": "query", "list": "categorymembers",
        "cmtitle": f"Category:{commons_cat}", "cmtype": "file",
        "cmlimit": limit, "format": "json"})
    names = [m["title"].split(":", 1)[-1]
             for m in ((data or {}).get("query") or {}).get("categorymembers", [])]
    return [n for n in names if _PHOTO_EXT.search(n) and not _FILE_BLOCK.search(n)]


def resolve_entity(name, min_sitelinks=None):
    """(qid, p18, label, description, commons_cat, reason). qid None means
    rejected -- `reason` says why, so a skip is explainable."""
    min_sl = (settings.CONTEXT_PHOTO_MIN_SITELINKS if min_sitelinks is None else min_sitelinks)
    found = _search_entities(name)
    notable = sorted([f for f in found if f[1] >= min_sl], key=lambda f: -f[1])
    if not notable:
        return None, None, "", "", None, "no notable place/institution with that exact name"
    if len(notable) > 1 and notable[0][1] < 2 * notable[1][1]:
        return None, None, "", "", None, f"ambiguous: several notable entities named {name!r}"
    qid, sl, p18, desc, label, cat = notable[0]
    if not p18 and not cat:
        return None, None, label, desc, None, "notable, but no image or Commons category"
    return qid, p18, label, desc, cat, "ok"


def looks_like_photograph(path):
    """True for an actual photograph, False for a scanned document, table,
    diagram or line drawing. Filename filtering alone is not enough -- a
    real run picked a scanned GDP statistics table out of the Reserve Bank
    of India's Commons category, which is plainly not a picture of the
    bank. Documents are overwhelmingly near-white and almost colourless;
    photographs are neither."""
    from PIL import Image
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return False
    im.thumbnail((200, 200))
    hsv = im.convert("HSV")
    sats = list(hsv.getchannel("S").getdata())
    vals = list(hsv.getchannel("V").getdata())
    n = len(sats) or 1
    mean_sat = sum(sats) / n
    near_white = sum(1 for s, v in zip(sats, vals) if v > 235 and s < 25) / n
    if near_white > 0.45:
        return False
    if mean_sat < 16:
        return False
    return True


def _usable_candidates(filenames, label, max_tries=14):
    """Files that clear the licence allowlist, the size floor and the shape
    check, best first. A context shot fills the whole picture window, so a
    small file looks soft at 1080px wide -- hence a bigger floor than a
    portrait. Whether the file is actually a photograph is decided after
    download, by looks_like_photograph()."""
    tried = 0
    for fn in [f for f in filenames if f]:
        if tried >= max_tries:
            break
        tried += 1
        info = sp.commons_file_info(fn)
        if not info or not info.get("url"):
            continue
        # Share-alike is accepted only on the terms set out in the module
        # docstring: shown whole and unmodified, with author + licence
        # printed on the post. _usable_candidates flags it so the renderer
        # can enforce that; a plain CC BY / PD file has no such constraint.
        share_alike = bool(sp._LICENSE_SA.search(info["license"] or ""))
        allow_sa = settings.CONTEXT_PHOTO_ALLOW_SHARE_ALIKE
        if not sp.licence_allowed(info["license"], allow_share_alike=allow_sa):
            continue
        info = dict(info, share_alike=share_alike and allow_sa)
        if not info["mime"].startswith("image/"):
            continue
        if min(info["width"], info["height"]) < settings.CONTEXT_PHOTO_MIN_PIXELS:
            continue
        # landscape-ish or square reads best in the picture window; a very
        # tall file gets letterboxed and looks like a mistake
        if info["height"] and info["width"] / info["height"] < 0.75:
            continue
        yield info


_RECENT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "context_recent.json")


def _recent_subjects(hours=None):
    """Subjects used on a post in the last `hours`. Four posts in a row
    carrying the same photo of the Election Commission reads as a broken
    feed, so a subject is not reused while it is still fresh."""
    import datetime as dt
    import json
    hours = settings.CONTEXT_PHOTO_REUSE_HOURS if hours is None else hours
    try:
        with open(_RECENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    now = dt.datetime.utcnow()
    out = {}
    for subject, stamp in data.items():
        try:
            age = (now - dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
        except Exception:
            continue
        if age < hours:
            out[subject] = stamp
    return out


def note_subject_used(subject):
    """Records that `subject` has just been posted."""
    import datetime as dt
    import json
    data = _recent_subjects()
    data[subject] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(_RECENT_PATH), exist_ok=True)
    with open(_RECENT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def find_context_photo(headline, summary="", dest_dir=None):
    """A verified, licensed photograph of a place or institution named in
    the headline, or None. The returned dict carries `note` -- the caption
    that must be shown on the post so the image is never mistaken for a
    picture of the event itself."""
    if not settings.CONTEXT_PHOTOS_ENABLED:
        return None
    if unsafe_for_context(headline) or unsafe_for_context(summary):
        return None

    for name in candidate_entities(headline):
        key = sp._norm(name)
        if key in _CACHE:
            hit = _CACHE[key]
            if hit is _UNUSABLE:
                return None
            if hit is not None:
                # the recency check has to happen here too: without it the
                # cache hands back the same photo for a run of stories about
                # one institution, which is what it exists to prevent
                if hit["subject"] in _recent_subjects():
                    print(f"context_photos: {hit['subject']} used too recently -- trying another entity")
                    continue
                return hit
            continue
        qid, p18, label, desc, cat, reason = resolve_entity(name)
        if not qid:
            _CACHE[key] = None
            continue
        if label in _recent_subjects():
            print(f"context_photos: {label} used too recently -- trying another entity")
            continue
        # The lead image first, then the entity's Commons category -- the
        # lead image is frequently share-alike while the same category holds
        # public-domain / CC BY photographs of the same place. Each
        # candidate is downloaded and checked before it is accepted, since
        # whether a file is a photograph or a scanned document cannot be
        # told from its metadata.
        import hashlib
        dest_dir = dest_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "context_photos")
        info = path = None
        pool = [p18] + category_photos(cat) + search_photos(label)
        for cand in _usable_candidates(pool, label):
            ext = ".png" if cand["mime"] == "image/png" else ".jpg"
            trial = os.path.join(dest_dir, "ctx_" + hashlib.sha1(
                (qid + cand["url"]).encode()).hexdigest()[:12] + ext)
            try:
                sp._download(cand["url"], trial)
            except Exception as e:
                print("context_photos: download failed:", e)
                continue
            if not looks_like_photograph(trial):
                print(f"context_photos: {label}: skipped a non-photograph file")
                try:
                    os.remove(trial)
                except OSError:
                    pass
                continue
            info, path = cand, trial
            break
        if not info:
            print(f"context_photos: {label}: no usable photograph found")
            _CACHE[key] = None
            continue
        who = info["artist"] or "Wikimedia Commons contributor"
        sa = bool(info.get("share_alike"))
        result = {
            "path": path, "subject": label, "wikidata_id": qid, "description": desc,
            "license": info["license"], "license_url": info["license_url"],
            "artist": who, "source_url": info["page"], "is_context": True,
            # a share-alike file must carry its author and licence on the
            # image itself, and must be shown whole and uncropped
            "share_alike": sa,
            "note": (f"File photo: {label} · {who} / {info['license']}"
                     if sa else f"File photo: {label}"),
            "attribution": f"{who} / {info['license']} (Wikimedia Commons)",
        }
        _CACHE[key] = result
        return result
    return None
