"""Morning report: a text summary for the terminal and an HTML page with players."""

from __future__ import annotations

import html
import urllib.parse
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from . import config, logbook
from .classify import radio_tags

ICON = {"voice": "VOICE", "music": "MUSIC", "voice+music": "V+M", "noise": "noise", None: "?"}


def _select(mission: str | None, since: str | None):
    db = logbook.connect()
    if mission:
        return logbook.rows(db, "mission=?", (mission,), 5000)
    if since:
        return logbook.rows(db, "utc_start>=?", (since,), 5000)
    return logbook.rows(db, limit=200)


def text_summary(rows) -> str:
    rows = list(reversed(rows))
    ok = [r for r in rows if not r["error"]]
    c = Counter(r["label"] for r in ok)
    lines = [
        f"Recordings: {len(ok)}   errors: {len(rows) - len(ok)}",
        f"  voice {c['voice']}   music {c['music']}   voice+music {c['voice+music']}"
        f"   noise {c['noise']}   unclassified {c[None]}",
    ]
    heard = [r for r in ok if r["label"] and r["label"] != "noise"]
    if ok:
        lines.append(f"  success rate {100 * len(heard) / len(ok):.0f}% (something other than noise)")
    by_region = Counter((r["region"], r["label"] != "noise" and r["label"] is not None) for r in ok)
    regions = sorted({r["region"] for r in ok})
    if regions:
        lines.append("")
        lines.append("By region (heard / recorded):")
        for g in regions:
            lines.append(f"  {g:<20} {by_region[(g, True)]:>3} / {by_region[(g, True)] + by_region[(g, False)]}")
    lines.append("")
    lines.append("UTC    kHz      Station                    Country          Pred  Mod  Result")
    for r in rows:
        res = r["error"][:30] if r["error"] else ICON.get(r["label"], r["label"])
        mod = f"{r['modulation_db']:>4.0f}" if r["modulation_db"] is not None else "   -"
        lines.append(f"{r['utc_start'][11:16]}  {r['khz']:>7g}  {r['station'][:26]:<26} "
                     f"{(r['country'] or '')[:16]:<16} {r['predicted_score'] or 0:>3}%  {mod}  {res}")
    return "\n".join(lines)


def write(mission: str | None = None, since: str | None = None,
          settings: config.Settings | None = None) -> Path:
    settings = settings or config.load()
    rows = _select(mission, since)
    out_dir = config.HOME / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = mission or f"log-{datetime.now():%Y-%m-%d_%H%M}"
    txt = text_summary(rows)
    (out_dir / f"{name}.txt").write_text(f"NightDX listening report - {name}\n"
                                         f"Receiver location: {settings.location.name}\n\n{txt}\n")
    path = out_dir / f"{name}.html"
    path.write_text(_html(name, rows, settings, out_dir))
    return path


def _cards_cell(r, settings, key: str, got: dict) -> str:
    """Card(s) attached to this recording, a note if the station already sent one for
    another recording, and the 'Add QSL card' link."""
    from . import cards, server
    esc = html.escape
    out = []
    for c in cards.for_recording(r["id"]):
        src = f"/cards/file/{urllib.parse.quote(c['file'])}"
        thumb = (f'<img src="{src}" alt="QSL card">' if cards.is_image(c) else "&#128196; PDF")
        out.append(f'<div class="qslcard"><a target="_blank" href="{src}">{thumb}</a>'
                   f'<small>QSL received {esc(c["received"])}'
                   f'{" &middot; " + esc(c["notes"]) if c["notes"] else ""}</small>'
                   f'<form method="post" action="/card/delete/{esc(c["id"])}" '
                   f'onsubmit="return confirm(\'Remove this QSL card?\')">'
                   f'<input type="hidden" name="t" value="{server.token()}">'
                   f'<input type="hidden" name="back" value="{r["id"]}">'
                   f'<button class="linkbtn">remove</button></form></div>')
    other = [c for c in got.get(key, []) if not (c["rid"] == r["id"] and
                                                 c["home"].lower() == str(config.HOME).lower())]
    if other and not out:
        c = other[0]
        out.append(f'<span class="qslnote">&#9733; QSL already received from this station '
                   f'({esc(c["received"])}, for {esc(c["utc_start"][:10])}) &middot; '
                   f'<a target="_blank" href="/cards/file/{urllib.parse.quote(c["file"])}">view card</a></span>')
    add = f'{server.base_url(settings)}/card/{r["id"]}?t={server.token()}'
    out.append(f'<a class="addcard" href="{esc(add)}">+ Add QSL card</a>')
    return "".join(out)


