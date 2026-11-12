"""MOSS Recognition Center v0.1 — low-resource, fail-closed WSGI application.

Only Pillow is required. The service stores user-verified image examples and
returns ADVISORY visual matches. It never commands motors or authorizes sorting.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote

from PIL import Image, ImageOps, UnidentifiedImageError

VERSION = "0.1.0"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 9_000_000
MAX_EXAMPLES_SEARCHED = 20_000
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
UUID_RE = re.compile(r"^[a-f0-9]{32}$")


class RequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dhash(img):
    """Compute a 64-bit difference hash; this is NOT a neural AI model."""
    reduced = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = [reduced.getpixel((x, y)) for y in range(8) for x in range(9)]
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def clean_text(value, max_len=200):
    return str(value or "").strip()[:max_len]


def candidate_list(raw):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise RequestError(400, "invalid_candidates", "candidates must be a list of at most 20 entries")
    result = []
    for obj in raw:
        if not isinstance(obj, dict):
            raise RequestError(400, "invalid_candidates", "each candidate must be an object")
        pid = clean_text(obj.get("product_id"), 128)
        if not ID_RE.fullmatch(pid):
            raise RequestError(400, "invalid_product_id", "candidate product_id must be a short safe identifier")
        try:
            distance = float(obj.get("distance", 0))
        except (ValueError, TypeError):
            raise RequestError(400, "invalid_distance", "candidate distance must be numeric")
        if not 0 <= distance <= 1_000_000:
            raise RequestError(400, "invalid_distance", "candidate distance is out of range")
        result.append({
            "product_id": pid,
            "name": clean_text(obj.get("name")),
            "set": clean_text(obj.get("set"), 32),
            "collector_number": clean_text(obj.get("collector_number", obj.get("number")), 64),
            "distance": distance,
        })
    return result


def decode_image(value):
    if not isinstance(value, str):
        raise RequestError(400, "missing_image", "image_base64 must be a base64-encoded JPEG or PNG")
    if len(value) > (MAX_IMAGE_BYTES * 4 // 3) + 8:
        raise RequestError(413, "image_too_large", "image exceeds configured size limit")
    try:
        binary = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise RequestError(400, "invalid_image", "image_base64 is not valid base64")
    if not binary or len(binary) > MAX_IMAGE_BYTES:
        raise RequestError(413 if binary else 400, "invalid_image_size", "image is empty or too large")
    try:
        with Image.open(io.BytesIO(binary)) as original:
            if original.format not in {"JPEG", "PNG"}:
                raise RequestError(415, "unsupported_image", "only JPEG and PNG are accepted")
            width, height = original.size
            if width < 120 or height < 160 or width * height > MAX_PIXELS:
                raise RequestError(400, "invalid_dimensions", "image dimensions are outside accepted range")
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
    except RequestError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise RequestError(400, "invalid_image", "file is not a valid image")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue(), dhash(image)


def _escape(s):
    return html.escape(str(s if s is not None else ""), quote=True)


class RecognitionApp:
    def __init__(self, config=None):
        c = dict(config or {})
        self.api_key = c.get("api_key", os.environ.get("MOSS_API_KEY", ""))
        self.admin_password = c.get("admin_password", os.environ.get("MOSS_ADMIN_PASSWORD", ""))
        self.secret = c.get("session_secret", os.environ.get("MOSS_SESSION_SECRET", ""))
        self.data_dir = Path(c.get("data_dir", os.environ.get("MOSS_DATA_DIR", "./private_data"))).resolve()
        self.cookie_secure = c.get("cookie_secure", os.environ.get("MOSS_COOKIE_SECURE", "1") != "0")
        self.session_seconds = 12 * 60 * 60
        if any(not isinstance(x, str) or len(x) < 16 for x in (self.api_key, self.admin_password, self.secret)):
            raise RuntimeError("MOSS_API_KEY, MOSS_ADMIN_PASSWORD, MOSS_SESSION_SECRET must each contain at least 16 characters")
        if self.data_dir.name == "public_html" or "public_html" in self.data_dir.parts:
            raise RuntimeError("MOSS_DATA_DIR must NOT be inside public_html")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.data_dir / "scan_images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "moss_recognition.db"
        self._init_db()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout = 10000")
            db.execute("PRAGMA foreign_keys = ON")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self):
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Database schema is newer than this application")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                device_id TEXT NOT NULL, image_file TEXT NOT NULL,
                image_dhash TEXT NOT NULL, candidates_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','verified','rejected')),
                verified_product_id TEXT, verified_name TEXT,
                verified_set TEXT, verified_number TEXT,
                reviewed_at TEXT, review_note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scans_state_created ON scans(state, created_at);
            CREATE TABLE IF NOT EXISTS verified_examples (
                scan_id TEXT PRIMARY KEY REFERENCES scans(id),
                product_id TEXT NOT NULL, name TEXT NOT NULL,
                set_code TEXT NOT NULL, collector_number TEXT NOT NULL,
                image_dhash TEXT NOT NULL, verified_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL REFERENCES scans(id),
                action TEXT NOT NULL, product_id TEXT,
                recorded_at TEXT NOT NULL, source_ip TEXT NOT NULL,
                note TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                source_ip TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
                window_start INTEGER NOT NULL
            );
            PRAGMA user_version = 1;
            """)

    def _json(self, start_response, status, data, headers=()):
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self._send(start_response, status, payload, "application/json; charset=utf-8", headers)

    def _html(self, start_response, status, content, headers=()):
        return self._send(start_response, status, content.encode("utf-8"), "text/html; charset=utf-8", headers)

    def _send(self, start_response, status, body, content_type, headers=()):
        phrase = HTTPStatus(status).phrase
        common = [("Content-Type", content_type), ("Content-Length", str(len(body))),
                  ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                  ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                  ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; base-uri 'none'")]
        start_response(f"{status} {phrase}", common + list(headers))
        return [body]

    def _redirect(self, start_response, destination, headers=()):
        return self._send(start_response, 303, b"", "text/plain", [("Location", destination)] + list(headers))

    def _json_body(self, environ):
        if "application/json" not in environ.get("CONTENT_TYPE", "").lower():
            raise RequestError(415, "content_type", "use application/json")
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
        except (TypeError, ValueError):
            raise RequestError(411, "length_required", "Content-Length is required")
        if length <= 0 or length > MAX_JSON_BYTES:
            raise RequestError(413, "invalid_length", "request exceeds size limit or is empty")
        content = environ["wsgi.input"].read(length)
        if len(content) != length:
            raise RequestError(400, "incomplete_request", "incomplete request body")
        try:
            obj = json.loads(content)
        except (ValueError, UnicodeError):
            raise RequestError(400, "invalid_json", "invalid JSON request")
        if not isinstance(obj, dict):
            raise RequestError(400, "invalid_json", "JSON root must be an object")
        return obj

    def _form_body(self, environ):
        if "application/x-www-form-urlencoded" not in environ.get("CONTENT_TYPE", "").lower():
            raise RequestError(415, "content_type", "expected standard HTML form")
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
        except (TypeError, ValueError):
            raise RequestError(411, "length_required", "form Content-Length required")
        if length < 0 or length > 8192:
            raise RequestError(413, "form_too_large", "form exceeds size limit")
        fields = parse_qs(environ["wsgi.input"].read(length).decode("utf-8"), keep_blank_values=True)
        return {key: values[0] for key, values in fields.items()}

    def _api_authorized(self, environ):
        supplied = environ.get("HTTP_X_MOSS_API_KEY", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.api_key)

    def _session_cookie(self, environ):
        cookies = environ.get("HTTP_COOKIE", "")
        for field in cookies.split(";"):
            if field.strip().startswith("moss_admin="):
                return field.strip().split("=", 1)[1]
        return ""

    def _new_session(self):
        payload = f"{int(time.time()) + self.session_seconds}:{secrets.token_hex(12)}"
        digest = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + digest

    def _session_valid(self, cookie):
        try:
            encoded, digest = cookie.split(".", 1)
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
            expected = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            expiry_str, _nonce = payload.split(":", 1)
            return hmac.compare_digest(expected, digest) and int(expiry_str) > int(time.time())
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            return False

    def _cookie_header(self, cookie, maximum_age=None):
        extra = "; Secure" if self.cookie_secure else ""
        age = f"; Max-Age={maximum_age}" if maximum_age is not None else ""
        return ("Set-Cookie", f"moss_admin={cookie}; Path=/admin; HttpOnly; SameSite=Strict{extra}{age}")

    def _csrf(self, cookie):
        return hmac.new(self.secret.encode(), ("csrf:" + cookie).encode(), hashlib.sha256).hexdigest()

    def _csrf_valid(self, environ, form):
        cookie = self._session_cookie(environ)
        return self._session_valid(cookie) and hmac.compare_digest(self._csrf(cookie), form.get("csrf_token", ""))

    def _login_allowed(self, source_ip):
        now = int(time.time())
        with self._connect() as db:
            row = db.execute("SELECT attempts, window_start FROM login_attempts WHERE source_ip=?", (source_ip,)).fetchone()
            if row and now - row["window_start"] < 900 and row["attempts"] >= 5:
                return False
        return True

    def _note_login_failure(self, source_ip):
        now = int(time.time())
        with self._connect() as db:
            db.execute("""INSERT INTO login_attempts(source_ip,attempts,window_start) VALUES(?,1,?)
                ON CONFLICT(source_ip) DO UPDATE SET
                attempts = CASE WHEN ?-window_start >= 900 THEN 1 ELSE attempts+1 END,
                window_start = CASE WHEN ?-window_start >= 900 THEN ? ELSE window_start END""",
                (source_ip, now, now, now, now))

    def _client_info(self, obj):
        device_id = clean_text(obj.get("device_id"), 80)
        if not device_id or not ID_RE.fullmatch(device_id):
            raise RequestError(400, "invalid_device_id", "device_id must contain letters, digits, underscore, dash, period or colon")
        return device_id, candidate_list(obj.get("candidates"))

    def _suggestions(self, image_hash):
        """Simple verified-example retrieval, NOT a calibrated AI confidence."""
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM verified_examples").fetchone()[0]
            # Never silently pretend a truncated gallery is an exhaustive result.
            if total > MAX_EXAMPLES_SEARCHED:
                return [], "gallery_limit_exceeded"
            examples = db.execute("SELECT product_id,name,set_code,collector_number,image_dhash FROM verified_examples").fetchall()
        query_hash = int(image_hash, 16)
        best = {}
        for row in examples:
            distance = (query_hash ^ int(row["image_dhash"], 16)).bit_count()
            if distance > 10:
                continue
            pid = row["product_id"]
            if pid not in best or distance < best[pid]["dhash_distance"]:
                best[pid] = {"product_id": pid, "name": row["name"], "set": row["set_code"],
                             "collector_number": row["collector_number"], "dhash_distance": distance}
        return sorted(best.values(), key=lambda v: (v["dhash_distance"], v["product_id"]))[:5], None

    def _ingest(self, environ, start_response, persist):
        obj = self._json_body(environ)
        device_id, candidates = self._client_info(obj)
        image_bytes, digest = decode_image(obj.get("image_base64"))
        suggestions, warning = self._suggestions(digest)
        base = {"version": VERSION, "advisory_only": True, "suggestions": suggestions,
                "warning": warning, "message": "Do not use server suggestions alone to command sorter motion."}
        if not persist:
            return self._json(start_response, 200, dict(base, state="advisory_only"))
        sid = uuid.uuid4().hex
        filename = sid + ".jpg"
        destination = self.image_dir / filename
        temporary = self.image_dir / (sid + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(image_bytes)
            os.replace(temporary, destination)
            with self._connect() as db:
                db.execute("""INSERT INTO scans(id,created_at,device_id,image_file,image_dhash,candidates_json,state)
                              VALUES(?,?,?,?,?,?,'pending')""",
                           (sid, utc_now(), device_id, filename, digest, json.dumps(candidates)))
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return self._json(start_response, 201, dict(base, scan_id=sid, state="pending"))

    def _scan_status(self, sid, start_response):
        with self._connect() as db:
            scan = db.execute("SELECT id,created_at,device_id,state,verified_product_id,verified_name,verified_set,verified_number,reviewed_at FROM scans WHERE id=?", (sid,)).fetchone()
        if not scan:
            raise RequestError(404, "not_found", "scan does not exist")
        return self._json(start_response, 200, dict(scan))

    def _metrics(self, start_response):
        with self._connect() as db:
            rows = db.execute("SELECT state, COUNT(*) AS n FROM scans GROUP BY state").fetchall()
            examples = db.execute("SELECT COUNT(*) FROM verified_examples").fetchone()[0]
        counts = {"pending": 0, "verified": 0, "rejected": 0}
        counts.update({row["state"]: row["n"] for row in rows})
        return self._json(start_response, 200, {"version": VERSION, "scans": counts, "verified_examples": examples})

    def _layout(self, title, body):
        style = """body{background:#111827;color:#f1f5f9;font-family:system-ui,Arial,sans-serif;margin:0;padding:30px;max-width:1100px}
        a{color:#93c5fd} .muted{color:#9ca3af} .card{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:18px;margin:14px 0}
        input,select,textarea{background:#111827;color:white;border:1px solid #6b7280;padding:9px;border-radius:5px;max-width:100%}
        button{background:#2563eb;color:white;border:0;border-radius:6px;padding:10px 18px;cursor:pointer;margin:8px 4px 8px 0}
        table{border-collapse:collapse;width:100%} td,th{text-align:left;padding:10px;border-bottom:1px solid #374151}
        .warn{color:#fbbf24} .small{font-size:.9em} img{max-width:300px;max-height:440px;border-radius:6px}
        label{display:block;margin:10px 0}input[type=checkbox]{width:auto} .row{display:flex;gap:24px;flex-wrap:wrap}"""
        return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{_escape(title)}</title><style>{style}</style></head><body><header><h1>MOSS Recognition Center</h1><p class='muted'>Version {VERSION} · Operator-verified learning examples · No motor control</p></header>{body}</body></html>"

    def _login(self, environ, start_response):
        if environ["REQUEST_METHOD"] == "POST":
            form = self._form_body(environ)
            ip = clean_text(environ.get("REMOTE_ADDR", "unknown"), 100)
            if not self._login_allowed(ip):
                raise RequestError(429, "login_throttled", "too many attempts, retry later")
            if hmac.compare_digest(form.get("password", ""), self.admin_password):
                with self._connect() as db:
                    db.execute("DELETE FROM login_attempts WHERE source_ip=?", (ip,))
                return self._redirect(start_response, "/admin", [self._cookie_header(self._new_session(), self.session_seconds)])
            self._note_login_failure(ip)
            content = "<div class='card'><p class='warn'>Incorrect password.</p></div>"
        else:
            content = ""
        content += "<div class='card'><form method='post' action='/admin/login'><label>Admin password <input type='password' name='password' required autocomplete='current-password'></label><button type='submit'>Sign in</button></form></div>"
        return self._html(start_response, 200, self._layout("Sign in", content))

    def _dashboard(self, environ, start_response):
        csrf = self._csrf(self._session_cookie(environ))
        with self._connect() as db:
            rows = db.execute("SELECT id,created_at,device_id,candidates_json FROM scans WHERE state='pending' ORDER BY created_at DESC LIMIT 50").fetchall()
            counts = {r["state"]: r["n"] for r in db.execute("SELECT state,COUNT(*) n FROM scans GROUP BY state")}
        content = (f"<p>Pending: <b>{counts.get('pending',0)}</b> · Verified: <b>{counts.get('verified',0)}</b> · Rejected: <b>{counts.get('rejected',0)}</b></p>"
                   f"<form method='post' action='/admin/logout'><input type='hidden' name='csrf_token' value='{csrf}'><button type='submit'>Sign out</button></form>"
                   "<div class='card'><h2>Pending review</h2><table><tr><th>Scan</th><th>Device</th><th>Received (UTC)</th><th>Suggested product</th></tr>")
        for row in rows:
            candidates = json.loads(row["candidates_json"])
            candidate = candidates[0] if candidates else {}
            content += (f"<tr><td><a href='/admin/review/{row['id']}'>{row['id'][:10]}</a></td>"
                        f"<td>{_escape(row['device_id'])}</td><td>{_escape(row['created_at'])}</td>"
                        f"<td>{_escape(candidate.get('name', '(none)'))}</td></tr>")
        content += "</table></div><p class='muted'>Only human-approved examples enter the matching gallery. Pending scans do not teach the recognizer.</p>"
        return self._html(start_response, 200, self._layout("Review queue", content))

    def _review_page(self, environ, start_response, sid):
        with self._connect() as db:
            row = db.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
        if not row:
            raise RequestError(404, "not_found", "scan does not exist")
        candidates = json.loads(row["candidates_json"])
        csrf = self._csrf(self._session_cookie(environ))
        options = "<option value=''>Manual correction (enter all identity fields below)</option>"
        for c in candidates:
            label = f"{c['name']} · {c['set']} · #{c['collector_number']} · ID {c['product_id']}"
            options += f"<option value='{_escape(c['product_id'])}'>{_escape(label)}</option>"
        body = (f"<p><a href='/admin'>← Review queue</a></p><div class='card'><h2>Scan {_escape(sid[:12])}</h2>"
                f"<p>Device {_escape(row['device_id'])} · {_escape(row['created_at'])} · State <b>{_escape(row['state'])}</b></p>"
                f"<div class='row'><div><img src='/admin/image/{sid}' alt='Captured physical card'></div><div>"
                "<p class='warn'>Verify the exact physical card, printing and collector number. Image similarity alone is not proof.</p>")
        if row["state"] == "pending":
            body += (f"<form action='/admin/review/{sid}' method='post'><input type='hidden' name='csrf_token' value='{csrf}'>"
                     f"<label>Choose known candidate <select name='candidate_id'>{options}</select></label>"
                     "<p class='small muted'>For a manual correction, leave the selection on Manual and fill every field. A selected candidate is confirmed using the supplied candidate identity, not replaced with arbitrary text.</p>"
                     "<label>Manual product ID <input name='product_id' maxlength='128'></label>"
                     "<label>Manual card name <input name='name' maxlength='200'></label>"
                     "<label>Manual set code <input name='set' maxlength='32'></label>"
                     "<label>Manual collector number (use N/A if unavailable) <input name='collector_number' maxlength='64'></label>"
                     "<label>Reviewer note <textarea name='note' maxlength='1000' rows='2'></textarea></label>"
                     "<label><input type='checkbox' name='physical_verified' value='yes'> I checked the exact physical printing and its identity.</label>"
                     "<button name='action' value='approve' type='submit'>Approve verified identity</button>"
                     "<button name='action' value='reject' type='submit'>Reject this sample</button></form>")
        else:
            body += f"<p>Review complete. Verified product: {_escape(row['verified_product_id'] or 'none')}.</p>"
        body += "</div></div></div>"
        return self._html(start_response, 200, self._layout("Review scan", body))

    def _review_submit(self, environ, start_response, sid):
        form = self._form_body(environ)
        if not self._csrf_valid(environ, form):
            raise RequestError(403, "csrf", "invalid review token")
        action = form.get("action", "")
        if action not in {"approve", "reject"}:
            raise RequestError(400, "action", "expected approve or reject")
        note = clean_text(form.get("note"), 1000)
        ip = clean_text(environ.get("REMOTE_ADDR", "unknown"), 100)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise RequestError(404, "not_found", "scan does not exist")
            if row["state"] != "pending":
                raise RequestError(409, "already_reviewed", "this scan has already been reviewed")
            pid = name = set_code = number = None
            if action == "approve":
                if form.get("physical_verified") != "yes":
                    raise RequestError(400, "verification_required", "confirm exact physical card identity")
                cid = clean_text(form.get("candidate_id"), 128)
                candidates = json.loads(row["candidates_json"])
                if cid:
                    match = next((c for c in candidates if c["product_id"] == cid), None)
                    if match is None:
                        raise RequestError(400, "candidate_not_found", "selected candidate is not in this scan")
                    pid, name, set_code, number = (match[k] for k in ("product_id", "name", "set", "collector_number"))
                else:
                    pid = clean_text(form.get("product_id"), 128)
                    name = clean_text(form.get("name"), 200)
                    set_code = clean_text(form.get("set"), 32)
                    number = clean_text(form.get("collector_number"), 64)
                if not ID_RE.fullmatch(pid or "") or not all((name, set_code, number)):
                    raise RequestError(400, "incomplete_identity", "exact product ID, name, set and collector number are required")
                db.execute("""UPDATE scans SET state='verified',verified_product_id=?,verified_name=?,
                    verified_set=?,verified_number=?,reviewed_at=?,review_note=? WHERE id=?""",
                    (pid, name, set_code, number, utc_now(), note, sid))
                db.execute("""INSERT INTO verified_examples(scan_id,product_id,name,set_code,collector_number,image_dhash,verified_at)
                    VALUES(?,?,?,?,?,?,?)""", (sid, pid, name, set_code, number, row["image_dhash"], utc_now()))
            else:
                db.execute("UPDATE scans SET state='rejected',reviewed_at=?,review_note=? WHERE id=?", (utc_now(), note, sid))
            db.execute("INSERT INTO review_audit(scan_id,action,product_id,recorded_at,source_ip,note) VALUES(?,?,?,?,?,?)",
                       (sid, action, pid, utc_now(), ip, note))
        return self._redirect(start_response, "/admin")

    def _image(self, start_response, sid):
        with self._connect() as db:
            row = db.execute("SELECT image_file FROM scans WHERE id=?", (sid,)).fetchone()
        if not row:
            raise RequestError(404, "not_found", "scan does not exist")
        filename = row["image_file"]
        if filename != sid + ".jpg":
            raise RequestError(500, "storage", "stored filename mismatch")
        try:
            payload = (self.image_dir / filename).read_bytes()
        except OSError:
            raise RequestError(404, "not_found", "image data is missing")
        return self._send(start_response, 200, payload, "image/jpeg")

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        try:
            if path == "/health" and method == "GET":
                return self._json(start_response, 200, {"status": "ok", "service": "moss-recognition", "version": VERSION})
            if path == "/" and method == "GET":
                return self._redirect(start_response, "/admin")
            if path == "/admin/login" and method in {"GET", "POST"}:
                return self._login(environ, start_response)
            if path.startswith("/api/v1/"):
                if not self._api_authorized(environ):
                    raise RequestError(401, "unauthorized", "valid X-MOSS-API-Key required")
                if path == "/api/v1/scans" and method == "POST":
                    return self._ingest(environ, start_response, True)
                if path == "/api/v1/match" and method == "POST":
                    return self._ingest(environ, start_response, False)
                if path == "/api/v1/metrics" and method == "GET":
                    return self._metrics(start_response)
                matched = re.fullmatch(r"/api/v1/scans/([a-f0-9]{32})", path)
                if matched and method == "GET":
                    return self._scan_status(matched.group(1), start_response)
                raise RequestError(404, "not_found", "API route does not exist")
            if path.startswith("/admin"):
                if not self._session_valid(self._session_cookie(environ)):
                    return self._redirect(start_response, "/admin/login")
                if path == "/admin" and method == "GET":
                    return self._dashboard(environ, start_response)
                if path == "/admin/logout" and method == "POST":
                    form = self._form_body(environ)
                    if not self._csrf_valid(environ, form):
                        raise RequestError(403, "csrf", "invalid logout token")
                    return self._redirect(start_response, "/admin/login", [self._cookie_header("", 0)])
                matched = re.fullmatch(r"/admin/review/([a-f0-9]{32})", path)
                if matched and method == "GET":
                    return self._review_page(environ, start_response, matched.group(1))
                if matched and method == "POST":
                    return self._review_submit(environ, start_response, matched.group(1))
                matched = re.fullmatch(r"/admin/image/([a-f0-9]{32})", path)
                if matched and method == "GET":
                    return self._image(start_response, matched.group(1))
            raise RequestError(404, "not_found", "route does not exist")
        except RequestError as exc:
            if path.startswith("/admin"):
                return self._html(start_response, exc.status,
                                  self._layout("Request error", f"<p class='warn'>{_escape(exc.message)}</p><p><a href='/admin'>Back</a></p>"))
            return self._json(start_response, exc.status, {"error": exc.code, "message": exc.message})
        except Exception:
            # Log server-side traceback without exposing secrets or filesystem paths to the client.
            import logging
            logging.exception("Unhandled MOSS Recognition Server request failure")
            return self._json(start_response, 500, {"error": "internal_error", "message": "Internal server error"})


def create_app(config=None):
    return RecognitionApp(config)


def main():
    from wsgiref.simple_server import make_server
    app = create_app()
    host = os.environ.get("MOSS_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("MOSS_PORT", "8080"))
    print(f"MOSS Recognition Center {VERSION} listening at http://{host}:{port}")
    with make_server(host, port, app) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
