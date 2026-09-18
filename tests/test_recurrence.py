"""Unit tests for recurrence expansion, v2 fields, occurrence delete, migration guard."""
import sqlite3
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import load_plugin_module
from fake_plugin_context import FakePluginContext

PLUGIN_MODULE = load_plugin_module()

_V1_SCHEMA = """
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
"""

_V2_SCHEMA = _V1_SCHEMA + """
ALTER TABLE calendar_events ADD COLUMN recurrence TEXT;
ALTER TABLE calendar_events ADD COLUMN end_date TEXT;
ALTER TABLE calendar_events ADD COLUMN color TEXT;
ALTER TABLE calendar_events ADD COLUMN exceptions TEXT;
"""


class FakeDbm:
    def __init__(self, schema=_V2_SCHEMA):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(schema)
        self.conn.commit()

    def get_db(self):
        return self.conn

    def now_utc(self):
        return "2026-09-17T00:00:00+00:00"


class RecordingContext(FakePluginContext):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.migrations = []

    def register_migration(self, version, sql):
        super().register_migration(version, sql)
        self.migrations.append((version, sql))


def _make_plugin_and_client(dbm=None):
    ctx = FakePluginContext(db_module=dbm or MagicMock())
    plugin = PLUGIN_MODULE.Plugin()
    plugin.on_load(ctx)
    app = FastAPI()
    for name, router in ctx.registry.routers:
        app.include_router(router, prefix=f"/plugins/{name}")
    return plugin, TestClient(app)


def _ev(**overrides):
    event = {
        "id": "e1",
        "title": "T",
        "date": "2026-01-05",
        "time": None,
        "note": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "source": "manual",
        "recurrence": None,
        "end_date": None,
        "color": None,
        "exceptions": None,
        "is_occurrence": False,
        "series_date": None,
    }
    event.update(overrides)
    return event


# --- _expand_occurrences: pure function ---


def test_weekly_across_month_boundary():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-26", recurrence="weekly"), "2026-02-01", "2026-02-28"
    )
    assert [e["date"] for e in out] == ["2026-02-02", "2026-02-09", "2026-02-16", "2026-02-23"]
    assert all(e["is_occurrence"] for e in out)
    assert all(e["series_date"] == "2026-01-26" for e in out)
    assert all(e["id"] == "e1" for e in out)


def test_monthly_skips_months_without_day_31():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-31", recurrence="monthly"), "2026-01-01", "2026-07-31"
    )
    assert [e["date"] for e in out] == ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"]


def test_yearly_feb_29_only_in_leap_years():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2024-02-29", recurrence="yearly"), "2024-01-01", "2030-12-31"
    )
    assert [e["date"] for e in out] == ["2024-02-29", "2028-02-29"]


def test_exceptions_skip_dates():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-05", recurrence="weekly", exceptions=["2026-01-19"]),
        "2026-01-01",
        "2026-02-01",
    )
    assert [e["date"] for e in out] == ["2026-01-05", "2026-01-12", "2026-01-26"]


def test_end_date_stops_series():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-06", recurrence="weekly", end_date="2026-02-10"),
        "2026-01-01",
        "2026-03-01",
    )
    assert [e["date"] for e in out] == ["2026-01-06", "2026-01-13", "2026-01-20", "2026-01-27", "2026-02-03", "2026-02-10"]


def test_multi_day_spanning_range_edges():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-30", end_date="2026-02-03"), "2026-02-01", "2026-02-28"
    )
    assert [e["date"] for e in out] == ["2026-02-01", "2026-02-02", "2026-02-03"]
    assert all(e["is_occurrence"] for e in out)
    assert all(e["series_date"] == "2026-01-30" for e in out)

    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-30", end_date="2026-02-03"), "2026-01-01", "2026-01-31"
    )
    assert [e["date"] for e in out] == ["2026-01-30", "2026-01-31"]


def test_single_day_in_and_out_of_range():
    ev = _ev(date="2026-01-05")
    out = PLUGIN_MODULE._expand_occurrences(ev, "2026-01-01", "2026-01-31")
    assert len(out) == 1
    assert out[0]["is_occurrence"] is False
    assert out[0]["series_date"] is None
    assert PLUGIN_MODULE._expand_occurrences(ev, "2026-01-06", "2026-01-31") == []
    assert PLUGIN_MODULE._expand_occurrences(ev, "2025-12-01", "2026-01-04") == []


def test_end_date_equal_to_date_is_single_day():
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-05", end_date="2026-01-05"), "2026-01-01", "2026-01-31"
    )
    assert len(out) == 1
    assert out[0]["is_occurrence"] is False


def test_occurrence_cap():
    out = PLUGIN_MODULE._expand_occurrences(_ev(date="2026-01-05", recurrence="weekly"), None, None)
    assert len(out) == 366
    out = PLUGIN_MODULE._expand_occurrences(
        _ev(date="2026-01-05", recurrence="weekly"), None, None, cap=3
    )
    assert len(out) == 3