def _qsl_cell(r, settings, known: dict, got: dict | None = None) -> str:
    """'Send report' button: a Gmail draft with the recording attached (via the local
    NightDX server), plus fallbacks that work without it. Its colour shows whether this
    station was already contacted (refreshed live by the page script)."""
    from . import qsl, server
    if not r["file"] or r["error"] or r["label"] == "noise":
        return ""
    items = []
    if "programme" in r.keys() and r["programme"]:
        import json
        items = json.loads(r["programme"])
    d = qsl.draft(r, settings, items, attached=False)
    c = d["contact"] or {}
    key = qsl.station_key(r)
    url = html.escape(server.qsl_link(settings, r["id"]))
    plain = html.escape(qsl.gmail_url(d, settings.qsl.gmail_account))
    alt = html.escape(qsl.mailto_url(d))
    if d["to"]:
        note = html.escape(d["to"]) + (f" &middot; {html.escape(c.get('qsl_status', ''))}"
                                       if c.get("qsl_status") else "")
        if c.get("confidence") == "low":
            note += " &middot; <b>unconfirmed address</b>"
        cls, label = "qsl", "&#9993; Send report"
    else:
        form = c.get("web_form")
        note = (f'no e-mail on file &middot; <a target="_blank" href="{html.escape(form)}">web form</a>'
                if form else "no address on file - add it to qsl_contacts.json")
        cls, label = "qsl noaddr", "&#9993; Draft report"
    got = got or {}
    if key in got:                               # a QSL card already arrived for this station
        c = sorted(got[key], key=lambda x: x["received"])[0]
        cls, label = "qsl got", f"&#9733; QSL received {html.escape(c['received'])}"
    elif key in known:                           # last known state; the page script refreshes it
        if known[key]["state"] == "contacted":
            cls, label = "qsl done", f"&#10003; Reported {html.escape(known[key]['date'])}"
        else:
            cls, label = "qsl pending", f"&#9993; Draft ready {html.escape(known[key]['date'])}"
    gotattr = ' data-got="1"' if key in got else ""
    return (f'<br><a class="{cls}" target="_blank" href="{url}" data-key="{html.escape(key)}" '
            f'data-station="{html.escape(r["station"])}"{gotattr}>{label}</a>'
            f'<span class="qslnote">{note}<br>without attachment: '
            f'<a target="_blank" href="{plain}">Gmail</a> &middot; <a href="{alt}">mail app</a></span>'
            + _cards_cell(r, settings, key, got))


STATUS_JS = """<script>
// Live "already contacted" state from the local NightDX server (no-op without it).
const STATUS = "%s";
function paint(st) {
  document.querySelectorAll("a.qsl:not([data-got])").forEach(a => {
    if (!a.dataset.orig) a.dataset.orig = a.innerHTML;
    const s = st[a.dataset.key];
    a.classList.remove("done", "pending");
    if (!s) { a.innerHTML = a.dataset.orig; return; }
    if (s.state === "draft") { a.classList.add("pending"); a.innerHTML = "&#9993; Draft ready " + s.date; }
    else { a.classList.add("done"); a.innerHTML = "&#10003; Reported " + s.date; }
    a.dataset.state = s.state; a.dataset.date = s.date;
  });
}
function refresh() { fetch(STATUS).then(r => r.json()).then(paint).catch(() => {}); }
document.addEventListener("click", e => {
  const a = e.target.closest("a.qsl");
  if (a && a.dataset.got) {
    if (!confirm("You already have a QSL card from " + a.dataset.station + ". Prepare another report anyway?")) e.preventDefault();
    return;
  }
  if (!a || !a.dataset.state) return;
  const msg = a.dataset.state === "draft"
    ? "A report to " + a.dataset.station + " is already waiting in your Gmail drafts (" + a.dataset.date + "). Prepare another one?"
    : "You already reported to " + a.dataset.station + " on " + a.dataset.date + ". Prepare another report?";
  if (!confirm(msg)) e.preventDefault();
});
refresh();
window.addEventListener("focus", refresh);
setInterval(refresh, 60000);
</script>"""


