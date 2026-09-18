"""
Incident Photo Discovery + Verification + Licensing Engine
===========================================================
Finds a REAL photograph of the SPECIFIC event in a story, verifies it
actually depicts that event, and separately determines whether it may
lawfully be republished. Those are two independent questions and this
module never conflates them:

    AUTHENTICITY  -- is this really a photo of THIS event?
    REUSE RIGHTS  -- are we allowed to republish it?

A photo is only ever auto-published when BOTH pass. An authentic photo
with unclear rights goes to human review; an unverified photo is never
published no matter how permissive its licence. "No image" always beats
"wrong image".

MEASURED on this project's own live feeds (see tests/test_incident_photos.py):
  - og:image discovery succeeded on 7 of 12 live stories.
  - The misses were IPO-listing / box-office stories that genuinely have
    no incident photograph to find.

DISCOVERY TIERS actually implemented here (each verified working before
being included -- nothing is listed that was not tested):
  Tier 2  article og:image   -- the photo the publisher itself chose for
                               THIS story, so event-match is structural
                               rather than guessed. Highest hit rate.
                               Almost always LICENSE_REQUIRED.
  Tier 2b GDELT 2.0 DOC API  -- free, no key, cross-outlet corroboration
                               of the same event. Rate-limits on shared
                               IPs, so failures are tolerated quietly.
  Tier 4  Openverse          -- free, no key, returns explicit CC licence
                               metadata. This is the ONLY tier that
                               routinely yields FREE_REUSE, but it rarely
                               holds same-day incident photos.

NOT implemented, with the reason (verified, not assumed):
  - Bing Image Search: returns 401 PermissionDenied; Microsoft moved it to
    paid Azure AI. Not free, so excluded.
  - X/Twitter API: no free tier for search since 2023 (paid from $100/mo).
  - Instagram/Facebook Graph: cannot read arbitrary third-party accounts'
    media; permission model forbids it.
  - PIB photo gallery: free and same-day, but tested against 25 real
    headlines from this project and matched 0 -- it covers ceremonial
    photo-ops, not the incidents actually reported on.
"""

import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request

from config import settings

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------- licensing

FREE_REUSE = "FREE_REUSE"
PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
CREATIVE_COMMONS = "CREATIVE_COMMONS"
OFFICIAL_REUSE_ALLOWED = "OFFICIAL_REUSE_ALLOWED"
LICENSE_REQUIRED = "LICENSE_REQUIRED"
PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
UNKNOWN_LICENSE = "UNKNOWN"
DO_NOT_USE = "DO_NOT_USE"

# Commercial news publishers: their photographs are their property (or
# licensed to them from a wire service, which they cannot sub-license to
# us). Discovering these is fine and useful; republishing them is not, so
# they are always routed to human review rather than auto-publish.
_PUBLISHER_DOMAINS = (
    "thehindu.com", "indiatoday.in", "timesofindia", "indianexpress.com",
    "ndtv.com", "hindustantimes.com", "news18.com", "bbc.co", "bbc.com",
    "reuters.com", "aptn", "apnews.com", "ptinews.com", "aninews.in",
    "tosshub.com", "thgim.com", "livemint.com", "economictimes",
    "deccanherald.com", "thenewsminute.com", "dinamalar", "dailythanthi",
)

# Indian government / official domains. GoI material is generally
# reproducible, but terms differ per site, so this is deliberately
# OFFICIAL_REUSE_ALLOWED (needs attribution + a human eye) rather than
# being waved through as FREE_REUSE.
_OFFICIAL_DOMAINS = (
    ".gov.in", ".nic.in", "pib.gov.in", "isro.gov.in", "rbi.org.in",
    "indianrailways.gov.in", "ndrf.gov.in", "imd.gov.in", ".gov", "nasa.gov",
)


def classify_license(image_url, source_url, explicit_license=None):
    """Returns the licence block for a candidate. Never guesses permissive:
    anything unrecognised ends up UNKNOWN, which is not auto-publishable."""
    if explicit_license:
        lic = explicit_license.lower()
        if lic in ("cc0", "pdm", "public domain"):
            status, name = PUBLIC_DOMAIN, explicit_license
        else:
            status, name = CREATIVE_COMMONS, explicit_license
        return {
            "license_status": status,
            "license_name": name,
            "license_url": f"https://creativecommons.org/licenses/{lic}/4.0/",
            "attribution_required": status != PUBLIC_DOMAIN,
            "permission_required": False,
            "copyright_owner": "",
            "source_url": source_url,
        }

    host = urllib.parse.urlparse(image_url or source_url or "").netloc.lower()
    blob = f"{host} {source_url or ''}".lower()

    if any(d in blob for d in _OFFICIAL_DOMAINS):
        return {
            "license_status": OFFICIAL_REUSE_ALLOWED,
            "license_name": "Government/official source - verify page terms",
            "license_url": source_url,
            "attribution_required": True,
            "permission_required": False,
            "copyright_owner": host,
            "source_url": source_url,
        }

    if any(d in blob for d in _PUBLISHER_DOMAINS):
        return {
            "license_status": LICENSE_REQUIRED,
            "license_name": "All rights reserved (news publisher)",
            "license_url": source_url,
            "attribution_required": True,
            "permission_required": True,
            "copyright_owner": host,
            "source_url": source_url,
        }

    return {
        "license_status": UNKNOWN_LICENSE,
        "license_name": "Unknown - rights not established",
        "license_url": source_url,
        "attribution_required": True,
        "permission_required": True,
        "copyright_owner": host,
        "source_url": source_url,
    }


