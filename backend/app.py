from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import urllib.request
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("UNISYNC_DB", ROOT / "data" / "unysync.db"))
FRONTEND_DIST = ROOT / "frontend" / "dist"
DEFAULT_TIMEZONE = os.getenv("UNISYNC_TIMEZONE", "America/Denver")
SESSION_DAYS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def change_is_newer_or_equal(candidate: str, candidate_device: str, current: str, current_device: str) -> bool:
    def parsed(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    try:
        left = parsed(candidate)
        right = parsed(current)
        return (left, candidate_device) >= (right, current_device)
    except (ValueError, TypeError):
        return (candidate, candidate_device) >= (current, current_device)


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#4fc1e9',
                enabled INTEGER NOT NULL DEFAULT 1,
                filter_rules TEXT NOT NULL DEFAULT '{}',
                last_sync TEXT,
                last_success TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
                uid TEXT,
                kind TEXT NOT NULL DEFAULT 'imported',
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                start_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                all_day INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                completed INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                change_at TEXT NOT NULL,
                change_device TEXT NOT NULL DEFAULT 'server',
                UNIQUE(source_id, uid)
            );
            CREATE TABLE IF NOT EXISTS custom_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                start_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                all_day INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                completed INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                change_at TEXT NOT NULL,
                change_device TEXT NOT NULL DEFAULT 'server'
            );
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_operations (
                operation_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL
            );
            """
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return algorithm == "pbkdf2_sha256" and hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def user_exists() -> bool:
    with db() as connection:
        return connection.execute("SELECT 1 FROM users WHERE id = 1").fetchone() is not None


def current_user(session: str | None = Cookie(default=None, alias="unysync_session")) -> int:
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    with db() as connection:
        row = connection.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (session,)
        ).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return int(row["user_id"])


class Credentials(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2000)
    color: str = Field(default="#4fc1e9", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True
    filter_rules: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool | None = None
    filter_rules: dict[str, Any] | None = None


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    start_at: str
    due_at: str
    all_day: bool = False


class CompletionInput(BaseModel):
    completed: bool
    client_changed_at: str | None = None
    device_id: str = Field(default="browser", max_length=100)


class SyncOperation(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)
    entity: str
    entity_id: int
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    client_changed_at: str | None = None
    device_id: str = Field(default="browser", max_length=100)


class SyncRequest(BaseModel):
    operations: list[SyncOperation] = Field(default_factory=list, max_length=100)


def unescape_ical(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def unfold_ical(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    parts = left.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.upper()] = val.strip('"')
    return name, params, unescape_ical(value)


def parse_ical(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in unfold_ical(text):
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.upper() == "END:VEVENT":
            if current and current.get("uid") and current.get("summary") and current.get("dtstart"):
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        parsed = parse_property(line)
        if not parsed:
            continue
        name, params, value = parsed
        key = name.lower()
        if name == "UID":
            current["uid"] = value
        elif name == "SUMMARY":
            current["summary"] = value
        elif name == "DESCRIPTION":
            current["description"] = value
        elif name == "DTSTART":
            current["dtstart"] = (value, params)
        elif name == "DTEND":
            current["dtend"] = (value, params)
        elif name == "DTSTAMP":
            current["dtstamp"] = value
        elif name == "URL":
            current["url"] = value
        else:
            current[key] = value
    return events


def parse_ical_datetime(item: tuple[str, dict[str, str]], fallback_tz: str = DEFAULT_TIMEZONE) -> tuple[str, bool]:
    value, params = item
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        parsed_date = datetime.strptime(value[:8], "%Y%m%d").date()
        return parsed_date.isoformat(), True
    raw = value.rstrip("Z")
    parsed = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        zone = params.get("TZID", fallback_tz)
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        except Exception:
            parsed = parsed.replace(tzinfo=ZoneInfo(fallback_tz))
    return parsed.isoformat(), False


DEFAULT_INCLUDE = [r"^HW\s*#?\s*\d+\s+(Upload|Report)\b", r"^RQuiz\b", r"^Statics\s+X[123]\b"]
DEFAULT_EXCLUDE = [r"^No\s+Class", r"^Student\s+Ratings$", r"^Corrections$", r"^nothnig$"]


def classify_event(event: dict[str, Any], rules: dict[str, Any] | None = None) -> str:
    rules = rules or {}
    uid = event.get("uid", "")
    title = event.get("summary", "").strip()
    lowered = f"{title}\n{event.get('description', '')}".lower()
    overrides = rules.get("overrides", {})
    if uid in overrides:
        return "include" if overrides[uid] in (True, "include") else "exclude"
    include = DEFAULT_INCLUDE + [str(x) for x in rules.get("include", [])]
    exclude = DEFAULT_EXCLUDE + [str(x) for x in rules.get("exclude", [])]
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in exclude):
        return "exclude"
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in include):
        return "include"
    if any(word in lowered for word in ("lecture", "study help", "office hours", "help link")):
        return "exclude"
    return "review"


def normalized_event(event: dict[str, Any]) -> dict[str, Any]:
    start, start_all_day = parse_ical_datetime(event["dtstart"])
    if event.get("dtend"):
        due, due_all_day = parse_ical_datetime(event["dtend"])
    else:
        due, due_all_day = start, start_all_day
    return {
        "uid": event["uid"],
        "title": " ".join(event["summary"].split()),
        "description": event.get("description", "").strip(),
        "start_at": start,
        "due_at": due,
        "all_day": start_all_day or due_all_day,
    }


def source_json(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "enabled": bool(row["enabled"]), "filter_rules": json.loads(row["filter_rules"] or "{}"), "url": None}


def assignment_json(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["completed"] = bool(item["completed"])
    item["active"] = bool(item["active"])
    item["all_day"] = bool(item["all_day"])
    item["source_type"] = "custom" if item["kind"] == "custom" else "imported"
    item["source_name"] = "Custom Task" if item["kind"] == "custom" else item.get("source_name")
    item["source_color"] = "#d19a66" if item["kind"] == "custom" else item.get("source_color")
    return item


def fetch_ical(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Calendar URL must use http or https")
    request = urllib.request.Request(url, headers={"User-Agent": "UniSync/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(5_000_000).decode("utf-8-sig", errors="replace")


def apply_source_events(source_id: int, rules: dict[str, Any], events: list[dict[str, Any]]) -> int:
    included = [normalized_event(event) for event in events if classify_event(event, rules) == "include"]
    included_uids = {event["uid"] for event in included}
    changed_at = now_iso()
    with db() as connection:
        connection.execute("UPDATE assignments SET active = 0 WHERE source_id = ?", (source_id,))
        for event in included:
            old = connection.execute(
                "SELECT * FROM assignments WHERE source_id = ? AND uid = ?", (source_id, event["uid"])
            ).fetchone()
            if old:
                changed = old["title"] != event["title"] or old["due_at"] != event["due_at"]
                connection.execute(
                    """UPDATE assignments SET title=?, description=?, start_at=?, due_at=?, all_day=?, active=1,
                       completed=CASE WHEN ? THEN 0 ELSE completed END,
                       completed_at=CASE WHEN ? THEN NULL ELSE completed_at END WHERE id=?""",
                    (event["title"], event["description"], event["start_at"], event["due_at"], int(event["all_day"]), int(changed), int(changed), old["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO assignments(source_id, uid, kind, title, description, start_at, due_at, all_day, change_at)
                       VALUES (?, ?, 'imported', ?, ?, ?, ?, ?, ?)""",
                    (source_id, event["uid"], event["title"], event["description"], event["start_at"], event["due_at"], int(event["all_day"]), changed_at),
                )
        connection.execute(
            "UPDATE sources SET last_sync=?, last_success=?, last_error=NULL, updated_at=? WHERE id=?",
            (changed_at, changed_at, changed_at, source_id),
        )
    return len(included_uids)


