---
name: no-real-site-requests-in-tests
description: Tests and checks must never hit real sites or touch the real database/photos; isolate everything
metadata:
  node_type: memory
  type: feedback
  originSessionId: 2b9c3d60-49db-4594-b76e-215737687ad3
  modified: 2026-11-01T10:36:13.040Z
---

Never let tests or verification scripts reach real CIAN/Avito/Yandex or use the real `database/`, `profiles/`, `logs/`, photos or backups.

**Why:** on 2026-11-01 a test stub missed a renamed loader, launched the real Chromium profile against Avito and caused captcha windows on the user's screen («капча за капчей»); a purge test would have deleted the real `database/images` (blocked by the auto-mode classifier). Test log lines also leaked into the real `logs/app.log` and had to be trimmed.

**How to apply:** `RealtyWatch_Win/tests/support.py` isolates DB, settings, images, backups, sound, profiles, run logs and guards `net.Http.request` (only 127.0.0.1) and `net.Browser.start`; `tests/__init__.py` silences logging and sound. Keep new code paths covered by these guards (stub `_get_search_html`, `enrich`, `verify`). For live checks prefer a single HTTP request (no browser window) and tell the user beforehand.
