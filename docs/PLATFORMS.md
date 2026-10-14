# Platform support and macOS port plan

This document is for contributors. User requirements and installation steps are in [Russian](../README.md) and [English](../README.en.md).

## Current support

Version 0.3.0 targets Windows with DaVinci Resolve Studio 21.1+. The installer and the complete separation/import flow have been tested on Windows with Resolve Studio 21.1.0.17. macOS is planned; Linux and the free edition of Resolve are not supported. Passing Python tests alone does not establish support for another operating system.

Use one repository and retain shared application behavior across platforms. Release assets identify their platform: `HVPC-Resolve-MVSEP-v0.3.0-Windows.zip`. A future macOS asset must have its own platform name and verified compatibility requirements. Keep existing release tags and assets immutable.

## Reusable behavior and adaptation boundaries

The current code is not fully platform independent. Preserve the tested Windows behavior while introducing these boundaries during the macOS port:

| Area | Existing implementation | Port approach |
| --- | --- | --- |
| MVSEP requests, polling, downloads and recovery | `src/resolve_mvsep/mvsep/`, job and runner modules in `core/` | Reuse the protocol and stored state; test network deadlines and recovery on macOS. |
| Catalog, presets, recent settings, translation and import rules | Modules in `core/` and `resolve/` | Share the domain behavior. Keep UI calls out of it; audit OS-specific file and process helpers. |
| Resolve connection and worker launch | `resolve/api.py`, `separation.py`, `import_results.py`, `resolve_monitor.py` | Select runtime and SDK discovery by platform, with clear failures. Preserve the independent worker/importer process model. |
| Setup and progress windows | `setup_window.py`, `progress_window.py` and Windows drawing helpers | Provide a macOS frontend using the same settings and job state. Select the frontend before importing Win32-only modules. |
| Dialogs, notifications, opening paths, icons | `file_dialog.py`, `core/feedback.py`, `tray_icon.py`, `app_icon.py` | Add macOS implementations. Use a menu-bar status item and an explicit quit action; closing a window must not silently discard a running job. |
| Installation | `installer/*.cs`, `Install.cmd`, `tools/build_installer.py` | Keep the Windows installer. Build a separate macOS installer with the same folder/token workflow and preservation/rollback guarantees. |

The Windows release does not add untested macOS dispatch or replace the working UI framework. The table defines the port work, not capabilities already implemented.

## macOS discovery and packaging

The Resolve 21.1 SDK documents its bundled macOS Python at `/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython`, and the per-user scripts directory at `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts`. Verify these against the actual supported installation and edition before implementing discovery; do not silently fall back to an arbitrary Python interpreter. Add the menu launcher under `Utility`.

Keep mutable settings, tokens, jobs, logs and downloaded media outside the signed application bundle. Preserve existing settings during updates. Do not assume that absolute file paths saved on Windows resolve on macOS, or promise that project/task histories can be copied between operating systems without path validation.

Plan Developer ID signing and Apple notarization for the public macOS application distributed through GitHub. Local development and testing can begin before enrolling in the paid developer program. Signing credentials and account data must never enter source control or release archives. See [Apple distribution guidance](https://developer.apple.com/developer-id/).

## Required macOS acceptance checks

- Record the macOS and Resolve Studio versions and processor architecture. Confirm Apple Silicon and Intel separately before claiming either as supported.
- Install into a clean writable folder, configure the token, add the menu item and update while preserving user data. Check failed-install rollback.
- Exercise Russian and English windows, long file names, resize behavior, clipboard selection, file dialogs and menu-bar restoration.
- Separate audio and MP4, recover interrupted polling/downloads and reopen the latest completed result.
- Close the window and Resolve while processing. Confirm visible completion and safe import after the original project reopens. Test sleep/wake recovery separately from merely closing Resolve.
- Import to Media Pool and new tracks; verify repeated import, moved/trimmed source clips, full source handles, muted new tracks and diagnostics for deletion, replacement or speed changes.
- Test the downloaded distribution on a clean Mac with normal macOS security settings. A successful build or CI run is not a substitute for this Resolve integration check.
