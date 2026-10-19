"""Reception reports: who to write to, and a ready-to-send draft.

The report page has a button per recording. With the local NightDX server
running (it starts with the TUI, or `nightdx serve`), the button creates a
real Gmail draft through the Gmail API - with the recording attached - and
opens it. Without the server it falls back to a Gmail compose link (no
attachment possible). Nothing is ever sent automatically: you check the
draft and press Send.

Addresses come from qsl_contacts.json (researched from the stations' pages
and DX-club QSL logs, with sources); add or correct entries in
qsl_contacts.json in the data folder, which takes precedence. The text is
qsl_template.txt in the data folder ({report} and {programme} are filled in).
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from . import config

PACKAGED = Path(__file__).with_name("qsl_contacts.json")
CHANNEL_DB = 26.0     # carrier per ~12 Hz bin -> SNR in a 5 kHz channel (as in scan.py)

DEFAULT_TEMPLATE = """\
Subject: Reception report - {station} {khz} kHz - {date} {time_from} UTC

Dear friends at {contact_name},

My name is {name} and I listen to shortwave and medium wave radio from {location}. \
I have been listening to international broadcasters and collecting QSL cards for more than \
{years} years, and I was very pleased to receive your transmission. Here is my reception report.

{report}

Programme details:
{programme}

{audio_note}

As a QSL collector for over {years} years, I would be very grateful if you could confirm my \
report with your QSL card. My postal address is:

{postal_address}

Thank you very much for your programmes. Best regards and 73,

{name}
{email}
"""


# --------------------------------------------------------------------------- contacts

def _contacts() -> list[dict]:
    out = []
    for path in (config.HOME / "qsl_contacts.json", PACKAGED):   # user entries first: they win
        try:
            out += json.loads(path.read_text())
        except Exception:
            pass
    return out


# Research sources sometimes use ISO-style codes; EiBi uses ITU ones.
ITU_ALIASES = {"PRK": "KRE", "VTC": "CVA", "VAT": "CVA", "ESW": "SWZ", "DEU": "D", "GER": "D",
               "ESP": "E", "FRA": "F", "GBR": "G", "UK": "G", "ITA": "I", "JPN": "J", "BRA": "B",
               "SWE": "S", "NLD": "HOL", "CHE": "SUI", "PHI": "PHL", "IDN": "INS"}


def _norm(s: str) -> str:
    """lower-case, accents removed: 'España' -> 'espana'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()


def _itu(code: str | None) -> str:
    code = (code or "").upper()
    return ITU_ALIASES.get(code, code)


def contact(station: str, itu: str | None = None) -> dict | None:
    """Best contact for an EiBi/MWLIST station name.

    Long names match as substrings (accents ignored); short codes like 'MWV' or 'REE'
    only as whole words and only within the same country. Same-country matches win."""
    name, itu = _norm(station), _itu(itu)
    best, best_score = None, 0
    for c in _contacts():
        citu = _itu(c.get("itu"))
        same = bool(citu) and citu == itu
        for m in c.get("match", []):
            nm = _norm(m)
            if not nm:
                continue
            if len(nm) < 5:
                if not same or not re.search(rf"(?<![a-z0-9]){re.escape(nm)}(?![a-z0-9])", name):
                    continue
            elif nm not in name or (citu and itu and not same and len(nm) < 15
                                    and itu not in ("CLA", "XUU")):
                continue
            score = len(nm) + (100 if same else 0)
            if score > best_score:
                best, best_score = c, score
    return best


def station_key(row) -> str:
    """Identity used for 'already contacted': the address if we have one, else the name."""
    c = contact(row["station"], row["itu"])
    return ((c or {}).get("email") or "").lower() or "name:" + _norm(row["station"])


# --------------------------------------------------------------------------- SINPO

def _grade(value, steps) -> int:
    for grade, threshold in steps:
        if value >= threshold:
            return grade
    return 1


def sinpo(row) -> tuple[int, int, int, int, int]:
    """Estimate SINPO (1-5 each) from what NightDX measured."""
    snr = (row["carrier_db"] or 0) - CHANNEL_DB
    s = _grade(snr, [(5, 40), (4, 30), (3, 20), (2, 10)])
    n = _grade(row["modulation_db"] or 0, [(5, 30), (4, 20), (3, 12), (2, 6)])
    i = 4                                     # adjacent/co-channel QRM isn't measured
    fading = row["fading_db"] if "fading_db" in row.keys() else None
    p = 3 if fading is None else _grade(-fading, [(5, -1.0), (4, -2.5), (3, -5.0), (2, -8.0)])
    if row["label"] == "noise":
        return min(s, 2), i, 1, p, 1
    o = max(1, min(round((s + i + n + p) / 4), min(s, n, p) + 1))
    return s, i, n, p, o


# --------------------------------------------------------------------------- template

def template() -> str:
    path = config.HOME / "qsl_template.txt"
    if path.exists():
        text = path.read_text()
        if "{report}" in text:
            return text
        path.with_name("qsl_template.old.txt").write_text(text)   # pre-{report} version
    path.write_text(DEFAULT_TEMPLATE)
    return DEFAULT_TEMPLATE


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# --------------------------------------------------------------------------- draft

def _place(settings) -> str:
    loc = settings.location
    return ", ".join(x for x in (loc.name, loc.country) if x) or "[your location]"


