"""Bootstrap for normal Python and the isolated built-in ResolvePython."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from resolve_mvsep.cli import main


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parents[1]))
