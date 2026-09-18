"""
Incident Photo Review Workflow
==============================
Glue between the discovery engine (src/incident_photos.py), the candidate
history (src/photo_db.py) and the human (Telegram + the dashboard).

Responsibilities, in the order they happen:
  1. Run discovery/verification/licensing for a candidate story.
  2. Persist EVERY candidate considered -- including rejected ones.
  3. Send the reviewer a card that states, separately and plainly, what
     the photo is and whether it may be republished.
  4. Record what the reviewer decided.
  5. Put stories with no usable photo on a retry ladder, because incident
     photographs are routinely published hours after the first text
     report.
  6. Compute the daily metrics from what actually happened -- counted from
     the database, never estimated.

The one rule that outranks everything else here: this module can show a
copyrighted photograph to its owner in a private chat, and it can record
that they licensed it. It cannot and does not republish it.
"""

import datetime as dt

from config import settings
from src import incident_photos as ip
from src import photo_db, telegram_bot

# Inline-button callback codes. Kept to a couple of bytes because
# Telegram caps callback_data at 64 bytes total and the candidate id
# takes 16 of them.
CB_LICENSED = "L"
CB_REJECT = "R"
CB_TEXT_ONLY = "T"
CB_PERMISSION = "P"

_RULE = "━" * 20


# --------------------------------------------------------------- persistence

def persist(result, story, conn=None):
    """Writes the article, every candidate, and their verification and
    licence rows. Rejected candidates are stored too -- that history is
    the only way to ever answer "why did it choose that one?"."""
    own = conn is None
    conn = conn or photo_db.connect()
    try:
        article_id = result["article_id"] or story.get("id", "")
        existing = photo_db.get_article(article_id, conn)
        photo_db.save("articles", {
            "article_id": article_id,
            "headline": result.get("headline", ""),
            "link": story.get("link", ""),
            "category": story.get("category", ""),
            "score": story.get("score", 0),
            "event_id": result.get("event_id", ""),
            "first_seen": (existing or {}).get("first_seen") or photo_db.now_iso(),
            "last_checked": photo_db.now_iso(),
            "retry_index": (existing or {}).get("retry_index", 0),
            "next_retry_at": (existing or {}).get("next_retry_at"),
            "image_status": result.get("image_status"),
            "decision": result.get("decision"),
            # A story is "resolved" only when there is nothing left worth
            # retrying for: a photo we can actually use, or the owner's
            # own media. A verified-but-copyrighted photo stays unresolved
            # precisely so the ladder keeps hunting for a reusable one.
            "resolved": int(result.get("image_status") in
                            (ip.VERIFIED_FREE_IMAGE, ip.VERIFIED_LICENSED_IMAGE,
                             ip.OWNER_MEDIA)),
        }, conn)

        for rec in result.get("candidates", []):
            cid = rec["candidate_id"]
            photo_db.save("image_candidates", {
                "candidate_id": cid,
                "article_id": article_id,
                "event_id": rec.get("event_id", result.get("event_id", "")),
                "image_url": rec.get("image_url"),
                "thumbnail_url": rec.get("thumbnail_url"),
                "source_name": rec.get("source_name"),
                "source_url": rec.get("source_url"),
                "caption": rec.get("caption"),
                "published_at": rec.get("published_at"),
                "discovered_at": rec.get("discovered_at"),
                "tier": rec.get("tier"),
                "rank_in_story": rec.get("rank_in_story", 0),
            }, conn)
            photo_db.save("verification_results", {
                "candidate_id": cid,
                "article_id": article_id,
                "event_match": int(bool(rec.get("event_match"))),
                "location_match": int(bool(rec.get("location_match"))),
                "person_match": int(bool(rec.get("person_match"))),
                "time_match": int(bool(rec.get("time_match"))),
                "is_file_photo": int(bool(rec.get("is_file_photo"))),
                "confidence": float(rec.get("confidence", 0.0)),
                "image_age": rec.get("image_age"),
                "age_bucket": rec.get("age_bucket"),
                "reason": rec.get("reason"),
                "verdict": rec.get("decision"),
            }, conn)
            photo_db.save("license_results", {
                "candidate_id": cid,
                "article_id": article_id,
                "license_status": rec.get("license_status"),
                "license_name": rec.get("license_name"),
                "license_url": rec.get("license_url"),
                "copyright_owner": rec.get("copyright_owner"),
                "photographer": rec.get("photographer", ""),
                "attribution_required": int(bool(rec.get("attribution_required"))),
                "permission_required": int(bool(rec.get("permission_required"))),
                "reusable": int(bool(rec.get("reusable"))),
            }, conn)

        # A real photo we are not allowed to use is exactly the case where
        # asking is the only legitimate route, so the contact record gets
        # opened automatically -- at NOT_REQUESTED. Nothing is ever sent
        # to anyone; that stays a manual decision (Step 11).
        if result.get("decision") == ip.MANUAL_REVIEW and result.get("chosen_candidate_id"):
            photo_db.save("permission_requests", {
                "candidate_id": result["chosen_candidate_id"],
                "article_id": article_id,
                "source_name": result.get("source_name"),
                "photographer": result.get("photographer") or "",
                "copyright_owner": result.get("copyright_owner") or "",
                "contact": "",
                "source_url": result.get("source_url"),
                "image_url": result.get("image_url"),
                "discovered_at": photo_db.now_iso(),
                "status": "NOT_REQUESTED",
            }, conn)
        return article_id
    finally:
        if own:
            conn.close()


