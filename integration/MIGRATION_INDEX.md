# Caelestia Native Plugin Development Migration — 21 October 2026

This index tracks **32 enabled plugin repositories**. The original production entry point contracts were preserved during migration. Branch-level development work was first isolated to `feat/upstream-plugin-dev-20261021` and targets upstream [caelestia-dots/shell#1703](https://github.com/caelestia-dots/shell/pull/1703), which is still **unmerged**. The archived `caelestia-edge-plugin` repository is deliberately excluded.

## Finalized fork-compatible release — 21 October 2026

**All 32 plugin migration pull requests were merged into their repositories' default branches**, after verifying that the default `manifest.json` retained its working Caelestia fork contract. Each repository also contains `manifest.native-dev.json` for the experimental upstream API. Loom, Protocol7, PhoneMirror and WindowBoard retain their native `shell-panel` entries on the default path. GitHub verification checked all 32 merged PRs and both default-branch manifest files in each repository.

**Deployed:** Both Mirai and Tachibana are running the 32-plugin, fork-compatible, locally reconciled release with 32/32 managed paths active and original installation backups retained. Mirai has 33 enabled plugin entries including its separately managed Home plugin; Tachibana retained its 25 enabled selections at rollout; a later read shows 26 because the separately managed Home plugin was enabled. The migration itself did not change plugin enablement. Critical Loom, Protocol7, Nexus, WindowBoard and Drawers IPC checks passed on both devices after reload. The source/rollback record is in [DEPLOYMENT_20261021.md](./DEPLOYMENT_20261021.md).

**Not completed:** The official upstream shell PR #1703 still requires its missing private panel/slot hosts and comprehensive runtime tests. The live per-device overlay preserves local modifications, so automatic Git updates may skip the managed copies until those diffs are reconciled into versioned source. Do not delete existing backups or replace the running fork with upstream development based on manifest checks alone.

---

## Historical preparation and migration audit (superseded by final release status above)

## Shared integration

- [Backend custom entry-point singleton host](./experimental-shell-custom-host.patch): GitHub Actions verified that this patch applies cleanly and reverses against upstream development commit `4e288c54a3b039154d19fe8a2064a174eb634405`.
- [Real installed-plugin page preview](./nexus/PluginsPage.qml): replaces fake plugin cards with the loader's `Plugins.plugins`, shows manifest issues, and enables native enable/disable actions. GitHub Actions verified the file can replace the pinned upstream page with no diff-whitespace errors. **Not QML runtime tested.**
- [Integration test and rollback instructions](./README.md).
- [Protocol 7 merged review PR](https://github.com/dcqwqc/CaelestiaPlugin-Protocol7/pull/2): integration CI exercised and merged into the repository default branch.

## Individual plugin migration status

| Plugin | Branch state | Verification |
|---|---|---|
| [AgentUsage](https://github.com/dcqwqc/CaelestiaPlugin-AgentUsage/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [AIWorkspaces](https://github.com/dcqwqc/CaelestiaPlugin-AIWorkspaces/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [AudioBoost](https://github.com/dcqwqc/CaelestiaPlugin-AudioBoost/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [AudioDevices](https://github.com/dcqwqc/CaelestiaPlugin-AudioDevices/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [AudioHub](https://github.com/dcqwqc/CaelestiaPlugin-AudioHub/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [CaptivePortal](https://github.com/dcqwqc/CaelestiaPlugin-CaptivePortal/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Color](https://github.com/dcqwqc/CaelestiaPlugin-Color/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [DisplayScale](https://github.com/dcqwqc/CaelestiaPlugin-DisplayScale/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [EdgeSuck](https://github.com/dcqwqc/CaelestiaPlugin-EdgeSuck/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Gestures](https://github.com/dcqwqc/CaelestiaPlugin-Gestures/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Hotkeys](https://github.com/dcqwqc/CaelestiaPlugin-Hotkeys/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [IPadMode](https://github.com/dcqwqc/CaelestiaPlugin-IPadMode/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Kagami](https://github.com/dcqwqc/CaelestiaPlugin-Kagami/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [LidKeepAwake](https://github.com/dcqwqc/CaelestiaPlugin-LidKeepAwake/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Loom](https://github.com/dcqwqc/CaelestiaPlugin-Loom/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged; legacy shell panel needs adapter | Static source-file paths verified; runtime untested |
| [MediaPlayer](https://github.com/dcqwqc/CaelestiaPlugin-MediaPlayer/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [MicMute](https://github.com/dcqwqc/CaelestiaPlugin-MicMute/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [OpenRGB](https://github.com/dcqwqc/CaelestiaPlugin-OpenRGB/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [PhoneMirror](https://github.com/dcqwqc/CaelestiaPlugin-PhoneMirror/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged; legacy shell panel needs adapter | Static source-file paths verified; runtime untested |
| [PowerButton](https://github.com/dcqwqc/CaelestiaPlugin-PowerButton/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [Protocol7](https://github.com/dcqwqc/CaelestiaPlugin-Protocol7/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged; legacy shell panel needs adapter | Static source-file paths verified; runtime untested |
| [RemoteDesktop](https://github.com/dcqwqc/CaelestiaPlugin-RemoteDesktop/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [SmartClose](https://github.com/dcqwqc/CaelestiaPlugin-SmartClose/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [SpatialAudio](https://github.com/dcqwqc/CaelestiaPlugin-SpatialAudio/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [TabletMode](https://github.com/dcqwqc/CaelestiaPlugin-TabletMode/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [TapZones](https://github.com/dcqwqc/CaelestiaPlugin-TapZones/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [WallpaperEngine](https://github.com/dcqwqc/CaelestiaPlugin-WallpaperEngine/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |
| [WindowBoard](https://github.com/dcqwqc/CaelestiaPlugin-WindowBoard/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged; legacy shell panel needs adapter | Static source-file paths verified; runtime untested |
| [WorkspaceGrid](https://github.com/dcqwqc/CaelestiaPlugin-WorkspaceGrid/tree/feat/upstream-plugin-dev-20261021) | Native manifest staged | Static source-file paths verified; runtime untested |


| [BatteryOptimizer](https://github.com/dcqwqc/CaelestiaPlugin-BatteryOptimizer/tree/feat/upstream-plugin-dev-20261021) | New private repository, native manifest staged | Original main preserved; Backend.qml host adapter needed upstream |
| [PowerBank](https://github.com/dcqwqc/CaelestiaPlugin-PowerBank/tree/feat/upstream-plugin-dev-20261021) | New private repository, native manifest staged | Original main preserved; batteryTank slot host needed upstream |
| [VolumeThumbMute](https://github.com/dcqwqc/CaelestiaPlugin-VolumeThumbMute/tree/feat/upstream-plugin-dev-20261021) | New private repository, native manifest staged | Original main preserved; OSD slot host needed upstream |

## Verified release candidates — 21 October 2026

- All **32** enabled plugins now have private or existing GitHub repositories, migration branches, draft pull requests and **passing manifest CI**.
- All 32 are cloned in an isolated Philipedia workspace. Release packager at [prepare_native_release.py](./prepare_native_release.py) produces an upstream-development profile and a fork-compatible profile without touching live installations. Safety unit tests cover overwrite refusal, dirty working trees, symlink escape, duplicate plugin identity and legacy panel preservation.
- The **fork-compatible 32-plugin candidate** is staged on Mirai at `~/.cache/caelestia-native-migration/release-fork-32-20261021`, outside the live plugin loader roots. It has **109 of 109 QML files parse successfully** using Qt 6.11.2 `qmlformat --ignore-settings` in read-only mode.
- This is **not an activated deployment**. Mirai has local or newer plugin differences (including Loom, Color, TabletMode, PhoneMirror, Devices and other plugins), and its shell worktree has approximately 60 uncommitted changes. Overwriting those would violate non-destructive migration.
- Tachibana SSH remains unavailable, so its live state cannot be verified. Upstream official PR #1703 is still open/unmerged.
- Native upstream integration still lacks custom slot hosting and the legacy shell-panel host; the fork already contains those UI hooks. The two profiles must not be confused.

## Hard blockers and remaining work

1. **Native custom shell panels:** `shell-panel` is unsupported by upstream PR #1703. Loom, Protocol7, PhoneMirror, WindowBoard have `manifest.legacy.json` backups and upstream-compatible root manifests; their original panel QML source is preserved but cannot render through the upstream development API without a shell-side panel host adapter.
2. **Custom placement hooks:** `properties.slot` dashboard cards, calendar markers and specialized overlays require real host integration. The backend host intentionally does not load them into an invisible component.
3. **Plugin manager installation/update:** upstream's registry/store, install, GitHub update and auto-update paths are not yet implemented. The preview UI does not pretend they work.
4. **Dynamic tests:** isolated Quickshell/Qt build with actual plugin autostart, disable/reload, settings and device functionality, multi-monitor/fullscreen behavior, and graphics rendering.
5. **Deployment (completed after this historical audit):** a staged, rollback-protected fork-compatible release was activated and verified on Mirai and Tachibana; see the finalization section and deployment record above.

**Acceptance standard:** Each plugin must have a stable one-time initialization behavior, correct visible placement, persistent settings, verified enable/disable and reload, and verified rollback. A green manifest-only workflow is insufficient proof of this.

## Additional package security verification

The release builder now selects Git-tracked files only; locally untracked caches,
credentials, generated state, and uncommitted additions are excluded from the
candidate even if present in a developer checkout. A second regression test suite
verifies profile preservation, overwrite refusal, duplicate identity rejection,
and rejection of symlink escapes. No working plugin directory is overwritten.
Mirai staged verification passed 32/32 manifest identities, 32/32 SHA-256 checks,
and 110/110 QML syntax parses. At the time of the staging audit, the live installation had not yet been switched; it was subsequently deployed as described above.


## Disabled experimental plugin outside the enabled set

An earlier Mirai inventory contained 33 installed directories, with 32 enabled at that time. The most recent post-deployment read showed 33 enabled entries, including Home. The additional installed plugin is **dcqwqc/Home**, an independently released Home OS dashboard tab from the separate Home project. It is **not enabled**, has a valid dashboard-tab manifest and its Home.qml parsed successfully. It is not part of the 32 active CaelestiaPlugin repository migrations and must not be silently treated as missing or overwritten. Coordinate updates through its own project and release process.

The Mirai read-only preflight reports **10 modified plugin worktrees** and **63 local shell changes**. Source snapshots must not overwrite these files. At the time of this preflight Tachibana was temporarily unreachable. It subsequently reconnected and both devices were deployed and verified with their original backups retained.
