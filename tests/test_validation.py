"""Unit tests for calendar date validation and route wiring."""
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import load_plugin_module
from fake_plugin_context import FakePluginContext


def _make_plugin_and_client():
    ctx = FakePluginContext(db_module=MagicMock())
    plugin = load_plugin_module().Plugin()
    plugin.on_load(ctx)
    app = FastAPI()
    for name, router in ctx.registry.routers:
        app.include_router(router, prefix=f"/plugins/{name}")
    return plugin, TestClient(app)


def test_create_event_rejects_bad_date_format():
    _, client = _make_plugin_and_client()
    resp = client.post("/plugins/fake/events", json={"title": "Test", "date": "tomorrow"})
    assert resp.status_code == 422


def test_create_event_rejects_bad_time_format():
    _, client = _make_plugin_and_client()
    resp = client.post("/plugins/fake/events", json={"title": "Test", "date": "2026-01-02", "time": "noon"})
    assert resp.status_code == 422


def test_update_event_rejects_bad_date_format():
    _, client = _make_plugin_and_client()
    resp = client.put("/plugins/fake/events/some-id", json={"date": "2026/01/02"})
    assert resp.status_code == 422


def test_list_events_rejects_bad_from_param():
    _, client = _make_plugin_and_client()
    resp = client.get("/plugins/fake/events", params={"from": "not-a-date"})
    assert resp.status_code == 400


def test_list_events_rejects_bad_to_param():
    _, client = _make_plugin_and_client()
    resp = client.get("/plugins/fake/events", params={"to": "2026-13-99"})
    assert resp.status_code == 400


def test_create_event_accepts_valid_body():
    _, client = _make_plugin_and_client()
    resp = client.post("/plugins/fake/events", json={"title": "Test", "date": "2026-01-02", "time": "09:30"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "2026-01-02"
    assert data["time"] == "09:30"


def test_create_event_tool_returns_error_for_invalid_date():
    plugin, _ = _make_plugin_and_client()
    result = plugin.create_event({"title": "Test", "date": "2026-02-30"})
    assert result.startswith("Error:")