def schedule_retry(article_id, conn=None):
    """Advances a story to the next rung of the retry ladder. Returns the
    scheduled time, or None once the ladder is exhausted."""
    own = conn is None
    conn = conn or photo_db.connect()
    try:
        art = photo_db.get_article(article_id, conn)
        if not art or art.get("resolved"):
            return None
        idx = art.get("retry_index") or 0
        if idx >= len(settings.PHOTO_RETRY_LADDER_MIN):
            row = dict(art)
            row["next_retry_at"] = None
            photo_db.save("articles", row, conn)
            return None
        when = (dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(minutes=settings.PHOTO_RETRY_LADDER_MIN[idx]))
        row = dict(art)
        row["retry_index"] = idx + 1
        row["next_retry_at"] = when.replace(microsecond=0).isoformat()
        photo_db.save("articles", row, conn)
        return row["next_retry_at"]
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------ telegram output

def _tick(flag):
    return "✅" if flag else "❌"


def format_card(result):
    """The reviewer's card. Deliberately says the authenticity verdict and
    the rights verdict as two separate blocks -- conflating them is the
    mistake this whole subsystem is built to prevent."""
    status = result.get("image_status")
    headline = (result.get("headline") or "")[:200]

    if status == ip.NO_VERIFIED_IMAGE:
        return (
            f"{_RULE}\n⚪ NO VERIFIED INCIDENT PHOTO\n{_RULE}\n\n"
            f"\U0001F4F0 {headline}\n\n"
            "No authentic incident photograph was found from the available sources.\n\n"
            "ACTION:\nTEXT ONLY\n\n"
            f"{_RULE}"
        )

    conf = int(round(float(result.get("authenticity_confidence") or 0) * 100))
    source = result.get("source_name") or "unknown"
    published = result.get("published_at") or "unknown"
    age = result.get("age_bucket") or "UNKNOWN"

    if result.get("decision") == ip.AUTO_PUBLISH:
        lic = result.get("license_name") or result.get("license_status")
        attribution = result.get("attribution") or "not required"
        return (
            f"{_RULE}\n\U0001F7E2 FREE TO REUSE\n{_RULE}\n\n"
            f"\U0001F4F0 {headline}\n\n"
            f"{_tick(result.get('event_match'))} Event Match\n"
            f"{_tick(result.get('location_match'))} Location Match\n"
            f"{_tick(result.get('time_match'))} Time Match\n\n"
            f"\U0001F3AF Confidence: {conf}%\n"
            f"\U0001F4C5 Published: {published} ({age})\n"
            f"\U0001F4F0 Source: {source}\n\n"
            f"License: {lic}\n"
            f"Attribution: {attribution}\n\n"
            f"✅ AUTO-PUBLISH ELIGIBLE\n\n"
            f"{_RULE}"
        )

    banner = ("\U0001F4C1 FILE PHOTO — NOT TODAY'S EVENT"
              if status == ip.VERIFIED_FILE_PHOTO else
              "\U0001F4F8 REAL INCIDENT PHOTO FOUND")
    owner = result.get("copyright_owner") or source
    return (
        f"{_RULE}\n{banner}\n{_RULE}\n\n"
        f"\U0001F4F0 {headline}\n\n"
        f"{_tick(result.get('event_match'))} Event Match\n"
        f"{_tick(result.get('location_match'))} Location Match\n"
        f"{_tick(result.get('time_match'))} Time Match\n\n"
        f"\U0001F3AF Confidence: {conf}%\n"
        f"\U0001F4C5 Published: {published} ({age})\n"
        f"\U0001F4F0 Source: {source}\n"
        f"© Owner: {owner}\n\n"
        f"⚠️ RIGHTS: {result.get('license_status')}\n"
        f"❌ DO NOT AUTO-PUBLISH\n\n"
        f"{result.get('reason','')}\n\n"
        f"{_RULE}"
    )


