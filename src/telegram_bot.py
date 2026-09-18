"""
Telegram Notify
===============
Same job as src/whatsapp.py (send candidate stories to the user, let a
human reply with a real photo/video for the ones they choose) but over
the Telegram Bot API instead of WhatsApp's Cloud API. Two concrete wins
over WhatsApp for this use case:

  1. No template-approval queue. WhatsApp requires a pre-approved message
     template for any business-initiated message outside an active 24h
     session -- that approval sat PENDING for hours with no ETA. Telegram
     bots can message a chat that has started the bot at any time, no
     review process, ever.
  2. No webhook/always-on receiver needed. WhatsApp's receive side needed
     a separate always-on Cloudflare Worker. Telegram's getUpdates is a
     pull API -- GitHub Actions can just poll it directly on its own
     30-minute schedule, so telegram_cycle() in main.py handles BOTH send
     and receive with nothing else to deploy or maintain.

Setup (one-time, by the user):
  1. Message @BotFather on Telegram, send /newbot, get a token back.
  2. Send the new bot any message (e.g. "hi") so it has a chat to reply
     into -- a bot cannot message a user who has never started it.
  3. Resolve that chat id (see resolve_chat_id() below) and store both
     TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub Actions secrets.
"""

import json
import os
import urllib.error
import urllib.request

from config import settings

API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}"


def _call(method, params=None):
    url = f"{API}/{method}"
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"Telegram {method} failed:", e, "-", e.read().decode(errors="replace"))
        return None
    except Exception as e:
        print(f"Telegram {method} failed:", e)
        return None
    if not data.get("ok"):
        print(f"Telegram {method} error:", data)
        return None
    return data["result"]


def resolve_chat_id():
    """One-time helper: after the user has sent the bot at least one
    message, this reads it back to find their chat id -- run manually
    once during setup, not part of the regular pipeline."""
    updates = _call("getUpdates") or []
    for u in updates:
        msg = u.get("message")
        if msg:
            return msg["chat"]["id"]
    return None


def send_candidate(story):
    """Send one candidate as its own message -- Telegram's reply-to lets
    the user reply directly to THIS message, which is how the pipeline
    knows which story a returned photo/video belongs to (message_id ->
    reply_to_message.message_id, mirroring WhatsApp's context.id)."""
    summary = story.get("summary", "").strip()
    link = story.get("link", "").strip()
    text = (
        f"\U0001F4F0 {story['category']} (score {story['score']})\n"
        f"{story['title']}\n"
    )
    if summary:
        text += f"\n{summary}\n"
    if link:
        text += f"\nSource: {link}\n"
    if settings.TEXT_ONLY_MODE:
        # Photos are off entirely right now -- don't ask for one that
        # would just be ignored at render time.
        text += (
            f"\nPosts text-only automatically after "
            f"{settings.TELEGRAM_GRACE_MINUTES} min (no image attached)."
        )
    else:
        text += (
            f"\nReply to this message with a photo or video to use it for this "
            f"post — otherwise it posts automatically after "
            f"{settings.TELEGRAM_GRACE_MINUTES} min.\n\n"
            f"For best quality, send it as a FILE, not a photo: attach \U0001F4CE "
            f"→ File → pick from gallery. A normal photo attachment gets "
            f"compressed by Telegram before it reaches us.\n\n"
            f"For a video: it's trimmed to 50s if it's over 60s, otherwise "
            f"posted at its own length. Add \"no trim\" / \"full length\" "
            f"anywhere in the caption to post it exactly as sent regardless "
            f"of length."
        )
    result = _call("sendMessage", {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        # Without this, Telegram auto-generates a rich preview (title/
        # description/photo pulled from the article) for the "Source:"
        # link -- that preview card visually splits the message when
        # scrolling, which can make it look like a candidate has no
        # "reply with a photo" instructions when it's really just been
        # pushed out of view by the link card above/below it.
        "disable_web_page_preview": True,
    })
    return result["message_id"] if result else None


def send_photo_reply(message_id, photo_url, caption, buttons=None):
    """Shows a DISCOVERED incident photo to the reviewer as a reply to its
    candidate message. This is a private preview so a human can judge it --
    it is not publishing. Nothing reaches Instagram/Facebook from here;
    that only happens after the reviewer sends the media back themselves.

    `buttons` attaches an inline keyboard (see build_keyboard). Note what
    is deliberately NOT offered there: any button that would download and
    republish someone else's copyrighted photograph. The buttons open the
    original, or record a decision -- they never reuse the file."""
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption[:1000],
        "reply_to_message_id": message_id,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = _call("sendPhoto", payload)
    if result is None and buttons:
        # Telegram refuses a photo URL it cannot fetch itself (hotlink
        # protection, redirects, oversized files). The rights information
        # is the useful part, so fall back to sending it as text with the
        # image as a link rather than losing the whole review card.
        return _call("sendMessage", {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "text": caption[:4000],
            "reply_to_message_id": message_id,
            "disable_web_page_preview": False,
            "reply_markup": {"inline_keyboard": buttons},
        })
    return result


