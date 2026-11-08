---
name: mac-target-machine
description: "The Mac the RealtyWatch fork must run on — MacBook Air M1 (2020), 8 GB, macOS Sequoia 15.7.3"
metadata:
  node_type: memory
  type: project
  originSessionId: 774c9ad3-5fbe-4899-940c-9d8d2493c4c7
  modified: 2026-11-03T06:22:09.172Z
---

The macOS fork (`RealtyWatch_Mac/`) targets the user's MacBook Air M1 (2020), 8 GB RAM, macOS Sequoia 15.7.3 — confirmed from a photo of the About screen on 2026-11-03.

Why it matters: Apple Silicon, so `bin/bootstrap.sh` takes its `aarch64-apple-darwin` branch, and the GitHub `macos-15` runner is the same chip generation and the same OS version — a green run there is strong evidence the app works on the user's own Mac. Keep the workflow pinned to `macos-15` rather than `macos-latest`, which will drift to macOS 26.

Sequoia (macOS 15) also removed the Control-click "Open anyway" bypass, so a `.app` that is *sent* to someone needs System Settings → Privacy & Security, or `xattr -dr com.apple.quarantine`. Building on the target Mac avoids the quarantine flag entirely.

Related: [[version-2-deferred]]
