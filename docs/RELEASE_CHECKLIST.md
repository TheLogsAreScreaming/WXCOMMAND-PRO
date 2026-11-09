# WXCOMMAND-PRO 1.2.0 release checklist

Published 2026-11-08 as `1.2.0` (Intel Mac and Raspberry Pi only). The tag has no `v`, so `release.yml` did not build Docker or Windows artifacts. Unticked items below were **not** verified for this release.

## Automated acceptance
- [x] Python 3.11, 3.12, 3.13 CI jobs pass.
- [x] `python -m compileall -q app` passes.
- [x] `python -m app.smokecheck` passes.
- [x] `python -m pytest -q tests` passes.
- [x] API routes and responsive UI tested in desktop and mobile browsers.

## Security
- [x] Default HTTP host set to 127.0.0.1 on Phase 4 branch.
- [ ] Review authentication for all settings, profile, radio and manual send routes.
- [x] Verify CSRF protection for state-changing endpoints (origin and fetch-metadata checks, `tests/test_http_security.py`).
- [ ] Verify secrets and radio identifiers never appear in diagnostics.
- [ ] Verify NWS response handling, request timeouts and geographic accuracy.
- [ ] Confirm reverse-proxy configuration before any remote access.
- [ ] Review third-party dependencies and license compliance.

## Hardware and data
- [ ] Raspberry Pi 4 boot, restart, backup and offline restore verified.
- [ ] Intel Mac standalone ZIP and DMG tested on actual target hardware, including Finder launch, native window, Dock reopen, ⌘Q quit, and Gatekeeper approval.
- [ ] Meshtastic and MeshCore USB, TCP, BLE tests completed where supported.
- [ ] Failover tested without duplicate sends on test channels.
- [ ] NWS/IPAWS filter, dedupe, cancellation and expiration behavior verified.
- [ ] All tests conducted with dry run enabled or dedicated test channels.

## GitHub
- [x] Version updated from 1.1.2 to 1.2.0 after passing gates.
- [x] Changelog and release notes reviewed.
- [x] Git tag `1.2.0` created only from verified main (4aa8b56).
- [x] Release assets published; checksums verified after download.
- [ ] Release assets tested on real hardware.

Do not claim production readiness until every critical gate passes.
