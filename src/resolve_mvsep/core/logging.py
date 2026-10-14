"""Per-run UTF-8 logs without changing Resolve's root logger."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .secrets import RedactingFormatter, Redactor


def create_logger(root: Path, *, prefix: str = "m1", secrets: tuple[str, ...] = ()) -> tuple[logging.Logger, Path]:
    directory = root / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = directory / f"{prefix}_{stamp}_{uuid4().hex[:8]}.log"
    logger = logging.getLogger(f"resolve_mvsep.{path.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = logging.FileHandler(path, encoding="utf-8")
    redactor = Redactor(*secrets)
    file_handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", redactor))
    logger.addHandler(file_handler)
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(RedactingFormatter("%(levelname)s: %(message)s", redactor))
        logger.addHandler(console)
    return logger, path


def close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
