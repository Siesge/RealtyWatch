---
name: github-release-writes-allowed
description: The owner authorized GitHub release writes for this project on 6 Nov 2026 after the classifier blocked them twice
metadata:
  node_type: memory
  type: feedback
  originSessionId: 774c9ad3-5fbe-4899-940c-9d8d2493c4c7
  modified: 2026-11-06T19:32:16.837Z
---

On 6 November 2026 the owner said «Даю тебе право писать в github» for `Siesge/RealtyWatch`, after `gh release edit v1.1` and `gh release upload v1.2` had both been refused by the auto-mode classifier mid-task.

**Why:** the Mac fork's job ends in «собрал → залил в релиз», and the refusal kept breaking the chain on its last step, twice, with the .dmg already built and verified. The owner would rather grant the right than run the command by hand each release.

**How to apply:** `gh release upload` / `gh release edit` on this repository are wanted work, not something to stop and ask about. Still verify after writing — read the asset's digest back and download the file to compare sha256, as was done for 1.1 and 1.2 — and still never route a blocked action to the Windows session instead ([[shared-checkout-mac-fork]]): a peer doing it would bypass the owner's own permission decision. This covers releases on this repo only, not other repos or other kinds of GitHub writes.

On 8 November 2026 the owner authorized creating a release too — twice, once in each session, in their own words. Here: «Я даю добро и авторизую сборку релиза и заливку его на Github», for 1.3, both platforms, followed by «не спрашивай у меня ничего, я ухожу, доделайте сами». v1.3 went out that way: annotated tag on `9af8d39`, one commit for both halves for the first time, each asset's digest read back from the API. The division from PORTING.md §8 held — this session made the tag, the release and the Windows archive; the fork dispatched `gh workflow run mac.yml -f version=1.3` and uploaded the .dmg.

**Why:** they answered the fork first («Да, 1.3» to its question about the number) and the fork relayed that here. A relayed approval is not approval, so this session held and prepared everything instead; they wrote directly minutes later and the release went out at once. The fork held the same line in reverse, refusing to create the tag on a relay from here — permission holds only where it was given, and that cuts both ways.

**How to apply:** treat the create grant as spent on 1.3 and ask again for the next release. Expect them to authorize once, name the version and leave with «доделайте сами»: then finish end to end with the fork by message and do not come back with questions. When they approve a release in one session, tell the other as information, not as its authorization, and expect the same back — asking them in both places costs them one sentence. Holding cost nothing in 1.3: the archive, the suite run on the release commit and the notes were all ready when their word came.
