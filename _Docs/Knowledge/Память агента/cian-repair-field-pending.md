---
name: cian-repair-field-pending
description: "CIAN renovation now comes from the offer's repairType field — never checked against a live response; read the run log after the next CIAN run"
metadata:
  node_type: memory
  type: project
  originSessionId: 17ddf4a7-e792-4195-b40e-1d4d01c39edf
  modified: 2026-11-11T10:29:20.943Z
---

On 11 Nov 2026 the CIAN scraper (`RealtyWatch_Win/app/scrapers/cian.py`) started reading renovation from the offer field `repairType` (`no` / `cosmetic` / `euro` / `design`), which wins over the description, and the server-side `repair: [1, 2, 3]` filter was removed (the database showed it let "без ремонта" through, marked "any", and kept designer renovations out — code 1 at CIAN looks like "no renovation").

The field name comes from knowledge of CIAN's format, not from a live response: the one allowed GET of a card page that day got a captcha, and the project has no saved CIAN offer samples (tests are synthetic).

**Why:** if the name is wrong, every CIAN offer silently falls back to text-only renovation — and with the CIAN-side filter gone, a no-renovation flat with nothing in its description passes with a yellow "ремонт не указан" tag.

**How to apply:** after the owner's next real CIAN run, read the newest `RealtyWatch_Win/logs/runs/*_cian.log` for the line `поле «Ремонт»: …` (counts per value). `⚠ поле «Ремонт» ЦИАН не пришло ни в одном из N объявлений` means the field is named differently. Owner's two reference flats should end up "без ремонта": CIAN 333647311 and 336007473. Do not fetch CIAN to check — read the log. Same pattern as [[avito-run-review-pending]]; live-site rule in [[no-real-site-requests-in-tests]].
