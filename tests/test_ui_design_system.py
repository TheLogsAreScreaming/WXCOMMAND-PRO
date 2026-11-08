"""UI redesign guards: every template compiles, pages share one design system,
and the UI works offline (no CDN scripts)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEMPLATES = Path("app/web/templates")
STATIC = Path("app/web/static")


def test_every_template_compiles():
    from app.web.routes import TEMPLATES as T
    names = sorted(p.name for p in TEMPLATES.glob("*.html"))
    assert names, "no templates found"
    for name in names:
        T.env.get_template(name)   # raises TemplateSyntaxError on a broken template


def test_ui_has_no_cdn_dependencies():
    for page in TEMPLATES.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "unpkg.com" not in text and "cdn." not in text, page.name


def test_design_system_assets_are_bundled():
    for name in ("wx.css", "wx.js", "htmx.min.js"):
        assert (STATIC / name).is_file(), name
    css = (STATIC / "wx.css").read_text(encoding="utf-8")
    # light + dark themes and reduced motion are part of the contract
    assert "prefers-color-scheme: light" in css
    assert '[data-theme="light"]' in css
    assert "prefers-reduced-motion" in css


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MESH_WX_HOST", "127.0.0.1")
    monkeypatch.setenv("MESH_WX_DB", str(tmp_path / "ui.db"))
    from app.main import create_app
    return TestClient(create_app())


@pytest.mark.parametrize("path,title", [
    ("/operator", "Operator Center"),
    ("/settings/radio-tools", "Radios"),
    ("/settings/bluetooth", "Bluetooth scan"),
])
def test_pages_use_the_shared_layout(client, path, title):
    r = client.get(path)
    assert r.status_code == 200
    assert "/static/wx.css?v=" in r.text and "/static/htmx.min.js" in r.text
    assert 'id="confirm-dialog"' in r.text       # styled confirmations on every page
    assert 'aria-current="page"' in r.text        # navigation shows where you are
    assert "<title>%s · WXCOMMAND-PRO</title>" % title in r.text


def test_static_assets_are_served(client):
    for name in ("wx.css", "wx.js", "htmx.min.js", "us-basemap.json"):
        assert client.get("/static/" + name).status_code == 200


def test_over_the_air_actions_ask_first():
    """Every control that keys up a radio goes through a confirmation."""
    checks = {
        "manual_send.html": "/manual/send",
        "_transmit_rows.html": "/transmit-log/resend/",
        "troubleshoot.html": "/troubleshoot/test",
        "_dash_top.html": "/dry-run/toggle",
        "_errors.html": "/troubleshoot/clear-errors",
    }
    for name, action in checks.items():
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert action in text and "hx-confirm" in text, name