# ------------------------------------------------------- file-photo detection

_FILE_PHOTO_MARKERS = (
    "file photo", "file picture", "file image", "representative image",
    "representational image", "image for representation",
    "used for representation", "photo for representation", "archive photo",
    "archival", "earlier photograph", "old photograph", "stock image",
    "photo: archive", "picture for representation",
)


def detect_file_photo(*texts):
    """A publisher labelling its own image 'file photo' is the single most
    reliable signal available that the picture is NOT of today's event."""
    blob = " ".join(t.lower() for t in texts if t)
    return any(marker in blob for marker in _FILE_PHOTO_MARKERS)


# ------------------------------------------------------------- entity extract

_ENTITY_NOISE = {
    "the", "a", "an", "of", "in", "on", "at", "for", "to", "and", "or", "by",
    "after", "over", "with", "from", "his", "her", "its", "their", "new",
    "says", "said", "will", "not", "has", "have", "been", "was", "were",
    # capitalised pairs that are NOT people -- without these, "World
    # Championship" and "Supreme Court" get treated as person names.
    "world", "championship", "supreme", "court", "high", "union", "chief",
    "prime", "minister", "president", "police", "india", "indian", "state",
    "central", "national", "general", "department", "government",
}

# Words too generic to prove two texts describe the SAME event. Overlap
# consisting only of these is treated as no match at all.
_GENERIC_NEWS_WORDS = {
    "world", "championship", "china", "india", "indian", "national",
    "international", "today", "news", "photo", "image", "picture", "video",
    "generic", "logo", "event", "people", "person", "police", "government",
    "minister", "official", "officials", "report", "reports", "update",
    "latest", "breaking", "story", "media", "public", "state", "central",
    "country", "city", "district", "gold", "medal", "final", "match",
}

_INDIA_PLACES = (
    "chennai", "madurai", "coimbatore", "trichy", "salem", "tirunelveli",
    "vellore", "erode", "thanjavur", "tuticorin", "delhi", "mumbai",
    "kolkata", "bengaluru", "bangalore", "hyderabad", "pune", "ahmedabad",
    "jaipur", "lucknow", "patna", "bhopal", "kochi", "thiruvananthapuram",
    "goa", "kerala", "keralam", "tamil nadu", "karnataka", "maharashtra",
    "gujarat", "rajasthan", "punjab", "haryana", "bihar", "odisha", "assam",
    "telangana", "andhra pradesh", "uttar pradesh", "madhya pradesh",
    "west bengal", "jharkhand", "chhattisgarh", "uttarakhand", "gurugram",
    "noida", "gandhinagar", "mysuru", "nagpur", "surat", "indore", "kanpur",
)

_EVENT_TYPES = {
    "fire": ("fire", "blaze", "burn", "gutted"),
    "accident": ("accident", "crash", "collision", "derail", "overturn"),
    "flood": ("flood", "inundat", "deluge", "waterlog"),
    "collapse": ("collapse", "caved in", "crumbl"),
    "protest": ("protest", "rally", "march", "agitation", "strike", "dharna"),
    "arrest": ("arrest", "detain", "held", "nabbed", "surrender", "custody"),
    "raid": ("raid", "search operation", "seiz"),
    "court": ("court", "verdict", "judgment", "judgement", "bail", "fir", "plea"),
    "storm": ("cyclone", "storm", "rain", "landslide", "quake", "earthquake"),
    "blast": ("blast", "explosion", "bomb"),
    "announcement": ("announce", "launch", "unveil", "inaugurat", "approve"),
}


