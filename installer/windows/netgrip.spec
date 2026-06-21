# PyInstaller spec for the NetGrip Windows build (PyInstaller 6.x).
#
# Produces a one-folder app (dist/NetGrip/) that Inno Setup then wraps into a
# setup.exe. Run from the repo root:
#
#     pyinstaller installer/windows/netgrip.spec
#
# PyInstaller's bundled PySide6 hook pulls in the Qt runtime and platform
# plugins automatically. We only KEEP what NetGrip imports (QtCore, QtGui,
# QtWidgets and QtSvg — the SVG glyphs in ui/glyphs.py) and exclude the large
# Qt modules it never touches, to keep the installer small.

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent  # installer/windows -> repo root
ICON = str(ROOT / "data" / "icons" / "netgrip.ico")

# Heavy Qt modules NetGrip doesn't use. QtSvg is deliberately NOT here.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
]

a = Analysis(
    [str(ROOT / "installer" / "windows" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NetGrip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NetGrip",
)
