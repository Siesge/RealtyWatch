---
name: first-run-mass-removal
description: "The first run after the 7 Nov 2026 off-sale change removes everything that piled up at once — a one-day spike on the charts, not a bug"
metadata:
  node_type: memory
  type: project
  originSessionId: 774c9ad3-5fbe-4899-940c-9d8d2493c4c7
  modified: 2026-11-08T10:11:42.000Z
---

The «снято с продажи по квартире целиком» rule (commit `7a6f6a1` on Windows, `78a279a` in the Mac fork, 7 November 2026) makes the **first run after the update** write off everything that had been accumulating since late October in one go. On the Windows base the main session measured it on a copy: 112 listings, objects 1217 → 1107; a second `reconcile` pass gives 0.

**Why:** Avito answers page checks with a captcha, so candidates for removal only piled up — they were never confirmed. The new rule decides per flat, so all of them resolve in the first trustworthy run.

**How to apply:** on the owner's Mac the numbers will differ, since that base lived its own life. Count on a copy before the first run, not on the live base. In «Изменениях» and on the charts it looks like a one-day spike although it accumulated over weeks — say so before they see it and think the program broke. The warning is in writing where they will see it: the 1.3 release notes carry a section of their own — but **without these numbers**, by the owner ruling of 8 Nov 2026 ([[release-notes-no-local-numbers]]): it is scoped to «Если обновляетесь с уже собранной базой» and says the amount depends on how long that base had been collecting. The measured figures live here, not in the published text. Related: [[no-real-site-requests-in-tests]], [[mac-target-machine]].
