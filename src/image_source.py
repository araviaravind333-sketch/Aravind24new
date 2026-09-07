"""
Image Source
============
Gets a high-quality, LEGALLY SAFE photo for the story. Order:
  1. Wikimedia Commons — searched for a real named person/place/institution
     found in the headline (e.g. "Revanth Reddy", "RBI"). Everything on
     Commons is explicitly free-licensed (CC/public domain) by its own
     policy, so this is the one source that can show the ACTUAL subject
     of the story without any copyright risk.
  2. Openverse — a free (no API key) aggregator covering Wikimedia Commons
     + Flickr Creative Commons + museum archives. Tried after Commons for
     named subjects Commons's own search doesn't surface well.
  3. Pexels, then Unsplash — real stock photography, keyword-matched to
     the headline, for stories with no specific named subject.
  4. Openverse again — broadest net, last resort, for topical queries
     neither Pexels nor Unsplash matched.
No AI-generated images (never a hallucinated scene), no publisher/wire
photos scraped from news sites or search engines (copyright / account-ban
risk — this is the thing that actually gets pages nuked at scale).
"""

import os
import re
import urllib.request
import urllib.parse
import json

from config import settings

TMP = os.path.join(os.path.dirname(__file__), "..", "data", "tmp_image.jpg")

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
    "is", "are", "was", "were", "with", "by", "from", "as", "after",
    "over", "amid", "amidst", "into", "out", "up", "down", "this", "that",
    "will", "has", "have", "had", "its", "it's", "says", "said", "not",
    "new", "latest",
    # Generic news-reporting verbs — these flood headlines ("hold talks",
    # "meets", "tells reporters") but have no distinctive topical meaning,
    # and a literal-word search engine can match them against something
    # totally unrelated (confirmed: "hold" matched a "hold on to your
    # children" safety-sign stock photo for a diplomatic-talks story).
    "hold", "holds", "holding", "held", "talk", "talks", "talking",
    "meet", "meets", "meeting", "met", "tell", "tells", "telling", "told",
    "give", "gives", "giving", "gave", "given", "plan", "plans", "planning",
    "planned", "get", "gets", "getting", "got", "today", "announce",
    "announces", "announcing", "announced", "report", "reports",
    "reporting", "reported", "claim", "claims", "claiming", "claimed",
    "urge", "urges", "urging", "urged", "call", "calls", "calling",
    "called", "seek", "seeks", "seeking", "sought", "vow", "vows",
    "vowing", "vowed", "eye", "eyes", "eyeing", "eyed", "face", "faces",
    "facing", "faced", "set", "sets", "setting", "aim", "aims", "aiming",
    "aimed", "move", "moves", "moving", "moved", "push", "pushes",
    "pushing", "pushed", "want", "wants", "wanting", "wanted", "ask",
    "asks", "asking", "asked", "hit", "hits", "hitting", "make", "makes",
    "making", "made", "take", "takes", "taking", "taken", "keep", "keeps",
    "keeping", "kept", "come", "comes", "coming", "came", "goes", "going",
    "went", "gone", "lead", "leads", "leading", "led", "show", "shows",
    "showing", "shown", "showed", "remain", "remains", "remaining",
    "remained", "stay", "stays", "staying", "stayed", "continue",
    "continues", "continuing", "continued", "begin", "begins",
    "beginning", "began", "begun", "start", "starts", "starting",
    "started", "end", "ends", "ending", "ended", "find", "finds",
    "finding", "found", "bring", "brings", "bringing", "brought", "send",
    "sends", "sending", "sent", "raise", "raises", "raising", "raised",
    "cut", "cuts", "cutting", "add", "adds", "adding", "added",
    "clarify", "clarifies", "clarifying", "clarified", "confirm",
    "confirms", "confirming", "confirmed", "deny", "denies", "denying",
    "denied", "admit", "admits", "admitting", "admitted", "reveal",
    "reveals", "revealing", "revealed", "insist", "insists", "insisting",
    "insisted", "stress", "stresses", "stressing", "stressed", "assure",
    "assures", "assuring", "assured", "explain", "explains", "explaining",
    "explained", "defend", "defends", "defending", "defended", "blame",
    "blames", "blaming", "blamed", "accuse", "accuses", "accusing",
    "accused", "respond", "responds", "responding", "responded", "react",
    "reacts", "reacting", "reacted", "welcome", "welcomes", "welcoming",
    "welcomed", "praise", "praises", "praising", "praised", "criticise",
    "criticises", "criticising", "criticised", "criticize", "criticizes",
    "criticizing", "criticized", "back", "backs", "backing", "backed",
    "reject", "rejects", "rejecting", "rejected", "support", "supports",
    "supporting", "supported", "oppose", "opposes", "opposing", "opposed",
    "demand", "demands", "demanding", "demanded", "appeal", "appeals",
    "appealing", "appealed", "order", "orders", "ordering", "ordered",
    "direct", "directs", "directing", "directed", "instruct", "instructs",
    "instructing", "instructed", "warn", "warns", "warning", "warned",
    "pledge", "pledges", "pledging", "pledged", "promise", "promises",
    "promising", "promised", "slam", "slams", "slamming", "slammed",
    "note", "notes", "noting", "noted", "state", "states", "stating",
    "stated", "argue", "argues", "arguing", "argued", "express",
    "expresses", "expressing", "expressed", "highlight", "highlights",
    "highlighting", "highlighted", "outline", "outlines", "outlining",
    "outlined", "describe", "describes", "describing", "described",
    "discuss", "discusses", "discussing", "discussed", "debate", "debates",
    "debating", "debated", "question", "questions", "questioning",
    "questioned", "doubt", "doubts", "doubting", "doubted",
    # Common headline NOUNS — a Title-Case headline capitalizes these too,
    # and 2+ of them in a row previously passed the "multi-word = entity"
    # check even though neither is a proper noun (confirmed: "Passengers
    # Stranded" was searched as a named entity and matched an unrelated
    # stranded-hikers photo for a Jakarta volcanic-ash story).
    "passenger", "passengers", "stranded", "official", "officials",
    "authority", "authorities", "resident", "residents", "worker",
    "workers", "student", "students", "teacher", "teachers", "doctor",
    "doctors", "nurse", "nurses", "soldier", "soldiers", "troop", "troops",
    "force", "forces", "protester", "protesters", "demonstrator",
    "demonstrators", "activist", "activists", "supporter", "supporters",
    "opponent", "opponents", "critic", "critics", "expert", "experts",
    "analyst", "analysts", "survivor", "survivors", "victim", "victims",
    "witness", "witnesses", "volunteer", "volunteers", "citizen",
    "citizens", "voter", "voters", "farmer", "farmers", "employee",
    "employees", "staff", "minister", "ministers", "leader", "leaders",
    "chief", "chiefs", "member", "members", "delegate", "delegates",
    "representative", "representatives", "officer", "officers", "agent",
    "agents", "investigator", "investigators", "rescuer", "rescuers",
    "firefighter", "firefighters", "operation", "operations", "mission",
    "missions", "project", "projects", "policy", "policies", "deal",
    "deals", "agreement", "agreements", "summit", "summits", "visit",
    "visits", "tour", "tours", "campaign", "campaigns", "result",
    "results", "survey", "surveys", "figure", "figures", "flight",
    "flights", "train", "trains", "bus", "buses", "bridge", "bridges",
    "school", "schools", "hospital", "hospitals", "market", "markets",
    "price", "prices", "rate", "rates", "tax", "taxes", "bill", "bills",
    "law", "laws", "rule", "rules", "order", "orders", "concern",
    "concerns", "issue", "issues", "problem", "problems", "challenge",
    "challenges", "crisis", "disaster", "incident", "accident", "event",
    "ceremony",
}


