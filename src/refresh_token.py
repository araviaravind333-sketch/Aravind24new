"""
Refresh Meta Token
==================
Exchanges the current long-lived token for a new one, then writes it back
into the repo's GitHub Actions secret via the GitHub API (needs a fine-grained
PAT stored as GH_PAT with 'secrets: write' on this repo).

This keeps the whole system running forever with zero manual work.
"""

import os
import json
import base64
import urllib.request
import urllib.parse

GRAPH = "https://graph.facebook.com/v21.0"


def get_new_token():
    old = os.environ["META_PAGE_ACCESS_TOKEN"]
    app_id = os.environ["META_APP_ID"]
    secret = os.environ["META_APP_SECRET"]
    params = urllib.parse.urlencode({
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": secret,
        "fb_exchange_token": old,
    })
    with urllib.request.urlopen(f"{GRAPH}/oauth/access_token?{params}", timeout=40) as r:
        data = json.load(r)
    return data["access_token"]


def update_github_secret(new_token):
    """Encrypt with repo public key and PUT the secret."""
    from nacl import encoding, public  # pynacl
    repo = os.environ["REPO"]
    pat = os.environ["GH_PAT"]
    hdr = {"Authorization": f"Bearer {pat}",
           "Accept": "application/vnd.github+json"}

    # get repo public key
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        key_data = json.load(r)

    pk = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(new_token.encode())
    enc = base64.b64encode(sealed).decode()

    body = json.dumps({
        "encrypted_value": enc,
        "key_id": key_data["key_id"],
    }).encode()
    put = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/secrets/META_PAGE_ACCESS_TOKEN",
        data=body, headers=hdr, method="PUT")
    with urllib.request.urlopen(put, timeout=30) as r:
        print("secret update status:", r.status)


if __name__ == "__main__":
    token = get_new_token()
    print("Got new token (len):", len(token))
    update_github_secret(token)
    print("Token refreshed and stored.")
