"""Frozen-app entry point for the Windows build.

PyInstaller needs a real script file to analyse. The installed ``netgrip``
package provides everything; this just calls into it. Kept separate from
``src/netgrip/__main__.py`` so the spec's import path is unambiguous (the
package is pip-installed into the build venv, so ``import netgrip`` resolves
from site-packages regardless of the working directory).
"""

from netgrip.app import main

raise SystemExit(main())