def perform_sync(source_id: int) -> int:
    with db() as connection:
        source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise ValueError("Source not found")
    try:
        events = parse_ical(fetch_ical(source["url"]))
        return apply_source_events(source_id, json.loads(source["filter_rules"] or "{}"), events)
    except Exception as exc:
        with db() as connection:
            connection.execute("UPDATE sources SET last_sync=?, last_error=?, updated_at=? WHERE id=?", (now_iso(), str(exc), now_iso(), source_id))
        raise


async def daily_sync_loop() -> None:
    while True:
        try:
            with db() as connection:
                rows = connection.execute("SELECT id, last_success FROM sources WHERE enabled=1").fetchall()
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            for row in rows:
                if not row["last_success"] or datetime.fromisoformat(row["last_success"]) < cutoff:
                    await asyncio.to_thread(perform_sync, row["id"])
        except Exception:
            pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(daily_sync_loop())
    yield
    task.cancel()


app = FastAPI(title="UniSync", version="1.0.0", lifespan=lifespan)


@app.get("/api/status")
def status_info() -> dict[str, Any]:
    return {"setup_required": not user_exists(), "app": "UniSync", "version": "1.0.0"}


@app.post("/api/setup")
def setup(credentials: Credentials, response: Response) -> dict[str, bool]:
    if user_exists():
        raise HTTPException(status_code=409, detail="Setup has already been completed")
    with db() as connection:
        connection.execute("INSERT INTO users(id, password_hash, created_at) VALUES (1, ?, ?)", (hash_password(credentials.password), now_iso()))
    return login(credentials, response)


