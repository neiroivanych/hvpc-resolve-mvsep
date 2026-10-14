"""Wire-independent request/result values. No credentials in these objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    VOCALS_INSTRUMENTAL = "vocals_instrumental"
    SPEECH_MUSIC_SFX = "speech_music_sfx"


# Starting profiles, not a restriction on the per-job choice of algorithm.
# Official /full_api, verified 2026-10-08: BS Roformer=40, BandIt v2=45.
DEFAULT_ALGORITHMS = {Mode.VOCALS_INSTRUMENTAL: 40, Mode.SPEECH_MUSIC_SFX: 45}

# Preparation can precede the documented separation phases, particularly for
# video inputs. Other nonempty server statuses remain pending, never successful.
PREPARATION_STATUSES = frozenset({'preparing', 'converting', 'conversion', 'preprocessing'})
SEPARATION_STATUSES = frozenset({'waiting', 'processing', 'distributing', 'merging'})


@dataclass(frozen=True)
class SeparationSpec:
    mode: Mode
    sep_type: int | None
    preset_id: int | None = None
    api_options: dict[str, str] = field(default_factory=dict)
    output_format: int = 1  # documented WAV, uncompressed 16-bit

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise ValueError("Unknown logical separation mode.")
        if (self.sep_type is None) == (self.preset_id is None):
            raise ValueError("Specify exactly one of sep_type and preset_id.")
        for value in (self.sep_type, self.preset_id):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Algorithm/preset IDs must be non-negative integers.")
        if type(self.output_format) is not int or self.output_format not in range(6):
            raise ValueError("output_format must be an integer from 0 to 5.")
        if not isinstance(self.api_options, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r"add_opt[1-9][0-9]*", key)
            or not isinstance(value, str) or len(value) > 4096
            for key, value in self.api_options.items()
        ):
            raise ValueError("api_options accepts only add_optN keys with string values.")
        if self.preset_id is not None and self.api_options:
            raise ValueError("A preset defines its own options; remove api_options.")

    def form_fields(self) -> dict[str, str]:
        fields = {"is_demo": "0"}  # Never publish user media on the demo page.
        if self.preset_id is not None:
            fields["preset_id"] = str(self.preset_id)
        else:
            fields.update(sep_type=str(self.sep_type), output_format=str(self.output_format))
            fields.update(self.api_options)
        return fields


@dataclass(frozen=True)
class RemoteFile:
    filename: str
    url: str = field(repr=False)


@dataclass(frozen=True)
class RemoteResult:
    status: str
    files: tuple[RemoteFile, ...] = ()
    message: str = ""
    queue_position: int | None = None
    output_format: str | None = None
    queue_count: int | None = None
    finished_chunks: int | None = None
    all_chunks: int | None = None


@dataclass(frozen=True)
class AccountAccess:
    premium_enabled: bool | None
    credits: float | None


@dataclass(frozen=True)
class DailyQuota:
    remaining: int
    maximum: int


@dataclass(frozen=True)
class QueueSummary:
    # Account-wide: this is NOT this job's time to completion.
    estimated_wait_seconds: float | None = None
    ahead: int | None = None


@dataclass(frozen=True)
class TransferProgress:
    direction: str  # upload or download; bytes refer to audio, not multipart headers
    transferred: int
    total: int | None
    file_index: int = 1
    file_count: int = 1
