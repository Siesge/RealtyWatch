---
name: release-notes-no-local-numbers
description: Release notes must not quote counts from the owner's own database - a release is installed later, often on a clean system where those numbers do not exist
metadata:
  node_type: memory
  type: feedback
  originSessionId: 17ddf4a7-e792-4195-b40e-1d4d01c39edf
  modified: 2026-11-08T10:17:27.000Z
---

On 8 November 2026, right after 1.3 went out, the owner ruled that release notes and release descriptions must not name concrete counts taken from a particular database - no "112 listings", no "1217 -> 1107", no "157 of them were missing from search".

**Why:** a release gets downloaded and run a month or two later, and the numbers belong to one machine at one moment. There are no cards in the release itself; on a clean install on a fresh OS the count is 0. Their words: write as if the program is being installed on a new operating system with no previous installation.

**How to apply:** facts about the program and the build stay - asset sizes, OS requirements, the ~300 MB download / ~800 MB on disk of the first-run environment, test counts, build commits, and measured behaviour of the program itself (for example "the slider missed the cut-out by 20 px at half the frame rate"). What must go is the contents of one machine's database: listing and object counts, how long a backlog had been piling up, and lines like "on the Mac the numbers will be different". Where a warning only applies to an upgrade, scope it that way and say what it depends on instead of giving a figure: in the v1.3 body the section became "Если обновляетесь с уже собранной базой" with "сколько объявлений уйдёт, зависит от того, как долго собиралась именно ваша база", plus a sentence that a clean install has no base yet. The measured numbers stay in memory ([[first-run-mass-removal]]), not in the published text. The rule is written down in the repo too, in two places that outlive a session: the docstring of `release/make_release.py` (81ce948 — the file you open to build the next release) and `RealtyWatch_Mac/PORTING.md` §8 (21c8e73, the fork's), where the can / cannot / replace-with split is listed line by line. Related: [[github-release-writes-allowed]], [[shared-checkout-mac-fork]].