@app.post("/api/login")
def login(credentials: Credentials, response: Response) -> dict[str, bool]:
    with db() as connection:
        user = connection.execute("SELECT * FROM users WHERE id=1").fetchone()
        if not user or not verify_password(credentials.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid password")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        connection.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,?)", (token, 1, expires.isoformat()))
    response.set_cookie("unysync_session", token, httponly=True, samesite="lax", secure=os.getenv("UNISYNC_SECURE_COOKIE", "0") == "1", max_age=SESSION_DAYS * 86400)
    return {"authenticated": True}


@app.post("/api/logout")
def logout(response: Response, session: str | None = Cookie(default=None, alias="unysync_session")) -> dict[str, bool]:
    if session:
        with db() as connection:
            connection.execute("DELETE FROM sessions WHERE token=?", (session,))
    response.delete_cookie("unysync_session")
    return {"authenticated": False}


@app.get("/api/preferences")
def get_preferences(_: int = Depends(current_user)) -> dict[str, Any]:
    with db() as connection:
        rows = connection.execute("SELECT key, value FROM preferences").fetchall()
    values = {row["key"]: json.loads(row["value"]) for row in rows}
    values.setdefault("timezone", DEFAULT_TIMEZONE)
    values.setdefault("week_start", 1)
    values.setdefault("theme", "vscode-dark")
    return values


