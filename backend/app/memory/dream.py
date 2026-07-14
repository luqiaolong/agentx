"""Dream 记忆整理：对全部记忆（全局画像 + 工作区记忆）做整理。

工作流：
1. 读取全部记忆条目
   - 全局画像 ``data/config/profile.json``（preference / fact / custom / project）
   - 工作区记忆 ``<workspace>/.agentx/memory/*.md``（若提供 workspace_path）
2. 调 LLM 分析，输出变更补丁（patches）列表
   - 每个 patch 指定 operation: upsert / delete / move / keep
   - target_scope: global / workspace
3. 校验全部 patch（非法 target_scope → 拒绝应用全部变更）
4. 创建快照（snapshot）
5. 应用变更
   - operation=upsert → 写入 target_scope
   - operation=delete → 从 target_scope 删除
   - operation=move   → 跨 scope 移动
   - operation=keep   → 无操作
6. 若应用阶段抛异常 → 回滚到快照

安全原则：
- LLM 遗漏 key 不等于删除 — 仅 operation=delete 才删除
- 非法 target_scope 拒绝应用全部变更
- snapshot/rollback 保证原子性
- 复用 profile_store / memory_store 的 upsert + delete，保持并发安全
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import get_chat_model, make_structured_llm
from app.observability.logger import logger


class DreamOperation(str, Enum):
    """Dream patch 操作类型。"""

    UPSERT = "upsert"
    DELETE = "delete"
    MOVE = "move"
    KEEP = "keep"


class DreamEntry(BaseModel):
    """dream 输出的单条记忆内容（不含目标位置，由 DreamPatch.target_scope 指定）。"""

    key: str = Field(description="条目唯一键")
    category: str = Field(description="分类：preference / project / fact / custom")
    content: str = Field(description="条目内容")
    title: str | None = Field(default=None, description="可读标题")
    keywords: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class DreamPatch(BaseModel):
    """dream 结构化输出的单条变更补丁。"""

    operation: DreamOperation = Field(description="操作：upsert / delete / move / keep")
    key: str = Field(description="条目唯一键")
    target_scope: str | None = Field(
        default=None,
        description="目标位置：global（长期记忆）或 workspace（工作区记忆）",
    )
    entry: DreamEntry | None = Field(
        default=None,
        description="upsert / move 操作时的条目内容；delete / keep 无需",
    )
    reason: str = Field(default="", description="变更原因")


class DreamResult(BaseModel):
    """dream 结构化输出 — 变更补丁列表。"""

    patches: list[DreamPatch] = Field(
        default_factory=list,
        description="变更补丁列表",
    )
    summary: str = Field(default="", description="本次整理的简短总结")


_VALID_SCOPES = ("global", "workspace")


_DREAM_SYSTEM = (
    "你是一个记忆整理器（Dream）。分析全部记忆条目（含全局长期记忆 + 工作区记忆），"
    "输出变更补丁（patches）列表。\n"
    "\n"
    "每个 patch 必须指定 operation：\n"
    "- ``upsert``：写入/更新条目到 target_scope 指定的位置\n"
    "- ``delete``：从 target_scope 指定的位置删除条目\n"
    "- ``move``：将条目从当前位置移动到 target_scope 指定的位置\n"
    "- ``keep``：保持条目不变（无需操作）\n"
    "\n"
    "target_scope 必须为 ``global`` 或 ``workspace``。\n"
    "\n"
    "重要：遗漏某个 key 不等于删除。只有 operation=``delete`` 才会删除条目。\n"
    "\n"
    "整理规则：\n"
    "- 合并语义重复的条目为一个 key（选最完整的 content，合并 keywords/scenarios）\n"
    "- 纯冗余/重复的条目用 operation=``delete`` 删除\n"
    "- 不要编造，只基于已有记忆整理\n"
    "- global 条目的 category 应为 preference / fact / custom\n"
    "- workspace 条目的 category 应为 project / custom\n"
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


def _validate_patches(patches: list[DreamPatch]) -> bool:
    """校验全部 patch。

    - upsert / delete / move 操作必须有 target_scope 且为 global / workspace
    - upsert / move 必须有 entry

    Returns:
        True 全部合法；False 有非法 patch（调用方应放弃应用全部变更）。
    """
    for patch in patches:
        if patch.operation in (DreamOperation.UPSERT, DreamOperation.DELETE, DreamOperation.MOVE):
            if patch.target_scope not in _VALID_SCOPES:
                logger.error(
                    "dream.patch.invalid_target_scope",
                    key=patch.key,
                    operation=patch.operation.value,
                    target_scope=patch.target_scope,
                )
                return False
        if patch.operation in (DreamOperation.UPSERT, DreamOperation.MOVE):
            if patch.entry is None:
                logger.error(
                    "dream.patch.missing_entry",
                    key=patch.key,
                    operation=patch.operation.value,
                )
                return False
    return True


async def _rollback_global(snapshot_entries: list[Any]) -> int:
    """回滚全局画像到 snapshot 状态。

    - 删除 snapshot 中不存在的 key（dream 新增的）
    - 恢复 snapshot 中所有条目（upsert_from_llm 更新已有 + 新建缺失）

    Returns:
        回滚过程中遇到的错误数（0 表示完全成功）。
    """
    from app.memory.profile_store import (
        get_all as get_global_entries,
        delete as global_delete,
        upsert_from_llm as global_upsert,
    )

    errors = 0
    current = get_global_entries(workspace_path=None)
    current_keys = {e.key for e in current}
    snapshot_keys = {e.key for e in snapshot_entries}

    # 删除 dream 新增的 key
    for key in current_keys - snapshot_keys:
        try:
            await global_delete(key, workspace_path=None)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("dream.rollback.global_delete_failed", key=key, error=str(exc))

    # 恢复 snapshot 条目
    if snapshot_entries:
        dicts = [
            {
                "key": e.key,
                "category": e.category,
                "content": e.content,
                "title": getattr(e, "title", None),
                "keywords": list(getattr(e, "keywords", []) or []),
                "scenarios": list(getattr(e, "scenarios", []) or []),
            }
            for e in snapshot_entries
        ]
        try:
            await global_upsert(dicts, workspace_path=None)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("dream.rollback.global_upsert_failed", error=str(exc))

    return errors


async def _rollback_workspace(workspace_path: str, snapshot_entries: list[Any]) -> int:
    """回滚工作区记忆到 snapshot 状态。

    Returns:
        回滚过程中遇到的错误数（0 表示完全成功）。
    """
    from app.workspace.memory_store import (
        list_entries as list_ws_entries,
        delete_entry as ws_delete,
        save_entry as ws_save,
    )

    errors = 0
    current = list_ws_entries(workspace_path)
    current_keys = {e.key for e in current}
    snapshot_keys = {e.key for e in snapshot_entries}

    # 删除 dream 新增的 key
    for key in current_keys - snapshot_keys:
        try:
            await ws_delete(workspace_path, key)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("dream.rollback.ws_delete_failed", key=key, error=str(exc))

    # 恢复 snapshot 条目
    for e in snapshot_entries:
        try:
            await ws_save(
                workspace_path,
                e.key,
                e.category,
                e.content,
                source=getattr(e, "source", "manual"),
                title=getattr(e, "title", None),
                keywords=list(getattr(e, "keywords", []) or []),
                scenarios=list(getattr(e, "scenarios", []) or []),
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("dream.rollback.ws_save_failed", key=e.key, error=str(exc))

    return errors


async def dream_all_memory(workspace_path: str | None = None) -> dict[str, Any]:
    """整理全部记忆：全局画像 + 工作区记忆。

    Args:
        workspace_path: 工作区根目录绝对路径；None 时只整理全局画像。

    Returns:
        ``{"applied": int, "rolled_back": int, "rollback_errors": int, "summary": str}``

        ``rollback_errors`` 为回滚过程中遇到的错误数（仅在触发回滚时可能 > 0）。
    """
    from app.memory.profile_store import (
        get_all as get_global_entries,
        upsert_from_llm as global_upsert,
        delete as global_delete,
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
        return {"applied": 0, "rolled_back": 0, "rollback_errors": 0, "summary": "无记忆条目，无需整理"}

    # 2. 调 LLM 分析
    llm = get_chat_model(temperature=get_settings().llm_temperature_extraction)
    structured_llm = make_structured_llm(llm, DreamResult)
    prompt = _DREAM_PROMPT.invoke({
        "count": len(all_tagged),
        "entries": _format_entries_for_llm(all_tagged),
    })

    try:
        result: DreamResult = await structured_llm.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dream llm failed", error=str(exc))
        return {"applied": 0, "rolled_back": 0, "rollback_errors": 0, "summary": f"LLM 分析失败: {exc}"}

    # 3. 校验全部 patch — 非法 patch 拒绝应用全部变更
    if not _validate_patches(result.patches):
        return {"applied": 0, "rolled_back": 0, "rollback_errors": 0, "summary": "patch 校验失败，未应用任何变更"}

    if not result.patches:
        return {"applied": 0, "rolled_back": 0, "rollback_errors": 0, "summary": result.summary or "无变更"}

    # 4. 快照（snapshot）— 用于失败时回滚
    global_snapshot = list(global_entries)
    workspace_snapshot = list(workspace_entries)

    # source 溯源映射
    source_map: dict[str, str] = {}
    for e in global_entries:
        source_map[e.key] = getattr(e, "source", "manual")
    for e in workspace_entries:
        source_map[e.key] = getattr(e, "source", "manual")

    # 5. 应用变更
    applied_count = 0
    try:
        # 5a. global upserts
        global_upserts = [
            p for p in result.patches
            if p.operation == DreamOperation.UPSERT and p.target_scope == "global"
        ]
        if global_upserts:
            dicts = []
            for p in global_upserts:
                d = p.entry.model_dump(exclude_none=True)
                d["source"] = source_map.get(p.key, "llm_extracted")
                dicts.append(d)
            await global_upsert(dicts, workspace_path=None)
            applied_count += len(global_upserts)

        # 5b. workspace upserts
        if workspace_path:
            from app.workspace.memory_store import save_entry as ws_save
            for p in result.patches:
                if p.operation == DreamOperation.UPSERT and p.target_scope == "workspace":
                    original_source = source_map.get(p.key, "llm_extracted")
                    await ws_save(
                        workspace_path,
                        p.key,
                        p.entry.category,
                        p.entry.content,
                        source=original_source,
                        title=p.entry.title,
                        keywords=p.entry.keywords,
                        scenarios=p.entry.scenarios,
                    )
                    applied_count += 1

        # 5c. deletes
        for p in result.patches:
            if p.operation == DreamOperation.DELETE:
                if p.target_scope == "global":
                    await global_delete(p.key, workspace_path=None)
                    applied_count += 1
                elif p.target_scope == "workspace" and workspace_path:
                    from app.workspace.memory_store import delete_entry as ws_delete
                    await ws_delete(workspace_path, p.key)
                    applied_count += 1

        # 5d. moves
        for p in result.patches:
            if p.operation == DreamOperation.MOVE:
                if p.target_scope == "global":
                    # workspace → global：写入 global + 删除 workspace
                    d = p.entry.model_dump(exclude_none=True)
                    d["source"] = source_map.get(p.key, "llm_extracted")
                    await global_upsert([d], workspace_path=None)
                    if workspace_path:
                        from app.workspace.memory_store import delete_entry as ws_delete
                        await ws_delete(workspace_path, p.key)
                elif p.target_scope == "workspace" and workspace_path:
                    # global → workspace：写入 workspace + 删除 global
                    from app.workspace.memory_store import save_entry as ws_save
                    original_source = source_map.get(p.key, "llm_extracted")
                    await ws_save(
                        workspace_path,
                        p.key,
                        p.entry.category,
                        p.entry.content,
                        source=original_source,
                        title=p.entry.title,
                        keywords=p.entry.keywords,
                        scenarios=p.entry.scenarios,
                    )
                    await global_delete(p.key, workspace_path=None)
                applied_count += 1

    except Exception as exc:  # noqa: BLE001
        # 6. 回滚
        logger.error(
            "dream.apply_failed.rolling_back",
            error=str(exc),
            applied_before_failure=applied_count,
        )
        rollback_errors = await _rollback_global(global_snapshot)
        if workspace_path:
            rollback_errors += await _rollback_workspace(workspace_path, workspace_snapshot)
        return {
            "applied": 0,
            "rolled_back": applied_count,
            "rollback_errors": rollback_errors,
            "summary": f"整理失败已回滚（回滚错误: {rollback_errors}）: {exc}",
        }

    logger.info(
        "dream.completed",
        workspace=workspace_path,
        applied=applied_count,
    )
    return {
        "applied": applied_count,
        "rolled_back": 0,
        "rollback_errors": 0,
        "summary": result.summary,
    }


__all__ = ["dream_all_memory"]
