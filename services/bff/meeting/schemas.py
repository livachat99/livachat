"""join_meeting 请求模型（docs/livachat_meeting.md §6.2）。"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

# `\d` in Python also matches non-ASCII decimal digits; only [0-9] is a meeting number.
_MEETING_NUMBER_RE = re.compile(r"^[0-9]{8}$")


def normalize_meeting_number(raw: str) -> str:
    """NFKC 全角转半角、去空白/连字符，必须是 8 位数字。"""
    value = unicodedata.normalize("NFKC", raw).strip()
    value = re.sub(r"[\s-]+", "", value)
    if not _MEETING_NUMBER_RE.fullmatch(value):
        raise ValueError("meeting_number must be 8 digits")
    return value


class JoinMeetingRequest(BaseModel):
    """POST /api/mylife/join_meeting 请求体；strict 拒绝非字符串。"""

    model_config = ConfigDict(strict=True)

    meeting_number: str = Field(min_length=1, max_length=32)
    participant_name: str = Field(min_length=1, max_length=40)

    @field_validator("meeting_number")
    @classmethod
    def _valid_meeting_number(cls, value: str) -> str:
        return normalize_meeting_number(value)

    @field_validator("participant_name")
    @classmethod
    def _valid_participant_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("participant_name must not be empty")
        if len(name) > 40:
            raise ValueError("participant_name must be at most 40 characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            raise ValueError("participant_name must not contain control characters")
        return name
