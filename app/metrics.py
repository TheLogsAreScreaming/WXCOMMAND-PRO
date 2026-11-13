"""History export: InfluxDB line protocol push and a Prometheus /metrics page.

WXCOMMAND-PRO has no weather sensors of its own; what it collects is the
alert history (every NWS alert it saw and what it did with it), the transmit
log (every send, per radio, with its size and result) and its own health
(radios connected, queue depth, last good poll). This module exports those.

Both outputs are OFF by default and are switched on in Settings > Data export
(stored in the database like every other setting):

* metrics_influx_url / metrics_influx_token: new history and transmit rows are
  pushed as line protocol, with the row's own timestamp, plus a status point
  each round. The ids already sent are remembered in the database, so a
  restart does not send them again.
* metrics_prometheus_enabled: GET /metrics on the app's own server, which
  listens on 127.0.0.1 unless MESH_WX_HOST says otherwise (remote access then
  needs WXCOMMAND_API_TOKEN, like every other page).

See docs/METRICS_EXPORT.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger("mesh_wx.metrics")

PUSH_INTERVAL_SECONDS = 60
BATCH = 500
CURSOR_HISTORY = "metrics_influx_history_id"
CURSOR_TRANSMIT = "metrics_influx_transmit_id"


# ---- line protocol ------------------------------------------------------------

def _esc_tag(v) -> str:
    return (str(v).replace("\\", "\\\\").replace(",", r"\,")
            .replace("=", r"\=").replace(" ", r"\ ").replace("\n", " "))


def _esc_str_field(v) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _field(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return "%di" % v
    if isinstance(v, float):
        return repr(v)
    return _esc_str_field(v)


def line(measurement: str, tags: dict, fields: dict, ts_seconds: int) -> str:
    tag_text = "".join(",%s=%s" % (_esc_tag(k), _esc_tag(v))
                       for k, v in sorted(tags.items()) if v not in (None, ""))
    field_text = ",".join("%s=%s" % (_esc_tag(k), _field(v))
                          for k, v in sorted(fields.items()) if v is not None)
    return "%s%s %s %d" % (_esc_tag(measurement), tag_text, field_text, int(ts_seconds))


def _epoch(ts: str) -> int:
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return int(time.time())
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


def history_line(row) -> str:
    return line("wxcommand_alert",
                {"event": row["event"] or "unknown", "disposition": row["disposition"] or "unknown"},
                {"count": 1, "nws_id": row["nws_id"] or "", "area": (row["area"] or "")[:200],
                 "row_id": int(row["id"])},
                _epoch(row["ts"]))


def transmit_line(row) -> str:
    ok = bool(row["success"])
    return line("wxcommand_transmit",
                {"transport": row["transport"] or "meshtastic", "result": "ok" if ok else "failed",
                 "manual": "true" if row["manual"] else "false"},
                {"bytes": int(row["byte_count"] or 0), "ok": ok,
                 "channel": int(row["channel"] or 0), "row_id": int(row["id"])},
                _epoch(row["ts"]))


def status_line(snap: dict, ts_seconds: int) -> str:
    fields = {"queue_depth": snap["queue_depth"], "active_alerts": snap["active_alerts"],
              "dry_run": snap["dry_run"], "errors": snap["errors"]}
    for radio, up in snap["radios"].items():
        fields["radio_%s_connected" % radio] = up
    if snap.get("last_poll_success") is not None:
        fields["last_poll_success"] = int(snap["last_poll_success"])
    return line("wxcommand_status", {}, fields, ts_seconds)


def write_url(url: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    if not any(k == "precision" for k, _ in q):
        q.append(("precision", "s"))
    return urlunsplit(parts._replace(query=urlencode(q)))


def auth_headers(token: str) -> dict:
    token = (token or "").strip()
    if not token:
        return {}
    return {"Authorization": token if " " in token else "Token " + token}


# ---- snapshot of the app's health ---------------------------------------------

def snapshot(db, tx=None, poller=None) -> dict:
    counts = db.metric_counts()
    radios = {}
    if tx is not None:
        try:
            for t in tx.status():
                if t.get("enabled"):
                    radios[t["name"]] = bool(t.get("connected"))
        except Exception:
            logger.debug("transport status unavailable", exc_info=True)
    last_ok = None
    if poller is not None:
        stamp = getattr(getattr(poller, "status", None), "last_poll_success_time", None)
        if stamp:
            last_ok = _epoch(stamp)
    return {**counts, "radios": radios,
            "queue_depth": int(getattr(tx, "queue_depth", 0) or 0) if tx is not None else 0,
            "dry_run": bool(db.get_setting("dry_run", True)),
            "last_poll_success": last_ok,
            "uptime": int(getattr(getattr(poller, "status", None), "uptime_seconds", 0) or 0)}


# ---- Prometheus ---------------------------------------------------------------

def _lbl(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(d: dict) -> str:
    return "{" + ",".join('%s="%s"' % (k, _lbl(v)) for k, v in d.items()) + "}"


def render_prometheus(snap: dict) -> str:
    out: list[str] = []

    def metric(name, kind, help_text, rows):
        out.append("# HELP %s %s" % (name, help_text))
        out.append("# TYPE %s %s" % (name, kind))
        for labels, value in rows:
            out.append("%s%s %s" % (name, _labels(labels) if labels else "", value))

    metric("wxcommand_alerts_total", "counter",
           "Alerts recorded in the history (kept 90 days), by event and what was done.",
           [({"event": r["event"] or "unknown", "disposition": r["disposition"] or "unknown"}, r["n"])
            for r in snap["alerts"]])
    metric("wxcommand_transmits_total", "counter", "Messages sent, by radio and result.",
           [({"transport": r["transport"], "result": "ok" if r["success"] else "failed",
              "manual": "true" if r["manual"] else "false"}, r["n"]) for r in snap["transmits"]])
    by_transport: dict[str, int] = {}
    for r in snap["transmits"]:
        if r["success"]:
            by_transport[r["transport"]] = by_transport.get(r["transport"], 0) + int(r["bytes"])
    metric("wxcommand_transmit_bytes_total", "counter", "Payload bytes sent successfully, by radio.",
           [({"transport": k}, v) for k, v in sorted(by_transport.items())])
    metric("wxcommand_active_alerts", "gauge", "Tracked alerts that have not expired yet.",
           [(None, snap["active_alerts"])])
    metric("wxcommand_radio_connected", "gauge", "1 when an enabled radio is connected.",
           [({"radio": k}, int(v)) for k, v in sorted(snap["radios"].items())])
    metric("wxcommand_queue_depth", "gauge", "Messages waiting to be sent.", [(None, snap["queue_depth"])])
    metric("wxcommand_errors", "gauge", "Rows in the error log.", [(None, snap["errors"])])
    metric("wxcommand_dry_run", "gauge", "1 while dry run is on (nothing is transmitted).",
           [(None, int(snap["dry_run"]))])
    metric("wxcommand_uptime_seconds", "gauge", "Seconds since the poller started.",
           [(None, snap["uptime"])])
    if snap.get("last_poll_success") is not None:
        metric("wxcommand_last_poll_success_timestamp_seconds", "gauge",
               "When a poll last reached the NWS (Unix time).", [(None, snap["last_poll_success"])])
    return "\n".join(out) + "\n"


# ---- InfluxDB push --------------------------------------------------------------

class InfluxExporter:
    """Background loop: pushes new history/transmit rows plus a status point.
    Reads its settings each round, so changes in Settings apply without a
    restart, and does nothing while no URL is set."""

    def __init__(self, db, tx=None, poller=None, interval: int = PUSH_INTERVAL_SECONDS,
                 client: httpx.AsyncClient | None = None):
        self.db, self.tx, self.poller = db, tx, poller
        self.interval = interval
        self._client = client
        self._task: asyncio.Task | None = None
        self.last_error = ""
        self.last_success: float = 0.0
        self.points_sent = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="metrics-influx")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.push_once()
            except Exception:
                logger.exception("metrics push round failed (non-fatal)")
            await asyncio.sleep(self.interval)

    async def _post(self, url: str, body: str, headers: dict) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, content=body, headers=headers, timeout=10)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await client.post(url, content=body, headers=headers, timeout=10)

    async def push_once(self, now: float | None = None) -> int:
        """One round. Returns the number of points written (0 when off or failed)."""
        url = (self.db.get_setting("metrics_influx_url", "") or "").strip()
        if not url:
            return 0
        h_after = int(self.db.get_setting(CURSOR_HISTORY, 0) or 0)
        t_after = int(self.db.get_setting(CURSOR_TRANSMIT, 0) or 0)
        hist = self.db.history_since(h_after, BATCH)
        sends = self.db.transmit_since(t_after, BATCH)
        lines = [history_line(r) for r in hist] + [transmit_line(r) for r in sends]
        lines.append(status_line(snapshot(self.db, self.tx, self.poller), int(now or time.time())))
        headers = {"Content-Type": "text/plain; charset=utf-8",
                   **auth_headers(self.db.get_setting("metrics_influx_token", "") or "")}
        try:
            resp = await self._post(write_url(url), "\n".join(lines), headers)
        except httpx.HTTPError as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            logger.warning("InfluxDB push failed (%s); will retry", self.last_error)
            return 0
        if resp.status_code >= 300:
            self.last_error = "HTTP %d: %s" % (resp.status_code, resp.text[:200])
            logger.warning("InfluxDB push refused (%s); will retry", self.last_error)
            return 0
        if hist:
            self.db.set_setting(CURSOR_HISTORY, int(hist[-1]["id"]))
        if sends:
            self.db.set_setting(CURSOR_TRANSMIT, int(sends[-1]["id"]))
        self.last_error = ""
        self.last_success = time.time()
        self.points_sent += len(lines)
        return len(lines)
