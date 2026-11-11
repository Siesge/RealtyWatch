---
name: cian-repair-field-pending
description: CIAN renovation comes from separate repair-filtered search queries (the search API has no repair field) and the main query is sorted by price — first real run after 11 Nov 2026 not yet read
metadata:
  node_type: memory
  type: project
  originSessionId: 17ddf4a7-e792-4195-b40e-1d4d01c39edf
  modified: 2026-11-11T15:37:05.987Z
---

The CIAN search API (`search-offers-desktop`) returns no renovation field at all — checked on one live response on 11 Nov 2026 (about 150 keys per offer, no `repairType`). The first fix (reading `repairType`) therefore logged "⚠ поле «Ремонт» ЦИАН не пришло ни в одном из 388".

Since 11 Nov 2026 evening, `RealtyWatch_Win/app/scrapers/cian.py` runs four short queries with `repair: [code]` first (1 = без ремонта, 2 = косметический, 3 = евро, 4 = дизайнерский) and marks every offer that came back with that code. Then it runs the main query without a repair filter. Code 1 = «без ремонта» was confirmed live: the owner's flat 336007473 came back in the repair=1 result. Codes 2–4 are inferred from the order, not checked live.

The main query now has `sort: price_object_order`. Without a sort, CIAN ranks offers with an ML model, the order changes between page requests, and runs ended incomplete (371 of 418, with duplicates). The live check confirmed that the sort is honoured; it did not confirm that the run is now complete.

**Why:** if either assumption is wrong, CIAN runs either stay incomplete, so off-sale detection is skipped, or carry wrong renovation labels.

**How to apply:** after the owner's next CIAN run, read the newest `RealtyWatch_Win/logs/runs/*_cian.log`:
- `вид ремонта: без ремонта N, …` shows the repair queries worked.
- `выдача: … заявлено сайтом X, собрано уникальных Y` should have Y ≥ 95% of X.

The owner's flats 333647311 and 336007473 should end up «без ремонта». Do not fetch CIAN to check — read the log. Same pattern as [[avito-run-review-pending]]; live-site rule in [[no-real-site-requests-in-tests]].
