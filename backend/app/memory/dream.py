"""Dream 记忆整理：对全部记忆（全局画像 + 工作区记忆）做整理。

工作流：
1. 读取全部记忆条目
   - 全局画像 ``data/config/profile.json``（preference / fact / custom / project）
   - 工作区记忆 ``<workspace>/.agentx/memory/*.md``（若提供 workspace_path）
2. 调 LLM 分析，输出整理后的最终状态：
   - 每条条目标注目标位置（``global`` 或 ``workspace``）
   - 冗余/重复条目列入 ``removed_keys``
3. 应用变更：
   - ``target=global`` → upsert 到全局画像
   - ``target=workspace`` → upsert 到工作区记忆
   - ``removed_keys`` → 从全局 + 工作区删除
   - 工作区 → 全局的条目：从工作区删除
   - 全局 → 工作区的条目：从全局删除

设计原则：
- Dream 是全局功能，整理全部记忆，不限于工作区
- 非破坏性：LLM 输出最终状态，后端 diff 后应用（upsert + delete moved）
- 保留 source 溯源：压缩/合并时不改变原来源标记
- 复用 profile_store / memory_store 的 upsert + delete，保持并发安全
"""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import get_chat_model
from app.observability.logger import logger


class DreamEntry(BaseModel):
    """dream 输出的单条记忆（含目标位置）。"""

    key: str = Field(description="条目唯一键")
    category: str = Field(description="分类：preference / project / fact / custom")
    content: str = Field(description="条目内容")
    target: str = Field(description="目标位置：global（长期记忆）或 workspace（工作区记忆）")
    title: str | None = Field(default=None, description="可读标题")
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class DreamResult(BaseModel):
    """dream 结构化输出 — 整理后的最终状态。"""

    entries: list[DreamEntry] = Field(
        default_factory=list,
        description="整理后的全部条目，每条含 target 指明写入位置",
    )
    removed_keys: list[str] = Field(
        default_factory=list,
        description="建议删除的冗余/重复 key（从全局 + 工作区删除）",
    )
    summary: str = Field(default="", description="本次整理的简短总结")


_DREAM_SYSTEM = (
    "你是一个记忆整理器（Dream）。分析全部记忆条目（含全局长期记忆 + 工作区记忆），"
    "输出整理后的最终状态。\n"
    "\n"
    "对每条条目，决定其目标位置：\n"
    "- ``global``：跨工作区的长期事实/偏好/约定 → 写入全局长期记忆\n"
    "  例如：用户喜欢简洁回复、用户是前端工程师、用 pytest 测试\n"
    "- ``workspace``：工作区特定的上下文 → 保留在工作区记忆\n"
    "  例如：项目技术栈、项目结构、项目特定约定\n"
    "\n"
    "整理规则：\n"
    "- 合并语义重复的条目为一个 key（选最完整的 content，合并 keywords/scenarios）\n"
    "- 纯冗余/重复的条目 key 放入 removed_keys\n"
    "- 不要编造，只基于已有记忆整理\n"
    "- global 条目的 category 应为 preference / fact / custom\n"
    "- workspace 条目的 category 应为 project / custom\n"
    "- 如果某层记忆已经很精简，可以原样保留（target 不变）\n"
    "- summary 不超过 100 字，说明本次整理做了什么"
)

_DREAM_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _DREAM_SYSTEM),
    ("human", "全部记忆条目（共 {count} 条）：\n\n{entries}"),
])


def _format_entries_for_llm(entries: list[tuple[str, str, Any]]) -> str:
    """将记忆条目格式化为 LLM 可读的文本。

    Args:
        entries: ``(location, key, entry)`` 三元组列表，location 为 "global" / "workspace"。
    """
    lines = []
    for location, _key, e in entries:
        title = getattr(e, "title", None) or ""
        keywords = getattr(e, "keywords", None) or []
        kw = f" [keywords: {', '.join(keywords)}]" if keywords else ""
        lines.append(
            f"- key={e.key} | category={e.category} | location={location} | title={title}{kw}\n"
            f"  content: {e.content}"
        )
    return "\n".join(lines)


