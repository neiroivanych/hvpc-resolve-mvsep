"""Terminal entry point (use the script launcher inside Resolve)."""

import sys
from pathlib import Path

# ResolvePython uses an isolated ._pth configuration and does not automatically
# include the script's directory in sys.path, unlike a normal Python install.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from resolve_mvsep.main import run


if __name__ == "__main__":
    raise SystemExit(run(Path(__file__).resolve().parents[1]))
