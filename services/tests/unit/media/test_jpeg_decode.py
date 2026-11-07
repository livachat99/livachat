from __future__ import annotations

import io
import random

import pytest
from PIL import Image, UnidentifiedImageError

from media.jpeg import decode_jpeg, image_resize, to_data_url


def _make_jpeg(width: int = 640, height: int = 480) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _noise_jpeg(width: int, height: int, quality: int = 90) -> bytes:
    """随机噪声图：不可压缩，最坏体积样本（image_resize 预算用例用）。"""
    data = random.Random(7).randbytes(width * height * 3)
    img = Image.frombytes("RGB", (width, height), data)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class TestJpegDecode:
    def test_decode_returns_image(self):
        data = _make_jpeg()
        img = decode_jpeg(data)
        assert isinstance(img, Image.Image)
        assert img.size == (640, 480)

    def test_to_data_url_format(self):
        data = _make_jpeg()
        img = decode_jpeg(data)
        url = to_data_url(img)
        assert url.startswith("data:image/jpeg;base64,")
        # Should contain base64 data
        assert len(url) > 50

    def test_to_data_url_downscale(self):
        data = _make_jpeg(1920, 1080)
        img = decode_jpeg(data)
        url = to_data_url(img, max_long_side=512)
        # Re-decode to check dimensions
        import base64
        import re

        b64 = re.sub(r"^data:image/jpeg;base64,", "", url)
        decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
        long_side = max(decoded.size)
        assert long_side == 512

    def test_to_data_url_no_downscale_when_small(self):
        data = _make_jpeg(320, 240)
        img = decode_jpeg(data)
        url = to_data_url(img, max_long_side=1024)
        import base64
        import re

        b64 = re.sub(r"^data:image/jpeg;base64,", "", url)
        decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert decoded.size == (320, 240)


class TestImageResize:
    """docs/fd_pipeline_avdetector.md §10 条目 28：单档纯缩放契约。"""

    def test_downscale_long_edge_keeps_aspect(self):
        out = image_resize(_noise_jpeg(1280, 960), max_edge=960)
        assert out is not None
        assert Image.open(io.BytesIO(out)).size == (960, 720)

    def test_portrait_long_edge_downscaled(self):
        out = image_resize(_noise_jpeg(960, 1280), max_edge=960)
        assert out is not None
        assert Image.open(io.BytesIO(out)).size == (720, 960)

    def test_no_upscale_when_smaller(self):
        out = image_resize(_make_jpeg(320, 240), max_edge=960)
        assert out is not None
        assert Image.open(io.BytesIO(out)).size == (320, 240)

    def test_over_budget_returns_none(self):
        # 该档不可达 → None（非异常；调用方决定换档/跳过）
        assert image_resize(_noise_jpeg(1280, 960), max_edge=960, max_bytes=10_000) is None

    def test_within_budget_returns_bytes(self):
        out = image_resize(_noise_jpeg(1280, 960), max_edge=960, max_bytes=400_000)
        assert out is not None
        assert len(out) <= 400_000

    def test_decode_failure_raises(self):
        with pytest.raises(UnidentifiedImageError):
            image_resize(b"not a jpeg", max_edge=960)