def _save(data, path=TMP):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _keywords(title, limit=4):
    """Pull the most meaningful words out of the headline for a photo search."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", title)
    seen = []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or len(w) < 3:
            continue
        if lw not in [s.lower() for s in seen]:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


_ENTITY_STOPSTART = {
    "the", "a", "an", "this", "that", "why", "how", "what", "who", "when",
}

# A photo of a PLACE isn't the same as a photo of the EVENT that happened
# there — a scenic Sikkim mountain lake doesn't depict a landslide. So a
# place-name entity gets tried only as a fallback, after topical/thematic
# search (which encodes the actual event) has had its shot — unlike a
# person/institution entity (Zelensky, RBI), where a real photo of the
# subject genuinely IS the right image.
INDIAN_STATES_UTS = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand",
    "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "punjab", "rajasthan",
    "sikkim", "tamil nadu", "telangana", "tripura", "uttar pradesh",
    "uttarakhand", "west bengal", "andaman and nicobar", "chandigarh",
    "dadra and nagar haveli", "daman and diu", "delhi", "jammu and kashmir",
    "ladakh", "lakshadweep", "puducherry",
}
WORLD_PLACE_NAMES = {
    "india", "usa", "united states", "america", "china", "russia",
    "ukraine", "pakistan", "uk", "britain", "japan", "israel", "gaza",
    "france", "germany", "canada", "australia", "bangladesh", "sri lanka",
    "nepal", "iran", "saudi", "kyiv", "moscow", "beijing", "washington",
    "london", "paris", "berlin", "tokyo", "delhi", "mumbai", "kolkata",
    "chennai", "bengaluru", "bangalore", "hyderabad", "pune", "ahmedabad",
}
PLACE_NAMES = INDIAN_STATES_UTS | WORLD_PLACE_NAMES


def _is_place_entity(entity):
    return entity.lower() in PLACE_NAMES


_ENTITY_CONNECTORS = {"of", "and", "the", "de"}


def _proper_noun_phrases(title, limit=3):
    """Pull real named-entity candidates (people/places/institutions) out of
    the headline — runs of capitalized words, short ALL-CAPS acronyms
    (RBI, ISRO, TCS), plus single-word proper nouns like a surname
    ("Zelensky", "Putin") — most specific (longest) first.

    Built word-by-word (not one regex) specifically so a run BREAKS at a
    stopword/generic word — a regex that just matches "any run of
    capitalized words" has no way to know mid-match that a Title-Case
    headline's "Amid", "Clarifies", "Blocks" etc. aren't part of a name,
    so a fully Title-Cased headline was being swallowed whole as one giant
    "entity" (confirmed: "Pawan Goenka Clarifies ISRO's Future Amid
    Privatisation Concerns" matched as a single phrase and searched
    verbatim, finding nothing sensible)."""
    words = re.findall(r"[A-Za-z]+", title)
    runs, current = [], []
    for i, w in enumerate(words):
        lw = w.lower()
        if w[0].isupper() and lw not in STOPWORDS:
            current.append((i, w))
        elif lw in _ENTITY_CONNECTORS and current:
            current.append((i, w))  # tentative bridge, trimmed below if unused
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    seen, out = set(), []
    for run in runs:
        while run and run[-1][1].lower() in _ENTITY_CONNECTORS:
            run.pop()  # drop a trailing connector that never reached another name
        if not run:
            continue
        start_idx = run[0][0]
        ws = [w for _, w in run]
        phrase = " ".join(ws)
        if phrase.lower() in _ENTITY_STOPSTART:
            continue
        is_multi_word = len(ws) >= 2
        is_acronym = phrase.isupper() and 2 <= len(phrase) <= 6
        # a single capitalized word can be a real name too — but only if
        # it's not the sentence-initial word (every headline capitalizes
        # its first word regardless of whether it's a proper noun, so that
        # position carries no signal)
        is_real_name = len(ws) == 1 and start_idx > 0 and len(phrase) >= 4
        if not (is_multi_word or is_acronym or is_real_name):
            continue
        if phrase.lower() not in seen:
            seen.add(phrase.lower())
            out.append(phrase)
    out.sort(key=len, reverse=True)
    return out[:limit]


_COMMONS_UA = "AravindNews24Bot/1.0 (https://github.com/araviaravind333-sketch/Aravind24new)"
# Filename-based only — this catches a file literally named "X logo.png",
# but can't detect a small watermark baked into a real photo's pixels (e.g.
# some government press-office handouts stamp a corner logo). That needs
# actual image recognition (paid Vision APIs), which is out of scope for a
# zero-cost pipeline — a rare watermarked press photo can still slip through.
_LOGO_HINTS = (
    "logo", "icon", "wordmark", "emblem", "seal", "coat of arms",
    "banner", "poster", "advertisement", "watermark", "letterhead",
    "press release", "screenshot", "infographic",
)


_IMPACT_WORDS = {
    "person", "people", "man", "men", "woman", "women", "crowd", "protest",
    "protesters", "protester", "rescue", "rescuers", "rescuer", "victim",
    "victims", "worker", "workers", "children", "child", "family", "face",
    "faces", "portrait", "soldier", "soldiers", "police", "firefighter",
    "firefighters", "survivor", "survivors", "activist", "activists",
    "leader", "minister", "president", "officer", "officers", "crying",
    "grief", "hands", "hand", "eyes", "crying", "emotional", "injured",
}


def _impact_score(text):
    """A photo with real human presence — a face, a crowd, rescuers,
    grief — reads as far more compelling than an empty landscape or object
    shot, even when both are equally relevant. Used as a secondary ranking
    signal among already-relevant candidates, never as a relevance gate."""
    words = set(re.findall(r"[a-zA-Z]+", (text or "").lower()))
    return len(words & _IMPACT_WORDS)


def _text_relevance(words, text):
    """How many of `words` actually appear in `text` — used to reject a
    result that merely matched the search API's own fuzzy/full-text ranking
    without actually depicting the subject (e.g. a market-street stock photo
    surfacing for "Delhi building collapse" because it's tagged "Delhi")."""
    word_set = {w.lower() for w in words if len(w) >= 3}
    text_words = set(re.findall(r"[a-zA-Z]+", (text or "").lower()))
    return len(word_set & text_words)


def _title_relevance(entity, file_title):
    """How many of the entity's significant words actually appear in the
    file's title. Commons' full-text search matches on categories/
    descriptions too, so a query with little dedicated coverage (e.g. a
    small neighborhood name) can return something merely keyword-adjacent —
    a violinist for a Delhi building collapse story, say. Requiring the
    title itself to actually mention the subject catches that."""
    return _text_relevance(entity.split(), file_title)


def _wikimedia_commons(entity):
    """Search Commons for a real photo of a specific named subject. Every
    file on Commons is required by its own policy to be free-licensed, so
    no separate license check is needed — but logos/icons/svg wordmarks are
    filtered out since those carry separate trademark risk, not copyright."""
    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f'{entity} filetype:bitmap',
        "gsrnamespace": 6,
        "gsrlimit": 6,
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": 1600,
        "format": "json",
    })
    url = f"https://commons.wikimedia.org/w/api.php?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)

    NO_ATTRIBUTION_NEEDED = ("public domain", "pdm", "cc0", "godl-india")

    pages = (res.get("query") or {}).get("pages") or {}
    candidates = []
    for page in pages.values():
        title = page.get("title", "")
        if any(h in title.lower() for h in _LOGO_HINTS):
            continue
        relevance = _title_relevance(entity, title)
        if relevance < 1:
            continue  # title doesn't actually mention the subject — reject
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 1000 or h < 700:  # high-quality floor, not just non-thumbnail
            continue
        license_name = (info.get("extmetadata", {}).get("LicenseShortName", {})
                         .get("value", "")).lower()
        free_of_attribution = any(k in license_name for k in NO_ATTRIBUTION_NEEDED)
        impact = _impact_score(title)
        candidates.append((relevance, impact, w * h, free_of_attribution, info, title))
    if not candidates:
        raise RuntimeError(f"no relevant Commons photo for '{entity}'")

    # prefer: most relevant, then most visually/emotionally compelling,
    # then highest-res, then no-attribution-required as a final tie-break
    # only — quality matters more than a caption credit we don't even show
    # (confirmed live: license-first ranking picked a cluttered 3600x1333
    # Zoom-grid screenshot over a sharp 5572x3715 press photo of the same
    # person, purely because the press photo required attribution)
    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    _, _, _, free_of_attribution, info, title = candidates[0]
    img_url = info.get("thumburl") or info["url"]
    meta = info.get("extmetadata", {})
    artist = "" if free_of_attribution else re.sub(
        "<[^<]+?>", "", meta.get("Artist", {}).get("value", "")).strip()

    req2 = urllib.request.Request(img_url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req2, timeout=30) as r:
        path = _save(r.read())
    return path, artist


