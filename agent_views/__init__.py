"""Keep the existing Python module name usable from the standalone OneWork root.

The application ID, stored data and installed hook module names are intentionally
unchanged. Source packages live beside this small compatibility entry point.
"""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent)]
