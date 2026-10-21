# Experimental native custom backend host for Caelestia PR #1703

This is a **shell-side compatibility integration**, not a plugin component. It targets upstream `caelestia-dots/shell` pull request [#1703](https://github.com/caelestia-dots/shell/pull/1703), checked at head `4e288c54a3b039154d19fe8a2064a174eb634405`.

## Why it is needed

The upstream parser accepts `custom` entries but its shell root does not instantiate them. This patch creates a single host at `shell.qml` for enabled `custom` entry points ending in `Main.qml` and lacking `properties.slot`. Those entries predominantly start a background service (`Scope`); they must run **once**, not once per monitor.

Other `custom` entries are intentionally excluded: dashboard cards, calendar markers, overlay helpers and private `slot` targets require dedicated integration points. Also, `shell-panel` remains **unsupported** by PR #1703. The patch does not restore Loom/Protocol7/PhoneMirror/WindowBoard panel visuals.

## Isolated test procedure

1. Check out upstream shell at commit `4e288c54a3b039154d19fe8a2064a174eb634405` in a **separate, non-production directory**.
2. From that checkout, run `git apply --check /path/to/experimental-shell-custom-host.patch`, then `git apply /path/to/experimental-shell-custom-host.patch`.
3. Build upstream with Qt/Quickshell matching its README. Install/run only in a disposable user session or isolated test environment.
4. Put a copy of each plugin branch under the dev loader plugin search root, enable only the intended plugins in the **test** `plugins.json`. Check that each backend Main.qml starts once, is torn down on disable, reloads cleanly, and does not spawn duplicate processes across monitors.
5. Verify native bar/popout/quick-toggle entry points, configuration persistence, and check logs for QML import errors. **Do not** claim service readiness based on the manifest alone.
6. Roll back by reverting the patch in the test checkout and switching to the stable session. Never replace Mirai/Tachibana shell while panel hosts remain missing.

This is a proposed compatibility patch, **not runtime-tested or deployed**. Rebase/re-audit when upstream PR head changes. No production installation is modified by this repository commit.