# --- Route/helper level (real sqlite) ---


def test_create_with_recurrence_expands_in_get():
    _, client = _make_plugin_and_client(FakeDbm())
    resp = client.post("/plugins/fake/events", json={
        "title": "Standup", "date": "2026-01-05", "time": "09:30", "recurrence": "weekly",
    })
    assert resp.status_code == 200
    event_id = resp.json()["id"]
    assert resp.json()["recurrence"] == "weekly"

    resp = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-01-19"})
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert [e["date"] for e in events] == ["2026-01-05", "2026-01-12", "2026-01-19"]
    assert all(e["id"] == event_id for e in events)
    assert all(e["is_occurrence"] for e in events)
    assert all(e["series_date"] == "2026-01-05" for e in events)
    assert all(e["time"] == "09:30" for e in events)


def test_get_returns_color_and_recurrence_fields():
    _, client = _make_plugin_and_client(FakeDbm())
    client.post("/plugins/fake/events", json={
        "title": "Trip", "date": "2026-01-05", "color": "tomato",
        "recurrence": "weekly", "end_date": "2026-01-12",
    })
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-01-31"}).json()["events"]
    assert [e["date"] for e in events] == ["2026-01-05", "2026-01-12"]
    assert all(e["color"] == "tomato" for e in events)
    assert all(e["recurrence"] == "weekly" for e in events)
    assert all(e["end_date"] == "2026-01-12" for e in events)


def test_multi_day_event_expands_per_day():
    _, client = _make_plugin_and_client(FakeDbm())
    client.post("/plugins/fake/events", json={
        "title": "Conf", "date": "2026-01-30", "end_date": "2026-02-02",
    })
    events = client.get("/plugins/fake/events", params={"from": "2026-02-01", "to": "2026-02-28"}).json()["events"]
    assert [e["date"] for e in events] == ["2026-02-01", "2026-02-02"]
    assert all(e["series_date"] == "2026-01-30" for e in events)


