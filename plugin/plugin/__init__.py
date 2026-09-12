import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel


class EventCreate(BaseModel):
    title: str
    date: str
    time: Optional[str] = None
    note: Optional[str] = None


class EventUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    note: Optional[str] = None


def _row_to_dict(r):
    return {
        "id": r["id"],
        "title": r["title"],
        "date": r["event_date"],
        "time": r["event_time"],
        "note": r["note"],
        "created_at": r["created_at"],
        "source": r["source"],
    }


def _create_event(conn, vault_name, event_id, title, event_date, event_time, note, created_at, source="manual"):
    conn.execute(
        "INSERT INTO calendar_events (id, vault_name, title, event_date, event_time, note, created_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, vault_name, title, event_date, event_time, note, created_at, source),
    )
    conn.commit()


def _list_events(conn, vault_name, from_date=None, to_date=None):
    sql = "SELECT * FROM calendar_events WHERE vault_name = ?"
    params = [vault_name]
    if from_date:
        sql += " AND event_date >= ?"
        params.append(from_date)
    if to_date:
        sql += " AND event_date <= ?"
        params.append(to_date)
    sql += " ORDER BY event_date, event_time"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _find_events(conn, vault_name, query):
    rows = conn.execute(
        "SELECT * FROM calendar_events WHERE vault_name = ? AND title LIKE ? ORDER BY event_date",
        (vault_name, f"%{query}%"),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _update_event(conn, vault_name, event_id, title=None, date=None, time=None, note=None):
    fields = []
    params = []
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if date is not None:
        fields.append("event_date = ?")
        params.append(date)
    if time is not None:
        fields.append("event_time = ?")
        params.append(time)
    if note is not None:
        fields.append("note = ?")
        params.append(note)
    if not fields:
        return False
    sql = f"UPDATE calendar_events SET {', '.join(fields)} WHERE id = ? AND vault_name = ?"
    params.extend([event_id, vault_name])
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount > 0


def _delete_event(conn, vault_name, event_id):
    cur = conn.execute(
        "DELETE FROM calendar_events WHERE id = ? AND vault_name = ?",
        (event_id, vault_name),
    )
    conn.commit()
    return cur.rowcount > 0


class Plugin:
    def on_load(self, ctx):
        self.db = ctx.db_module
        self.vault_manager = ctx.vault_manager
        self.store = ctx.store
        self.plugin_name = ctx.plugin_name
        self._ctx = ctx

        ctx.register_migration(1, """
            CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                vault_name TEXT NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                source TEXT DEFAULT 'manual'
            );
            CREATE INDEX IF NOT EXISTS idx_cal_date ON calendar_events(vault_name, event_date);
        """)

        self._migrate_from_store()

        ctx.register_tool(
            "create_event",
            {
                "type": "function",
                "function": {
                    "description": "Create a calendar event with a title and date (YYYY-MM-DD). Optional time (HH:MM) and note.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "date": {"type": "string"},
                            "time": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["title", "date"],
                    },
                },
            },
            self.create_event,
        )
        ctx.register_tool(
            "list_events",
            {
                "type": "function",
                "function": {
                    "description": "List calendar events for a date range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "from_date": {"type": "string"},
                            "to_date": {"type": "string"},
                        },
                        "required": [],
                    },
                },
            },
            self.list_events,
        )
        ctx.register_tool(
            "find_event",
            {
                "type": "function",
                "function": {
                    "description": "Search calendar events by title keyword.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            },
            self.find_event,
        )
        ctx.register_tool(
            "update_event",
            {
                "type": "function",
                "function": {
                    "description": "Update a calendar event by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "date": {"type": "string"},
                            "time": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
            },
            self.update_event,
        )
        ctx.register_tool(
            "delete_event",
            {
                "type": "function",
                "function": {
                    "description": "Delete a calendar event by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
            },
            self.delete_event,
        )

        dbm = self.db

        router = APIRouter()

        @router.get("/events")
        async def list_events(
            from_date: Optional[str] = Query(default=None, alias="from"),
            to_date: Optional[str] = Query(default=None, alias="to"),
        ):
            events = await asyncio.to_thread(
                _list_events, dbm.get_db(), self._ctx.vault_name, from_date, to_date
            )
            return {"events": events}

        @router.post("/events")
        async def create_event(body: EventCreate):
            event_id = str(uuid.uuid4())
            created_at = self.db.now_utc()
            await asyncio.to_thread(
                _create_event,
                dbm.get_db(),
                self._ctx.vault_name,
                event_id,
                body.title,
                body.date,
                body.time,
                body.note,
                created_at,
            )
            return {
                "id": event_id,
                "title": body.title,
                "date": body.date,
                "time": body.time,
                "note": body.note,
            }

        @router.put("/events/{event_id}")
        async def update_event(event_id: str, body: EventUpdate):
            ok = await asyncio.to_thread(
                _update_event,
                dbm.get_db(),
                self._ctx.vault_name,
                event_id,
                body.title,
                body.date,
                body.time,
                body.note,
            )
            if not ok:
                raise HTTPException(404, "Event not found")
            return {"detail": "Event updated"}

        @router.delete("/events/{event_id}")
        async def delete_event(event_id: str):
            ok = await asyncio.to_thread(
                _delete_event, dbm.get_db(), self._ctx.vault_name, event_id
            )
            if not ok:
                raise HTTPException(404, "Event not found")
            return {"detail": "Event deleted"}

        class CalendarApi:
            def __init__(self, db_module):
                self._db = db_module

            def get_upcoming_events(self, vault_name, days=7):
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                end = now + timedelta(days=days)
                conn = self._db.get_db()
                rows = conn.execute(
                    "SELECT title, event_date, event_time, note FROM calendar_events "
                    "WHERE vault_name = ? AND event_date >= ? AND event_date <= ? ORDER BY event_date",
                    (vault_name, now.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
                ).fetchall()
                return [{"title": r["title"], "date": r["event_date"],
                         "time": r["event_time"], "note": r["note"]} for r in rows]

        ctx.register_api("calendar", CalendarApi(dbm))
        ctx.register_router(router)
        ctx.add_prompt_fragment(
            "chat",
            "You have access to calendar tools (calendar.create_event, calendar.list_events, "
            "calendar.find_event, calendar.update_event, calendar.delete_event). "
            "Use them to help the user plan and remember events. "
            "When the user mentions dates or planning, check upcoming events with calendar.list_events.",
        )

        # --- Context providers (Phase 6) ---
        ctx.register_context_provider("chat", self._events_context_provider)

    def _events_context_provider(self, user_text, thread_id, vault_name):
        """Return upcoming events section for chat context."""
        from datetime import datetime, timezone
        try:
            rows = self.db.get_db().execute(
                "SELECT title, event_date, event_time FROM calendar_events "
                "WHERE vault_name = ? AND event_date >= ? ORDER BY event_date LIMIT 5",
                (vault_name, datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            ).fetchall()
            if rows:
                events_text = "\n".join(
                    f"- {r['event_date']}: {r['title']}" + (f" at {r['event_time']}" if r["event_time"] else "")
                    for r in rows
                )
                return f"## Upcoming events\n\n{events_text}"
        except Exception:
            pass
        return None

    def _migrate_from_store(self):
        try:
            keys = self.store.list()
            if not keys:
                return
            vn = self._ctx.vault_name
            conn = self.db.get_db()
            for key in keys:
                val = self.store.get(key)
                if val is None:
                    continue
                parts = key.split(":", 1)
                if len(parts) == 2 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[0][7] == "-":
                    event_id = str(uuid.uuid4())
                    _create_event(
                        conn,
                        vn,
                        event_id,
                        val.get("title", parts[1]),
                        val.get("date", parts[0]),
                        None,
                        val.get("note", ""),
                        self.db.now_utc(),
                        source="migrated",
                    )
                    self.store.delete(key)
        except Exception:
            pass

    def create_event(self, args: dict) -> str:
        title = args.get("title", "").strip()
        date = args.get("date", "").strip()
        if not title or not date:
            return "Error: title and date are required."
        time_val = args.get("time")
        note = args.get("note", "")
        event_id = str(uuid.uuid4())
        _create_event(
            self.db.get_db(),
            self._ctx.vault_name,
            event_id,
            title,
            date,
            time_val,
            note,
            self.db.now_utc(),
        )
        suffix = f" at {time_val}" if time_val else ""
        return f"Event saved: {title} on {date}{suffix}"

    def list_events(self, args: dict) -> str:
        events = _list_events(
            self.db.get_db(),
            self._ctx.vault_name,
            args.get("from_date"),
            args.get("to_date"),
        )
        if not events:
            return "No events."
        lines = []
        for e in events:
            line = f"- {e['date']}"
            if e.get("time"):
                line += f" {e['time']}"
            line += f": {e['title']}"
            lines.append(line)
        return "\n".join(lines)

    def find_event(self, args: dict) -> str:
        query = args.get("query", "")
        events = _find_events(self.db.get_db(), self._ctx.vault_name, query)
        if not events:
            return "No matches."
        lines = []
        for e in events:
            line = f"- {e['date']}"
            if e.get("time"):
                line += f" {e['time']}"
            line += f": {e['title']}"
            lines.append(line)
        return "\n".join(lines)

    def update_event(self, args: dict) -> str:
        event_id = args.get("id", "").strip()
        if not event_id:
            return "Error: id is required."
        ok = _update_event(
            self.db.get_db(),
            self._ctx.vault_name,
            event_id,
            args.get("title"),
            args.get("date"),
            args.get("time"),
            args.get("note"),
        )
        if not ok:
            return "Error: event not found."
        return f"Event updated: {event_id}"

    def delete_event(self, args: dict) -> str:
        event_id = args.get("id", "").strip()
        if not event_id:
            return "Error: id is required."
        ok = _delete_event(self.db.get_db(), self._ctx.vault_name, event_id)
        if not ok:
            return "Error: event not found."
        return f"Event deleted: {event_id}"