def _html(name, rows, settings, out_dir: Path, nav: str = "", title: str = "") -> str:
    from . import cards, qslstate, server
    got = cards.by_key()
    rows = list(reversed(rows))
    ok = [r for r in rows if not r["error"]]
    c = Counter(r["label"] for r in ok)
    esc = html.escape
    known = qslstate.states(check_gmail=False)      # the page script refreshes this live
    first = {x[0] for x in logbook.connect().execute(      # first recording of each station
        "SELECT MIN(id) FROM log WHERE error IS NULL AND file IS NOT NULL GROUP BY lower(station)")}
    status_js = STATUS_JS % f"{server.base_url(settings)}/qsl/status?t={server.token()}"

    def rel(p):
        try:
            return esc(os.path.relpath(p, out_dir))
        except ValueError:
            return esc(p)

    trs = []
    for r in rows:
        label = r["label"] or ("error" if r["error"] else "pending")
        player = (f'<audio controls preload="none" src="{rel(r["file"])}"></audio>'
                  if r["file"] else f'<span class="err">{esc(r["error"] or "")}</span>')
        bar = ""
        if r["label"]:
            bar = (f'<div class="bar"><i class="v" style="width:{r["voice_pct"]}%"></i>'
                   f'<i class="m" style="width:{r["music_pct"]}%"></i>'
                   f'<i class="n" style="width:{r["noise_pct"]}%"></i></div>')
        trs.append(f"""<tr id="r{r['id']}" class="{esc(label.replace('+', '-'))}">
<td>{esc(r['utc_start'][11:16])}</td><td class="num">{r['khz']:g}</td>
<td><b>{esc(r['station'])}</b>{' <span class="new">&#9733; NEW</span>' if r['id'] in first else ''}<br><small>{esc(r['country'] or '')} &middot; {esc(r['language'] or '')}
&middot; to {esc(r['target'] or '')}</small></td>
<td class="num">{r['distance_km'] or 0:.0f}</td><td class="num">{r['predicted_score'] or 0}%</td>
<td class="num">{'' if r['modulation_db'] is None else f"{r['modulation_db']:.0f} dB"}</td>
<td><span class="tag">{esc(label)}</span>{bar}<small>{esc(', '.join(radio_tags(r['class_tags'] or '')))}</small></td>
<td>{player}{_qsl_cell(r, settings, known, got)}</td></tr>""")
    heard = sum(1 for r in ok if r["label"] and r["label"] != "noise")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NightDX {esc(name)}</title><style>
