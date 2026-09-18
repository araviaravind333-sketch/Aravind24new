"""
Static Admin Dashboard
======================
Renders the candidate database to a single self-contained HTML file at
public/dashboard/index.html, which GitHub Pages already serves from this
repo. No server, no framework, no build step, no cost -- the same
architecture that runs everything else here.

It is generated rather than dynamic on purpose: a dynamic dashboard would
need a host that is awake when you open it, which means paying for one.
A file regenerated after every pipeline run is never more than one cycle
stale, which for reviewing news photographs is the same thing.

Everything shown is read out of SQLite. No number on this page is
computed anywhere but src/photo_review.compute_daily_metrics().
"""

import datetime as dt
import html
import json
import os

from config import settings
from src import photo_db

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, settings.DASHBOARD_PATH)

_STATUS_STYLE = {
    "VERIFIED_FREE_IMAGE": ("free", "FREE"),
    "VERIFIED_LICENSED_IMAGE": ("free", "OFFICIAL"),
    "VERIFIED_COPYRIGHTED_IMAGE": ("copy", "COPYRIGHTED"),
    "VERIFIED_FILE_PHOTO": ("file", "FILE PHOTO"),
    "UNCERTAIN_IMAGE": ("unsure", "UNCERTAIN"),
    "NO_VERIFIED_IMAGE": ("none", "NO IMAGE"),
    "OWNER_MEDIA": ("own", "OWN MEDIA"),
}

_CSS = """
:root{--bg:#0f1115;--card:#181b22;--line:#252a34;--fg:#e8eaf0;--mute:#8b93a7;
--free:#2ea043;--copy:#d29922;--none:#6e7681;--file:#a371f7;--bad:#f85149;--accent:#2f81f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:24px 20px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:20px;letter-spacing:.3px}
.sub{color:var(--mute);font-size:13px;margin-top:4px}
.wrap{padding:20px;max-width:1200px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:28px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card .n{font-size:28px;font-weight:600}
.card .l{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-top:2px}
.rule{color:var(--mute);font-size:13px;background:var(--card);border:1px solid var(--line);
border-left:3px solid var(--accent);border-radius:8px;padding:12px 14px;margin-bottom:24px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.8px;color:var(--mute);margin:28px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.7px;
color:var(--mute);padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}
tr:last-child td{border-bottom:none}
img.thumb{width:92px;height:62px;object-fit:cover;border-radius:6px;background:#222;display:block}
.tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600;
letter-spacing:.4px;white-space:nowrap}
.tag.free{background:rgba(46,160,67,.15);color:var(--free)}
.tag.copy{background:rgba(210,153,34,.15);color:var(--copy)}
.tag.none{background:rgba(110,118,129,.18);color:var(--none)}
.tag.file{background:rgba(163,113,247,.15);color:var(--file)}
.tag.unsure{background:rgba(248,81,73,.13);color:var(--bad)}
.tag.own{background:rgba(47,129,247,.15);color:var(--accent)}
.mute{color:var(--mute)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.filters{margin:0 0 12px}
.filters button{background:var(--card);color:var(--mute);border:1px solid var(--line);
border-radius:99px;padding:6px 14px;font-size:12px;margin-right:6px;cursor:pointer}
.filters button.on{color:var(--fg);border-color:var(--accent)}
.hl{font-weight:500;max-width:380px}
.why{color:var(--mute);font-size:11px;margin-top:4px;max-width:380px}
@media(max-width:720px){img.thumb{width:64px;height:44px}td,th{padding:8px}.hl{max-width:180px}}
"""

_JS = """
function filt(k,btn){
  document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('tbody tr').forEach(r=>{
    r.style.display = (k==='all'||r.dataset.k===k) ? '' : 'none';
  });
}
"""


