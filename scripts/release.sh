#!/usr/bin/env bash
#
# Cut a NetGrip release. One command to run at a milestone:
#
#   1. Bump the version in src/netgrip/__init__.py and pyproject.toml first
#      (and update CHANGELOG.md + the metainfo <releases> section).
#   2. Run this:
#
#        scripts/release.sh              # check + build Linux dist locally
#        scripts/release.sh --tag        # also create the vX.Y.Z git tag
#        scripts/release.sh --tag --push # also push it -> CI builds & publishes
#
# Pushing the tag triggers .github/workflows/release.yml, which builds the
# Linux artifacts AND the Windows setup.exe and attaches them to a GitHub
# Release. The Windows installer is built by CI (or scripts/build-windows.ps1
# on a Windows box) — it can't be cross-compiled from Linux.
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

DO_TAG=0
DO_PUSH=0
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --tag)        DO_TAG=1 ;;
        --push)       DO_TAG=1; DO_PUSH=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help)    sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            echo "unknown option: $arg (try --help)" >&2; exit 1 ;;
    esac
done

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

# Version is single-sourced in the package; keep pyproject.toml in lockstep.
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/netgrip/__init__.py)"
[ -n "$VERSION" ] || die "could not read __version__ from src/netgrip/__init__.py"
PROJ_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
[ "$VERSION" = "$PROJ_VERSION" ] \
    || die "version mismatch: __init__.py=$VERSION pyproject.toml=$PROJ_VERSION"
TAG="v$VERSION"
echo "==> Releasing NetGrip $VERSION ($TAG)"

# Prefer the project's dev venv for checks; fall back to system tools.
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
RUFF=".venv/bin/ruff"; [ -x "$RUFF" ] || RUFF="ruff"
PYTEST=".venv/bin/pytest"; [ -x "$PYTEST" ] || PYTEST="pytest"

if [ "$SKIP_TESTS" = "0" ]; then
    echo "==> Lint"; "$RUFF" check src tests
    echo "==> Tests"; "$PYTEST" -q
fi

echo "==> Building Linux sdist + wheel"
rm -rf dist
# Build in a throwaway venv so 'build' need not be installed anywhere.
BV="$(mktemp -d)"; trap 'rm -rf "$BV"' EXIT
"$PY" -m venv "$BV"
"$BV/bin/pip" install --quiet --upgrade pip build
"$BV/bin/python" -m build --outdir dist
ls -1 dist

if [ "$DO_TAG" = "1" ]; then
    if git rev-parse "$TAG" >/dev/null 2>&1; then
        echo "==> Tag $TAG already exists, leaving it"
    else
        echo "==> Tagging $TAG"
        git tag -a "$TAG" -m "NetGrip $VERSION"
    fi
fi

if [ "$DO_PUSH" = "1" ]; then
    echo "==> Pushing $(git rev-parse --abbrev-ref HEAD) and $TAG"
    git push origin HEAD
    git push origin "$TAG"
    echo "==> CI is now building the installers; watch:"
    echo "    https://github.com/theyoungrossco/netgrip/actions"
else
    echo
    echo "Built dist/ for $VERSION. Next:"
    [ "$DO_TAG" = "1" ] && echo "  git push origin HEAD && git push origin $TAG   # triggers the release build" \
                        || echo "  scripts/release.sh --tag --push                # tag + publish via CI"
fi
