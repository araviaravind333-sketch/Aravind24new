"""
UGC permission ledger -- viral video, asked for properly
========================================================
Videos filmed by ordinary people are what news pages grow on. But the
person who filmed one owns it, and a credit line in the caption is NOT
permission -- it changes nothing legally. Instagram also detects reposted
video automatically, and the strikes land on the page, not on whoever
suggested it. A page can be deleted outright that way, which for this
account would mean losing everything it is being built for.

So this module does what an actual newsroom does with user footage: it
asks first. Reuters and AP run whole desks on exactly this loop.

Nothing here ever fetches or posts a video by itself. It only:

  1. records that a request was opened for one specific video URL,
  2. writes the message the owner sends to the creator,
  3. records that a HUMAN confirmed the creator said yes,
  4. releases that one video, with the agreed credit attached.

`releasable()` is the single gate. A video with no recorded, granted
permission never becomes postable -- there is deliberately no flag, no
setting and no override that turns that off.
"""

import datetime as dt
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(_ROOT, "data", "ugc_permissions.json")

# Only links to platforms where a person publishes their own footage are
# treated as a permission request. A plain news-article link is left alone
# (the urgent-submission path already owns those).
VIDEO_HOST_RE = re.compile(
    r"https?://(?:www\.|m\.|mobile\.)?"
    r"(x\.com|twitter\.com|instagram\.com|youtube\.com|youtu\.be|facebook\.com|fb\.watch)/\S+",
    re.I)

# x.com/NAME/status/123 -> @NAME. Other platforms do not put the author in
# the URL, so the creator stays unknown and the request text says so
# rather than inventing a handle.
_X_HANDLE_RE = re.compile(
    r"https?://(?:www\.|m\.|mobile\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/", re.I)

AWAITING = "awaiting_permission"
GRANTED = "granted"
DECLINED = "declined"
POSTED = "posted"


def _load():
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(records):
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=1, ensure_ascii=False)


def find_video_link(text):
    """The first social-video link in a message, or None."""
    m = VIDEO_HOST_RE.search(text or "")
    return m.group(0).rstrip(".,)") if m else None


def creator_handle(url):
    m = _X_HANDLE_RE.search(url or "")
    return "@" + m.group(1) if m else None


def permission_text(url, creator=None):
    """The message the owner sends to the person who filmed the video.
    Short, specific and honest -- it says who is asking, what for, and
    that credit will be given, which is what actually gets a yes."""
    who = creator or "there"
    return (
        f"Hi {who} — I run @aravindnews24, a news page on Instagram.\n\n"
        f"Could I share your video on my page? I'll credit you by name in "
        f"the caption, and I'll take it down straight away if you ever want "
        f"it removed.\n\n"
        f"Video: {url}\n\n"
        f"Thank you!"
    )


def open_request(url, prompt_message_id=None, creator=None, now=None):
    """Records that permission has been requested for one video."""
    now = now or dt.datetime.utcnow()
    records = _load()
    rec = {
        "id": str(int(now.timestamp() * 1000)),
        "url": url,
        "creator": creator if creator is not None else creator_handle(url),
        "status": AWAITING,
        "requested_at": now.strftime("%Y-%m-%d %H:%M"),
        "prompt_message_id": prompt_message_id,
        "video_path": None,
    }
    records.append(rec)
    _save(records)
    return rec


def get(record_id):
    return next((r for r in _load() if r["id"] == str(record_id)), None)


def by_prompt_message(message_id):
    """The record whose 'send me the file' prompt the owner just replied
    to -- this is how a forwarded video is tied to one specific grant,
    with no guessing."""
    if message_id is None:
        return None
    return next((r for r in _load() if r.get("prompt_message_id") == message_id), None)


def _update(record_id, **fields):
    records = _load()
    for r in records:
        if r["id"] == str(record_id):
            r.update(fields)
            _save(records)
            return r
    return None


def mark_granted(record_id, prompt_message_id=None, now=None):
    now = now or dt.datetime.utcnow()
    fields = {"status": GRANTED, "granted_at": now.strftime("%Y-%m-%d %H:%M")}
    if prompt_message_id is not None:
        fields["prompt_message_id"] = prompt_message_id
    return _update(record_id, **fields)


def mark_declined(record_id):
    return _update(record_id, status=DECLINED)


def mark_posted(record_id):
    return _update(record_id, status=POSTED)


def attach_video(record_id, video_path):
    return _update(record_id, video_path=video_path)


def releasable(record):
    """The one gate. True only when a human has recorded that the creator
    said yes to THIS video. Deliberately has no override."""
    return bool(record) and record.get("status") in (GRANTED, POSTED)


def credit_line(record):
    """What must appear in the caption of any post built from this video."""
    if not record:
        return ""
    who = record.get("creator") or "the original creator"
    return f"\U0001F3A5 Video: {who}, used with permission"
