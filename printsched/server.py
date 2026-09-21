"""Local web app: a small JSON API plus the static front end.

Binds to the loopback interface only. Because a page in your browser could
otherwise POST to localhost, every state-changing request must carry the
X-Printsched header and a same-origin Origin, which cross-site form posts
cannot forge without a preflight.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config, printing, store
from .schedule import DAILY, INTERVAL, ONCE, WEEKLY, Schedule, ScheduleError
from .scheduler import SchedulerThread, run_job, setup_logging

log = logging.getLogger("printsched")
WEB_DIR = Path(__file__).parent / "web"
GUARD_HEADER = "X-Printsched"

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def parse_multipart(body: bytes, content_type: str) -> dict[str, object]:
    """Pull fields and the uploaded file out of a multipart/form-data body.

    The stdlib cgi module that used to do this was removed in Python 3.13, so
    the body is handed to the email parser instead, which speaks the same MIME.
    """
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=email_policy).parsebytes(header + body)
    fields: dict[str, object] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            fields[name] = {"filename": filename, "data": payload}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields


def schedule_from_fields(fields: dict) -> Schedule:
    kind = str(fields.get("kind") or ONCE).strip()
    if kind == ONCE:
        raw = str(fields.get("run_at") or "").strip()
        if not raw:
            raise ScheduleError("pick a date and time")
        # <input type="datetime-local"> sends YYYY-MM-DDTHH:MM
        try:
            run_at = datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ScheduleError(f"{raw!r} is not a date and time") from None
        return Schedule(kind=ONCE, run_at=run_at)
    if kind == DAILY:
        return Schedule(kind=DAILY, time_of_day=str(fields.get("time_of_day") or "").strip())
    if kind == WEEKLY:
        raw = fields.get("weekdays") or ""
        days = tuple(int(d) for d in str(raw).split(",") if d.strip() != "")
        return Schedule(kind=WEEKLY, time_of_day=str(fields.get("time_of_day") or "").strip(), weekdays=days)
    if kind == INTERVAL:
        try:
            minutes = int(fields.get("interval_minutes") or 0)
        except (TypeError, ValueError):
            raise ScheduleError("interval must be a whole number of minutes") from None
        return Schedule(kind=INTERVAL, interval_minutes=minutes)
    raise ScheduleError(f"unknown schedule type {kind!r}")


def create_from_fields(conn: sqlite3.Connection, fields: dict) -> int:
    schedule = schedule_from_fields(fields)
    upload = fields.get("file")
    server_path = str(fields.get("source_path") or "").strip()
    mode = str(fields.get("source_mode") or store.SNAPSHOT).strip()

    if isinstance(upload, dict) and upload.get("data"):
        if len(upload["data"]) > config.MAX_UPLOAD_BYTES:
            raise ApiError(f"file is larger than the {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB limit", 413)
        path = store.save_upload(str(upload["filename"]), upload["data"])
        default_name = Path(str(upload["filename"])).stem
        mode = store.SNAPSHOT  # the original is the browser's, not ours to revisit
    elif server_path:
        source = Path(server_path).expanduser()
        if not source.exists():
            raise ApiError(f"no such file: {source}")
        if source.is_dir():
            raise ApiError(f"{source} is a folder, not a document")
        path = source if mode == store.LIVE else store.spool(source)
        default_name = source.stem
    else:
        raise ApiError("choose a file to print")

    name = str(fields.get("name") or "").strip() or default_name
    try:
        copies = max(1, int(fields.get("copies") or 1))
    except (TypeError, ValueError):
        raise ApiError("copies must be a whole number") from None

    return store.create_job(
        conn,
        name=name,
        source_path=path,
        schedule=schedule,
        source_mode=mode,
        printer=(str(fields.get("printer") or "").strip() or None),
        copies=copies,
        options=str(fields.get("options") or "").strip(),
    )


def job_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    data = store.row_to_dict(row)
    data["description"] = store.schedule_of(row).describe()
    data["file_name"] = Path(row["source_path"]).name
    data["file_missing"] = not Path(row["source_path"]).exists()
    return data


def state_payload(conn: sqlite3.Connection) -> dict:
    printers = printing.list_printers()
    return {
        "now": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "jobs": [job_payload(conn, row) for row in store.list_jobs(conn)],
        "printers": [{"name": p.name, "state": p.state, "is_default": p.is_default} for p in printers],
        "queue": printing.queue_status(),
        "runs": [store.row_to_dict(r) for r in store.recent_runs(conn, limit=25)],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "printsched"
    protocol_version = "HTTP/1.1"

    # --- plumbing -------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")

    def _guard(self) -> None:
        """Reject cross-site requests that try to drive the printer."""
        if self.headers.get(GUARD_HEADER) != "1":
            raise ApiError("missing request guard header", 403)
        origin = self.headers.get("Origin")
        if origin:
            host = urlparse(origin).netloc
            if host not in (f"{config.HOST}:{config.PORT}", f"localhost:{config.PORT}"):
                raise ApiError(f"cross-origin request from {origin} refused", 403)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > config.MAX_UPLOAD_BYTES + 1024 * 1024:
            raise ApiError("request too large", 413)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            return parse_multipart(raw, ctype)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise ApiError("body was not valid JSON") from None
        return {}

    # --- routes ---------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                conn = store.connect()
                try:
                    return self._json(state_payload(conn))
                finally:
                    conn.close()
            return self._static(path)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            log.exception("GET %s failed", path)
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        conn = None
        try:
            self._guard()
            conn = store.connect()
            if path == "/api/jobs":
                job_id = create_from_fields(conn, self._body())
                row = store.get_job(conn, job_id)
                return self._json({"job": job_payload(conn, row)}, 201)

            match = re.fullmatch(r"/api/jobs/(\d+)/(run|toggle)", path)
            if match:
                job_id, action = int(match.group(1)), match.group(2)
                row = store.get_job(conn, job_id)
                if row is None:
                    raise ApiError("no such job", 404)
                if action == "run":
                    status, message = run_job(conn, row, force=True)
                    if status == "error":
                        raise ApiError(message, 502)
                    return self._json({"status": status, "message": message})
                store.set_enabled(conn, job_id, not bool(row["enabled"]))
                return self._json({"job": job_payload(conn, store.get_job(conn, job_id))})

            raise ApiError("unknown endpoint", 404)
        except (ScheduleError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            log.exception("POST %s failed", path)
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            if conn:
                conn.close()

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        conn = None
        try:
            self._guard()
            match = re.fullmatch(r"/api/jobs/(\d+)", path)
            if not match:
                raise ApiError("unknown endpoint", 404)
            conn = store.connect()
            if not store.delete_job(conn, int(match.group(1))):
                raise ApiError("no such job", 404)
            return self._json({"deleted": True})
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            log.exception("DELETE %s failed", path)
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            if conn:
                conn.close()

    def _static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if not target.is_relative_to(WEB_DIR.resolve()) or not target.is_file():
            raise ApiError("not found", 404)
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


def serve(host: str | None = None, port: int | None = None, open_browser: bool = False) -> None:
    host = host or config.HOST
    port = port or config.PORT
    setup_logging(verbose=True)
    config.ensure_dirs()

    scheduler = SchedulerThread()
    scheduler.start()

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"Print Scheduler running at {url}")
    print(f"  data:  {config.DATA_DIR}")
    print(f"  log:   {config.LOG_PATH}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        scheduler.stop()
        httpd.server_close()
