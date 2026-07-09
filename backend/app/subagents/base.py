"""子代理公共基类与工具函数。

提取 code/rag/web/custom agent 共享的：
- ``make_fs_tools`` / ``make_rag_tools`` / ``make_web_tools``：工具构造（公开名，去掉下划线前缀）
- ``extract_text``：流式 chunk 文本提取
- ``THINK_PROMPT_SUFFIX``：思考过程提示常量
- ``run_react_agent_stream``：标准化事件流转换

消除三个 agent 文件的重复代码，并提供给 ``deep/tools.py`` 跨包复用。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from langchain_core.tools import tool

from app.config import get_settings
from app.llm import get_chat_model
from app.utils.text import extract_chunk_text

__all__ = [
    "make_fs_tools",
    "make_rag_tools",
    "make_web_tools",
    "extract_text",
    "THINK_PROMPT_SUFFIX",
    "run_react_agent_stream",
    "build_builtin_subagent",
    "run_builtin_subagent",
]


# 子代理思考过程提示：要求模型在思考时包裹 think 标签，供前端展示 reasoning block
# 用 chr(60)/chr(62) 拼接 <> 避免在源码字符串中直接出现标签导致渲染层歧义
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)
THINK_PROMPT_SUFFIX = (
    "\n\n在调用工具前，请先用 " + _THINK_OPEN + ".." + _THINK_CLOSE + " 标签包裹你的思考过程，"
    "例如：" + _THINK_OPEN + "我需要查看相关信息来回答这个问题" + _THINK_CLOSE + "。"
    "这样用户可以看到你的推理过程。"
    "\n\n重要：思考标签外不要输出任何可见文本。所有可见内容必须在工具调用完成后，"
    "根据工具返回结果再输出。"
)


def make_fs_tools(thread_id: str, workspace_path: str | None = None) -> list:
    """构建绑定 ``thread_id`` 的文件系统工具列表（仅只读工具）。

    filesystem 工具的签名含 ``thread_id``（用于沙箱授权校验），该参数不应暴露给
    LLM。这里通过闭包绑定 ``thread_id``，对外只声明业务参数。

    安全约束：subagent 不返回 write_file / edit_file，避免绕过 DeepAgent 审批流。

    ``workspace_path`` 用于沙箱授权时解析相对路径的基准，避免误解析到 PROJECT_ROOT。

    工具启用由 ``get_settings().tools_enabled`` 过滤；工具内部名与配置 key 的
    映射：``glob_files``→``glob``、``grep_files``→``grep``。
    """
    from app.tools import filesystem as fs

    @tool
    async def read_file(path: str) -> str:
        """读取文本文件内容。"""
        return await fs.read_file(thread_id, path, base=workspace_path)

    @tool
    async def list_dir(path: str) -> list[str]:
        """列出目录下的条目名称（不含路径前缀）。"""
        return await fs.list_dir(thread_id, path, base=workspace_path)

    @tool
    async def glob_files(pattern: str) -> list[str]:
        """glob 匹配文件路径，pattern 形如 ``d:/docs/**/*.md``。"""
        return await fs.glob(thread_id, pattern, base=workspace_path)

    @tool
    async def grep_files(pattern: str, path: str) -> list[str]:
        """在 path 目录下递归搜索匹配 pattern（正则）的行。"""
        return await fs.grep(thread_id, pattern, path, base=workspace_path)

    tools = [read_file, list_dir, glob_files, grep_files]
    # 工具内部函数名 → tools_enabled 配置 key 的映射
    tool_name_map = {
        "read_file": "read_file",
        "list_dir": "list_dir",
        "glob_files": "glob",
        "grep_files": "grep",
    }
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(tool_name_map.get(t.name, t.name), True)]


def make_git_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的 Git 工具列表（只读 + 写操作）。

    安全约束：
    - 子代理可暴露只读 Git 工具（status/diff/log/branches）。
    - 写操作 Git 工具（clone/pull/checkout/stage/commit）在 DeepAgent 中配合
      ``interrupt_before`` 审批流暴露；子代理通过 ``_sanitize_custom_tools``
      过滤掉危险工具，因此本函数即使返回写工具也不会被子代理实际绑定。

    工具启用由 ``get_settings().tools_enabled`` 过滤；函数名与配置 key 一致。
    """

    @tool
    async def git_status(repo_path: str) -> str:
        """获取 git 仓库工作区状态。"""
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain=v1", "-b",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git status 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace")

    @tool
    async def git_diff(repo_path: str, file: str | None = None) -> str:
        """获取工作区与暂存区差异；file 为空时返回整个仓库 diff。"""
        args = ["git", "diff"]
        if file:
            args.extend(["--", file])
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git diff 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or "(无差异)"

    @tool
    async def git_log(repo_path: str, limit: int = 20) -> str:
        """获取最近 N 条提交日志（默认 20）。"""
        proc = await asyncio.create_subprocess_exec(
            "git", "log", f"-n{limit}", "--pretty=format:%H%x09%h%x09%an%x09%ae%x09%ad%x09%s%x09%P", "--date=iso-strict",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git log 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace")

    @tool
    async def git_branches(repo_path: str) -> str:
        """获取本地与远程分支列表。

        输出格式：每行 ``<HEAD>\\t<refname:short>\\t<upstream:short>\\t<upstream:track>``。
        使用 ``|`` 作分隔符以规避 Git 2.53 Windows 版对 ``--format`` 连续占位符的展开 bug。
        """
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-a", "--format=%(HEAD)|%(refname:short)|%(upstream:short)|%(upstream:track)",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git branches 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace")

    @tool
    async def git_clone(repo_url: str, target_path: str) -> str:
        """克隆远程仓库到 target_path。"""
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", repo_url, target_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git clone 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or "克隆成功"

    @tool
    async def git_pull(repo_path: str) -> str:
        """拉取当前分支最新变更。"""
        proc = await asyncio.create_subprocess_exec(
            "git", "pull",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git pull 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or "已是最新"

    @tool
    async def git_checkout(repo_path: str, branch: str) -> str:
        """切换到指定分支。"""
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", branch,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git checkout 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or f"已切换到 {branch}"

    @tool
    async def git_stage(repo_path: str, files: list[str]) -> str:
        """将指定文件加入暂存区。"""
        if not files:
            return "未提供文件"
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "--", *files,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git stage 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or f"已暂存 {len(files)} 个文件"

    @tool
    async def git_commit(repo_path: str, message: str, files: list[str] | None = None) -> str:
        """提交改动；提供 files 时会先 stage 再 commit。"""
        if files:
            stage_proc = await asyncio.create_subprocess_exec(
                "git", "add", "--", *files,
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stage_err = await stage_proc.communicate()
            if stage_proc.returncode != 0:
                return f"git stage 失败：{stage_err.decode('utf-8', errors='replace').strip()}"
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"git commit 失败：{stderr.decode('utf-8', errors='replace').strip()}"
        return stdout.decode("utf-8", errors="replace") or "提交成功"

    tools = [
        git_status,
        git_diff,
        git_log,
        git_branches,
        git_clone,
        git_pull,
        git_checkout,
        git_stage,
        git_commit,
    ]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


def make_rag_tools(thread_id: str) -> list:
    """构建绑定 ``thread_id`` 的 RAG 检索工具列表。

    ``rag_retrieve`` 的 ``thread_id`` 用于 trace，不暴露给 LLM。

    工具启用由 ``get_settings().tools_enabled`` 过滤（key: ``rag_retrieve``）。
    """
    from app.tools.rag_retrieve import rag_retrieve as _rag_retrieve

    @tool
    async def rag_retrieve(query: str, top_k: int = 5) -> str:
        """检索知识库，返回带来源与相似度的上下文。"""
        return await _rag_retrieve(query, thread_id=thread_id, top_k=top_k)

    tools = [rag_retrieve]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


# Tavily API Key 环境变量名（config.py 未声明该字段，从 env 读取）
_TAVILY_KEY_ENV = "AGENTX_TAVILY_API_KEY"
# 缺 key 时的统一错误提示
_NO_KEY_MSG = "web_search 不可用：未配置 AGENTX_TAVILY_API_KEY"


def _get_tavily_key() -> str | None:
    """读取 Tavily API Key，缺失返回 None。"""
    return os.environ.get(_TAVILY_KEY_ENV)


def _format_tavily(result: dict) -> str:
    """将 Tavily search 返回值格式化为带来源的字符串。"""
    lines: list[str] = []
    answer = result.get("answer")
    if answer:
        lines.append(f"摘要: {answer}")
    for i, item in enumerate(result.get("results", []), start=1):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")
        lines.append(f"[{i}] {title}\n  链接: {url}\n  内容: {content}")
    if not lines:
        return "未检索到相关网页。"
    return "\n\n".join(lines)


def make_web_tools(thread_id: str) -> list:
    """构建 Web 搜索工具列表（``thread_id`` 保留以与其他子代理签名对齐）。

    工具启用由 ``get_settings().tools_enabled`` 过滤（key: ``web_search``）。
    """

    @tool
    async def web_search(query: str, max_results: int = 5) -> str:
        """联网搜索，返回带标题、链接与摘要的结果。"""
        key = _get_tavily_key()
        if not key:
            return _NO_KEY_MSG
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=key)
            # TavilyClient.search 是同步阻塞调用，放线程池避免阻塞事件循环
            result = await asyncio.to_thread(client.search, query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，错误以字符串回流
            return f"web_search 失败: {exc}"
        return _format_tavily(result)

    tools = [web_search]
    enabled = get_settings().tools_enabled
    return [t for t in tools if enabled.get(t.name, True)]


def extract_text(chunk: Any) -> str:
    """从流式 chunk 中提取纯文本内容（兼容 str / list 内容块）。

    委托给 ``app.utils.text.extract_chunk_text(strip=False)``——
    保留原始文本（不剥离 think 块），由下游 ``ThinkFilter`` 流式处理。
    保留本函数是为了向后兼容（``__all__`` 导出 + 外部可能引用）。
    """
    return extract_chunk_text(chunk, strip=False)


async def run_react_agent_stream(
    agent: Any,
    inputs: dict,
    source: str,
    config: dict | None = None,
) -> AsyncIterator[dict]:
    """运行 ReAct agent 并 yield 标准化事件流。

    事件类型:
    - ``{"type": "token", "content": str}``: 模型流式输出 token
    - ``{"type": "tool_call", "id": str, "name": str, "args": dict}``: 工具调用开始
    - ``{"type": "tool_result", "id": str, "name": str, "result": Any}``: 工具调用结束

    ``id`` 来自 astream_events v2 的 ``run_id``，同一 tool run 的 start/end 共享，
    供前端按 id 配对（chat-rendering-trace-v2 D5）。

    Args:
        agent: 已编译的 ReAct agent（CompiledStategraph）。
        inputs: agent 输入，形如 ``{"messages": [...]}``。
        source: 事件源标签（"code" / "rag" / "web" / 自定义 agent key）。
        config: 可选的 LangGraph 运行配置，含 ``configurable`` 等。
            若 agent 绑定了 checkpointer，必须提供 ``{"configurable": {"thread_id": ...}}``。
    """
    astream_kwargs: dict[str, Any] = {"version": "v2"}
    if config is not None:
        astream_kwargs["config"] = config
    async for event in agent.astream_events(inputs, **astream_kwargs):
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        run_id = event.get("run_id", "")
        if kind == "on_chat_model_stream":
            content = extract_text(data.get("chunk"))
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "id": run_id,
                "name": name,
                "args": data.get("input"),
                "source": source,
            }
        elif kind == "on_tool_end":
            yield {
                "type": "tool_result",
                "id": run_id,
                "name": name,
                "result": data.get("output"),
                "source": source,
            }


def build_builtin_subagent(
    name: str,
    thread_id: str,
    checkpointer: Any = None,
) -> Any:
    """构建内置子代理（rag/web）deep_agent 子图，返回 CompiledStateGraph。

    Args:
        name: 子代理名称，"rag" 或 "web"。
        thread_id: 会话 ID。
        checkpointer: 可选的 LangGraph checkpointer。
    """
    from app.deep.harness import create_agent

    settings = get_settings()
    cfg = settings.subagents[name]
    model = get_chat_model(temperature=cfg.temperature, streaming=True)
    if name == "rag":
        tools = make_rag_tools(thread_id)
    elif name == "web":
        tools = make_web_tools(thread_id)
    else:
        raise ValueError(f"unknown builtin subagent: {name}")
    prompt = (cfg.system_prompt or "") + THINK_PROMPT_SUFFIX
    return create_agent(
        model,
        tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        name=f"{name}_agent",
    )


async def run_builtin_subagent(
    name: str,
    thread_id: str,
    message: str,
    history: list | None = None,
    checkpointer: Any = None,
) -> AsyncIterator[dict]:
    """运行内置子代理，yield 标准化事件流。"""
    agent = build_builtin_subagent(name, thread_id, checkpointer=checkpointer)
    history_msgs = list(history) if history else []
    inputs = {"messages": [*history_msgs, {"role": "user", "content": message}]}
    config = {"configurable": {"thread_id": thread_id}}
    async for event in run_react_agent_stream(agent, inputs, source=name, config=config):
        yield event


# 向后兼容别名（历史 import 路径：from app.subagents.code_agent import _make_fs_tools）
_make_fs_tools = make_fs_tools
_make_git_tools = make_git_tools
_make_rag_tools = make_rag_tools
_make_web_tools = make_web_tools