def _openverse_search(query, entity=None, relevance_words=None, pool=8):
    """Shared by both the entity-search and topical-search stages. Filters
    to commercially-usable licenses via the API itself, then applies the
    same logo/relevance/license-preference rules used for Commons, plus a
    brand-safety check (mature content) Commons doesn't need.
    `relevance_words`, when given, requires the result's title to actually
    mention at least one of them — same purpose as `entity`, for the
    topical (non-named-subject) search path."""
    params = urllib.parse.urlencode({
        "q": query, "license_type": "commercial", "page_size": pool,
    })
    req = urllib.request.Request(
        f"https://api.openverse.org/v1/images/?{params}",
        headers={"User-Agent": _COMMONS_UA},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)

    NO_ATTRIBUTION_NEEDED = ("cc0", "publicdomain", "pdm")
    candidates = []
    for item in res.get("results", []):
        if item.get("mature"):
            continue
        title = item.get("title") or ""
        if any(h in title.lower() for h in _LOGO_HINTS):
            continue
        if entity and _title_relevance(entity, title) < 1:
            continue
        if relevance_words and _text_relevance(relevance_words, title) < 1:
            continue
        w, h = item.get("width", 0), item.get("height", 0)
        if w < 1000 or h < 700:  # high-quality floor, not just non-thumbnail
            continue
        img_url = item.get("url")
        if not img_url:
            continue
        free = any(k in (item.get("license") or "").lower() for k in NO_ATTRIBUTION_NEEDED)
        impact = _impact_score(title)
        candidates.append((impact, w * h, free, img_url))
    if not candidates:
        raise RuntimeError(f"no usable Openverse results for '{query}'")

    # quality (resolution) beats a bare attribution-license preference —
    # same reasoning as the Commons ranking above
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    _, _, _, img_url = candidates[0]
    req2 = urllib.request.Request(img_url, headers={"User-Agent": _COMMONS_UA})
    with urllib.request.urlopen(req2, timeout=30) as r:
        return _save(r.read())