def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _row(a, cands):
    style, label = _STATUS_STYLE.get(a["image_status"], ("none", a["image_status"] or "?"))
    top = next((c for c in cands if c.get("image_url")), None)
    if top and top.get("thumbnail_url"):
        thumb = (f'<a href="{_esc(top["image_url"])}" target="_blank" rel="noopener">'
                 f'<img class="thumb" loading="lazy" src="{_esc(top["thumbnail_url"])}" alt=""></a>')
    else:
        thumb = '<span class="mute">&mdash;</span>'
    conf = f'{int(round((top.get("confidence") or 0)*100))}%' if top else "&mdash;"
    age = (top or {}).get("age_bucket") or ""
    rights = (top or {}).get("license_status") or "&mdash;"
    why = _esc(((top or {}).get("reason") or "")[:160])
    src = (top or {}).get("source_name") or ""
    src_url = (top or {}).get("source_url") or a.get("link") or ""
    src_html = f'<a href="{_esc(src_url)}" target="_blank" rel="noopener">{_esc(src)}</a>' if src_url else _esc(src)
    action = {"AUTO_PUBLISH": "Publish", "MANUAL_REVIEW": "Review",
              "TEXT_ONLY": "Text only"}.get(a["decision"], a["decision"] or "")
    return (
        f'<tr data-k="{_esc(style)}">'
        f'<td><div class="hl">{_esc(a["headline"])}</div>'
        f'<div class="why">{why}</div></td>'
        f'<td>{thumb}</td>'
        f'<td><span class="tag {style}">{_esc(label)}</span></td>'
        f'<td>{src_html}</td>'
        f'<td class="mute">{_esc(a["last_checked"][11:16] if a.get("last_checked") else "")}'
        f'<div class="why">{_esc(age)}</div></td>'
        f'<td>{conf}</td>'
        f'<td class="mute">{_esc(rights)}</td>'
        f'<td>{_esc(action)}</td>'
        f'</tr>'
    )


def build(day=None, limit=200):
    """Regenerates the dashboard from whatever is currently in the DB."""
    day = day or dt.date.today().isoformat()
    conn = photo_db.connect()
    try:
        metrics = photo_db.query(
            "SELECT * FROM daily_metrics WHERE day = ?", (day,), conn)
        m = metrics[0] if metrics else {}
        arts = photo_db.query(
            "SELECT * FROM articles ORDER BY last_checked DESC LIMIT ?", (limit,), conn)
        rows = []
        for a in arts:
            rows.append(_row(a, photo_db.candidates_for(a["article_id"], conn)))
        total_candidates = photo_db.query(
            "SELECT COUNT(*) AS n FROM image_candidates", (), conn)[0]["n"]
    finally:
        conn.close()

    cards = [
        ("News today", m.get("stories", 0)),
        ("Real images found", m.get("exact_images", 0)),
        ("Free / reusable", m.get("free_images", 0)),
        ("Copyrighted", m.get("copyrighted", 0)),
        ("No image", m.get("no_image", 0)),
        ("Manual review", m.get("manual_review", 0)),
        ("File photos", m.get("file_photos", 0)),
        ("Wrong images", m.get("wrong_images", 0)),
    ]
    card_html = "".join(
        f'<div class="card"><div class="n">{v}</div><div class="l">{_esc(l)}</div></div>'
        for l, v in cards)

    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AravindNews24 &middot; Photo Review</title>
<style>{_CSS}</style>
</head><body>
<header>
  <h1>AravindNews24 &middot; Incident Photo Review</h1>
  <div class="sub">{_esc(day)} &middot; {total_candidates} candidates stored (rejects kept)
  &middot; generated {_esc(photo_db.now_iso())}</div>
</header>
<div class="wrap">
  <div class="cards">{card_html}</div>
  <div class="rule">
    <strong>Real incident photo &gt; generic photo. No photo &gt; wrong photo.</strong><br>
    Authenticity ("is this that event?") and copyright ("may we republish it?") are
    decided separately. A photo only auto-publishes when both pass.
    &ldquo;Wrong images&rdquo; counts photos <em>you</em> rejected after seeing them &mdash;
    the engine does not grade its own work.
  </div>
  <h2>Stories</h2>
  <div class="filters">
    <button class="on" onclick="filt('all',this)">All</button>
    <button onclick="filt('free',this)">Reusable</button>
    <button onclick="filt('copy',this)">Copyrighted</button>
    <button onclick="filt('file',this)">File photo</button>
    <button onclick="filt('none',this)">No image</button>
  </div>
  <table>
    <thead><tr>
      <th>News</th><th>Image</th><th>Status</th><th>Source</th>
      <th>Checked</th><th>Authenticity</th><th>Rights</th><th>Action</th>
    </tr></thead>
    <tbody>{"".join(rows) or '<tr><td colspan="8" class="mute">No stories reviewed yet.</td></tr>'}</tbody>
  </table>
</div>
<script>{_JS}</script>
</body></html>"""

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"dashboard written: {OUT_PATH} ({len(arts)} stories, {total_candidates} candidates)")
    return OUT_PATH
