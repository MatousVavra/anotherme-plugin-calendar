import asyncio
import json
import logging
import uuid
from datetime import date as _date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_TIME_PATTERN = r"^\d{2}:\d{2}$"

_RECURRENCE_VALUES = ("weekly", "monthly", "yearly")
_PALETTE = (
    "tomato", "flamingo", "banana", "sage", "basil",
    "peacock", "blueberry", "lavender", "graphite",
)
_V2_COLUMNS = ("recurrence", "end_date", "color", "exceptions")
_UNSET = object()


class EventCreate(BaseModel):
    title: str
    date: str = Field(pattern=_DATE_PATTERN)
    time: Optional[str] = Field(default=None, pattern=_TIME_PATTERN)
    note: Optional[str] = None
    recurrence: Optional[str] = None
    end_date: Optional[str] = Field(default=None, pattern=_DATE_PATTERN)
    color: Optional[str] = None

    @field_validator("recurrence")
    @classmethod
    def _check_recurrence(cls, v):
        if v is not None and v not in _RECURRENCE_VALUES:
            raise ValueError("recurrence must be one of: weekly, monthly, yearly")
        return v

    @field_validator("color")
    @classmethod
    def _check_color(cls, v):
        if v is not None and v not in _PALETTE:
            raise ValueError(f"color must be one of: {', '.join(_PALETTE)}")
        return v

    @model_validator(mode="after")
    def _check_end_date(self):
        if self.end_date is not None and self.end_date < self.date:
            raise ValueError("end_date must be on or after date")
        return self


class EventUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = Field(default=None, pattern=_DATE_PATTERN)
    time: Optional[str] = Field(default=None, pattern=_TIME_PATTERN)
    note: Optional[str] = None
    recurrence: Optional[str] = None
    end_date: Optional[str] = Field(default=None, pattern=_DATE_PATTERN)
    color: Optional[str] = None

    @field_validator("recurrence")
    @classmethod
    def _check_recurrence(cls, v):
        if v is not None and v not in _RECURRENCE_VALUES:
            raise ValueError("recurrence must be one of: weekly, monthly, yearly")
        return v

    @field_validator("color")
    @classmethod
    def _check_color(cls, v):
        if v is not None and v not in _PALETTE:
            raise ValueError(f"color must be one of: {', '.join(_PALETTE)}")
        return v

    @model_validator(mode="after")
    def _check_end_date(self):
        if self.end_date is not None and self.date is not None and self.end_date < self.date:
            raise ValueError("end_date must be on or after date")
        return self


def _validate_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"Invalid date format: {value!r}, expected YYYY-MM-DD")
    return value


