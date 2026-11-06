# Working on ios-a71

- Read `NOTES.md` before starting: current state, blockers, next action. Older Redmi history is in
  `docs/sweet-history-notes.md`; search it, do not read it in full.
- The user switches agents often. Update the handoff in `NOTES.md` after each milestone and before stopping:
  commits, CI run IDs, exact build and boot results, unfinished work. Separate observations from guesses.
- Work on Windows from this folder. Do not depend on WSL. The kernel builds in GitHub Actions.
- The phone boots iOS from its `boot` partition; there is no `fastboot boot`. Logs come over USB or from TWRP.
- Test images may be written to `boot` without asking, verified by hash, while debugging. Restore image:
  `backup/2026-11-06/boot.img`. Confirm every other destructive step first. Keep bootloaders, recovery, modem,
  persist and the backups intact.
- Do not start background jobs that open windows on the user's desktop.
- Keep device dumps, serial numbers, firmware and Apple files out of git.
