"""Shared prompt-hint snippets for proactive adapters (hermes / openclaw).

逐字文案是规范副本（docs/agent_relay_prompts.md / prompts_improvements.md /
webroot_feature.md）：hermes 与 openclaw 两个后端的 prompt 必须同文，改动
在此一处完成（原为 HermesAdapter 的 staticmethod，抽出后两 adapter 共用）。
"""

from __future__ import annotations

import re

# 语言 tag 安全 token（App 17 canonical code 均匹配：en / zh-Hans / zh-Hant …）。
# 客户端可控，禁止空白/引号/换行等可改写 prompt 结构的字符。
_SAFE_LANGUAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}")


def normalize_language(language: str | None) -> str:
    """语言 tag 归一：合法 token 原样透传；空/非法 → en。"""
    lang = (language or "").strip()
    return lang if _SAFE_LANGUAGE_RE.fullmatch(lang) else "en"


def language_hint(language: str, *, verbose: bool) -> str:
    """面向用户的输出语言指令（docs/agent_multilingual.md §3.2）。

    基线语言来自 `session.start.config.language`（空/未知 → en）；只有用户明确
    要求另一种语言时才覆盖基线（用户消息本身的语言不算要求）。只约束面向用户的
    文本字段，不改 JSON 字段名/action；不翻译 prompt 模板本身。
    """
    lang = normalize_language(language)
    if verbose:
        return (
            f'面向用户的输出语言:`"{lang}"`。用户消息本身使用的语言不算语言要求;'
            "只有用户明确要求使用另一种语言时(如“用中文回答”“reply in English”)"
            "才改用该语言,否则一律使用上述语言。所有面向用户的输出"
            "（message.text、job_request.summary、spec.task、job_status summary、"
            "后台任务最终 text、reactive ask 回复）按上述规则使用语言;"
            "spec.task 里的产出要求也必须使用该语言,不要要求最终结果改用其他语言;"
            "JSON 字段名和 action 值保持英文不变。"
        )
    return (f'面向用户输出语言:`"{lang}"`(用户消息本身的语言不算要求;'
            "仅当用户明确要求使用其他语言时才改用该语言;spec.task 的产出要求也用该语言);"
            "JSON 结构/字段名不变。")


def timezone_hint(timezone: str, *, verbose: bool) -> str:
    """时区提示行（docs/agent_relay_prompts.md §3.3 逐字文案；未上报 → 显式告知）。

    `verbose`：mission 版带完整使用说明（"今天/昨天"、日界、MCP 入参）；background 版一行即可。
    """
    if timezone:
        if verbose:
            return (f"本设备时区:`{timezone}`。判断\"今天/昨天\"、换算本地日界、"
                    f"以及调用 `lvdata` MCP 工具时,把 `timezone` 参数传这个值"
                    f"(不传会按服务端配置时区解释)。")
        return f"本设备时区:`{timezone}`(调 `lvdata` MCP 工具时把 `timezone` 传该值)。"
    if verbose:
        return ("本设备未上报时区;调用 `lvdata` MCP 时省略 `timezone` 参数,"
                "由服务端按其配置时区处理。")
    return "本设备未上报时区(调 MCP 时省略 `timezone`,按服务端配置时区)。"


def agent_name_hint(name: str, *, verbose: bool) -> str:
    """名字提示（docs/prompts_improvements.md §3.2 逐字文案；未配置 → 整段/整行消失）。

    `verbose`：mission 版含点名语义 + LivaChat 交接边界（审查修正：交接不改变判定标准、
    不得重复派生——LivaChat 的回复行只会随下一次 user 行批量进入 nudge）；background 版一行。
    """
    if not name:
        return ""
    if verbose:
        return (
            "# 你的名字\n"
            "\n"
            f"你叫「{name}」——用户会用这个名字称呼你,无需自我介绍。用户在对话里呼唤这个名字"
            f"(如「{name},帮我查下…」)\n"
            "就是在直接给你下指令:此时必须给出响应——需要工具 / 检索 / 历史记忆的用 job_request 派生任务\n"
            "(不要凭印象直接答);能秒级回答的用 message。用户只是顺口提到这个名字、并非对你说话时,按普通素材处理。\n"
            "LivaChat(实时语音助手,负责与用户的实时互动、回复会被朗读)可能在回复里建议「交给"
            f"{name}」——\n"
            "这只是它的对话措辞,不改变你的判定标准,也不要因此重复派生已处理过的请求。"
            "你的回复以文字上屏(不朗读)。"
        )
    return f"你的名字:「{name}」(面向用户的成品文案直接作答即可,无需自我介绍)。"


