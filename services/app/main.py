from app.application import create_app

app = create_app()


def main() -> None:
    import os
    from typing import Any

    import uvicorn

    from shared.config.loader import load_config, resolve_path
    from shared.config.ssl import build_ssl_kwargs

    cfg = load_config()
    reload = os.environ.get("LIVACHAT_SERVER_RELOAD", "1").lower() not in ("0", "false", "no")
    kwargs: dict[str, Any] = {}
    if reload:
        # uvicorn reload 默认监听 CWD，任意目录启动不可靠（install_design §4.3）→ 显式锚定 services/
        kwargs["reload_dirs"] = [str(resolve_path("services"))]
    uvicorn.run(
        "app.main:app" if reload else app,  # reload 模式必须传 import string
        host=cfg.server.host,
        port=cfg.server.port,
        reload=reload,
        **kwargs,
        **build_ssl_kwargs(cfg.server.ssl),
    )


if __name__ == "__main__":
    main()
