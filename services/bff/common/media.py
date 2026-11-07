from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from bff.common.deps import get_principal, get_store
from bff.common.store import BffError, LvdataStore

router = APIRouter()

MEDIA_TYPES = {
    "audio": "audio/ogg",
    "video": "image/jpeg",
    "image": "image/jpeg",
}
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


@router.get("/media/{device}/{session}/{kind}/{filename}")
def media(
    device: str,
    session: str,
    kind: str,
    filename: str,
    principal=Depends(get_principal),
    store=Depends(get_store),
    format: str | None = Query(default=None, pattern=r"^(ogg|m4a)$"),
):
    # def 端点：starlette 自动进线程池，避免同步 ffmpeg 阻塞事件循环（WS 链路）
    del principal  # §5.5 强制条款已兑现：media 经 get_principal 纳入同一 token 鉴权
    path = store.resolve_media(device, session, kind, filename)
    headers = {"Cache-Control": IMMUTABLE_CACHE}

    if format == "m4a" and kind == "audio":
        path = _transcode_m4a(store, path, filename)
        media_type = "audio/mp4"
    else:
        media_type = MEDIA_TYPES.get(kind, "application/octet-stream")

    # 不传 filename：避免 Content-Disposition: attachment 破坏 <audio> 内嵌播放
    return FileResponse(path, media_type=media_type, headers=headers)


def _transcode_m4a(store: LvdataStore, src: Path, filename: str) -> Path:
    cache_dir = Path(store.data_root) / "_cache" / "m4a"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(src).encode()).hexdigest()[:16]
    out = cache_dir / f"{digest}-{filename.rsplit('.', 1)[0]}.m4a"
    if out.exists() and out.stat().st_size > 0:
        return out

    tmp = out.with_suffix(".tmp.m4a")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        tmp.replace(out)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        tmp.unlink(missing_ok=True)
        raise BffError("internal", "audio transcoding failed")
    return out
