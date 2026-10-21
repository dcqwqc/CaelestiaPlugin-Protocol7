# Caelestia 32-plugin migration — Device deployment 21 October 2026

Profile: fork-compatible, not a replacement for upstream PR #1703.

| Check | Mirai | Tachibana |
|---|---:|---:|
| Live managed plugin paths switched | 32 | 32 |
| Original plugin enabled count preserved | 33 (Home included) | 25 |
| Local changes protected | 80 files | 99 files |
| Nonconflicting migration file changes | 116 | 85 |
| QML parsing | Pass | Pass |
| Shell IPC targets | Pass | Pass |
| Loom + Protocol7 debug IPC calls | Pass | Pass |
| Shell alive after reload trigger | Yes | Yes |
| Critical plugin-loader log errors (sampled) | None | None |
| Automated rollback guard | Disarmed after verification | Same |

The migrated release was assembled from all 32 development branches and overlaid over copies of device-local installation sources. Existing modified files were preserved. The active links were atomically switched. The original plugin directory entries, including pre-existing project links, remain in the backup. Home is an additional plugin managed separately. Original device settings and local shell source were not replaced.

## Recovering an earlier version

Rollback script (run separately on the affected device):

    python3 ~/.local/state/caelestia/native-plugin-migration/device_deploy_fork.py rollback

The saved transaction, release snapshot and original plugin directories are under:

    ~/.local/state/caelestia/native-plugin-migration/

After rollback, trigger a normal shell reload. Do not delete backup or source trees until all required UI/hardware flows are independently verified. The rollback code stops if an unexpected live path blocks restoration.

## Remaining validation and source maintenance

- The upstream PR #1703 is not yet a functional replacement for existing private panel/slot hosts. The deployed profile retained the established fork's panel integration.
- Post-deploy Python suites passed on both devices for Gestures, WorkspaceGrid, AgentUsage and AIWorkspaces. Mirai TabletMode passed 49 tests. Devices static UI/VPN tests can fail on locally preserved UI code and some tests do not exist in the old backups. Loom web-worker test errors were reproduced in the original backups. Full Mirai Loom tests timed out; Tachibana TabletMode has environment-specific test imports. These are not fully resolved.
- Shell and critical IPC probes succeeded but comprehensive hardware / graphical UI smoke testing is incomplete. No claim is made that all 32 plugins passed full end-to-end workflows.
- The managed deployment links point to immutable-intent release copies, not to previous local developer checkout paths. Changes to former project working trees will not automatically reach the active shell.
- Most release copies appear modified in Git because the local edits were intentionally retained. The existing auto-updater is expected to skip modified repositories. Normal automatic updates should resume only after those diffs are separately reconciled in reviewed branches without leaking secrets or overwriting per-device changes.
