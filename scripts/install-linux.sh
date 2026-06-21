#!/usr/bin/env bash
#
# Install NetGrip on Linux and add it to the application menu.
#
# Two halves:
#   1. Install the Python app in isolation — pipx if you have it, otherwise a
#      private venv this script manages. Either way you get a `netgrip` command.
#   2. Desktop integration: drop the .desktop launcher, icon and AppStream
#      metainfo into the right XDG directories so NetGrip shows up in your menu.
#
# Default is a per-user install under ~/.local (no root needed). Run as root, or
# pass --system, to install for everyone under /usr/local.
#
#   scripts/install-linux.sh                # this user, into ~/.local
#   sudo scripts/install-linux.sh --system  # everyone, into /usr/local
#   scripts/install-linux.sh --uninstall    # remove it again
#   scripts/install-linux.sh --desktop-only # just the menu entry (app already installed)
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="io.github.theyoungrossco.netgrip"

SCOPE="user"
ACTION="install"
INSTALL_APP=1

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --system)       SCOPE="system" ;;
        --user)         SCOPE="user" ;;
        --uninstall)    ACTION="uninstall" ;;
        --desktop-only) INSTALL_APP=0 ;;
        -h|--help)      usage 0 ;;
        *)              die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

# Running as root implies a system install unless the user forced --user.
if [ "$(id -u)" = "0" ] && [ "$SCOPE" = "user" ]; then
    SCOPE="system"
fi

if [ "$SCOPE" = "system" ]; then
    [ "$(id -u)" = "0" ] || die "--system needs root (try: sudo $0 --system)"
    PREFIX="/usr/local"
    BIN_DIR="$PREFIX/bin"
    DATA_DIR="$PREFIX/share"
    VENV_DIR="$PREFIX/lib/netgrip/venv"
else
    BIN_DIR="$HOME/.local/bin"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
    VENV_DIR="$DATA_DIR/netgrip/venv"
fi

APP_DIR="$DATA_DIR/applications"
ICON_DIR="$DATA_DIR/icons/hicolor/scalable/apps"
META_DIR="$DATA_DIR/metainfo"
DESKTOP_FILE="$APP_DIR/$APP_ID.desktop"
ICON_FILE="$ICON_DIR/$APP_ID.svg"
META_FILE="$META_DIR/$APP_ID.metainfo.xml"

refresh_caches() {
    have update-desktop-database && update-desktop-database "$APP_DIR" 2>/dev/null || true
    have gtk-update-icon-cache && gtk-update-icon-cache -qtf "$DATA_DIR/icons/hicolor" 2>/dev/null || true
}

# ----------------------------------------------------------------------------- uninstall
if [ "$ACTION" = "uninstall" ]; then
    rm -f "$DESKTOP_FILE" "$ICON_FILE" "$META_FILE"
    # Remove whichever install method put netgrip there.
    if have pipx && pipx list 2>/dev/null | grep -q 'package netgrip'; then
        pipx uninstall netgrip || true
    fi
    if [ -e "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        rmdir "$(dirname "$VENV_DIR")" 2>/dev/null || true
    fi
    # Drop our symlink only if it points back into the venv we manage.
    if [ -L "$BIN_DIR/netgrip" ] && readlink -f "$BIN_DIR/netgrip" 2>/dev/null | grep -q "${VENV_DIR}/"; then
        rm -f "$BIN_DIR/netgrip"
    fi
    refresh_caches
    echo "NetGrip removed."
    exit 0
fi

# ----------------------------------------------------------------------------- install app
NETGRIP_BIN=""
if [ "$INSTALL_APP" = "1" ]; then
    have python3 || die "python3 not found"
    if [ "$SCOPE" = "user" ] && have pipx; then
        echo "Installing NetGrip with pipx..."
        pipx install --force "$REPO"
        bindir="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"
        NETGRIP_BIN="$bindir/netgrip"
    else
        echo "Installing NetGrip into a private venv ($VENV_DIR)..."
        python3 -c 'import venv, ensurepip' 2>/dev/null \
            || die "python venv/ensurepip missing (install python3-venv)"
        mkdir -p "$(dirname "$VENV_DIR")"
        python3 -m venv "$VENV_DIR"
        "$VENV_DIR/bin/pip" install --quiet --upgrade pip wheel
        "$VENV_DIR/bin/pip" install --quiet "$REPO"
        NETGRIP_BIN="$VENV_DIR/bin/netgrip"
        mkdir -p "$BIN_DIR"
        ln -sf "$NETGRIP_BIN" "$BIN_DIR/netgrip"
    fi
else
    NETGRIP_BIN="$(command -v netgrip || true)"
    [ -n "$NETGRIP_BIN" ] || die "--desktop-only needs netgrip already on PATH"
fi
[ -x "$NETGRIP_BIN" ] || die "netgrip binary not found at: $NETGRIP_BIN"

# ----------------------------------------------------------------------------- desktop files
echo "Adding NetGrip to the application menu..."
mkdir -p "$APP_DIR" "$ICON_DIR" "$META_DIR"
install -m 0644 "$REPO/data/icons/$APP_ID.svg" "$ICON_FILE"
install -m 0644 "$REPO/data/$APP_ID.metainfo.xml" "$META_FILE"

# Point Exec at the absolute binary: desktop launchers don't always inherit the
# shell PATH, so ~/.local/bin may be invisible to them even when it's on PATH here.
sed "s|^Exec=netgrip|Exec=\"$NETGRIP_BIN\"|" "$REPO/data/$APP_ID.desktop" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"

refresh_caches

# ----------------------------------------------------------------------------- summary
echo
echo "NetGrip installed."
echo "  launch:   $NETGRIP_BIN        (or find 'NetGrip' in your app menu)"
echo "  demo:     $NETGRIP_BIN --demo (safe sandbox, runs nothing)"
if [ "$SCOPE" = "user" ] && ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
    echo
    echo "  note: $BIN_DIR is not on your PATH. The menu entry still works"
    echo "        (it uses the full path); add it to PATH to run 'netgrip' in a terminal."
fi
echo
if [ "$SCOPE" = "system" ]; then
    echo "  uninstall: sudo $0 --uninstall --system"
else
    echo "  uninstall: $0 --uninstall"
fi