def build_keyboard(rows):
    """rows: list of lists of (label, kind, value) where kind is "url" or
    "cb". Telegram caps callback_data at 64 bytes, which is why the
    callback values in photo_review are short codes rather than URLs."""
    keyboard = []
    for row in rows:
        built = []
        for label, kind, value in row:
            if kind == "url":
                if not value or not str(value).startswith("http"):
                    continue
                built.append({"text": label, "url": value})
            else:
                built.append({"text": label, "callback_data": str(value)[:64]})
        if built:
            keyboard.append(built)
    return keyboard


def answer_callback(callback_query_id, text=""):
    """Clears the spinner on a tapped inline button and shows a short
    toast. Telegram keeps re-delivering a callback that is never answered,
    so this is not optional."""
    return _call("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text[:200],
    })


def edit_caption(chat_id, message_id, caption):
    """Rewrites the review card in place after a button is pressed, so the
    message itself shows the decision instead of the reviewer having to
    remember which ones they already handled."""
    return _call("editMessageCaption", {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption[:1000],
    })


def send_message(text, buttons=None, reply_to=None):
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = _call("sendMessage", payload)
    return result["message_id"] if result else None


def reply_to_message(message_id, text):
    """Replies directly to a specific message the user sent -- used to
    tell them what happened to an urgent breaking-news submission
    (rejected by the daily cap, article link couldn't be read, etc.)
    instead of it silently disappearing with no feedback."""
    return _call("sendMessage", {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "reply_to_message_id": message_id,
    })


def get_new_updates(offset):
    """Long-poll isn't needed here -- this runs once per 30-min cycle, so
    a short timeout just drains whatever has queued up since last time.
    `offset` (last seen update_id + 1) tells Telegram to skip anything
    already processed rather than us tracking a separate seen-set."""
    result = _call("getUpdates", {"offset": offset, "timeout": 5})
    return result or []


def _multipart_post(method, fields, file_field, file_path):
    """Stdlib-only multipart/form-data upload -- this project has no HTTP
    client dependency beyond urllib (see requirements.txt), and Telegram's
    sendPhoto/sendDocument need a real file upload here since the carousel
    slides aren't pushed to a public URL until later in the pipeline."""
    boundary = "----AravindNews24Boundary7f3a9c"
    body = bytearray()

    def _field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    for name, value in fields.items():
        if value is None:
            continue
        _field(name, value if isinstance(value, str) else json.dumps(value))

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode())
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"{API}/{method}", data=bytes(body), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"Telegram {method} (upload) failed:", e, "-", e.read().decode(errors="replace"))
        return None
    except Exception as e:
        print(f"Telegram {method} (upload) failed:", e)
        return None
    if not data.get("ok"):
        print(f"Telegram {method} (upload) error:", data)
        return None
    return data["result"]


def send_photo_file(file_path, caption, buttons=None, reply_to=None):
    """Uploads a LOCAL image file directly (as opposed to send_photo_reply,
    which points Telegram at a URL) -- used for the daily carousel, whose
    slides are only rendered locally at review time, before the workflow's
    git-push step has made anything public yet."""
    fields = {"chat_id": settings.TELEGRAM_CHAT_ID, "caption": caption[:1000]}
    if reply_to:
        fields["reply_to_message_id"] = reply_to
    if buttons:
        fields["reply_markup"] = {"inline_keyboard": buttons}
    result = _multipart_post("sendPhoto", fields, "photo", file_path)
    return result["message_id"] if result else None


def download_file(file_id, dest_path_no_ext):
    """Downloads a photo/video the user sent, preserving its real
    extension (so main.py's VIDEO_EXTENSIONS check can tell photos and
    videos apart) rather than assuming one. Returns the final path."""
    meta = _call("getFile", {"file_id": file_id})
    if not meta or not meta.get("file_path"):
        return None
    ext = os.path.splitext(meta["file_path"])[1] or ".jpg"
    dest_path = f"{dest_path_no_ext}{ext}"
    url = f"{FILE_API}/{meta['file_path']}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
    except Exception as e:
        print("Telegram file download failed:", e)
        return None
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)
    return dest_path
