"""Make the in-repo ``src/`` packages importable without installing the project.

The project's runtime entrypoint runs from the repo root with ``src/`` on
``sys.path``; pytest uses the same convention so tests can import
``handlers...`` directly without needing the heavy runtime deps installed.
"""

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
