import asyncio

import pytest


@pytest.fixture
def client(make_client):
    return make_client()


def _get_registry():
    import src.main
    return src.main.plugin_manager.get_registry()


# --- 1. Plugin loaded, table created ---

def test_calendar_table_created(client):
    import src.database
    conn = src.database.get_db()
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "calendar_events" in tables


# --- 2. GET /events returns empty list initially ---

def test_calendar_events_empty(client):
    resp = client.get("/plugins/calendar/events")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}


# --- 3. Tools registered ---

def test_calendar_tools_registered(client):
    registry = _get_registry()
    tool_names = {t["function"]["name"] for t in registry.get_tools()}
    assert "calendar.create_event" in tool_names
    assert "calendar.list_events" in tool_names
    assert "calendar.find_event" in tool_names
    assert "calendar.update_event" in tool_names
    assert "calendar.delete_event" in tool_names


# --- 4. create_event tool ---

@pytest.mark.asyncio
async def test_calendar_create_event_tool(client):
    from src import tools as tools_module
    result = await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Dentist", "date": "2025-03-15", "time": "09:00", "note": "Cleaning"}',
        None, None,
    )
    assert "Event saved" in result
    assert "Dentist" in result

    import src.database
    conn = src.database.get_db()
    rows = conn.execute(
        "SELECT title, event_date, event_time, note FROM calendar_events WHERE vault_name = ?",
        ("test-main",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Dentist"
    assert rows[0]["event_date"] == "2025-03-15"
    assert rows[0]["event_time"] == "09:00"
    assert rows[0]["note"] == "Cleaning"


# --- 5. list_events tool ---

@pytest.mark.asyncio
async def test_calendar_list_events_tool(client):
    from src import tools as tools_module
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Meeting", "date": "2025-04-01"}',
        None, None,
    )
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Party", "date": "2025-04-10"}',
        None, None,
    )
    result = await tools_module.execute_tool(
        "calendar_list_events", '{}', None, None,
    )
    assert "Meeting" in result
    assert "Party" in result


# --- 6. find_event tool ---

@pytest.mark.asyncio
async def test_calendar_find_event_tool(client):
    from src import tools as tools_module
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Team Standup", "date": "2025-05-01"}',
        None, None,
    )
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Dentist", "date": "2025-05-05"}',
        None, None,
    )
    result = await tools_module.execute_tool(
        "calendar_find_event", '{"query": "dent"}', None, None,
    )
    assert "Dentist" in result
    assert "Team Standup" not in result


# --- 7. update_event tool ---

@pytest.mark.asyncio
async def test_calendar_update_event_tool(client):
    from src import tools as tools_module
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "Old Title", "date": "2025-06-01"}',
        None, None,
    )
    events = client.get("/plugins/calendar/events").json()["events"]
    event_id = events[0]["id"]

    result = await tools_module.execute_tool(
        "calendar_update_event",
        f'{{"id": "{event_id}", "title": "New Title", "time": "14:00"}}',
        None, None,
    )
    assert "updated" in result.lower()

    import src.database
    conn = src.database.get_db()
    row = conn.execute(
        "SELECT title, event_time FROM calendar_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    assert row["title"] == "New Title"
    assert row["event_time"] == "14:00"


# --- 8. delete_event tool ---

@pytest.mark.asyncio
async def test_calendar_delete_event_tool(client):
    from src import tools as tools_module
    await tools_module.execute_tool(
        "calendar_create_event",
        '{"title": "To Delete", "date": "2025-07-01"}',
        None, None,
    )
    events = client.get("/plugins/calendar/events").json()["events"]
    assert len(events) == 1
    event_id = events[0]["id"]

    result = await tools_module.execute_tool(
        "calendar_delete_event", f'{{"id": "{event_id}"}}', None, None,
    )
    assert "deleted" in result.lower()

    events = client.get("/plugins/calendar/events").json()["events"]
    assert len(events) == 0


# --- 9. POST /events route ---

def test_calendar_post_events_route(client):
    resp = client.post("/plugins/calendar/events", json={
        "title": "Conference",
        "date": "2025-08-20",
        "time": "10:00",
        "note": "Annual",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Conference"
    assert data["date"] == "2025-08-20"
    assert data["time"] == "10:00"
    assert "id" in data

    events = client.get("/plugins/calendar/events").json()["events"]
    assert any(e["title"] == "Conference" for e in events)


# --- 10. GET /events?from=&to= filters by date range ---

def test_calendar_date_range_filter(client):
    client.post("/plugins/calendar/events", json={"title": "Jan", "date": "2025-01-15"})
    client.post("/plugins/calendar/events", json={"title": "Feb", "date": "2025-02-20"})
    client.post("/plugins/calendar/events", json={"title": "Mar", "date": "2025-03-10"})

    resp = client.get("/plugins/calendar/events?from=2025-02-01&to=2025-02-28")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["title"] == "Feb"


# --- 11. PUT /events/{id} route ---

def test_calendar_put_events_route(client):
    resp = client.post("/plugins/calendar/events", json={"title": "Original", "date": "2025-09-01"})
    event_id = resp.json()["id"]

    resp = client.put(f"/plugins/calendar/events/{event_id}", json={
        "title": "Updated",
        "date": "2025-09-05",
        "time": "15:30",
    })
    assert resp.status_code == 200

    events = client.get("/plugins/calendar/events").json()["events"]
    ev = next(e for e in events if e["id"] == event_id)
    assert ev["title"] == "Updated"
    assert ev["date"] == "2025-09-05"
    assert ev["time"] == "15:30"


# --- 12. DELETE /events/{id} route ---

def test_calendar_delete_events_route(client):
    resp = client.post("/plugins/calendar/events", json={"title": "Temp", "date": "2025-10-01"})
    event_id = resp.json()["id"]

    resp = client.delete(f"/plugins/calendar/events/{event_id}")
    assert resp.status_code == 200

    events = client.get("/plugins/calendar/events").json()["events"]
    assert not any(e["id"] == event_id for e in events)


# --- 13. 404 on missing event ---

def test_calendar_put_missing_event_404(client):
    assert client.put("/plugins/calendar/events/nonexistent-id", json={"title": "x"}).status_code == 404


def test_calendar_delete_missing_event_404(client):
    assert client.delete("/plugins/calendar/events/nonexistent-id").status_code == 404


# --- 14. Prompt fragment ---

def test_calendar_prompt_fragment(client):
    registry = _get_registry()
    fragments = registry.get_prompt_fragments("chat")
    assert any("calendar.create_event" in frag for frag in fragments)


def test_calendar_api_registered(client):
    registry = _get_registry()
    api = registry.get_api("calendar")
    assert api is not None
    assert hasattr(api, "get_upcoming_events")


def test_calendar_prompt_fragment_includes_all_tool_names(client):
    registry = _get_registry()
    fragments = registry.get_prompt_fragments("chat")
    combined = " ".join(fragments)
    for tool in ("calendar.create_event", "calendar.list_events",
                 "calendar.find_event", "calendar.update_event", "calendar.delete_event"):
        assert tool in combined
    assert "check upcoming events" in combined.lower()


def test_calendar_api_get_upcoming_events(client):
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    client.post("/plugins/calendar/events", json={"title": "Today Event", "date": today})
    client.post("/plugins/calendar/events", json={"title": "Tomorrow Event", "date": tomorrow})

    registry = _get_registry()
    api = registry.get_api("calendar")
    events = api.get_upcoming_events("test-main", days=7)
    titles = {e["title"] for e in events}
    assert "Today Event" in titles
    assert "Tomorrow Event" in titles
