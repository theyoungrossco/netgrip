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

### Insider build (just the exe, no release)

To get a test installer off any branch without cutting a release, run the
[`Windows insider build`](../../.github/workflows/windows-insider.yml) workflow
from the **Actions** tab ("Run workflow" → pick the branch). It builds **only**
the `setup.exe` — no unit-test gate, no Linux dist, no GitHub Release — and
uploads it as the `windows-insider-installer` artifact (kept 14 days). The exe is
stamped with an insider version like `NetGrip-0.3.0-insider.42-a1b2c3d-setup.exe`
so it's never confused with a real release. It shares the installer AppId with
releases, so it upgrades/replaces an installed NetGrip rather than installing
alongside it.

> `workflow_dispatch` workflows only show a "Run workflow" button once the file
> is on the repo's **default branch**. So this workflow has to be on `main` to be
> launchable, even though you then point it at `dev`.
