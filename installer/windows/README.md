# Windows installer

Builds `NetGrip-<version>-setup.exe`: a standalone installer that bundles
Python and PySide6, so end users install nothing else. It creates a Start-Menu
entry (and an optional desktop icon) and registers a normal uninstaller.

NetGrip on Windows is an **SSH-only client** — there's no local network stack to
manage there (see the main [README](../../README.md)).

## Pieces

| File | Role |
|---|---|
| `launcher.py` | Entry point PyInstaller freezes. |
| `netgrip.spec` | PyInstaller config: one-folder app, keeps QtSvg, drops unused Qt modules. |
| `netgrip.iss` | Inno Setup script: shortcuts, uninstaller, per-user install (no UAC). |
| `../../scripts/build-windows.ps1` | Runs both steps end to end. |
| `../../data/icons/netgrip.ico` | App/shortcut icon (committed; regenerate with `scripts/make-ico.py`). |

## Build it yourself

You need a **Windows machine** — PyInstaller can't cross-compile from Linux.

1. Install [Python 3.10+](https://www.python.org/downloads/) and
   [Inno Setup 6](https://jrsoftware.org/isdl.php).
2. From the repo root:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
   ```

The installer lands in `dist\`.

## Or let CI build it

You don't need a Windows box: pushing a `vX.Y.Z` tag runs
[`.github/workflows/release.yml`](../../.github/workflows/release.yml), which
builds this installer on a `windows-latest` runner and attaches it to the GitHub
Release alongside the Linux artifacts. See
[docs/PACKAGING.md](../../docs/PACKAGING.md#cutting-a-release).
