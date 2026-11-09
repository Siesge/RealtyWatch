---
name: shared-checkout-mac-fork
description: "The Mac-fork session edits RealtyWatch_Mac in this same checkout — never `git add -A`"
metadata:
  node_type: memory
  type: project
  originSessionId: 17ddf4a7-e792-4195-b40e-1d4d01c39edf
  modified: 2026-11-09T06:53:26.166Z
---

The Mac fork is developed by a separate Claude session ("Парсер недвижимости Ялта (fork)") that works in the **same working tree**, `D:\Claude\RealtyWatch`, under `RealtyWatch_Mac`. Both sessions commit to `main` in the same repo.

**Why:** on 2026-11-05 a `git add -A` from the Windows side swept that session's uncommitted port (about.py, share.py, its tests and web files, ~780 lines) into commit d3295f4 and pushed it. Nothing was lost, but the work landed under someone else's commit message.

**How to apply:** stage explicit paths — `git add RealtyWatch_Win README.md` (plus whatever else is mine) — and check `git status --short` for foreign paths before committing. A clean tree at the start of a session says nothing about five minutes later. If it happens anyway: leave the files on disk alone, tell the fork session (SendMessage) and the user; never force-push a shared `main`. See [[user-profile]].

**The index is shared too, not just the files on disk.** `git add` with explicit paths covers only
half of it: `git commit` without paths commits **everything that is in the index**, including a
file the other session staged between your `add` and your `commit` — one working tree has one
index, and both sessions use it. `git commit -- <paths>` commits exactly those paths, with one
catch: it takes their content from the **working tree**, not from what was staged earlier, which
differs for a half-prepared edit (`git add -p`).

**Why:** on 9 Nov 2026 it happened twice in one morning, in the narrowest possible form. The
fork's `git push` reported `302c408..799515b` instead of `3b38db9..` — my commit had landed in the
shared directory between its `commit` and its `push`, so its commit went on top of mine; an hour
later the mirror image, its push reporting `2929999..6068011`. Nothing was lost either time, and
only because both sides committed by paths. The habit is what made the race harmless, not luck.

**How to apply:** commit with paths — `git commit -- RealtyWatch_Win README.md` — never a bare
`git commit`, however clean `git status` looked a minute earlier. The worked case is in
`RealtyWatch_Mac/PORTING.md` §12.8.

**The checkout is not the only shared thing.** Release 1.3 (8 Nov 2026) showed two more: the
GitHub release body and this memory folder. Both sessions edited each almost simultaneously, and
both times it only came out right by luck — the two texts happened to say the same thing. The
mechanism to remember is that `gh release edit --notes` / `--notes-file` replaces the **whole**
body, and a memory file written from recollection replaces the whole file: a concurrent edit is
lost silently, with no conflict and no error.

**Why this is worth its own note:** the obvious check does not catch it. "I sent my change, read
it back, it is there" proves your own edit survived and says nothing about whether you erased
someone else's — each side verifies only what it wrote, so a lost write is invisible to both.

**How to apply:** read the whole file (or body) immediately before writing it, not earlier in the
turn, and never append from memory of what it contained. Where something has a natural owner, let
it have one: the release description belongs to the session that keeps the notes, and the other
sends values for it to insert. Announce a write to a shared object **before** making it, not
after. And there is no «my memory» and «yours»: one folder per project, so asking the other
session to «write this in your own memory» names the same files and, taken literally, produces
the duplicate — on 8 Nov 2026 the Windows session asked exactly that about the release-notes
rule, and the fork wrote nothing — not because a file existed, but because reading it showed the
rule was already covered whole. That is the test: read the folder, add to the existing file only
what it does not already say, and never create a second file on the same subject. A file covering
half the rule has to be extended, not accompanied. The whole two-session protocol lives in the
repo, in `RealtyWatch_Mac/PORTING.md` §8 (the fork keeps that file).
Related: [[github-release-writes-allowed]], [[release-notes-no-local-numbers]].
