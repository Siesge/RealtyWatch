---
name: handover-docs-folder
description: "The repo carries a _Docs handover folder - both sessions keep it current after every finished batch"
metadata:
  node_type: memory
  type: project
  originSessionId: 774c9ad3-5fbe-4899-940c-9d8d2493c4c7
  modified: 2026-11-08T14:34:12.000Z
---

On 8 November 2026 the owner asked for a folder another agent could pick the project up from without asking questions, and for it to be kept current. It is `_Docs/` in the repo root: created by the Windows session (`6d54b47`), extended with the fork's half the same day (`2ac5014`). Its own `README.md` carries the rules, and `Instructions/` is where a new session starts reading.

**Why:** their words were to gather everything another agent would need, and, to the fork, that it should also be in the loop, add its own information and watch that the folder stays current. So updating it is a standing duty of both sessions, not a one-off task someone finished.

**How to apply:** after each finished batch update `Instructions/Заметка о проекте.md` (what is done, where things stopped); at a release add the published body to `Релизы/`; when memory changes refresh the copies under `Knowledge/Память агента/`. Only what the repository does not already hold goes in - point at README.md, BACKLOG.md and PORTING.md rather than copying them, or the folder becomes a second, aging documentation. Nothing personal goes in, because the folder is pushed to GitHub: the owner's photo of the About screen shows the Mac's serial number, so `Inbox/README.md` records it as a line, not a file. Every file in the folder is a shared object, so [[shared-checkout-mac-fork]] applies to all of them. Related: [[release-notes-no-local-numbers]].
