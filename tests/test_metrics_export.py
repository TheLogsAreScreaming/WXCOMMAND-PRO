"""History export: InfluxDB line protocol push and the Prometheus /metrics page."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import metrics
from app.db import Database

ROOT = Path(__file__).resolve().parents[1]


class FakeTx:
    queue_depth = 3

    def status(self):
        return [{"name": "meshtastic", "enabled": True, "connected": True},
                {"name": "meshcore", "enabled": False, "connected": False}]


def _poller():
    return SimpleNamespace(status=SimpleNamespace(last_poll_success_time="2026-11-13T18:00:00+00:00",
                                                  uptime_seconds=42))


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "m.db"))
    yield d
    d.close()


def _fill(db):
    db.add_history("urn:1", "Tornado Warning", "Richland, SC", "sent", "TOR")
    db.add_history("urn:2", "Flood Advisory", "Lexington, SC", "filtered")
    db.add_history("urn:3", "Tornado Warning", "Kershaw, SC", "sent", "TOR")
    db.add_transmit_log(0, 120, True, "TOR", transport="meshtastic")
    db.add_transmit_log(0, 80, False, "TOR", error="timeout", transport="meshcore")
    db.add_transmit_log(1, 10, True, "hi", manual=True, transport="meshtastic")
    db.upsert_state("urn:1", "Tornado Warning", "h", "2099-01-01T00:00:00-05:00", "x", "sent", None)
    db.upsert_state("urn:9", "Old Warning", "h", "2000-01-01T00:00:00-05:00", "x", "sent", None)


def test_off_by_default(db):
    assert db.get_setting("metrics_influx_url") == ""
    assert db.get_setting("metrics_prometheus_enabled") is False


# ---- line protocol ------------------------------------------------------------

def test_line_escaping_and_types():
    out = metrics.line("m x", {"event": "Tornado Warning", "k": "a,b=c", "empty": ""},
                       {"n": 1, "f": 1.5, "ok": True, "s": 'say "hi"'}, 1700000000)
    assert out == r'm\ x,event=Tornado\ Warning,k=a\,b\=c f=1.5,n=1i,ok=true,s="say \"hi\"" 1700000000'


def test_history_and_transmit_lines_use_row_time(db):
    _fill(db)
    h = metrics.history_line(db.history_since(0)[0])
    assert h.startswith(r'wxcommand_alert,disposition=sent,event=Tornado\ Warning area="Richland, SC",count=1i,')
    assert 'nws_id="urn:1"' in h
    t = metrics.transmit_line(db.transmit_since(0)[1])
    assert t.startswith("wxcommand_transmit,manual=false,result=failed,transport=meshcore ")
    assert "bytes=80i" in t and "ok=false" in t


def test_write_url_and_auth():
    assert metrics.write_url("http://i:8086/api/v2/write?bucket=wx").endswith("bucket=wx&precision=s")
    assert metrics.write_url("http://i:8086/write?db=wx&precision=ms").endswith("precision=ms")
    assert metrics.auth_headers("") == {}
    assert metrics.auth_headers("t0k") == {"Authorization": "Token t0k"}
    assert metrics.auth_headers("Basic abc") == {"Authorization": "Basic abc"}


# ---- push --------------------------------------------------------------------

async def test_push_does_nothing_without_a_url(db):
    _fill(db)
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(204)))
    exp = metrics.InfluxExporter(db, FakeTx(), _poller(), client=client)
    assert await exp.push_once() == 0
    assert calls == []
    await client.aclose()


async def test_push_sends_new_rows_once_and_keeps_them_on_failure(db):
    _fill(db)
    db.set_setting("metrics_influx_url", "http://influx:8086/api/v2/write?org=o&bucket=wx")
    db.set_setting("metrics_influx_token", "secret")
    seen, status = [], {"code": 503}

    def handler(request):
        seen.append(request)
        return httpx.Response(status["code"], text="busy")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    exp = metrics.InfluxExporter(db, FakeTx(), _poller(), client=client)

    assert await exp.push_once(now=1800000000) == 0          # refused: cursor stays
    assert db.get_setting(metrics.CURSOR_HISTORY, 0) == 0
    assert "HTTP 503" in exp.last_error

    status["code"] = 204
    assert await exp.push_once(now=1800000000) == 7          # 3 alerts + 3 sends + status
    body = seen[-1].content.decode()
    assert body.count("wxcommand_alert,") == 3 and body.count("wxcommand_transmit,") == 3
    assert "wxcommand_status queue_depth=3i" not in body     # fields are sorted
    assert "active_alerts=1i" in body and "radio_meshtastic_connected=true" in body
    assert body.rstrip().endswith(" 1800000000")
    assert seen[-1].headers["Authorization"] == "Token secret"
    assert "precision=s" in str(seen[-1].url)

    assert await exp.push_once(now=1800000060) == 1          # only the status point now
    db.add_history("urn:4", "Flash Flood Warning", "x", "sent")
    assert await exp.push_once(now=1800000120) == 2
    assert "Flash\\ Flood\\ Warning" in seen[-1].content.decode()
    await client.aclose()


async def test_push_survives_network_errors(db):
    db.set_setting("metrics_influx_url", "http://influx:8086/write?db=wx")

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    exp = metrics.InfluxExporter(db, client=client)
    assert await exp.push_once() == 0
    assert "ConnectError" in exp.last_error
    await client.aclose()


# ---- Prometheus -----------------------------------------------------------------

def test_render_prometheus(db):
    _fill(db)
    text = metrics.render_prometheus(metrics.snapshot(db, FakeTx(), _poller()))
    assert "# TYPE wxcommand_alerts_total counter" in text
    assert 'wxcommand_alerts_total{event="Tornado Warning",disposition="sent"} 2' in text
    assert 'wxcommand_transmits_total{transport="meshcore",result="failed",manual="false"} 1' in text
    assert 'wxcommand_transmit_bytes_total{transport="meshtastic"} 130' in text
    assert "wxcommand_active_alerts 1" in text
    assert 'wxcommand_radio_connected{radio="meshtastic"} 1' in text
    assert 'radio="meshcore"' not in text.split("wxcommand_radio_connected")[-1].split("#")[0]
    assert "wxcommand_queue_depth 3" in text
    assert "wxcommand_dry_run 1" in text
    assert "wxcommand_last_poll_success_timestamp_seconds 1794592800" in text


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("MESH_WX_HOST", "127.0.0.1")
    from app.main import create_app
    app = create_app()
    app.state.db, app.state.tx, app.state.poller = db, FakeTx(), _poller()
    return TestClient(app)


def test_metrics_endpoint_is_off_until_enabled(client, db):
    assert client.get("/metrics").status_code == 404
    db.set_setting("metrics_prometheus_enabled", True)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "wxcommand_queue_depth 3" in r.text


def test_settings_page_has_export_section(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert 'id="export"' in r.text and 'name="metrics_influx_url"' in r.text
    assert 'name="metrics_prometheus_enabled"' in r.text


def test_sample_grafana_dashboard_and_doc():
    dash = json.loads((ROOT / "deploy/grafana/wxcommand-dashboard.json").read_text())
    assert dash["title"] and dash["panels"]
    text = json.dumps(dash)
    for name in ("wxcommand_alerts_total", "wxcommand_transmits_total", "wxcommand_radio_connected"):
        assert name in text
    assert (ROOT / "docs/METRICS_EXPORT.md").is_file()
