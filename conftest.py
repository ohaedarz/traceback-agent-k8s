"""Root pytest configuration.

Ensures the package under ``src/`` is importable when it has not been
installed (e.g. running pytest from a bare checkout without ``pip install -e .``).
When the package *is* installed normally, this is a no-op.
"""

import importlib.util
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "src")
if _SRC not in sys.path and importlib.util.find_spec("kubetraceback") is None:
    sys.path.insert(0, _SRC)