:root{{--bg:#0000aa;--fg:#fff;--panel:#000080;--hi:#55ffff;--yel:#ffff55;--muted:#aaaaaa}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 "IBM Plex Mono",Menlo,monospace;margin:0;padding:16px}}
h1{{background:#aaaaaa;color:#000;margin:-16px -16px 16px;padding:6px 16px;font-size:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}}
.card{{background:var(--panel);border:3px double var(--hi);padding:8px 14px;min-width:110px}}
.card b{{display:block;font-size:24px;color:var(--yel)}}
table{{border-collapse:collapse;width:100%;background:var(--panel)}}
th,td{{border:1px solid #5555ff;padding:4px 6px;vertical-align:top;text-align:left}}
th{{background:#00aaaa;color:#000}} .num{{text-align:right}} small{{color:var(--muted)}}
.tag{{display:inline-block;padding:0 6px;background:#555;color:#fff}}
tr.voice .tag{{background:#00aa00}} tr.music .tag{{background:#aa00aa}}
tr.voice-music .tag{{background:#aa5500}} tr.noise .tag{{background:#555}} tr.error .tag{{background:#aa0000}}
.bar{{display:flex;height:6px;width:120px;margin:4px 0;background:#333}}
.bar i{{display:block}} .v{{background:#55ff55}} .m{{background:#ff55ff}} .n{{background:#777}}
.err{{color:#ff5555}} audio{{height:28px;max-width:260px}}
.qsl{{display:inline-block;margin-top:4px;padding:2px 10px;background:#00aa00;color:#fff;
      text-decoration:none;font-weight:bold;border:1px solid #55ff55}}
.qsl:hover{{background:#55ff55;color:#000}} .qsl.noaddr{{background:#0055aa;border-color:#55aaff}}
.qsl.pending{{background:#ff8800;border-color:#ffcc55;color:#000}}
.qsl.done{{background:#555555;border-color:#aaaaaa;color:#dddddd;font-weight:normal}}
.qslnote{{display:block;color:#aaaaaa;font-size:12px}} .qslnote a{{color:#55ffff}}
.qsl.got{{background:#ccaa00;border-color:#ffff55;color:#000}}
.new{{background:#ff55ff;color:#000;font-size:11px;padding:0 4px;font-weight:bold}}
.addcard{{display:inline-block;margin-top:4px;font-size:12px;color:#ffff55}}
.qslcard{{margin-top:6px}} .qslcard img{{max-height:90px;max-width:160px;border:2px solid #ffff55;display:block}}
.qslcard small{{display:block}} .qslcard form{{display:inline}}
.linkbtn{{background:none;border:none;color:#ff5555;cursor:pointer;font:inherit;font-size:12px;padding:0}}
.nav{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:-8px 0 14px}}
.nav a{{color:#ffff55;text-decoration:none;border:1px solid #5555ff;padding:2px 8px;background:#000080}}
.nav a:hover{{background:#00aa00;color:#fff}} .nav .off{{color:#5555aa;border:1px solid #333366;padding:2px 8px}}
.month{{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 14px}}
.month a,.month span{{display:inline-block;min-width:26px;text-align:center;padding:2px 4px;font-size:12px}}
.month a{{background:#000080;color:#55ffff;text-decoration:none;border:1px solid #5555ff}}
.month a.today{{background:#ffff55;color:#000}} .month span{{color:#33337a}}
.gallery img{{max-height:120px;max-width:200px;border:2px solid #ffff55}}
a{{color:#55ffff}} .gallery td a{{margin-right:8px}}
@media (max-width:700px){{td:nth-child(4),th:nth-child(4),td:nth-child(6),th:nth-child(6){{display:none}}}}
</style></head><body>
<h1>NightDX listening report &mdash; {esc(title or name)} &mdash; {esc(settings.location.name)}</h1>
{nav}
<div class="cards">
<div class="card">recorded<b>{len(ok)}</b></div><div class="card">heard<b>{heard}</b></div>
<div class="card">voice<b>{c['voice']}</b></div><div class="card">music<b>{c['music']}</b></div>
<div class="card">voice+music<b>{c['voice+music']}</b></div><div class="card">noise<b>{c['noise']}</b></div>
</div>
<table><tr><th>UTC</th><th>kHz</th><th>Station</th><th>km</th><th>Pred.</th><th>Mod.</th>
<th>AI verdict</th><th>Listen</th></tr>
{''.join(trs)}
</table>{status_js}</body></html>"""


# --------------------------------------------------------------------------- day navigation

def listening_day(utc_iso: str) -> str:
    """Local date of the listening night: noon to noon, so 23:30-06:30 stays together."""
    t = datetime.fromisoformat(utc_iso)
    if t.tzinfo is None:
        from datetime import timezone
        t = t.replace(tzinfo=timezone.utc)
    return (t.astimezone() - timedelta(hours=12)).strftime("%Y-%m-%d")


def _days() -> dict[str, list]:
    days: dict[str, list] = {}
    for r in logbook.rows(logbook.connect(), limit=1_000_000):
        days.setdefault(listening_day(r["utc_start"]), []).append(r)
    return dict(sorted(days.items()))


def day_page(day: str | None, settings) -> str:
    days = _days()
    keys = list(days)
    if not keys:
        return _html("no recordings yet", [], settings, config.HOME / "reports")
    if day not in days:
        later = [k for k in keys if day and k >= day]
        day = later[0] if later else keys[-1]
    i = keys.index(day)
    d = datetime.strptime(day, "%Y-%m-%d")
    month = d.strftime("%Y-%m")
    link = lambda k, text: f'<a href="/log/{k}">{text}</a>'
    off = lambda text: f'<span class="off">{text}</span>'
    prev_m = [k for k in keys if k[:7] < month]
    next_m = [k for k in keys if k[:7] > month]
    nav = ('<div class="nav">'
           + (link(prev_m[-1], "&laquo; prev. month") if prev_m else off("&laquo; prev. month"))
           + (link(keys[i - 1], "&#9664; previous day") if i > 0 else off("&#9664; previous day"))
           + f'<b>{d:%A %d %B %Y}</b> <small>(night of)</small>'
           + (link(keys[i + 1], "next day &#9654;") if i < len(keys) - 1 else off("next day &#9654;"))
           + (link(next_m[0], "next month &raquo;") if next_m else off("next month &raquo;"))
           + '<a href="/cards">&#9733; QSL cards</a></div>')
    import calendar
    strip = ['<div class="month">']
    for n in range(1, calendar.monthrange(d.year, d.month)[1] + 1):
        k = f"{month}-{n:02d}"
        if k in days:
            cls = ' class="today"' if k == day else ""
            strip.append(f'<a{cls} href="/log/{k}" title="{len(days[k])} recordings">{n}</a>')
        else:
            strip.append(f"<span>{n}</span>")
    strip.append("</div>")
    rows = days[day]                        # newest first, like logbook.rows(); _html flips it
    return _html(day, rows, settings, config.HOME / "reports", nav=nav + "".join(strip),
                 title=f"night of {d:%d %b %Y}")


def cards_page(settings) -> str:
    from . import cards
    esc = html.escape
    here = str(config.HOME).lower()
    rows = []
    for c in cards.all_cards():
        src = f"/cards/file/{urllib.parse.quote(c['file'])}"
        thumb = f'<img src="{src}">' if cards.is_image(c) else "&#128196; PDF"
        links = [f'<a target="_blank" href="{src}">card</a>']
        if c["home"].lower() == here:
            r = logbook.connect().execute("SELECT file FROM log WHERE id=?", (c["rid"],)).fetchone()
            if r and r["file"]:
                try:
                    rel = Path(r["file"]).relative_to(settings.recordings_dir)
                    links.append(f'<audio controls preload="none" src="/recordings/{esc(str(rel))}"></audio>')
                except ValueError:
                    pass
            links.append(f'<a href="/log/{listening_day(c["utc_start"])}#r{c["rid"]}">report of that night</a>')
        else:
            links.append("<small>(recorded with the other NightDX version)</small>")
        rows.append(f'<tr><td><a target="_blank" href="{src}">{thumb}</a></td>'
                    f'<td><b>{esc(c["station"])}</b><br><small>{esc(c["country"])}</small></td>'
                    f'<td class="num">{c["khz"]:g}</td><td>{esc(c["utc_start"][:16].replace("T", " "))} UTC</td>'
                    f'<td>{esc(c["received"])}</td><td>{esc(c["notes"])}</td><td>{" ".join(links)}</td></tr>')
    stations = len({c["key"] for c in cards.all_cards()})
    body = (f'<div class="nav"><a href="/log/">&#9664; back to the log</a></div>'
            f'<div class="cards"><div class="card">QSL cards<b>{len(rows)}</b></div>'
            f'<div class="card">stations<b>{stations}</b></div></div>'
            '<table class="gallery"><tr><th>Card</th><th>Station</th><th>kHz</th><th>Heard</th>'
            '<th>Received</th><th>Notes</th><th>Links</th></tr>' + "".join(rows) + "</table>"
            + ("" if rows else "<p>No QSL cards yet - use <b>+ Add QSL card</b> in the log.</p>"))
    page = _html("QSL cards", [], settings, config.HOME / "reports", title="QSL cards received")
    head, _, rest = page.partition('<div class="cards">')
    tail = rest[rest.index("</table>") + len("</table>"):]
    return head + body + tail