def extract_entities(headline, summary="", published=None):
    """Structured entities used to build search queries and to verify a
    candidate afterwards."""
    text = f"{headline} {summary}"
    low = text.lower()

    people = set()
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b", text):
        a, b = m.group(1), m.group(2)
        if a.lower() in _ENTITY_NOISE or b.lower() in _ENTITY_NOISE:
            continue
        people.add(f"{a} {b}")

    places = sorted({p for p in _INDIA_PLACES if p in low})
    event_type = next((k for k, pats in _EVENT_TYPES.items()
                       if any(p in low for p in pats)), "")

    when = published or dt.datetime.now(dt.timezone.utc)
    return {
        "event_type": event_type,
        "event_name": headline.strip(),
        "people": sorted(people),
        "organizations": [],
        "location": places[0] if places else "",
        "city": places[0] if places else "",
        "district": "",
        "state": "",
        "country": "India" if places else "",
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "case_number": "",
        "vehicle_number": "",
        "building_name": "",
        "specific_keywords": [w for w in re.findall(r"[a-z]{5,}", low)
                              if w not in _ENTITY_NOISE][:8],
    }


def build_queries(entities):
    """Multiple targeted queries per story, most specific first."""
    ev, loc, date = entities["event_type"], entities["location"], entities["date"]
    qs = []
    if loc and ev:
        qs += [f"{loc} {ev} today", f"{loc} {ev} {date}"]
    for person in entities["people"][:2]:
        qs += [f"{person} {ev}".strip(), f"{person} {date}"]
    if entities["event_name"]:
        qs.append(entities["event_name"])
    seen, out = set(), []
    for q in qs:
        q = " ".join(q.split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:6]


# ------------------------------------------------------------------ discovery

def _fetch(url, timeout=20):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=_UA), timeout=timeout
    ).read().decode("utf-8", errors="replace")


def discover_article_image(article_url):
    """Tier 2: the image the publisher attached to THIS article. Event match
    is structural -- the publisher selected it for this exact story -- which
    is why this is the highest-value discovery source despite almost always
    being rights-restricted."""
    try:
        page = _fetch(article_url)
    except Exception as e:
        return None
    img = None
    for prop in ("og:image", "twitter:image", "twitter:image:src"):
        m = (re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)', page, re.I)
             or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']', page, re.I))
        if m:
            img = html.unescape(m.group(1)).strip()
            break
    if not img:
        return None
    if img.startswith("//"):
        img = "https:" + img
    # caption/alt text near the image is where "file photo" usually appears
    context = " ".join(re.findall(r'(?:alt|title)=["\']([^"\']{10,200})["\']', page[:40000], re.I)[:12])
    m_time = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)', page, re.I)
    return {
        "image_url": img,
        "source_url": article_url,
        "source_name": urllib.parse.urlparse(article_url).netloc,
        "caption": context[:600],
        "published_at": m_time.group(1) if m_time else None,
        "tier": "article_og_image",
        "explicit_license": None,
    }


def discover_openverse(query, limit=4):
    """Tier 4: the only tier that reliably returns genuinely reusable
    images, with explicit CC licence metadata attached."""
    try:
        url = ("https://api.openverse.org/v1/images/?q="
               + urllib.parse.quote(query) + f"&page_size={limit}")
        data = json.loads(_fetch(url, timeout=25))
    except Exception:
        return []
    out = []
    for r in data.get("results", []):
        out.append({
            "image_url": r.get("url"),
            "thumbnail_url": r.get("thumbnail") or r.get("url"),
            "source_url": r.get("foreign_landing_url") or r.get("url"),
            "source_name": r.get("source") or "openverse",
            "caption": " ".join(filter(None, [r.get("title"), r.get("description")]))[:600],
            "published_at": r.get("created_on"),
            "tier": "openverse_cc",
            # Openverse is the one tier that hands us the licence directly
            # instead of us inferring it from the domain, and the one that
            # names a photographer -- both matter for attribution.
            "explicit_license": r.get("license"),
            "photographer": r.get("creator") or "",
        })
    return out


def discover_gdelt(query, limit=5):
    """Tier 2b: free, keyless, cross-outlet. Used for corroboration -- if
    several outlets carry the same event image it is very likely genuine.
    Rate-limits on shared IPs; failure is non-fatal by design."""
    try:
        url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
               + urllib.parse.quote(query)
               + f"&mode=ArtList&maxrecords={limit}&format=json&sort=datedesc")
        data = json.loads(_fetch(url, timeout=25))
    except Exception:
        return []
    out = []
    for a in data.get("articles", []):
        if not a.get("socialimage"):
            continue
        out.append({
            "image_url": a["socialimage"],
            "source_url": a.get("url"),
            "source_name": a.get("domain", ""),
            "caption": a.get("title", ""),
            "published_at": a.get("seendate"),
            "tier": "gdelt",
            "explicit_license": None,
        })
    return out


# --------------------------------------------------------------- verification

