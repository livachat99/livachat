#!/usr/bin/env python3
"""Real API test for LLM providers via OpenAI-compatible streaming.

Usage::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_llm_real.py "你好，请用一句话介绍自己"

    # Custom model
    python scripts/test_llm_real.py "What is 2+2?" --model qwen3.8-max-0902

    # Multi-turn conversation
    python scripts/test_llm_real.py --messages '[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"},{"role":"user","content":"what is AI?"}]'

    # 默认即走业务空间专属域名（与 qwen3.8 系列配套）；--workspace-id 只补
    # X-DashScope-WorkSpace 请求头（本账号实测缺头亦可连，头按官方推荐路径保留）。
    # 调公共域名或其它端点时用 --base-url 覆盖
    python scripts/test_llm_real.py "你好"
    python scripts/test_llm_real.py "你好" --workspace-id llm-1tmsqifo51cwfadr
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))


async def run_test(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    extra_body: dict | None = None,
    workspace_id: str | None = None,
) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers={"X-DashScope-WorkSpace": workspace_id} if workspace_id else None,
    )

    print(f"Model:       {model}")
    print(f"Base URL:    {base_url}")
    if workspace_id:
        print(f"WorkSpace:   {workspace_id}  (X-DashScope-WorkSpace)")
    print(f"Messages:    {len(messages)} turns")
    print(f"Temperature: {temperature}, max_tokens: {max_tokens}")
    if extra_body:
        print(f"Extra body:  {extra_body}")
    print()

    oai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    t0 = time.monotonic()
    stream = await client.chat.completions.create(
        model=model,
        messages=oai_messages,
        stream=True,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )

    first = True
    total_chars = 0
    total_tokens = 0
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            if first:
                t_first = time.monotonic()
                first = False
            sys.stdout.write(delta.content)
            sys.stdout.flush()
            total_chars += len(delta.content)
        total_tokens += 1

    t_end = time.monotonic()

    print(f"\n\n{'='*60}")
    if not first:
        print(f"First-token latency: {t_first - t0:.3f}s")
    print(f"Total time:          {t_end - t0:.3f}s")
    print(f"Total chars:         {total_chars}")
    print(f"Chunks received:     {total_tokens}")
    if not first and (t_end - t_first) > 0:
        print(f"Tokens/sec:          {total_chars / (t_end - t_first):.0f} chars/s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Real API test for LLM streaming")
    parser.add_argument("prompt", nargs="?", default="你好，请用一句话介绍自己。",
                        help="User prompt text")
    parser.add_argument("--model", default="qwen3.8-max-0902",
                        help="Model name (default: qwen3.8-max-0902)")
    parser.add_argument("--base-url", default="https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                        help="OpenAI-compatible base URL（默认业务空间专属域名，与默认模型配套；"
                             "公共域名覆盖：https://dashscope.aliyuncs.com/compatible-mode/v1）")
    parser.add_argument("--workspace-id", default=None,
                        help="DashScope workspace id: 仅附加 X-DashScope-WorkSpace 请求头。"
                             "须配合 --base-url 指向 workspace 专属域名"
                             "(https://{id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1)")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--system", default="你是一个有帮助的AI助手。",
                        help="System prompt")
    parser.add_argument("--messages", help="JSON messages array (overrides --prompt/--system)")
    parser.add_argument("--stream", action="store_true", default=True,
                        help="Use streaming (default: true)")
    parser.add_argument("--extra-body", default=None,
                        help='JSON dict for extra_body (e.g. \'{"enable_thinking": false}\')')

    args = parser.parse_args()

    if args.workspace_id and "maas.aliyuncs.com" not in args.base_url:
        print(
            "ERROR: --workspace-id 只附加 X-DashScope-WorkSpace 头；业务空间模型还必须把 "
            "--base-url 指向 workspace 专属域名（https://{id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1）。",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    extra_body = None
    if args.extra_body:
        import json
        extra_body = json.loads(args.extra_body)

    if args.messages:
        import json
        messages = json.loads(args.messages)
    else:
        messages = [
            {"role": "system", "content": args.system},
            {"role": "user", "content": args.prompt},
        ]

    asyncio.run(run_test(
        base_url=args.base_url,
        api_key=api_key,
        model=args.model,
        messages=messages,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        extra_body=extra_body,
        workspace_id=args.workspace_id,
    ))


if __name__ == "__main__":
    main()