def _parse_exceptions(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def _row_to_dict(r):
    d = dict(r)
    return {
        "id": d.get("id"),
        "title": d.get("title"),
        "date": d.get("event_date"),
        "time": d.get("event_time"),
        "note": d.get("note"),
        "created_at": d.get("created_at"),
        "source": d.get("source"),
        "recurrence": d.get("recurrence"),
        "end_date": d.get("end_date"),
        "color": d.get("color"),
        "exceptions": _parse_exceptions(d.get("exceptions")),
        "is_occurrence": False,
        "series_date": None,
    }


def _iter_series_dates(anchor, recurrence):
    if recurrence == "weekly":
        current = anchor
        while True:
            yield current
            current = current + timedelta(weeks=1)
    if recurrence == "monthly":
        year, month = anchor.year, anchor.month
        while True:
            try:
                yield _date(year, month, anchor.day)
            except ValueError:
                pass
            month += 1
            if month > 12:
                month = 1
                year += 1
    if recurrence == "yearly":
        year = anchor.year
        while True:
            try:
                yield _date(year, anchor.month, anchor.day)
            except ValueError:
                pass
            year += 1


def _expand_occurrences(event, from_date, to_date, cap=366):
    anchor = _date.fromisoformat(event["date"])
    from_d = _date.fromisoformat(from_date) if from_date else None
    to_d = _date.fromisoformat(to_date) if to_date else None
    end_d = _date.fromisoformat(event["end_date"]) if event.get("end_date") else None
    recurrence = event.get("recurrence")
    if recurrence not in _RECURRENCE_VALUES:
        recurrence = None
    exceptions = set(event.get("exceptions") or [])

    def occurrence(d, is_occurrence, series_date):
        out = dict(event)
        out["date"] = d.isoformat()
        out["is_occurrence"] = is_occurrence
        out["series_date"] = series_date
        return out

    if not recurrence:
        if end_d is None or end_d <= anchor:
            if (from_d is None or anchor >= from_d) and (to_d is None or anchor <= to_d):
                return [occurrence(anchor, False, None)]
            return []
        first = max(anchor, from_d) if from_d else anchor
        last = min(end_d, to_d) if to_d else end_d
        if first > last:
            return []
        return [
            occurrence(first + timedelta(days=i), True, anchor.isoformat())
            for i in range((last - first).days + 1)
        ]

    first = max(anchor, from_d) if from_d else anchor
    last = to_d
    if end_d is not None:
        last = min(end_d, to_d) if to_d else end_d
    dates = []
    for d in _iter_series_dates(anchor, recurrence):
        if last is not None and d > last:
            break
        if d >= first and d.isoformat() not in exceptions:
            dates.append(d)
            if len(dates) >= cap:
                break
    return [occurrence(d, True, anchor.isoformat()) for d in dates]


def _missing_v2_columns(conn):
    try:
        rows = conn.execute("PRAGMA table_info(calendar_events)").fetchall()
    except Exception:
        return list(_V2_COLUMNS)
    if not rows:
        return list(_V2_COLUMNS)
    present = {r[1] for r in rows}
    return [c for c in _V2_COLUMNS if c not in present]


def _create_event(dbm, vault_name, event_id, title, event_date, event_time, note, created_at,
                  source="manual", recurrence=None, end_date=None, color=None):
    conn = dbm.get_db()
    conn.execute(
        "INSERT INTO calendar_events (id, vault_name, title, event_date, event_time, note, created_at, source, "
        "recurrence, end_date, color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, vault_name, title, event_date, event_time, note, created_at, source,
         recurrence, end_date, color),
    )
    conn.commit()


def _list_events(dbm, vault_name, from_date=None, to_date=None):
    conn = dbm.get_db()
    sql = "SELECT * FROM calendar_events WHERE vault_name = ?"
    params = [vault_name]
    if to_date:
        sql += " AND event_date <= ?"
        params.append(to_date)
    if from_date:
        sql += " AND (end_date IS NULL OR end_date >= ?)"
        params.append(from_date)
    rows = conn.execute(sql, params).fetchall()
    events = []
    for r in rows:
        events.extend(_expand_occurrences(_row_to_dict(r), from_date, to_date))
    events.sort(key=lambda e: (e["date"], e.get("time") or ""))
    return events


