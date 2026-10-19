"""Tiny local web server: serves the reports and turns "Send report" clicks into
Gmail drafts. It listens on 127.0.0.1 only, and draft creation requires a
secret token that is embedded in the report links."""

from __future__ import annotations

import html
import json
import mimetypes
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

_started: ThreadingHTTPServer | None = None


def token() -> str:
    p = config.HOME / ".server_token"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(24))
        p.chmod(0o600)
    return p.read_text().strip()


def base_url(settings) -> str:
    return f"http://127.0.0.1:{settings.qsl.server_port}"


def qsl_link(settings, rid: int) -> str:
    return f"{base_url(settings)}/qsl/{rid}?t={token()}"


PREPARING = """<!doctype html><meta charset="utf-8"><title>Preparing report</title>
<body style="background:#0000aa;color:#fff;font:15px Menlo,monospace;padding:30px">
<h2 style="color:#ffff55">Preparing your reception report...</h2>
<p id="msg">Analysing the recording (speech recognition, about 10-60 s) and creating the Gmail
draft with the audio attached.</p>
<script>
fetch("/qsl/{rid}/make" + location.search).then(r => r.json()).then(j => {{
  if (j.url) {{ location.href = j.url; return; }}
  document.getElementById("msg").innerHTML = "<b style='color:#ff5555'>" + j.error + "</b>" +
    (j.fallback ? "<p><a style='color:#55ffff' href='" + j.fallback +
     "'>Open the draft in Gmail without the attachment</a></p>" : "");
}}).catch(e => document.getElementById("msg").textContent = "Error: " + e);
</script>"""


class Handler(BaseHTTPRequestHandler):
    settings = None

    def log_message(self, *a):           # keep the TUI quiet
        pass

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                       # never let one request kill the server
            try:
                self._send(500, f"error: {html.escape(str(e))}".encode())
            except Exception:
                pass

    def _get(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        if parts[:2] == ["qsl", "status"]:
            if not secrets.compare_digest(q.get("t", [""])[0], token()):
                return self._send(403, b"forbidden")
            from . import qslstate
            try:
                st = qslstate.states(check_gmail=True)
            except Exception:
                st = qslstate.states(check_gmail=False)
            return self._send(200, json.dumps(st).encode(), "application/json")
        if parts[:1] == ["qsl"]:
            if len(parts) < 2 or not parts[1].isdigit():
                return self._send(404, b"not found")
            if not secrets.compare_digest(q.get("t", [""])[0], token()):
                return self._send(403, b"forbidden")
            if len(parts) == 2:
                return self._send(200, PREPARING.format(rid=int(parts[1])).encode())
            if len(parts) == 3 and parts[2] == "make":
                return self._send(200, json.dumps(make_draft(int(parts[1]), self.settings)).encode(),
                                  "application/json")
        if parts[:1] == ["log"]:
            from . import report
            day = parts[1] if len(parts) > 1 else None
            return self._send(200, report.day_page(day, self.settings).encode())
        if parts == ["cards"]:
            from . import report
            return self._send(200, report.cards_page(self.settings).encode())
        if parts[:2] == ["cards", "file"] and len(parts) == 3:
            from . import cards
            f = (cards.DIR / urllib.parse.unquote(parts[2])).resolve()
            if cards.DIR.resolve() in f.parents and f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype)
            return self._send(404, b"not found")
        if parts[:1] == ["card"] and len(parts) == 2 and parts[1].isdigit():
            if not secrets.compare_digest(q.get("t", [""])[0], token()):
                return self._send(403, b"forbidden")
            return self._send(200, card_form(int(parts[1])).encode())
        if parts and parts[0] in ("reports", "recordings"):
            root = (config.HOME / parts[0]).resolve() if parts[0] == "reports" else \
                self.settings.recordings_dir.resolve()
            f = (root / "/".join(urllib.parse.unquote(p) for p in parts[1:])).resolve()
            if root in f.parents and f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if f.suffix == ".flac":
                    ctype = "audio/flac"
                return self._send(200, f.read_bytes(), ctype)
        self._send(404, b"not found")


    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(400, f"<p>Error: {html.escape(str(e))}</p><p><a href='javascript:history.back()'>back</a></p>".encode())
            except Exception:
                pass

    def _post(self):
        from . import cards, logbook, qsl, report
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        n = int(self.headers.get("Content-Length") or 0)
        if n > cards.MAX_BYTES + 1_000_000:
            return self._send(413, b"file too large (max 25 MB)")
        form = _parse_form(self.headers.get("Content-Type", ""), self.rfile.read(n))
        if not secrets.compare_digest(form.get("t", ("", b""))[1].decode(errors="ignore"), token()):
            return self._send(403, b"forbidden")
        db = logbook.connect()
        if parts[:2] == ["card", "delete"] and len(parts) == 3:
            cards.remove(parts[2])
            rid = int(form.get("back", ("", b"0"))[1] or 0)
        elif parts[:1] == ["card"] and len(parts) == 2 and parts[1].isdigit():
            rid = int(parts[1])
            row = db.execute("SELECT * FROM log WHERE id=?", (rid,)).fetchone()
            name, data = form.get("file", ("", b""))
            if not row or not data:
                raise ValueError("choose a file first")
            cards.add(row, qsl.station_key(row), name, data,
                      form.get("received", ("", b""))[1].decode().strip(),
                      form.get("notes", ("", b""))[1].decode().strip())
        else:
            return self._send(404, b"not found")
        row = db.execute("SELECT utc_start FROM log WHERE id=?", (rid,)).fetchone()
        where = f"/log/{report.listening_day(row['utc_start'])}#r{rid}" if row else "/cards"
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()


