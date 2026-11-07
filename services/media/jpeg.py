from __future__ import annotations

import base64
import io

from PIL import Image


def decode_jpeg(data: bytes) -> Image.Image:
    """Decode JPEG bytes to a Pillow :class:`~PIL.Image.Image`."""
    return Image.open(io.BytesIO(data))


def image_resize(
    jpeg: bytes,
    *,
    max_edge: int,
    max_bytes: int | None = None,
    quality: int = 80,
) -> bytes | None:
    """Downscale *jpeg* so its longest edge is at most *max_edge* and re-encode.

    Only downscales (never upscales); keeps aspect ratio (LANCZOS). Decode/encode
    failures propagate (Pillow). With *max_bytes*, returns ``None`` when the
    re-encoded result still exceeds it ("rung unreachable") — the caller picks a
    smaller edge or skips the frame (docs/fd_pipeline_avdetector.md §10 条目 28).
    """
    img = decode_jpeg(jpeg)
    img.load()  # 懒解码提前到此处：坏图按契约在此抛异常
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)  # type: ignore[attr-defined]
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    out = buf.getvalue()
    if max_bytes is not None and len(out) > max_bytes:
        return None
    return out


def to_data_url(img: Image.Image, *, max_long_side: int = 1024, quality: int = 70, mime: str = "image/jpeg") -> str:
    """Convert *img* to a data URL suitable for LLM Vision injection.

    If the longer side exceeds *max_long_side*, the image is proportionally
    downscaled before encoding.
    """
    w, h = img.size
    scale = min(1.0, max_long_side / max(w, h))
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)  # type: ignore[attr-defined]

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{mime};base64,{b64}"
