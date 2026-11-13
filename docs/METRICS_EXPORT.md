# History export (InfluxDB / Prometheus / Grafana)

WXCOMMAND-PRO can send what it records to your own time-series database so
you can keep a long history and graph it in Grafana. **Everything here is off
by default**; nothing is sent until you turn it on in **Settings → Data export**.

## What is exported

WXCOMMAND-PRO has no weather sensors of its own. What it collects, and what
this exports:

- **Alert history**: every NWS alert it saw, its event type and what was done
  with it (sent, filtered, duplicate, …).
- **Transmit log**: every send, per radio (Meshtastic / MeshCore), its size in
  bytes, channel, result, and whether it was manual.
- **Health**: radios connected, send-queue depth, tracked alerts not yet
  expired, dry-run on/off, error-log size, time of the last good NWS poll.

Alert text and the transmitted message text are **not** exported.

## InfluxDB push

In Settings → Data export, enter the **InfluxDB write URL** and, if needed, a
**token**:

- InfluxDB 2 / 3: `http://influx.local:8086/api/v2/write?org=home&bucket=wx`
- InfluxDB 1.x: `http://influx.local:8086/write?db=wx`

`precision=s` is added if the URL names no precision. The token is sent as
`Authorization: Token <token>` (a value with a space, such as `Basic …`, is
sent as written). The token field never shows the saved token; leave it blank
to keep it, or tick *Forget the saved token*.

Every minute the app sends the history and transmit rows it has not sent yet,
each stamped with the time it was recorded, plus one status point:

```
wxcommand_alert,disposition=sent,event=Tornado\ Warning area="Richland, SC",count=1i,nws_id="urn:…",row_id=12i 1794592800
wxcommand_transmit,manual=false,result=ok,transport=meshtastic bytes=120i,channel=0i,ok=true,row_id=40i 1794592805
wxcommand_status active_alerts=1i,dry_run=false,errors=0i,last_poll_success=1794592790i,queue_depth=0i,radio_meshtastic_connected=true 1794592860
```

The last row id sent is stored in the database, so a restart does not send
rows twice. If InfluxDB is down or refuses a push, the rows stay queued and
go with the next successful push.

## Prometheus

Turn on **Prometheus metrics page**. The app then serves `/metrics` on its
own address, `http://127.0.0.1:8000/metrics` by default. It is reachable
from this computer only, unless remote access is configured with
`MESH_WX_HOST` + `WXCOMMAND_API_TOKEN`; Prometheus must then send the
`X-WXCOMMAND-Token` header. When the switch is off, `/metrics` returns 404.

```yaml
scrape_configs:
  - job_name: wxcommand
    scrape_interval: 60s
    static_configs:
      - targets: ["127.0.0.1:8000"]
```

Metrics:

| Name | Type | Labels |
| --- | --- | --- |
| `wxcommand_alerts_total` | counter | `event`, `disposition` |
| `wxcommand_transmits_total` | counter | `transport`, `result`, `manual` |
| `wxcommand_transmit_bytes_total` | counter | `transport` (successful sends) |
| `wxcommand_active_alerts` | gauge | |
| `wxcommand_radio_connected` | gauge | `radio` (enabled radios only) |
| `wxcommand_queue_depth`, `wxcommand_errors`, `wxcommand_dry_run`, `wxcommand_uptime_seconds` | gauge | |
| `wxcommand_last_poll_success_timestamp_seconds` | gauge | |

The counters are counts of the rows the app keeps (history is pruned after
90 days), so a prune shows up in Prometheus like a counter reset; `rate()`
and `increase()` handle that.

## Grafana

Import `deploy/grafana/wxcommand-dashboard.json` (Dashboards → New → Import)
and choose your Prometheus data source. It shows radios, active alerts,
queue, dry run and poll age as tiles, plus alerts per hour by event, sends
per hour by radio and result, airtime bytes, and radio link history. For
InfluxDB, build panels from the `wxcommand_alert`, `wxcommand_transmit` and
`wxcommand_status` measurements, e.g.:

```flux
from(bucket: "wx")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wxcommand_alert" and r._field == "count")
  |> group(columns: ["event"])
  |> aggregateWindow(every: 1h, fn: sum)
```