def webroot_hint(webroot_dir: str, *, verbose: bool) -> str:
    """Web 页面提示（docs/webroot_feature.md §3.3 逐字文案；未启用 → 整段消失）。

    verbose：mission 版只给能力声明 + 触发场景；background 版为完整执行规范
    （D6：主 agent 不知道能力就派不出正确任务，执行细节只在任务书里）。
    """
    if not webroot_dir:
        return ""
    if verbose:
        return (
            "# Web 页面(复杂 / 可交互内容)\n"
            "\n"
            "用户对话中明确提到使用「网页方式呈现」,或者用户的需求超出文字/图片能承载的形式(图表、看板、\n"
            "可操作的界面、需要来回交互的小工具等),在 job_request 的 `spec.task` 里明确要求\"产出一个 Web 页面\",\n"
            "说明页面要展示与交互的内容。\n"
            "后台任务会把页面放进服务器可直接访问的目录,并在结果里附上可点击的链接;你无需关心部署细节。"
        )
    return (
        "# Web 页面(复杂 / 可交互内容)\n"
        "\n"
        "当任务适合用网页呈现(图表、看板、可操作界面、多步交互等),产出一个 Web 页面:\n"
        "\n"
        f"- 新建独立目录:`{webroot_dir}/<uuidv7>/`,每个页面一个新目录,禁止复用已有目录;\n"
        "  uuidv7 可用如下命令生成(任一等价方式亦可):\n"
        "  `python3 -c \"import time,uuid,os;ts=int(time.time()*1000).to_bytes(6,'big');"
        "r=os.urandom(10);a=((int.from_bytes(r[:2],'big')&0x0FFF)|0x7000).to_bytes(2,'big');"
        "b=((int.from_bytes(r[2:8],'big')&0x3FFFFFFFFFFFFFFF)|0x8000000000000000).to_bytes(8,'big');"
        "print(uuid.UUID(bytes=ts+a+b))\"`\n"
        "- 入口文件 `index.html`;所有资源放进该目录,引用一律用**相对路径**(页面挂在子路径下,\n"
        "  以 `/` 开头的绝对路径会 404)。\n"
        "- 技术栈按需求选最佳实践:默认零构建的 HTML/CSS/JS;需要图表/组件库时把库文件放进目录内(自包含),\n"
        "  复杂交互应用可先本地构建、最终产物必须是该目录内的纯静态文件。\n"
        "- 页面在手机端 WebView 打开(已启用 JS 与 DOM storage、忽略自签证书)与桌面浏览器都要可用。\n"
        "- 验收(强制门禁):页面**必须经 livachat server 的实际访问路径验证通过后,才允许把链接写进结果**——\n"
        "  用 `playwright-dev` MCP 工具(该 server 已配置 --ignore-https-errors,自签证书可直接打开)\n"
        "  访问 `https://127.0.0.1:8080/webroot/<uuid>/`(这是客户端 WebView 将打开的同一地址),\n"
        "  验证渲染与主要交互。不要用其他 playwright server(未忽略证书,会报 ERR_CERT_AUTHORITY_INVALID)。\n"
        "  验证未通过 → 修复后重新验证;最终仍未通过 → 结果**不得**包含 livachat_url 链接(如实说明未能完成即可)。\n"
        "- 结果文案中的链接用 markdown 链接格式、放在开头前几行(结果有 16KB 截断):\n"
        "  `[页面标题](livachat_url://webroot/<uuid>/)`——原样使用这个内部链接,客户端会转成实际地址;\n"
        "  不要写 http(s) 绝对地址。"
    )
