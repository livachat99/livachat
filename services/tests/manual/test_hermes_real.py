"""Manual smoke test: HermesAdapter ↔ real Hermes Dashboard at 127.0.0.1:9119.

Usage:
    cd services
    python tests/manual/test_hermes_real.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from agent_relay.adapters.base import AgentContext
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import HermesAdapterConfig
from shared.config.loader import _load_dotenv

_load_dotenv()
TOKEN = os.environ.get("HERMES_AUTH_TOKEN", "")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_hermes_real")


async def main():
    cfg = HermesAdapterConfig(
        base_url="http://127.0.0.1:9119",
        api_key=TOKEN,
        timeout_s=30,
        max_retries=0,
    )

    adapter = HermesAdapter(cfg)

    # 1. Connect
    logger.info("=== Step 1: connect ===")
    try:
        await adapter.connect()
        logger.info("Connected successfully!")
    except Exception:
        logger.exception("Connect failed")
        return

    # 2. Send a test message
    logger.info("=== Step 2: ask ===")
    ctx = AgentContext(
        session_id="manual-test-sess",
        device_id="manual-test-dev",
        language="en",
    )

    try:
        chunks = []
        async for chunk in adapter.ask("Hello! What day is today?", ctx):
            chunks.append(chunk)
            logger.info(f"Chunk: text={chunk.text!r}, is_final={chunk.is_final}")

        if not chunks:
            logger.warning("No chunks received!")
        elif not chunks[-1].is_final:
            logger.warning("Last chunk is not final!")
        else:
            logger.info(f"Full response: {chunks[-1].text}")
    except Exception:
        logger.exception("Ask failed")

    # 3. Second turn to verify session persistence
    logger.info("=== Step 3: second ask (same session) ===")
    ctx2 = AgentContext(
        session_id="manual-test-sess",
        device_id="manual-test-dev",
        language="en",
    )

    try:
        chunks2 = []
        async for chunk in adapter.ask("What did I just ask you?", ctx2):
            chunks2.append(chunk)
            logger.info(f"Chunk2: text={chunk.text!r}, is_final={chunk.is_final}")

        if chunks2:
            logger.info(f"Full response 2: {chunks2[-1].text}")
    except Exception:
        logger.exception("Second ask failed")

    # 4. Cleanup
    logger.info("=== Step 4: close ===")
    await adapter.close()
    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
