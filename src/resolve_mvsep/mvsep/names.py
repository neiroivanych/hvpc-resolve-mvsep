"""Source-based output names. Roles come from server labels, never file order."""

from __future__ import annotations

import re
import os
from pathlib import Path

from .errors import MVSEPError


def name_units(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


def name_prefix(value: str, limit: int) -> str:
    return value.encode('utf-16-le')[:max(0, limit) * 2].decode('utf-16-le', errors='ignore')


def destination_name_limit(directory: Path) -> int:
    # Leave room for __copy9999 and .settings.json below Windows MAX_PATH.
    length = name_units(str(directory.resolve()))
    limit = min(150, 235 - length - 1) if os.name == 'nt' else 150
    if limit < 30:
        raise MVSEPError('Output path is too long. Choose a shorter results folder.')
    return limit

# Longer compound labels precede their suffixes. Unknown outputs retain their
# server label instead of being guessed to be vocals/instrumental by list order.
ROLES = (
    "backing_vocals", "lead_vocals", "background_vocals", "electric_guitar", "acoustic_guitar",
    "no_vocals", "no_drums", "no_bass", "no_music", "no_reverb", "dry_vocals",
    "instrumental", "instruments", "instrum", "vocals", "vocal", "others", "other", "drums", "bass",
    "guitar", "piano", "keys", "strings", "brass", "woodwinds", "synth", "percussion",
    "speech", "dialogue", "dialog", "music", "effects", "sfx", "noise", "denoised",
    "reverb", "dry", "wet", "kick", "snare", "cymbals", "toms", "hihat", "hi_hat",
)


def safe_name(name: str, max_length: int = 150) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "stem.bin"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", cleaned):
        cleaned = "_" + cleaned
    if name_units(cleaned) > max_length:
        suffix = name_prefix(Path(cleaned).suffix, 12)
        cleaned = name_prefix(cleaned, max_length - name_units(suffix)) + suffix
    return cleaned


def output_name(source_stem: str, remote_name: str, *, preset_output: bool = False,
                run_index: int | None = None, max_length: int = 150) -> str:
    if run_index is not None and (type(run_index) is not int or run_index < 1):
        raise ValueError("run_index must be a positive integer.")
    attempt = f"_{run_index:03d}" if run_index is not None else ""
    remote = safe_name(remote_name)
    suffix = name_prefix(Path(remote).suffix, 12)
    label = remote[:-len(suffix)] if suffix else remote
    if not preset_output:
        lowered = label.lower()
        for role in ROLES:
            if lowered == role or lowered.endswith("_" + role):
                label = label[-len(role):]
                break
        else:
            # Some models expose detailed stem names such as vocals-fullness-level-1.
            detail = re.search(r"(?:^|_)((?:vocals|instrum)-fullness-[\w-]+)$", label, re.IGNORECASE)
            if detail:
                label = detail.group(1)
            elif label.startswith("source_"):
                label = label[len("source_"):]
    label = name_prefix(safe_name(label), min(70, max_length // 2))
    base = safe_name(source_stem)
    base = name_prefix(base, max(1, max_length - name_units(label + suffix + attempt) - 1)).rstrip(" .")
    return safe_name(f"{base}_{label}{attempt}{suffix}")
