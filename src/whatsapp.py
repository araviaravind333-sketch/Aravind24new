"""
WhatsApp Notify
===============
Sends candidate news stories to the user's own WhatsApp (Meta's WhatsApp
Cloud API) so a human can reply with a real, relevant photo for any of
them — this puts a person in the one specific step (finding the right
photo) that's been hardest to get reliably right by heuristics alone,
while everything else (writing, rendering, scheduling, publishing) stays
fully automated. No reply within the grace period -> src/main.py posts
the story anyway, text-only, rather than ever guessing with a stock photo.
"""

import json
import urllib.request

from config import settings

GRAPH = f"https://graph.facebook.com/{settings.GRAPH_VERSION}"


def _post(payload):
    url = f"{GRAPH}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        return data["messages"][0]["id"]
    except Exception as e:
        print("WhatsApp send failed:", e)
        return None


def send_candidate(story):
    """Send one candidate as its own message (not batched) — WhatsApp's
    reply-to feature lets the user reply directly to THIS message, which
    is how the pipeline knows which story a returned photo belongs to."""
    text = (
        f"\U0001F4F0 *{story['category']}* (score {story['score']})\n"
        f"{story['title']}\n\n"
        f"Reply to *this* message with a photo to use it for this post — "
        f"otherwise it posts as text-only after {settings.WHATSAPP_GRACE_MINUTES} min."
    )
    return _post({
        "messaging_product": "whatsapp",
        "to": settings.WHATSAPP_RECIPIENT,
        "type": "text",
        "text": {"body": text},
    })