@app.put("/api/preferences")
def put_preferences(values: dict[str, Any], _: int = Depends(current_user)) -> dict[str, Any]:
    allowed = {"timezone", "week_start", "theme"}
    with db() as connection:
        for key, value in values.items():
            if key in allowed:
                connection.execute("INSERT INTO preferences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
    return get_preferences()


@app.get("/api/sources")
def list_sources(_: int = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as connection:
        return [source_json(row) for row in connection.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY name").fetchall()]


@app.post("/api/sources")
def create_source(payload: SourceInput, _: int = Depends(current_user)) -> dict[str, Any]:
    timestamp = now_iso()
    with db() as connection:
        cursor = connection.execute("INSERT INTO sources(name,url,color,enabled,filter_rules,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (payload.name.strip(), payload.url, payload.color, int(payload.enabled), json.dumps(payload.filter_rules), timestamp, timestamp))
        row = connection.execute("SELECT * FROM sources WHERE id=?", (cursor.lastrowid,)).fetchone()
    return source_json(row)


@app.patch("/api/sources/{source_id}")
def update_source(source_id: int, payload: SourceUpdate, _: int = Depends(current_user)) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    if "filter_rules" in values:
        values["filter_rules"] = json.dumps(values["filter_rules"])
    if "enabled" in values:
        values["enabled"] = int(values["enabled"])
    if not values:
        raise HTTPException(400, "No changes supplied")
    values["updated_at"] = now_iso()
    assignments_sql = ", ".join(f"{key}=?" for key in values)
    with db() as connection:
        cursor = connection.execute(f"UPDATE sources SET {assignments_sql} WHERE id=?", (*values.values(), source_id))
        row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if cursor.rowcount == 0 or not row:
        raise HTTPException(404, "Source not found")
    return source_json(row)


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int, _: int = Depends(current_user)) -> dict[str, bool]:
    with db() as connection:
        cursor = connection.execute("UPDATE sources SET enabled=0, updated_at=? WHERE id=?", (now_iso(), source_id))
        connection.execute("UPDATE assignments SET active=0 WHERE source_id=?", (source_id,))
    if cursor.rowcount == 0:
        raise HTTPException(404, "Source not found")
    return {"hidden": True}


def preview_payload(events: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"include": [], "review": [], "exclude": []}
    for event in events:
        category = classify_event(event, rules)
        normalized = normalized_event(event)
        normalized["category"] = category
        result[category].append(normalized)
    return result


@app.post("/api/sources/{source_id}/preview")
def preview_source(source_id: int, _: int = Depends(current_user)) -> dict[str, Any]:
    with db() as connection:
        source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        raise HTTPException(404, "Source not found")
    try:
        return preview_payload(parse_ical(fetch_ical(source["url"])), json.loads(source["filter_rules"] or "{}"))
    except Exception as exc:
        raise HTTPException(502, f"Could not read calendar: {exc}") from exc


@app.post("/api/sources/{source_id}/sync")
def sync_source(source_id: int, _: int = Depends(current_user)) -> dict[str, Any]:
    try:
        count = perform_sync(source_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Sync failed: {exc}") from exc
    return {"synced": count}


@app.get("/api/assignments")
def list_assignments(_: int = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("""SELECT a.*, s.name AS source_name, s.color AS source_color
            FROM assignments a LEFT JOIN sources s ON s.id=a.source_id
            WHERE a.active=1
            UNION ALL
            SELECT id, NULL, NULL, 'custom', title, description, start_at, due_at, all_day, active, completed, completed_at, change_at, change_device, 'Custom Task', '#d19a66'
            FROM custom_tasks WHERE active=1
            ORDER BY due_at, title""").fetchall()
    return [assignment_json(row) for row in rows]


def update_completion(table: str, item_id: int, payload: CompletionInput) -> dict[str, Any]:
    changed_at = payload.client_changed_at or now_iso()
    with db() as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Assignment not found")
        if change_is_newer_or_equal(changed_at, payload.device_id, row["change_at"], row["change_device"]):
            connection.execute(f"UPDATE {table} SET completed=?, completed_at=?, change_at=?, change_device=? WHERE id=?", (int(payload.completed), now_iso() if payload.completed else None, changed_at, payload.device_id, item_id))
        updated = connection.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    return assignment_json(updated)


@app.patch("/api/assignments/{assignment_id}/completion")
def assignment_completion(assignment_id: int, payload: CompletionInput, _: int = Depends(current_user)) -> dict[str, Any]:
    return update_completion("assignments", assignment_id, payload)


@app.post("/api/custom-tasks")
def create_task(payload: TaskInput, _: int = Depends(current_user)) -> dict[str, Any]:
    timestamp = now_iso()
    with db() as connection:
        cursor = connection.execute("INSERT INTO custom_tasks(title,description,start_at,due_at,all_day,change_at) VALUES(?,?,?,?,?,?)", (payload.title.strip(), payload.description, payload.start_at, payload.due_at, int(payload.all_day), timestamp))
        row = connection.execute("SELECT * FROM custom_tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
    return assignment_json(row)


@app.patch("/api/custom-tasks/{task_id}")
def update_task(task_id: int, payload: TaskInput, _: int = Depends(current_user)) -> dict[str, Any]:
    with db() as connection:
        cursor = connection.execute("UPDATE custom_tasks SET title=?,description=?,start_at=?,due_at=?,all_day=?,change_at=?,change_device='browser' WHERE id=?", (payload.title.strip(), payload.description, payload.start_at, payload.due_at, int(payload.all_day), now_iso(), task_id))
        row = connection.execute("SELECT * FROM custom_tasks WHERE id=?", (task_id,)).fetchone()
    if cursor.rowcount == 0 or not row:
        raise HTTPException(404, "Task not found")
    return assignment_json(row)


@app.delete("/api/custom-tasks/{task_id}")
def delete_task(task_id: int, _: int = Depends(current_user)) -> dict[str, bool]:
    with db() as connection:
        cursor = connection.execute("UPDATE custom_tasks SET active=0, change_at=? WHERE id=?", (now_iso(), task_id))
    if cursor.rowcount == 0:
        raise HTTPException(404, "Task not found")
    return {"deleted": True}


@app.patch("/api/custom-tasks/{task_id}/completion")
def task_completion(task_id: int, payload: CompletionInput, _: int = Depends(current_user)) -> dict[str, Any]:
    return update_completion("custom_tasks", task_id, payload)


@app.post("/api/sync")
def sync_operations(payload: SyncRequest, _: int = Depends(current_user)) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for operation in payload.operations:
        with db() as connection:
            exists = connection.execute("SELECT 1 FROM sync_operations WHERE operation_id=?", (operation.operation_id,)).fetchone()
            if exists:
                results.append({"operation_id": operation.operation_id, "duplicate": True})
                continue
            connection.execute("INSERT INTO sync_operations(operation_id,received_at) VALUES(?,?)", (operation.operation_id, now_iso()))
        if operation.entity in ("assignment", "custom_task") and operation.action == "complete":
            table = "assignments" if operation.entity == "assignment" else "custom_tasks"
            update_completion(table, operation.entity_id, CompletionInput(completed=bool(operation.payload.get("completed")), client_changed_at=operation.client_changed_at, device_id=operation.device_id))
            results.append({"operation_id": operation.operation_id, "applied": True})
    return {"results": results}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        requested = FRONTEND_DIST / path
        if path and requested.is_file() and FRONTEND_DIST in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