def build_buttons(result):
    """Open-the-original and record-a-decision only. There is deliberately
    no 'use this image' button for restricted material -- a button that
    downloads and republishes someone else's photograph is the one thing
    this system must never offer, however convenient it would be."""
    cid = result.get("chosen_candidate_id")
    if not cid:
        return []
    rows = [[("\U0001F5BC OPEN ORIGINAL", "url", result.get("image_url")),
             ("\U0001F517 OPEN SOURCE", "url", result.get("source_url"))]]
    if result.get("decision") == ip.AUTO_PUBLISH:
        rows.append([("❌ REJECT IMAGE", "cb", f"{CB_REJECT}|{cid}"),
                     ("\U0001F4DD TEXT ONLY", "cb", f"{CB_TEXT_ONLY}|{cid}")])
    else:
        rows.append([("✅ MARK LICENSED", "cb", f"{CB_LICENSED}|{cid}"),
                     ("✉ ASKED PERMISSION", "cb", f"{CB_PERMISSION}|{cid}")])
        rows.append([("❌ REJECT IMAGE", "cb", f"{CB_REJECT}|{cid}"),
                     ("\U0001F4DD TEXT ONLY", "cb", f"{CB_TEXT_ONLY}|{cid}")])
    return telegram_bot.build_keyboard(rows)


def known_events(story, conn=None):
    """Events already on record that this story could be another outlet's
    version of. Scoped to the same place/type/day prefix so the lookup
    stays a cheap indexed query rather than a scan of everything ever
    seen."""
    entities = ip.extract_entities(story.get("title", ""), story.get("summary", ""),
                                   story.get("published"))
    prefix = ip.event_prefix(entities)
    rows = photo_db.query(
        "SELECT event_id, headline FROM articles WHERE event_id LIKE ?",
        (prefix + "-%",), conn)
    return [(r["event_id"], r["headline"]) for r in rows]


def review_story(story, message_id=None, conn=None, send=True):
    """Full review pass for one candidate story: discover, verify,
    classify, persist, notify, schedule a retry if nothing usable was
    found. Returns the structured result."""
    own_conn = conn is None
    lookup = conn or photo_db.connect()
    try:
        prior = known_events(story, lookup)
    finally:
        if own_conn:
            lookup.close()
    result = ip.find_incident_photo(story, known_events=prior)
    own = conn is None
    conn = conn or photo_db.connect()
    try:
        persist(result, story, conn)
        if result["image_status"] in (ip.NO_VERIFIED_IMAGE, ip.UNCERTAIN_IMAGE,
                                      ip.VERIFIED_COPYRIGHTED_IMAGE,
                                      ip.VERIFIED_FILE_PHOTO):
            schedule_retry(result["article_id"], conn)
    finally:
        if own:
            conn.close()

    if send and message_id:
        caption = format_card(result)
        buttons = build_buttons(result)
        try:
            if result.get("image_url") and result["image_status"] != ip.NO_VERIFIED_IMAGE:
                telegram_bot.send_photo_reply(message_id, result["image_url"],
                                              caption, buttons)
            else:
                telegram_bot.send_message(caption, reply_to=message_id)
        except Exception as e:
            print("photo review card failed to send:", e)
    return result


# -------------------------------------------------------------- button replies

_ACTION_LABELS = {
    CB_LICENSED: ("LICENSED", "Marked as licensed. Attribute the owner when you post it."),
    CB_REJECT: ("REJECTED", "Image rejected. It will not be suggested again."),
    CB_TEXT_ONLY: ("TEXT_ONLY", "This story will go out text-only."),
    CB_PERMISSION: ("REQUEST_PERMISSION", "Logged as permission requested."),
}