def test_delete_with_date_adds_exception():
    dbm = FakeDbm()
    _, client = _make_plugin_and_client(dbm)
    event_id = client.post("/plugins/fake/events", json={
        "title": "Gym", "date": "2026-01-05", "recurrence": "weekly",
    }).json()["id"]

    resp = client.delete(f"/plugins/fake/events/{event_id}", params={"date": "2026-01-12"})
    assert resp.status_code == 200
    assert resp.json() == {"detail": "Occurrence deleted"}

    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-01-31"}).json()["events"]
    assert [e["date"] for e in events] == ["2026-01-05", "2026-01-19", "2026-01-26"]

    row = dbm.get_db().execute("SELECT exceptions FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    assert row["exceptions"] is not None

    resp = client.delete(f"/plugins/fake/events/{event_id}", params={"date": "2026-01-12"})
    assert resp.status_code == 200
    row = dbm.get_db().execute("SELECT exceptions FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    assert row["exceptions"].count("2026-01-12") == 1


def test_delete_with_date_on_non_recurring_is_400():
    _, client = _make_plugin_and_client(FakeDbm())
    event_id = client.post("/plugins/fake/events", json={"title": "Once", "date": "2026-01-05"}).json()["id"]
    resp = client.delete(f"/plugins/fake/events/{event_id}", params={"date": "2026-01-05"})
    assert resp.status_code == 400
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-01-31"}).json()["events"]
    assert len(events) == 1


def test_delete_without_date_removes_series():
    _, client = _make_plugin_and_client(FakeDbm())
    event_id = client.post("/plugins/fake/events", json={
        "title": "Gym", "date": "2026-01-05", "recurrence": "weekly",
    }).json()["id"]
    resp = client.delete(f"/plugins/fake/events/{event_id}")
    assert resp.status_code == 200
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-12-31"}).json()["events"]
    assert events == []


def test_put_clears_recurrence():
    _, client = _make_plugin_and_client(FakeDbm())
    event_id = client.post("/plugins/fake/events", json={
        "title": "Gym", "date": "2026-01-05", "recurrence": "weekly",
    }).json()["id"]
    resp = client.put(f"/plugins/fake/events/{event_id}", json={"recurrence": None})
    assert resp.status_code == 200
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-12-31"}).json()["events"]
    assert len(events) == 1
    assert events[0]["is_occurrence"] is False
    assert events[0]["recurrence"] is None


def test_put_sets_new_fields():
    _, client = _make_plugin_and_client(FakeDbm())
    event_id = client.post("/plugins/fake/events", json={"title": "Gym", "date": "2026-01-05"}).json()["id"]
    resp = client.put(f"/plugins/fake/events/{event_id}", json={
        "recurrence": "monthly", "end_date": "2026-03-05", "color": "sage",
    })
    assert resp.status_code == 200
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-12-31"}).json()["events"]
    assert [e["date"] for e in events] == ["2026-01-05", "2026-02-05", "2026-03-05"]
    assert all(e["color"] == "sage" for e in events)


def test_invalid_recurrence_color_and_end_date_rejected():
    _, client = _make_plugin_and_client(FakeDbm())
    assert client.post("/plugins/fake/events", json={
        "title": "T", "date": "2026-01-05", "recurrence": "daily",
    }).status_code == 422
    assert client.post("/plugins/fake/events", json={
        "title": "T", "date": "2026-01-05", "color": "hotpink",
    }).status_code == 422
    assert client.post("/plugins/fake/events", json={
        "title": "T", "date": "2026-01-05", "end_date": "2026-01-04",
    }).status_code == 422
    assert client.put("/plugins/fake/events/some-id", json={
        "date": "2026-01-05", "end_date": "2026-01-01",
    }).status_code == 422


def test_list_events_tool_marks_recurring():
    plugin, client = _make_plugin_and_client(FakeDbm())
    plugin.create_event({"title": "Gym", "date": "2026-01-05", "recurrence": "weekly"})
    result = plugin.list_events({"from_date": "2026-01-01", "to_date": "2026-01-12"})
    assert "2026-01-05: Gym (repeats weekly)" in result
    assert "2026-01-12: Gym (repeats weekly)" in result


def test_create_event_tool_validates_new_fields():
    plugin, _ = _make_plugin_and_client(FakeDbm())
    assert plugin.create_event({"title": "T", "date": "2026-01-05", "recurrence": "daily"}).startswith("Error:")
    assert plugin.create_event({"title": "T", "date": "2026-01-05", "color": "hotpink"}).startswith("Error:")
    assert plugin.create_event({"title": "T", "date": "2026-01-05", "end_date": "2026-01-01"}).startswith("Error:")
    assert plugin.create_event({"title": "T", "date": "2026-01-05", "recurrence": "weekly"}).startswith("Event saved:")


def test_delete_event_tool_occurrence_and_errors():
    plugin, client = _make_plugin_and_client(FakeDbm())
    event_id = client.post("/plugins/fake/events", json={
        "title": "Gym", "date": "2026-01-05", "recurrence": "weekly",
    }).json()["id"]
    assert plugin.delete_event({"id": event_id, "date": "2026-01-12"}) == f"Occurrence deleted: {event_id} on 2026-01-12"
    events = client.get("/plugins/fake/events", params={"from": "2026-01-01", "to": "2026-01-31"}).json()["events"]
    assert "2026-01-12" not in [e["date"] for e in events]

    plain_id = client.post("/plugins/fake/events", json={"title": "Once", "date": "2026-02-01"}).json()["id"]
    assert plugin.delete_event({"id": plain_id, "date": "2026-02-01"}).startswith("Error:")
    assert plugin.delete_event({"id": event_id}) == f"Event deleted: {event_id}"


# --- Migration guard ---


def test_migration_guard_registers_v2_on_v1_schema():
    ctx = RecordingContext(db_module=FakeDbm(schema=_V1_SCHEMA))
    PLUGIN_MODULE.Plugin().on_load(ctx)
    v2 = [m for m in ctx.migrations if m[0] == 2]
    assert len(v2) == 1
    sql = v2[0][1]
    for column in ("recurrence", "end_date", "color", "exceptions"):
        assert f"ALTER TABLE calendar_events ADD COLUMN {column} TEXT;" in sql


def test_migration_guard_skips_v2_on_current_schema():
    ctx = RecordingContext(db_module=FakeDbm(schema=_V2_SCHEMA))
    PLUGIN_MODULE.Plugin().on_load(ctx)
    assert [m[0] for m in ctx.migrations] == [1]


def test_migration_guard_registers_v2_on_missing_table():
    ctx = RecordingContext(db_module=FakeDbm(schema=""))
    PLUGIN_MODULE.Plugin().on_load(ctx)
    v2 = [m for m in ctx.migrations if m[0] == 2]
    assert len(v2) == 1
    assert v2[0][1].count("ALTER TABLE") == 4


def test_migration_guard_partial_columns():
    dbm = FakeDbm(schema=_V1_SCHEMA)
    dbm.conn.execute("ALTER TABLE calendar_events ADD COLUMN color TEXT")
    dbm.conn.commit()
    ctx = RecordingContext(db_module=dbm)
    PLUGIN_MODULE.Plugin().on_load(ctx)
    v2 = [m for m in ctx.migrations if m[0] == 2]
    assert len(v2) == 1
    sql = v2[0][1]
    assert "ADD COLUMN color" not in sql
    for column in ("recurrence", "end_date", "exceptions"):
        assert f"ADD COLUMN {column}" in sql
