from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from shared.config.models import BffConfig, RecordingConfig
from shared.timeutil import canonicalize_timezone, lvdata_db_path, resolve_tzinfo


class TestCanonicalizeTimezone:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Asia/Shanghai", "Asia/Shanghai"),
            ("America/New_York", "America/New_York"),
            ("Etc/GMT+8", "Etc/GMT+8"),  # IANA 名，原样（与 GMT+08:00 的 POSIX 反号无关）
            ("UTC", "UTC"),
            ("utc", "UTC"),
            ("gmt", "UTC"),
            ("+00:00", "UTC"),
            ("-00:00", "UTC"),
            ("GMT+08:00", "+08:00"),
            ("gmt+8", "+08:00"),
            ("8", "+08:00"),
            ("+08:00", "+08:00"),
            ("-05:30", "-05:30"),
            ("GMT-05:30", "-05:30"),
            ("8.5", "+08:30"),
            ("+14:00", "+14:00"),
        ],
    )
    def test_valid_inputs(self, raw, expected):
        assert canonicalize_timezone(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            "garbage",
            "+15:00",  # 超 IANA 极值
            "-14:01",
            "+08:60",  # 分钟 ≥ 60
            "+08:-1",
            "8.999",  # 分钟换算不精确
            "GMT+08:60",
            "1.5.2",
            "+8:30x",
            "inf",  # 非有限浮点（int(inf) 抛 OverflowError 击穿契约）
            "+inf",
            "-inf",
            "1e400",  # float 解析为 inf
            "nan",
            "GMT+inf",
        ],
    )
    def test_invalid_inputs(self, raw):
        assert canonicalize_timezone(raw) is None


class TestResolveTzinfo:
    def test_iana(self):
        assert isinstance(resolve_tzinfo("Asia/Shanghai"), ZoneInfo)

    def test_utc(self):
        assert resolve_tzinfo("UTC") is UTC

    def test_fixed_offset_positive(self):
        tz = resolve_tzinfo("+08:00")
        assert tz.utcoffset(None) == timedelta(hours=8)

    def test_fixed_offset_negative(self):
        tz = resolve_tzinfo("-05:30")
        assert tz.utcoffset(None) == -timedelta(hours=5, minutes=30)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            resolve_tzinfo("garbage")

    def test_fixed_offset_same_semantics_as_lvdata(self):
        """固定偏移规范形与 IANA 的 utcoffset 语义一致（§5.1 不变式）。"""
        shanghai = datetime(2024, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai")).utcoffset()
        assert resolve_tzinfo("+08:00").utcoffset(None) == shanghai


class TestLvdataDbPath:
    def test_explicit_path_wins(self):
        bff = BffConfig(lvdata_db_path="/custom/lv.db", data_root="/custom")
        recording = RecordingConfig(base_dir="/fallback")
        assert lvdata_db_path(bff, recording) == "/custom/lv.db"

    def test_default_uses_data_root(self):
        bff = BffConfig(data_root="/data")
        recording = RecordingConfig(base_dir="/fallback")
        assert lvdata_db_path(bff, recording) == "/data/lvdata_inventory.db"

    def test_default_falls_back_to_recording_base_dir(self):
        bff = BffConfig()
        recording = RecordingConfig(base_dir="/opt/lvdata")
        assert lvdata_db_path(bff, recording) == "/opt/lvdata/lvdata_inventory.db"