async def dream_all_memory(workspace_path: str | None = None) -> dict[str, Any]:
    """整理全部记忆：全局画像 + 工作区记忆。

    Args:
        workspace_path: 工作区根目录绝对路径；None 时只整理全局画像。

    Returns:
        ``{"promoted": int, "compressed": int, "removed": int, "summary": str}``
    """
    from app.memory.profile_store import (
        get_all as get_global_entries,
        upsert_from_llm as global_upsert,
        delete_entry as global_delete,
    )

    # 1. 读取全部记忆
    global_entries = get_global_entries(workspace_path=None)
    all_tagged: list[tuple[str, str, Any]] = [
        ("global", e.key, e) for e in global_entries
    ]

    workspace_entries = []
    if workspace_path:
        from app.workspace.memory_store import list_entries as list_ws_entries
        try:
            workspace_entries = list_ws_entries(workspace_path)
            all_tagged.extend(
                ("workspace", e.key, e) for e in workspace_entries
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream: read workspace memory failed", workspace=workspace_path, error=str(exc))

    if not all_tagged:
        return {"promoted": 0, "compressed": 0, "removed": 0, "summary": "无记忆条目，无需整理"}

    # 2. 调 LLM 分析
    llm = get_chat_model(temperature=get_settings().llm_temperature_extraction)
    structured_llm = llm.with_structured_output(DreamResult)
    prompt = _DREAM_PROMPT.invoke({
        "count": len(all_tagged),
        "entries": _format_entries_for_llm(all_tagged),
    })

    try:
        result: DreamResult = await structured_llm.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dream llm failed", error=str(exc))
        return {"promoted": 0, "compressed": 0, "removed": 0, "summary": f"LLM 分析失败: {exc}"}

    # 3. 计算变更
    current_global_keys = {e.key for e in global_entries}
    current_workspace_keys = {e.key for e in workspace_entries}
    target_global_keys = {e.key for e in result.entries if e.target == "global"}
    target_workspace_keys = {e.key for e in result.entries if e.target == "workspace"}
    removed_keys = set(result.removed_keys)

    # source 溯源映射
    source_map: dict[str, str] = {}
    for e in global_entries:
        source_map[e.key] = getattr(e, "source", "manual")
    for e in workspace_entries:
        source_map[e.key] = getattr(e, "source", "manual")

    # 4. 应用变更
    promoted_count = 0   # workspace → global
    compressed_count = 0 # 原地压缩更新
    removed_count = 0

    # 4a. 写入 global 条目
    global_to_write = [e for e in result.entries if e.target == "global"]
    if global_to_write:
        dicts = [e.model_dump(exclude={"target"}) for e in global_to_write]
        # 补 source 溯源
        for d in dicts:
            d["source"] = source_map.get(d["key"], "manual")
        try:
            await global_upsert(dicts, workspace_path=None)
            # 统计 promoted（原在 workspace，现 target=global）
            for e in global_to_write:
                if e.key in current_workspace_keys and e.key not in current_global_keys:
                    promoted_count += 1
                else:
                    compressed_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream global upsert failed", error=str(exc))

    # 4b. 写入 workspace 条目
    if workspace_path:
        from app.workspace.memory_store import save_entry as ws_save
        workspace_to_write = [e for e in result.entries if e.target == "workspace"]
        for e in workspace_to_write:
            try:
                original_source = source_map.get(e.key, "manual")
                await ws_save(
                    workspace_path,
                    e.key,
                    e.category,
                    e.content,
                    source=original_source,
                    title=e.title,
                    keywords=e.keywords,
                    scenarios=e.scenarios,
                )
                # 统计 promoted（原在 global，现 target=workspace = demoted，也算整理）
                if e.key not in current_workspace_keys:
                    promoted_count += 1
                else:
                    compressed_count += 1
            except (ValueError, Exception) as exc:  # noqa: BLE001
                logger.warning("dream workspace save failed", key=e.key, error=str(exc))

    # 4c. 删除已移动的条目（workspace→global 的原 workspace 副本，global→workspace 的原 global 副本）
    # workspace → global：key 在 target_global 但不在 target_workspace → 从 workspace 删除
    if workspace_path:
        from app.workspace.memory_store import delete_entry as ws_delete
        ws_to_delete = (current_workspace_keys - target_workspace_keys) | removed_keys
        for key in ws_to_delete:
            if key in current_workspace_keys:
                try:
                    await ws_delete(workspace_path, key)
                    removed_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("dream ws delete failed", key=key, error=str(exc))

    # global → workspace：key 在 target_workspace 但不在 target_global → 从 global 删除
    gl_to_delete = (current_global_keys - target_global_keys) | removed_keys
    for key in gl_to_delete:
        if key in current_global_keys:
            try:
                global_delete(key, workspace_path=None)
                removed_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("dream global delete failed", key=key, error=str(exc))

    logger.info(
        "dream.completed",
        workspace=workspace_path,
        promoted=promoted_count,
        compressed=compressed_count,
        removed=removed_count,
    )
    return {
        "promoted": promoted_count,
        "compressed": compressed_count,
        "removed": removed_count,
        "summary": result.summary,
    }


__all__ = ["dream_all_memory"]
