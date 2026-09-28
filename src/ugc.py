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
#
# The scheme (https://) is OPTIONAL -- copying a link from a phone's share
# sheet very often gives "x.com/name/status/123" with no "https://" at all,
# and requiring it meant those links were silently ignored (a real
# report: a shared link produced no response at all). \b before the
# domain still stops "box.com" etc. from matching mid-word.
VIDEO_HOST_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|mobile\.)?"
    r"\b(x\.com|twitter\.com|vxtwitter\.com|fxtwitter\.com|nitter\.\w+|"
    r"instagram\.com|youtube\.com|youtu\.be|facebook\.com|fb\.watch)/\S+",
    re.I)

# x.com/NAME/status/123 -> @NAME. Other platforms do not put the author in
# the URL, so the creator stays unknown and the request text says so
# rather than inventing a handle.
_X_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|mobile\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/", re.I)

AWAITING = "awaiting_permission"
GRANTED = "granted"
DECLINED = "declined"
POSTED = "posted"

# Some creators say "use it, but don't put my name on it" -- usually to
# avoid attention. That is their call, and it is honoured: CREDIT_NONE
# posts the video with no credit line at all.
CREDIT_NAMED = "named"
CREDIT_NONE = "none"

# Instagram caps Reels at 90s. Anything longer is the wrong shape for the
# feed anyway, and a very large file wastes the runner's time and disk.
MAX_DURATION_SEC = 180
MAX_FILESIZE_MB = 120


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
    """The first social-video link in a message, or None. Always returned
    with a scheme -- everything downstream (the ledger, yt-dlp) needs a
    real URL, even when the person pasted it without "https://"."""
    m = VIDEO_HOST_RE.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,)")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


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


def open_request(url, prompt_message_id=None, creator=None, now=None, headline="", article_url=None):
    """Records that permission has been requested for one video. `article_url`
    is the news article the owner paired with it (if any) -- used as the
    post's actual source link instead of the video platform's own URL."""
    now = now or dt.datetime.utcnow()
    records = _load()
    rec = {
        "id": str(int(now.timestamp() * 1000)),
        "url": url,
        "creator": creator if creator is not None else creator_handle(url),
        "status": AWAITING,
        "requested_at": now.strftime("%Y-%m-%d %H:%M"),
        "prompt_message_id": prompt_message_id,
        "headline": headline,
        "article_url": article_url,
        "credit_mode": CREDIT_NAMED,
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


def mark_granted(record_id, prompt_message_id=None, now=None, credit_mode=None):
    now = now or dt.datetime.utcnow()
    fields = {"status": GRANTED, "granted_at": now.strftime("%Y-%m-%d %H:%M")}
    if prompt_message_id is not None:
        fields["prompt_message_id"] = prompt_message_id
    if credit_mode is not None:
        fields["credit_mode"] = credit_mode
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
    """What appears in the caption of a post built from this video. Empty
    when the creator asked NOT to be named -- honouring that is part of
    the deal that got the yes."""
    if not record or record.get("credit_mode") == CREDIT_NONE:
        return ""
    who = record.get("creator") or "the original creator"
    return f"\U0001F3A5 Video: {who}, used with permission"


# --------------------------------------------------------------- fetching
# Only ever called for a video whose creator has already said yes (see
# releasable). Reading the page to show the owner what a link contains is
# fine before that; pulling the actual file is not.

def probe(url):
    """Title/duration/uploader for a link, WITHOUT downloading the video --
    so the owner can see what they are approving. Returns None if the link
    cannot be read (private, deleted, or login-walled)."""
    try:
        import yt_dlp
    except ImportError:
        return None
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
            i = y.extract_info(url, download=False)
    except Exception as e:
        print("ugc.probe failed:", str(e)[:160])
        return None
    return {
        "title": (i.get("title") or "").strip(),
        "duration": i.get("duration"),
        "uploader": i.get("uploader") or i.get("channel"),
        "uploader_id": i.get("uploader_id"),
    }


def download(record, dest_dir):
    """Fetches the video for an ALREADY-GRANTED record. Refuses outright
    for anything else -- this is the second place the gate is enforced, so
    a coding mistake upstream still cannot pull an unpermitted file."""
    if not releasable(record):
        raise PermissionError("no recorded permission for this video -- refusing to download")
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp is not installed on this runner")
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, f"ugc-{record['id']}.%(ext)s")
    opts = {
        "quiet": True, "no_warnings": True, "outtmpl": out,
        "format": f"mp4[filesize_approx<{MAX_FILESIZE_MB}M]/best[height<=1080]/best",
        "max_filesize": MAX_FILESIZE_MB * 1024 * 1024,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(record["url"], download=True)
    dur = info.get("duration")
    if dur and dur > MAX_DURATION_SEC:
        print(f"ugc: {dur:.0f}s video will be trimmed to fit a Reel")
    import glob
    hits = sorted(glob.glob(os.path.join(dest_dir, f"ugc-{record['id']}.*")))
    if not hits:
        raise RuntimeError("download produced no file")
    return hits[0]
