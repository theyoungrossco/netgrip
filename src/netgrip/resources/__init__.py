"""Bundled runtime assets (the app icon), loaded via importlib.resources.

Kept inside the package so they ship with every install — pip/pipx/venv — and
are findable however netgrip is launched, including straight from the venv's
``bin/netgrip`` without any desktop integration. This SVG is also the source the
desktop-integration installer and the Windows ``.ico`` generator read from, so
there's a single canonical icon.
"""