def _report_fields(row, settings) -> list[tuple[str, str]]:
    q, loc = settings.qsl, settings.location
    start = datetime.fromisoformat(row["utc_start"])
    end = start + timedelta(seconds=row["duration_s"] or 120)
    s, i, n, p, o = sinpo(row)
    fading = row["fading_db"] if "fading_db" in row.keys() else None
    fade = ("fading not measured" if fading is None else
            "little fading" if fading < 2.5 else "moderate fading" if fading < 5 else "deep fading")
    snr = max(0, (row["carrier_db"] or 0) - CHANNEL_DB)
    return [
        ("Station", row["station"]),
        ("Frequency", f"{row['khz']:g} kHz"),
        ("Date", start.strftime("%d %B %Y")),
        ("Time", f"{start:%H:%M} - {end:%H:%M} UTC"),
        ("Language", row["language"] or "-"),
        ("Transmitter site", f"{row['tx_site'] or '-'} (according to the schedule)"),
        ("Reception place", f"{_place(settings)} ({abs(loc.lat):.2f}{'N' if loc.lat >= 0 else 'S'}, "
                            f"{abs(loc.lon):.2f}{'E' if loc.lon >= 0 else 'W'})"),
        ("Distance", f"about {row['distance_km'] or 0:.0f} km"),
        ("Receiver", q.receiver),
        ("Antenna", q.antenna),
        ("SINPO", f"{s}{i}{n}{p}{o} (Signal {s}, Interference {i}, Noise {n}, "
                  f"Propagation {p}, Overall {o})"),
        ("Signal", f"carrier about {snr:.0f} dB above the noise, {fade}"),
    ]


def draft(row, settings, items: list[dict] | None = None, attached: bool = False) -> dict:
    """{'to', 'subject', 'text', 'html', 'contact'} for one logbook row.
    `items` are programme details from programme.analyse()."""
    from .programme import as_lines
    q = settings.qsl
    c = contact(row["station"], row["itu"])
    start = datetime.fromisoformat(row["utc_start"])
    fields = _report_fields(row, settings)
    lines = as_lines(items or [])
    if not lines:
        lines = [f"{start:%H:%M} UTC - [please add what you heard: station ID, news item, song...]"]
    minutes = max(1, round((row["duration_s"] or 120) / 60))
    audio_note = (f"I have attached a {minutes}-minute recording of the reception "
                  f"({Path(row['file']).suffix.lstrip('.').upper()} audio), so you can check the "
                  f"programme details." if attached else
                  "I would be happy to send you an audio recording of the reception.")
    common = dict(
        station=row["station"],
        contact_name=re.sub(r"\s*\(.*?\)", "", (c or {}).get("name") or row["station"]).strip(),
        khz=f"{row['khz']:g}", date=start.strftime("%d %B %Y"), time_from=f"{start:%H:%M}",
        location=_place(settings), name=q.name or "[your name]", email=q.email or "",
        years=q.years_collecting, audio_note=audio_note,
        postal_address="\n".join(x.strip() for x in q.postal_address.split("/") if x.strip())
        or "[your postal address]",
    )
    tpl = template()
    report_txt = "RECEPTION REPORT\n" + "\n".join(f"{k}: {v}" for k, v in fields)
    text = tpl.format_map(_Safe(common, report=report_txt,
                                programme="\n".join(f"- {l}" for l in lines)))
    subject = ""
    if text.startswith("Subject:"):
        first, _, text = text.partition("\n")
        subject = first[len("Subject:"):].strip()
        text = text.lstrip("\n")
    # HTML twin: same template, report as a table, programme as a list
    marker_r, marker_p = "\x00REPORT\x00", "\x00PROGRAMME\x00"
    h = tpl.format_map(_Safe(common, report=marker_r, programme=marker_p))
    if h.startswith("Subject:"):
        h = h.partition("\n")[2].lstrip("\n")
    table = ('<p><b>RECEPTION REPORT</b></p><table cellpadding="3" style="border-collapse:collapse">'
             + "".join(f'<tr><td style="padding-right:16px;vertical-align:top;font-size:14px">'
                       f'<b>{html.escape(k)}</b></td><td style="font-size:14px">{html.escape(v)}</td></tr>'
                       for k, v in fields) + "</table>")
    plist = "<ul>" + "".join(f"<li>{html.escape(l)}</li>" for l in lines) + "</ul>"
    parts = []
    for para in h.split("\n\n"):
        if marker_r in para:
            parts.append(table)
        elif marker_p in para:
            head = para.replace(marker_p, "").strip()
            parts.append((f"<p>{html.escape(head)}</p>" if head else "") + plist)
        elif para.strip():
            parts.append("<p>" + html.escape(para.strip()).replace("\n", "<br>") + "</p>")
    body_html = '<div style="font-family:Arial,sans-serif;font-size:14px">' + "".join(parts) + "</div>"
    return {"to": (c or {}).get("email") or "", "subject": subject, "text": text,
            "html": body_html, "contact": c}


def gmail_url(d: dict, account: str = "") -> str:
    params = {"view": "cm", "fs": "1", "to": d["to"], "su": d["subject"], "body": d["text"]}
    if account:
        params["authuser"] = account
    return "https://mail.google.com/mail/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def mailto_url(d: dict) -> str:
    q = urllib.parse.urlencode({"subject": d["subject"], "body": d["text"]},
                               quote_via=urllib.parse.quote)
    return f"mailto:{urllib.parse.quote(d['to'])}?{q}"