def _parse_when(value):
    """Robust enough for the formats these sources actually emit: ISO with
    or without fractional seconds/offset, bare dates, and GDELT's compact
    20260916T084500Z. A parse failure here used to silently fail
    time_match and downgrade a perfectly good same-day photo to UNCERTAIN."""
    if not value:
        return None
    value = str(value).strip()
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            d = dt.datetime.strptime(value[:len(dt.datetime.now().strftime(fmt)) + 6], fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def verify(candidate, entities, story_published=None):
    """Structured verification. Deliberately conservative: every check must
    actively PASS. Anything unproven stays False and drags the decision to
    UNCERTAIN, which never publishes."""
    cap = (candidate.get("caption") or "").lower()
    head = entities["event_name"].lower()

    # EVENT MATCH. For an article's own og:image this is structural: the
    # publisher attached it to this very story. For search-derived
    # candidates it must be earned from overlapping content words.
    if candidate["tier"] == "article_og_image":
        event_match, reason = True, "image published by the outlet on this exact article"
    else:
        head_words = {w for w in re.findall(r"[a-z]{5,}", head) if w not in _ENTITY_NOISE}
        cap_words = {w for w in re.findall(r"[a-z]{5,}", cap) if w not in _ENTITY_NOISE}
        overlap = head_words & cap_words
        # Generic overlap is NOT event match. "World Championship logo" vs
        # "archer wins World Championship gold" shares two whole words and
        # is still the wrong picture -- that is precisely the failure this
        # system exists to prevent. At least one DISTINCTIVE token (a
        # place, a name, a specific noun) has to match, not just filler.
        distinctive = overlap - _GENERIC_NEWS_WORDS
        named = [p.lower() for p in entities.get("people", [])]
        # If the story is about a named person, that person must appear.
        person_required_ok = (not named) or any(p in cap for p in named)
        event_match = bool(distinctive) and person_required_ok
        if not distinctive:
            reason = f"only generic words in common {sorted(overlap)} - not the same event"
        elif not person_required_ok:
            reason = f"story is about {named[0]}, who does not appear in the image caption"
        else:
            reason = f"caption shares distinctive {sorted(distinctive)} with headline"

    loc = entities.get("location", "")
    location_match = bool(loc) and loc in cap if not event_match or candidate["tier"] != "article_og_image" else True
    if candidate["tier"] == "article_og_image":
        location_match = True  # same article, so same event location

    people = entities.get("people", [])
    if people:
        person_match = any(p.lower() in cap for p in people) or candidate["tier"] == "article_og_image"
    else:
        person_match = True  # no person claimed, nothing to contradict

    img_when = _parse_when(candidate.get("published_at"))
    story_when = story_published or dt.datetime.now(dt.timezone.utc)
    if img_when:
        age_h = (story_when - img_when).total_seconds() / 3600
        time_match = -24 <= age_h <= 72
        image_age = f"{abs(age_h):.0f}h"
    else:
        time_match, image_age = False, "unknown"

    is_file_photo = detect_file_photo(cap, candidate.get("caption", ""))

    score = (30 * event_match + 20 * location_match + 15 * time_match
             + 10 * person_match + 10 * (candidate["tier"] == "article_og_image") + 5)
    confidence = round(min(score / 100.0, 0.99), 2)

    if not event_match:
        decision = "REJECT"
    elif is_file_photo:
        decision = "FILE_PHOTO"
    elif not time_match:
        decision = "UNCERTAIN"
    else:
        decision = "APPROVE"

    return {
        "event_match": event_match,
        "location_match": bool(location_match),
        "person_match": bool(person_match),
        "time_match": bool(time_match),
        "is_file_photo": is_file_photo,
        "confidence": confidence,
        "image_age": image_age,
        "reason": reason,
        "decision": decision,
    }


# ------------------------------------------------------------------- pipeline

def find_incident_photo_simple(story):
    """Full pipeline for one story. Returns the contract described in the
    project spec. Authenticity and reuse rights are evaluated separately
    and BOTH must pass before anything is auto-published."""
    published = story.get("published")
    entities = extract_entities(story.get("title", ""), story.get("summary", ""), published)

    candidates = []
    if story.get("link"):
        art = discover_article_image(story["link"])
        if art:
            candidates.append(art)
    for q in build_queries(entities)[:2]:
        candidates.extend(discover_openverse(q, limit=3))

    best = None
    for cand in candidates:
        v = verify(cand, entities, published)
        lic = classify_license(cand["image_url"], cand["source_url"], cand.get("explicit_license"))
        rec = {**cand, **v, **lic}
        if v["decision"] == "REJECT":
            continue
        if best is None or rec["confidence"] > best["confidence"]:
            best = rec

    if not best:
        return {
            "news_id": story.get("id", ""),
            "headline": story.get("title", ""),
            "image_status": "NO_VERIFIED_REAL_IMAGE",
            "decision": "TEXT_ONLY",
        }

    rights_ok = best["license_status"] in (FREE_REUSE, PUBLIC_DOMAIN,
                                            CREATIVE_COMMONS, OFFICIAL_REUSE_ALLOWED)
    authentic = best["decision"] == "APPROVE"

    if authentic and rights_ok:
        decision = "AUTO_PUBLISH"
    elif authentic or best["decision"] == "FILE_PHOTO":
        decision = "MANUAL_REVIEW"   # real photo, rights unclear/restricted
    else:
        decision = "TEXT_ONLY"       # not proven authentic -> never publish

    attribution = ""
    if best.get("attribution_required"):
        attribution = f"Photo: {best.get('copyright_owner') or best.get('source_name')}"

    return {
        "news_id": story.get("id", ""),
        "headline": story.get("title", ""),
        "image_status": "VERIFIED_REAL_IMAGE" if authentic else "UNVERIFIED",
        "image_url": best["image_url"],
        "source_url": best["source_url"],
        "source_name": best["source_name"],
        "published_at": best.get("published_at"),
        "image_age": best.get("image_age"),
        "event_match": best["event_match"],
        "location_match": best["location_match"],
        "person_match": best["person_match"],
        "time_match": best["time_match"],
        "is_file_photo": best["is_file_photo"],
        "authenticity_confidence": best["confidence"],
        "license_status": best["license_status"],
        "license": best["license_name"],
        "attribution_required": best["attribution_required"],
        "attribution": attribution,
        "tier": best["tier"],
        "decision": decision,
    }


# ============================================================================
# DECISION STATES  (the only primary states this engine may return)
# ============================================================================
VERIFIED_FREE_IMAGE = "VERIFIED_FREE_IMAGE"
VERIFIED_LICENSED_IMAGE = "VERIFIED_LICENSED_IMAGE"
VERIFIED_COPYRIGHTED_IMAGE = "VERIFIED_COPYRIGHTED_IMAGE"
VERIFIED_FILE_PHOTO = "VERIFIED_FILE_PHOTO"
NO_VERIFIED_IMAGE = "NO_VERIFIED_IMAGE"
UNCERTAIN_IMAGE = "UNCERTAIN_IMAGE"
OWNER_MEDIA = "OWNER_MEDIA"

AUTO_PUBLISH = "AUTO_PUBLISH"
MANUAL_REVIEW = "MANUAL_REVIEW"
TEXT_ONLY = "TEXT_ONLY"
REJECT = "REJECT"
REQUEST_PERMISSION = "REQUEST_PERMISSION"

# Licences that actually permit republication. OWNER_CONTROLLED is the
# user's own camera roll (see OWN_MEDIA below) -- the only source that is
# free by construction rather than by verification.
OWNER_CONTROLLED = "OWNER_CONTROLLED"
_REUSABLE = {FREE_REUSE, PUBLIC_DOMAIN, CREATIVE_COMMONS,
             OFFICIAL_REUSE_ALLOWED, OWNER_CONTROLLED}


def is_reusable(license_status):
    """The single place that decides 'may we republish this?'. Everything
    not explicitly listed as permissive is not -- UNKNOWN is a refusal,
    not a maybe."""
    return license_status in _REUSABLE


# ============================================================================
# IMAGE AGE
# ============================================================================

def age_bucket(published_at, now=None):
    """How fresh the PHOTOGRAPH is. A same-day photo of a fire is the story;
    a three-year-old one of the same building is a file photo wearing a
    disguise, which is exactly the mistake this project keeps guarding
    against."""
    when = _parse_when(published_at)
    if not when:
        return "UNKNOWN"
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    hours = (now - when).total_seconds() / 3600.0
    if hours < 0:
        # Future-dated metadata (timezone sloppiness at the publisher) --
        # treat small negatives as "just now", anything wilder as unknown.
        return "BREAKING" if hours > -6 else "UNKNOWN"
    if hours <= 1:
        return "BREAKING"
    if hours <= 6:
        return "VERY_RECENT"
    if hours <= 24:
        return "SAME_DAY"
    if hours <= 72:
        return "RECENT"
    return "OLD"


# ============================================================================
# SAME-EVENT IDENTITY  (clustering)
# ============================================================================

def _stem(word):
    """Minimal, deliberately conservative stemmer -- strips one trailing
    's' when the result is still a real-looking word. Found necessary
    from a live carousel test: 'ECI freezes the symbol' and 'Mamata
    Protests EC Freeze' are obviously the same event, but 'freezes' and
    'freeze' shared no keyword at all without this, and the pair fell
    just under the same_event() threshold. This intentionally does not
    attempt '-ing'/'-ed'/full stemming -- a single trailing-'s' strip
    covers plural/third-person-verb mismatches (the actual failure seen)
    without the false-collision risk of a more aggressive stemmer."""
    if word.endswith("s") and len(word) > 5:
        return word[:-1]
    return word


def event_keywords(entities):
    """The distinctive words that identify an event, with filler and
    generic news vocabulary stripped out. Two outlets rarely use the same
    sentence for the same fire, but they almost always use the same
    handful of concrete nouns."""
    return frozenset(
        _stem(w) for w in re.findall(r"[a-z]{5,}", (entities.get("event_name") or "").lower())
        if w not in _ENTITY_NOISE and w not in _GENERIC_NEWS_WORDS)


def same_event(a_kw, b_kw, threshold=0.5):
    """Do two headlines describe the same real event? Measured as overlap
    against the SMALLER keyword set, not Jaccard: "Massive fire guts
    Chennai godown, three dead" and "Three dead as fire guts godown in
    Chennai" are the same fire, but one carries an extra adjective, and
    Jaccard punishes that asymmetry enough to split them apart."""
    if not a_kw or not b_kw:
        return False
    return len(a_kw & b_kw) / min(len(a_kw), len(b_kw)) >= threshold


def event_prefix(entities):
    """IN-CHENNAI-FIRE-2026-09-16 -- everything about an event identity
    that can be derived from one article on its own."""
    country = "IN" if entities.get("country") == "India" else "XX"
    place = (entities.get("location") or "unknown").upper().replace(" ", "-")
    ev = (entities.get("event_type") or "news").upper()
    return f"{country}-{place}-{ev}-{entities.get('date','')}"


def make_event_id(entities, known=()):
    """A stable identity for the real-world EVENT, independent of which
    outlet reported it: IN-CHENNAI-RAID-2026-09-16-001.

    This is what lets the system say "The Hindu owns the photo on article
    A, but the district administration published one of the same raid on
    article C". Without an event identity, every article is an island and
    cross-source alternatives are invisible.

    `known` is [(event_id, headline), ...] of events already recorded --
    normally passed in from the database. A story that matches one of them
    joins it; otherwise the next free sequence number under this prefix is
    allocated. The sequence is deliberately NOT a hash of the headline:
    hashing makes two outlets' wording of the same fire collide only when
    they choose identical words, which they do not."""
    prefix = event_prefix(entities)
    mine = event_keywords(entities)
    used = set()
    for event_id, headline in known:
        if not str(event_id).startswith(prefix + "-"):
            continue
        used.add(str(event_id))
        theirs = event_keywords(extract_entities(headline or ""))
        if same_event(mine, theirs):
            return str(event_id)
    return f"{prefix}-{len(used) + 1:03d}"


# ============================================================================
# RIGHTS-TARGETED ALTERNATIVE SEARCH  (the point of the whole deep layer)
# ============================================================================
# The measured reality on this account's own stories: discovery works
# (9/14 exact photos found) and rights fail (0/14 reusable). Finding the
# India Today photo a second time does not help. What helps is finding a
# photograph of THE SAME EVENT taken by someone whose material can be
# reused -- the police department, the district administration, the state
# disaster force, a government press release, a CC contributor.
#
# So these queries are deliberately biased towards the official record of
# the event rather than towards the best picture of it.

_RIGHTS_QUERY_SUFFIXES = (
    "official photo",
    "police photo",
    "press release photo",
    "district administration",
    "government statement",
)


def build_rights_queries(entities, limit=6):
    """Alternative-source queries: same event, different (hopefully
    reusable) photographer."""
    loc = entities.get("location", "")
    ev = entities.get("event_type", "")
    date = entities.get("date", "")
    core = " ".join(x for x in (loc, ev) if x) or entities.get("event_name", "")[:60]
    qs = [f"{core} {suffix}" for suffix in _RIGHTS_QUERY_SUFFIXES]
    if loc and ev:
        qs.append(f"{loc} {ev} {date}")
    for person in entities.get("people", [])[:1]:
        qs.append(f"{person} {ev} photo".strip())
    for org in entities.get("organizations", [])[:1]:
        qs.append(f"{org} {ev} photo".strip())
    seen, out = set(), []
    for q in qs:
        q = " ".join(q.split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:limit]


def _domain_class(url):
    blob = (url or "").lower()
    if any(d in blob for d in _OFFICIAL_DOMAINS):
        return "official"
    if any(d in blob for d in _PUBLISHER_DOMAINS):
        return "publisher"
    return "other"


def discover_alternatives(entities, budget=8):
    """Step 7-10: hunt for other photographs of the SAME event, preferring
    sources whose material might actually be reusable.

    Note on GDELT: its DOC API has no 'only official domains' filter, so
    this searches normally and then sorts official-domain hits to the
    front rather than pretending a server-side filter exists. Openverse is
    queried too because it is the only tier that returns an explicit
    licence -- it rarely has same-day incident photos, but when it does,
    that is the single most valuable result this system can produce."""
    found, seen_urls = [], set()
    for q in build_rights_queries(entities):
        if len(found) >= budget:
            break
        batch = discover_gdelt(q, limit=5) + discover_openverse(q, limit=3)
        for cand in batch:
            url = cand.get("image_url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            cand["query"] = q
            found.append(cand)
            if len(found) >= budget:
                break
    # Official first, then CC, then everything else: cheapest possible way
    # to spend the remaining verification effort where a reusable result is
    # most likely to come from.
    rank = {"official": 0, "other": 1, "publisher": 2}
    found.sort(key=lambda c: (0 if c.get("explicit_license") else 1,
                              rank.get(_domain_class(c.get("source_url") or c.get("image_url")), 1)))
    return found


# ============================================================================
# FULL PIPELINE
# ============================================================================

def _evaluate(cand, entities, published, rank):
    """One candidate -> the flat record the DB and dashboard both store."""
    v = verify(cand, entities, published)
    lic = classify_license(cand["image_url"], cand.get("source_url"),
                           cand.get("explicit_license"))
    img_url = cand.get("image_url") or ""
    rec = {
        "candidate_id": hashlib.sha1(
            f"{entities.get('event_name','')}|{img_url}".encode("utf-8")).hexdigest()[:16],
        "image_url": img_url,
        "thumbnail_url": cand.get("thumbnail_url") or img_url,
        "source_url": cand.get("source_url"),
        "source_name": cand.get("source_name"),
        "caption": cand.get("caption", "")[:600],
        "published_at": cand.get("published_at"),
        "discovered_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "tier": cand.get("tier"),
        "photographer": cand.get("photographer", ""),
        "rank_in_story": rank,
        "age_bucket": age_bucket(cand.get("published_at")),
    }
    rec.update(v)
    rec.update(lic)
    rec["reusable"] = is_reusable(rec["license_status"])
    rec["authentic"] = v["decision"] == "APPROVE"
    return rec


def _image_status(rec):
    if rec is None:
        return NO_VERIFIED_IMAGE
    if not rec["event_match"]:
        return NO_VERIFIED_IMAGE
    if rec["is_file_photo"]:
        return VERIFIED_FILE_PHOTO
    if not rec["authentic"]:
        return UNCERTAIN_IMAGE
    if rec["license_status"] in (FREE_REUSE, PUBLIC_DOMAIN, CREATIVE_COMMONS):
        return VERIFIED_FREE_IMAGE
    if rec["license_status"] == OFFICIAL_REUSE_ALLOWED:
        return VERIFIED_LICENSED_IMAGE
    return VERIFIED_COPYRIGHTED_IMAGE


def _decide(rec):
    """Authenticity and rights are combined HERE and only here, after both
    have been established independently. Order matters: a photo that is
    not proven to be this event can never publish, however free it is."""
    if rec is None:
        return TEXT_ONLY, "No authentic incident photograph was found from the available sources."
    if not rec["authentic"]:
        if rec["is_file_photo"]:
            return MANUAL_REVIEW, "Real photo from this story, but the publisher labelled it a file photo."
        if not rec["event_match"]:
            return TEXT_ONLY, rec.get("reason") or "Could not confirm this photo shows this event."
        return MANUAL_REVIEW, rec.get("reason") or "Photo could not be fully verified."
    if rec["reusable"]:
        return AUTO_PUBLISH, f"Exact incident photo, {rec['license_name']} -- cleared to publish."
    if rec["license_status"] == LICENSE_REQUIRED:
        return MANUAL_REVIEW, "Exact incident photo found, but publisher copyright applies."
    return MANUAL_REVIEW, "Exact incident photo found, but reuse rights could not be established."


def find_incident_photo(story, deep=None, budget=None, known_events=()):
    """Full pipeline for one story.

    Two passes, and the second one is the whole reason this exists:
      1. Find the photo the publisher attached to this story, plus a few
         CC candidates. Verify each; keep the best authentic one.
      2. If that best photo is authentic but NOT reusable, keep going --
         search for a different photograph of the SAME event from a source
         whose material can be republished. Stop the moment one verifies.

    Returns the full structured contract plus every candidate considered
    (winners and losers), so the caller can persist the rejects too."""
    deep = settings.PHOTO_DEEP_SEARCH if deep is None else deep
    budget = budget or settings.PHOTO_SEARCH_BUDGET

    published = story.get("published")
    entities = extract_entities(story.get("title", ""), story.get("summary", ""), published)
    event_id = make_event_id(entities, known_events)

    evaluated, rank = [], 0
    best = None          # best AUTHENTIC candidate, reusable or not
    best_reusable = None # best authentic AND reusable candidate

    def consider(cands):
        nonlocal rank, best, best_reusable
        for cand in cands:
            if rank >= budget:
                return
            if not cand.get("image_url"):
                continue
            rank += 1
            rec = _evaluate(cand, entities, published, rank)
            rec["event_id"] = event_id
            evaluated.append(rec)
            if rec["decision"] == "REJECT":
                continue   # kept in `evaluated` on purpose -- rejects are evidence
            if best is None or rec["confidence"] > best["confidence"]:
                best = rec
            if rec["authentic"] and rec["reusable"]:
                if best_reusable is None or rec["confidence"] > best_reusable["confidence"]:
                    best_reusable = rec

    primary = []
    if story.get("link"):
        art = discover_article_image(story["link"])
        if art:
            primary.append(art)
    for q in build_queries(entities)[:2]:
        primary.extend(discover_openverse(q, limit=3))
    consider(primary)

    # Pass 2 only runs when it could change the answer: we already have a
    # real photo of the event but are not allowed to use it (or have
    # nothing at all). If pass 1 already produced a reusable photo there
    # is nothing left to look for, and the remaining budget is not spent.
    deep_ran = False
    if deep and best_reusable is None and rank < budget:
        deep_ran = True
        consider(discover_alternatives(entities, budget=budget - rank))

    chosen = best_reusable or best
    decision, reason = _decide(chosen)
    status = _image_status(chosen)

    result = {
        "article_id": story.get("id", ""),
        "news_id": story.get("id", ""),          # legacy key, kept for callers
        "headline": story.get("title", ""),
        "article_url": story.get("link", ""),
        "event_id": event_id,
        "image_status": status,
        "decision": decision,
        "reason": reason,
        "deep_search_ran": deep_ran,
        "candidates_evaluated": len(evaluated),
        "candidates": evaluated,
        "entities": entities,
    }
    if chosen is None:
        result.update({
            "image_url": None, "thumbnail_url": None, "source_name": None,
            "source_url": None, "published_at": None, "discovered_at": None,
            "event_match": False, "location_match": False, "person_match": False,
            "time_match": False, "authenticity_confidence": 0.0,
            "is_file_photo": False, "license_status": None, "copyright_owner": None,
            "photographer": None, "license_name": None, "license_url": None,
            "attribution_required": None, "attribution": "", "age_bucket": "UNKNOWN",
            "tier": None, "image_age": None,
        })
        return result

    attribution = ""
    if chosen.get("attribution_required"):
        who = chosen.get("photographer") or chosen.get("copyright_owner") or chosen.get("source_name")
        attribution = f"Photo: {who}"

    result.update({
        "image_url": chosen["image_url"],
        "thumbnail_url": chosen["thumbnail_url"],
        "source_name": chosen["source_name"],
        "source_url": chosen["source_url"],
        "published_at": chosen["published_at"],
        "discovered_at": chosen["discovered_at"],
        "event_match": chosen["event_match"],
        "location_match": chosen["location_match"],
        "person_match": chosen["person_match"],
        "time_match": chosen["time_match"],
        "authenticity_confidence": chosen["confidence"],
        "is_file_photo": chosen["is_file_photo"],
        "license_status": chosen["license_status"],
        "copyright_owner": chosen["copyright_owner"],
        "photographer": chosen.get("photographer") or "",
        "license_name": chosen["license_name"],
        "license_url": chosen["license_url"],
        "attribution_required": chosen["attribution_required"],
        "attribution": attribution,
        "age_bucket": chosen["age_bucket"],
        "tier": chosen["tier"],
        "image_age": chosen.get("image_age"),
        "license": chosen["license_name"],   # legacy key
        "chosen_candidate_id": chosen["candidate_id"],
    })
    return result


def owner_media_result(story, media_path):
    """Step 12: OWN_MEDIA. Footage the user shot or personally chose and
    sent through Telegram. Rights are not in question -- they own it -- and
    authenticity is established the strongest way available anywhere in
    this system: a human looked at it and said 'this is the thing'. That
    is why this path, and only this path, publishes without verification
    searching."""
    return {
        "article_id": story.get("id", ""),
        "headline": story.get("title", ""),
        "image_status": OWNER_MEDIA,
        "decision": AUTO_PUBLISH,
        "reason": "Supplied by the account owner.",
        "image_url": media_path,
        "thumbnail_url": media_path,
        "source_name": "OWN_MEDIA",
        "source_url": "",
        "license_status": OWNER_CONTROLLED,
        "license_name": "Owned by the account",
        "license_url": None,
        "copyright_owner": settings.BRAND_HANDLE,
        "photographer": settings.BRAND_HANDLE,
        "attribution_required": False,
        "attribution": "",
        "event_match": True, "location_match": True,
        "person_match": True, "time_match": True,
        "is_file_photo": False,
        "authenticity_confidence": 1.0,
        "age_bucket": "BREAKING",
        "candidates": [],
        "candidates_evaluated": 0,
        "event_id": "",
        "deep_search_ran": False,
    }
