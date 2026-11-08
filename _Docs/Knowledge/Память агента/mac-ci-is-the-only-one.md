---
name: mac-ci-is-the-only-one
description: ".github/workflows/mac.yml is the repo's only CI — the Windows version's tests are run by hand, so browser bugs surface only in the fork's runs"
metadata:
  node_type: memory
  type: project
  originSessionId: 774c9ad3-5fbe-4899-940c-9d8d2493c4c7
  modified: 2026-11-07T21:46:01.534Z
---

`.github/workflows/mac.yml` is the only workflow in `Siesge/RealtyWatch`. The Windows version has no automated runs at all — the main session runs its tests by hand on a fast machine (confirmed by that session, 7 November 2026).

**Why it matters:** the browser tests (`test_avito_nav`, `test_slider`, `test_click_aim`) are timing-sensitive, and a fast developer machine hides what a loaded `macos-15` runner exposes. That is how the real mouse-aim defect was found: the cursor walked to a remembered point for ~1 s and pressed without re-checking, so on any slow machine it hit past the link — on the live site too, not just in tests.

**How to apply:** treat a red Mac run as information for both versions, not just the fork, and say so when reporting it. Do **not** verify a timing fix by running the workflow again until it comes back green: at the rates these flakes actually had (3 of 24 runs, 2 of 24), three greens in a row happen by luck two times in three, so they prove nothing. Verify with a test that reproduces the cause deliberately on any machine — `test_click_aim` and `test_slow_page_is_not_mistaken_for_a_stopped_piece` are both of that shape, and the fork's suite runs on the Windows box in ~215 s (PORTING.md level 1). Runs also cost: the repo is private and macOS bills at x10, a job is about 4 minutes of machine time, so 26 runs over 2-7 Nov 2026 took roughly 1000 of the 2000 free monthly minutes. I burned ~120 of them on three "just to be sure" runs that showed nothing. On 8 Nov 2026 the owner narrowed the triggers (relayed through the main session, «Да, передай форку»), so **a push no longer runs the browser tests at all**: a code push runs the fast set plus the whole build, the «Run workflow» button (`gh workflow run mac.yml`) runs everything including them — and that same dispatch builds a release dmg, so a release always has them — and a change to `**.md` alone runs nothing. The switch is `RW_SKIP_BROWSER_TESTS` in `tests/support.py`, which turns off the `@needs_browser` gate; the step fails if the skips in its log are not the ones intended. So after a push, "CI is green" does not mean the browser tests passed — press the button before trusting a timing fix or cutting a release. Related: [[mac-target-machine]], [[no-real-site-requests-in-tests]].