def _openverse_entity(entity):
    return _openverse_search(entity, entity=entity), ""


def _openverse_topical(query, relevance_words=None):
    return _openverse_search(query, relevance_words=relevance_words)


def _search_query(story):
    cat = story["category"].replace(" NEWS", "")
    kws = _keywords(story["title"])
    return f"{cat} " + " ".join(kws) if kws else cat


# Pexels/Unsplash both prohibit visible brand logos/trademarks/watermarks
# in contributed photos as part of their own submission guidelines, so we
# don't need to detect logos ourselves — it's already filtered upstream.

# Relevance ranking (the search API's own ordering) picks WHAT the photo is
# of; among that relevant set we then pick the visually strongest one, so
# the post doesn't always default to the plainest/first match. Wider than
# it used to be — a combined multi-word query needs more candidates for a
# genuinely relevant one to still be in the pool.
RELEVANT_POOL = 15


# ---------- Pexels stock ----------
def _stock_pexels(query, relevance_words=None):
    key = settings.PEXELS_API_KEY
    if not key:
        raise RuntimeError("no pexels key")
    q = urllib.parse.quote(query)
    url = f"https://api.pexels.com/v1/search?query={q}&per_page={RELEVANT_POOL}&orientation=portrait"
    req = urllib.request.Request(url, headers={"Authorization": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    photos = res.get("photos", [])
    if not photos:
        raise RuntimeError(f"no pexels results for '{query}'")
    # the search API's own ranking can drift for niche/sensitive topics —
    # require the photo's own alt-text to actually mention the story
    # (when relevance_words given), THEN prefer the highest resolution
    if relevance_words:
        photos = [p for p in photos if _text_relevance(relevance_words, p.get("alt", "")) >= 1]
        if not photos:
            raise RuntimeError(f"no relevant pexels results for '{query}'")
    # among the relevant matches: most visually/emotionally compelling
    # (real human presence reads far stronger than an empty scene), then
    # highest resolution
    best = max(photos, key=lambda p: (
        _impact_score(p.get("alt", "")), p.get("width", 0) * p.get("height", 0)
    ))
    img_url = best["src"]["large2x"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


# ---------- Unsplash stock ----------
def _stock_unsplash(query, relevance_words=None):
    key = settings.UNSPLASH_ACCESS_KEY
    if not key:
        raise RuntimeError("no unsplash key")
    q = urllib.parse.quote(query)
    url = (f"https://api.unsplash.com/search/photos?query={q}"
           f"&orientation=portrait&per_page={RELEVANT_POOL}&client_id={key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)
    results = res.get("results", [])
    if not results:
        raise RuntimeError(f"no unsplash results for '{query}'")
    if relevance_words:
        def _desc(p):
            return f"{p.get('alt_description') or ''} {p.get('description') or ''}"
        results = [p for p in results if _text_relevance(relevance_words, _desc(p)) >= 1]
        if not results:
            raise RuntimeError(f"no relevant unsplash results for '{query}'")
    # among the relevant matches: real human presence first (more
    # compelling than an empty scene), then most-liked as a popularity/
    # visual-quality proxy
    def _desc2(p):
        return f"{p.get('alt_description') or ''} {p.get('description') or ''}"
    best = max(results, key=lambda p: (_impact_score(_desc2(p)), p.get("likes", 0)))
    img_url = best["urls"]["regular"]
    with urllib.request.urlopen(img_url, timeout=30) as r:
        return _save(r.read())


def _try_entities(entities):
    for entity in entities:
        for fn in (_wikimedia_commons, _openverse_entity):
            try:
                path, artist = fn(entity)
                print(f"image via {fn.__name__} (entity: '{entity}')")
                return path, artist
            except Exception as e:
                print(f"{fn.__name__} failed for '{entity}': {e}")
    return None, None


def get_image(story):
    """Find a photo for the story; return path to a saved image.
    story is mutated with story['photo_credit'] when the photo came from
    a source with a known author (CC attribution)."""
    entities = _proper_noun_phrases(story["title"])
    # A person/institution entity (Zelensky, RBI) genuinely IS the right
    # image — try those first. A place-name entity (Sikkim, Kyiv) only
    # gives a generic photo of the location, not the event that happened
    # there, so it's deferred to a last-resort fallback, tried after the
    # topical/event search below has had its shot.
    person_entities = [e for e in entities if not _is_place_entity(e)]
    place_entities = [e for e in entities if _is_place_entity(e)]

    path, artist = _try_entities(person_entities)
    if path:
        if artist:
            story["photo_credit"] = artist
        return path

    # The combined query (category + keywords together) usually wins: tested
    # live, "WORLD Iran warns faster" surfaced genuinely thematic editorial
    # photos ("toy soldiers surrounding Iran's flag on a map"), while the
    # solo place-name query "Iran" alone just returned generic tourism shots
    # of the same country — geographically correct, thematically empty. But
    # a combined query can also be too specific to match anything at all
    # (confirmed separately: a Sikkim-landslide combined query found zero
    # results where the solo word "landslide" found an excellent match) — so
    # try combined first, then fall back to individual distinctive keywords.
    # No ungated bare-category fallback: that was exactly what surfaced a
    # Jaipur palace photo for a Sikkim landslide story. Better to skip a post
    # than post the wrong picture — the caller treats "no image found" the
    # same as "no story found" and moves on.
    # A bare short acronym alone ("ICET") is search poison — it collides
    # with whatever else shares that acronym globally (confirmed: Openverse
    # mostly indexes "ICET" as a German train model). No context word to
    # disambiguate a solo acronym, so it doesn't get tried alone — it can
    # still appear inside the combined query, with surrounding words.
    relevance_words = _keywords(story["title"])
    solo_words = [kw for kw in relevance_words[:3] if not (kw.isupper() and len(kw) <= 6)]
    attempts = [(_search_query(story), relevance_words)]
    attempts += [(kw, [kw]) for kw in solo_words]

    for query, words in attempts:
        for fn in (_stock_pexels, _stock_unsplash, _openverse_topical):
            try:
                path = fn(query, words)
                print(f"image via {fn.__name__} (query: '{query}')")
                return path
            except Exception as e:
                print(f"{fn.__name__} failed for '{query}': {e}")

    # Last resort: a real photo of the place, even though it won't show
    # the specific event — still better than nothing, geographically honest.
    path, artist = _try_entities(place_entities)
    if path:
        if artist:
            story["photo_credit"] = artist
        return path

    raise RuntimeError("ALL image sources failed")