def handle_callback(cb):
    """Records a tapped inline button and reports back what should happen
    next. Called from main.py's Telegram poll, which is the only place
    that reads updates AND the only place that knows the message-id ->
    queue-entry mapping needed to actually act on the decision: download
    the now-licensed photo into the inbox (so the existing "human supplied
    media" post path picks it up), or post text-only right away instead of
    waiting out the grace period.

    Returns (label, info) where info = {"article_id", "candidate_id",
    "image_url", "source_url"}. label is None on an unrecognised button,
    with info = {}."""
    data = (cb.get("data") or "")
    code, _, cid = data.partition("|")
    label, toast = _ACTION_LABELS.get(code, (None, None))
    if not label or not cid:
        telegram_bot.answer_callback(cb["id"], "Unrecognised action.")
        return None, {}

    conn = photo_db.connect()
    try:
        rows = photo_db.query(
            "SELECT * FROM image_candidates WHERE candidate_id = ?", (cid,), conn)
        cand = rows[0] if rows else {}
        article_id = cand.get("article_id", "")
        photo_db.save("publication_results", {
            "article_id": article_id,
            "candidate_id": cid,
            "decision": label,
            "acted_by": "telegram_reviewer",
            "acted_at": photo_db.now_iso(),
            "note": "",
        }, conn)
        if code in (CB_LICENSED, CB_PERMISSION):
            perm = photo_db.query(
                "SELECT * FROM permission_requests WHERE candidate_id = ?", (cid,), conn)
            if perm:
                row = dict(perm[0])
                row["status"] = "APPROVED" if code == CB_LICENSED else "REQUESTED"
                photo_db.save("permission_requests", row, conn)
        if code in (CB_LICENSED, CB_REJECT, CB_TEXT_ONLY):
            # The reviewer has ruled, so stop the retry ladder spending
            # requests on a question that has already been answered.
            art = photo_db.get_article(article_id, conn)
            if art:
                row = dict(art)
                row["resolved"] = 1
                row["next_retry_at"] = None
                photo_db.save("articles", row, conn)
    finally:
        conn.close()

    telegram_bot.answer_callback(cb["id"], toast)
    msg = cb.get("message") or {}
    if msg.get("message_id") and msg.get("caption"):
        try:
            telegram_bot.edit_caption(msg["chat"]["id"], msg["message_id"],
                                      msg["caption"] + f"\n\n✅ YOU MARKED: {label}")
        except Exception:
            pass
    return label, {
        "article_id": article_id,
        "candidate_id": cid,
        "image_url": cand.get("image_url"),
        "source_url": cand.get("source_url"),
    }


# -------------------------------------------------------------------- retries

def run_retries(limit=5):
    """Re-checks stories that had no usable photograph when first seen.
    The ladder exists because a flood is reported in text within minutes
    and photographed within hours -- treating the first 'no image' as
    final throws away most of the photos that ever become available."""
    conn = photo_db.connect()
    changed = []
    try:
        for art in photo_db.due_retries(limit, conn):
            story = {
                "id": art["article_id"],
                "title": art["headline"],
                "summary": "",
                "link": art["link"],
                "category": art["category"],
                "score": art["score"],
            }
            before = art["image_status"]
            result = ip.find_incident_photo(story, known_events=known_events(story, conn))
            persist(result, story, conn)
            if result["image_status"] != before:
                changed.append((art["headline"], before, result["image_status"]))
                print(f"retry: {art['headline'][:60]} {before} -> {result['image_status']}")
            if result["image_status"] in (ip.VERIFIED_FREE_IMAGE, ip.VERIFIED_LICENSED_IMAGE):
                # Found something we can actually use -- stop retrying and
                # tell the reviewer, since this is the outcome the ladder
                # exists for.
                telegram_bot.send_message(format_card(result))
            else:
                schedule_retry(art["article_id"], conn)
    finally:
        conn.close()
    return changed


# -------------------------------------------------------------------- metrics

def compute_daily_metrics(day=None, conn=None):
    """Counted from the database. Every number here is a COUNT(*) over
    rows that exist -- nothing is estimated, extrapolated or rounded up.
    If the system had a bad day, these say so."""
    day = day or dt.date.today().isoformat()
    own = conn is None
    conn = conn or photo_db.connect()
    try:
        arts = photo_db.query(
            "SELECT * FROM articles WHERE substr(last_checked,1,10) = ?", (day,), conn)
        by = lambda pred: sum(1 for a in arts if pred(a))
        verified = {ip.VERIFIED_FREE_IMAGE, ip.VERIFIED_LICENSED_IMAGE,
                    ip.VERIFIED_COPYRIGHTED_IMAGE, ip.OWNER_MEDIA}
        # "Wrong images" counts candidates a human REJECTED after seeing
        # them -- the only honest source for that number. The engine
        # cannot mark its own output wrong; that is the reviewer's call.
        wrong = photo_db.query(
            "SELECT COUNT(*) AS n FROM publication_results "
            "WHERE decision = 'REJECTED' AND substr(acted_at,1,10) = ?", (day,), conn)[0]["n"]
        row = {
            "day": day,
            "stories": len(arts),
            "exact_images": by(lambda a: a["image_status"] in verified),
            "wrong_images": wrong,
            "no_image": by(lambda a: a["image_status"] == ip.NO_VERIFIED_IMAGE),
            "free_images": by(lambda a: a["image_status"] in
                              (ip.VERIFIED_FREE_IMAGE, ip.VERIFIED_LICENSED_IMAGE, ip.OWNER_MEDIA)),
            "copyrighted": by(lambda a: a["image_status"] == ip.VERIFIED_COPYRIGHTED_IMAGE),
            "manual_review": by(lambda a: a["decision"] == ip.MANUAL_REVIEW),
            "file_photos": by(lambda a: a["image_status"] == ip.VERIFIED_FILE_PHOTO),
            "computed_at": photo_db.now_iso(),
        }
        photo_db.save("daily_metrics", row, conn)
        return row
    finally:
        if own:
            conn.close()
