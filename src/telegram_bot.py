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
    text = (
        f"\U0001F4F0 {story['category']} (score {story['score']})\n"
        f"{story['title']}\n\n"
        f"Reply to this message with a photo or video to use it for this "
        f"post — otherwise it posts automatically after "
        f"{settings.TELEGRAM_GRACE_MINUTES} min."
    )
    result = _call("sendMessage", {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
    })
    return result["message_id"] if result else None


def get_new_updates(offset):
    """Long-poll isn't needed here -- this runs once per 30-min cycle, so
    a short timeout just drains whatever has queued up since last time.
    `offset` (last seen update_id + 1) tells Telegram to skip anything
    already processed rather than us tracking a separate seen-set."""
    result = _call("getUpdates", {"offset": offset, "timeout": 5})
    return result or []


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