def _find_events(dbm, vault_name, query):
    conn = dbm.get_db()
    rows = conn.execute(
        "SELECT * FROM calendar_events WHERE vault_name = ? AND title LIKE ? ORDER BY event_date",
        (vault_name, f"%{query}%"),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _update_event(dbm, vault_name, event_id, title=None, date=None, time=None, note=None,
                  recurrence=_UNSET, end_date=_UNSET, color=_UNSET):
    conn = dbm.get_db()
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
    if recurrence is not _UNSET:
        fields.append("recurrence = ?")
        params.append(recurrence)
    if end_date is not _UNSET:
        fields.append("end_date = ?")
        params.append(end_date)
    if color is not _UNSET:
        fields.append("color = ?")
        params.append(color)
    if not fields:
        return False
    sql = f"UPDATE calendar_events SET {', '.join(fields)} WHERE id = ? AND vault_name = ?"
    params.extend([event_id, vault_name])
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount > 0


def _delete_event(dbm, vault_name, event_id):
    conn = dbm.get_db()
    cur = conn.execute(
        "DELETE FROM calendar_events WHERE id = ? AND vault_name = ?",
        (event_id, vault_name),
    )
    conn.commit()
    return cur.rowcount > 0


def _delete_occurrence(dbm, vault_name, event_id, date):
    conn = dbm.get_db()
    row = conn.execute(
        "SELECT recurrence, exceptions FROM calendar_events WHERE id = ? AND vault_name = ?",
        (event_id, vault_name),
    ).fetchone()
    if row is None:
        return "missing"
    if not row["recurrence"]:
        return "not_recurring"
    exceptions = _parse_exceptions(row["exceptions"]) or []
    if date not in exceptions:
        exceptions.append(date)
        conn.execute(
            "UPDATE calendar_events SET exceptions = ? WHERE id = ? AND vault_name = ?",
            (json.dumps(exceptions), event_id, vault_name),
        )
        conn.commit()
    return "deleted"


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

        try:
            missing_v2 = _missing_v2_columns(self.db.get_db())
        except Exception:
            missing_v2 = list(_V2_COLUMNS)
        if missing_v2:
            ctx.register_migration(2, "\n".join(
                f"ALTER TABLE calendar_events ADD COLUMN {column} TEXT;"
                for column in missing_v2
            ))

        self._migrate_from_store()

        ctx.register_tool(
            "create_event",
            {
                "type": "function",
                "function": {
                    "description": "Create a calendar event with a title and date (YYYY-MM-DD). "
                    "Optional time (HH:MM), note, recurrence (weekly/monthly/yearly), "
                    "end_date (YYYY-MM-DD, multi-day span end or recurring series end), "
                    "and color (palette key).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "date": {"type": "string"},
                            "time": {"type": "string"},
                            "note": {"type": "string"},
                            "recurrence": {"type": "string", "enum": list(_RECURRENCE_VALUES)},
                            "end_date": {"type": "string"},
                            "color": {"type": "string", "enum": list(_PALETTE)},
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
                            "recurrence": {"type": "string", "enum": list(_RECURRENCE_VALUES)},
                            "end_date": {"type": "string"},
                            "color": {"type": "string", "enum": list(_PALETTE)},
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
                    "description": "Delete a calendar event by id. Pass date (YYYY-MM-DD) to delete "
                    "only the occurrence on that date — recurring events only.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "date": {"type": "string"},
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
            from_date = _validate_date(from_date)
            to_date = _validate_date(to_date)
            events = await asyncio.to_thread(
                _list_events, dbm, self._ctx.vault_name, from_date, to_date
            )
            return {"events": events}

        @router.post("/events")
        async def create_event(body: EventCreate):
            event_id = str(uuid.uuid4())
            created_at = self.db.now_utc()
            await asyncio.to_thread(
                _create_event,
                dbm,
                self._ctx.vault_name,
                event_id,
                body.title,
                body.date,
                body.time,
                body.note,
                created_at,
                recurrence=body.recurrence,
                end_date=body.end_date,
                color=body.color,
            )
            return {
                "id": event_id,
                "title": body.title,
                "date": body.date,
                "time": body.time,
                "note": body.note,
                "created_at": created_at,
                "source": "manual",
                "recurrence": body.recurrence,
                "end_date": body.end_date,
                "color": body.color,
                "exceptions": None,
                "is_occurrence": False,
                "series_date": None,
            }

        @router.put("/events/{event_id}")
        async def update_event(event_id: str, body: EventUpdate):
            provided = body.model_fields_set
            ok = await asyncio.to_thread(
                _update_event,
                dbm,
                self._ctx.vault_name,
                event_id,
                body.title,
                body.date,
                body.time,
                body.note,
                recurrence=body.recurrence if "recurrence" in provided else _UNSET,
                end_date=body.end_date if "end_date" in provided else _UNSET,
                color=body.color if "color" in provided else _UNSET,
            )
            if not ok:
                raise HTTPException(404, "Event not found")
            return {"detail": "Event updated"}

        @router.delete("/events/{event_id}")
        async def delete_event(event_id: str, date: Optional[str] = Query(default=None)):
            date = _validate_date(date)
            if date is None:
                ok = await asyncio.to_thread(
                    _delete_event, dbm, self._ctx.vault_name, event_id
                )
                if not ok:
                    raise HTTPException(404, "Event not found")
                return {"detail": "Event deleted"}
            result = await asyncio.to_thread(
                _delete_occurrence, dbm, self._ctx.vault_name, event_id, date
            )
            if result == "missing":
                raise HTTPException(404, "Event not found")
            if result == "not_recurring":
                raise HTTPException(
                    400, "Event is not recurring; omit date to delete it entirely"
                )
            return {"detail": "Occurrence deleted"}

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
            logging.getLogger(__name__).exception("Failed to build calendar events context")
        return None

    def _migrate_from_store(self):
        try:
            keys = self.store.list()
            if not keys:
                return
            vn = self._ctx.vault_name
            for key in keys:
                val = self.store.get(key)
                if val is None:
                    continue
                parts = key.split(":", 1)
                if len(parts) == 2 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[0][7] == "-":
                    event_id = str(uuid.uuid4())
                    _create_event(
                        self.db,
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
            logging.getLogger(__name__).exception("Calendar store migration failed")

    def create_event(self, args: dict) -> str:
        title = args.get("title", "").strip()
        date = args.get("date", "").strip()
        if not title or not date:
            return "Error: title and date are required."
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return "Error: invalid date format, expected YYYY-MM-DD."
        time_val = args.get("time")
        note = args.get("note", "")
        recurrence = args.get("recurrence")
        if recurrence is not None and recurrence not in _RECURRENCE_VALUES:
            return "Error: recurrence must be weekly, monthly, or yearly."
        end_date = args.get("end_date")
        if end_date:
            try:
                datetime.strptime(end_date, "%Y-%m-%d")
            except ValueError:
                return "Error: invalid end_date format, expected YYYY-MM-DD."
            if end_date < date:
                return "Error: end_date must be on or after date."
        color = args.get("color")
        if color is not None and color not in _PALETTE:
            return "Error: color must be one of: " + ", ".join(_PALETTE) + "."
        event_id = str(uuid.uuid4())
        _create_event(
            self.db,
            self._ctx.vault_name,
            event_id,
            title,
            date,
            time_val,
            note,
            self.db.now_utc(),
            recurrence=recurrence,
            end_date=end_date,
            color=color,
        )
        suffix = f" at {time_val}" if time_val else ""
        if recurrence:
            suffix += f" (repeats {recurrence}"
            if end_date:
                suffix += f" until {end_date}"
            suffix += ")"
        elif end_date:
            suffix += f" until {end_date}"
        return f"Event saved: {title} on {date}{suffix}"

    def list_events(self, args: dict) -> str:
        events = _list_events(
            self.db,
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
            if e.get("recurrence"):
                line += f" (repeats {e['recurrence']})"
            lines.append(line)
        return "\n".join(lines)

    def find_event(self, args: dict) -> str:
        query = args.get("query", "")
        events = _find_events(self.db, self._ctx.vault_name, query)
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
        kwargs = {}
        if "recurrence" in args:
            value = args["recurrence"]
            if value is not None and value not in _RECURRENCE_VALUES:
                return "Error: recurrence must be weekly, monthly, or yearly."
            kwargs["recurrence"] = value
        if "end_date" in args:
            value = args["end_date"]
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return "Error: invalid end_date format, expected YYYY-MM-DD."
            kwargs["end_date"] = value
        if "color" in args:
            value = args["color"]
            if value is not None and value not in _PALETTE:
                return "Error: color must be one of: " + ", ".join(_PALETTE) + "."
            kwargs["color"] = value
        ok = _update_event(
            self.db,
            self._ctx.vault_name,
            event_id,
            args.get("title"),
            args.get("date"),
            args.get("time"),
            args.get("note"),
            **kwargs,
        )
        if not ok:
            return "Error: event not found."
        return f"Event updated: {event_id}"

    def delete_event(self, args: dict) -> str:
        event_id = args.get("id", "").strip()
        if not event_id:
            return "Error: id is required."
        date = args.get("date")
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return "Error: invalid date format, expected YYYY-MM-DD."
            result = _delete_occurrence(self.db, self._ctx.vault_name, event_id, date)
            if result == "missing":
                return "Error: event not found."
            if result == "not_recurring":
                return "Error: event is not recurring; omit date to delete the whole event."
            return f"Occurrence deleted: {event_id} on {date}"
        ok = _delete_event(self.db, self._ctx.vault_name, event_id)
        if not ok:
            return "Error: event not found."
        return f"Event deleted: {event_id}"