def _parse_form(ctype: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """multipart/form-data or urlencoded -> {field: (filename, bytes)}."""
    from email.parser import BytesParser
    from email.policy import HTTP
    out: dict[str, tuple[str, bytes]] = {}
    if ctype.startswith("multipart/form-data"):
        msg = BytesParser(policy=HTTP).parsebytes(
            f"Content-Type: {ctype}\r\n\r\n".encode() + body)
        for part in msg.iter_parts():
            field = part.get_param("name", header="content-disposition")
            if field:
                out[field] = (part.get_filename() or "", part.get_payload(decode=True) or b"")
    else:
        for k, v in urllib.parse.parse_qsl(body.decode(errors="ignore")):
            out[k] = ("", v.encode())
    return out


CARD_FORM = """<!doctype html><meta charset="utf-8"><title>Add QSL card</title>
<body style="background:#0000aa;color:#fff;font:15px Menlo,monospace;padding:30px">
<h2 style="color:#ffff55">Add QSL card</h2>
<p><b>{station}</b> &middot; {khz} kHz &middot; heard {when} UTC</p>
{already}
<form method="post" enctype="multipart/form-data" action="/card/{rid}"
      style="background:#aaaaaa;color:#000;padding:16px;max-width:560px;border:3px double #fff">
<input type="hidden" name="t" value="{token}">
<p>Card (photo or scan: JPG, PNG, HEIC or PDF)<br><input type="file" name="file" accept="image/*,.pdf,.heic" required></p>
<p>Received on<br><input type="date" name="received" value="{today}"></p>
<p>Notes (optional)<br><input type="text" name="notes" size="50" placeholder="e.g. paper card + pennant, 3 months"></p>
<p><button style="background:#00aa00;color:#fff;border:none;padding:4px 16px;font:inherit;font-weight:bold">Save card</button>
&nbsp; <a href="/log/{day}#r{rid}" style="color:#aa0000">Cancel</a></p>
</form>"""


def card_form(rid: int) -> str:
    from datetime import date
    from . import cards, logbook, qsl, report
    row = logbook.connect().execute("SELECT * FROM log WHERE id=?", (rid,)).fetchone()
    if not row:
        return "<p>recording not found</p>"
    have = cards.by_key().get(qsl.station_key(row), [])
    already = (f"<p style='color:#ffff55'>&#9733; You already have {len(have)} card(s) from this station "
               f"(latest received {html.escape(max(c['received'] for c in have))}).</p>" if have else "")
    return CARD_FORM.format(station=html.escape(row["station"]), khz=f"{row['khz']:g}",
                            when=html.escape(row["utc_start"][:16].replace("T", " ")), already=already,
                            rid=rid, token=token(), today=date.today().isoformat(),
                            day=report.listening_day(row["utc_start"]))


def make_draft(rid: int, settings) -> dict:
    from . import gmail, logbook, programme, qsl, qslstate
    prev = qslstate.draft_for(rid)
    if prev and prev.get("status") != "sent" and gmail.draft_status(prev["draft_id"]) == "draft":
        return {"url": prev["url"]}                  # reopen it instead of making a duplicate
    db = logbook.connect()
    row = db.execute("SELECT * FROM log WHERE id=?", (rid,)).fetchone()
    if not row or not row["file"]:
        return {"error": "recording not found"}
    try:
        items = programme.analyse(row, settings)
    except Exception as e:
        items = []
        note = f" (programme analysis failed: {e})"
    else:
        note = ""
    d = qsl.draft(row, settings, items, attached=True)
    fallback = qsl.gmail_url(qsl.draft(row, settings, items, attached=False),
                             settings.qsl.gmail_account)
    try:
        url = gmail.create_draft(d["to"], d["subject"], d["text"], d["html"], Path(row["file"]),
                                 settings.qsl.gmail_account)
    except gmail.NotSetUp as e:
        return {"error": html.escape(str(e)) + note, "fallback": fallback}
    except Exception as e:
        return {"error": "Gmail error: " + html.escape(str(e)) + note, "fallback": fallback}
    qslstate.record(row, qsl.station_key(row), gmail.last_draft_id[0], url, d["to"])
    return {"url": url}


def start(settings) -> str | None:
    """Start in a background thread (idempotent). Returns an error text if the port is busy."""
    global _started
    if _started:
        return None
    Handler.settings = settings
    try:
        _started = ThreadingHTTPServer(("127.0.0.1", settings.qsl.server_port), Handler)
    except OSError as e:
        return f"report server not started: {e}"
    threading.Thread(target=_started.serve_forever, daemon=True).start()
    return None


def serve_forever(settings) -> None:
    Handler.settings = settings
    srv = ThreadingHTTPServer(("127.0.0.1", settings.qsl.server_port), Handler)
    print(f"NightDX report server on {base_url(settings)}/reports/  (Ctrl+C to stop)")
    srv.serve_forever()
