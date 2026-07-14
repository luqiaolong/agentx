"""记忆与持久化：checkpointer（会话状态）+ skills 文件 CRUD。

新增模块：
- ``skills_store``：技能文件 CRUD（``data/skills/<name>/SKILL.md``）+ ``SkillDef`` 解析
- ``skills_loader``：仅保留 ``SkillDef`` 模型与 frontmatter 解析辅助函数
- ``profile_store``：长期用户画像 CRUD（``data/config/profile.json``）
- ``checkpointer_view``：checkpointer 只读视图 + 单会话清理
- ``summarizer``：消息摘要压缩（``/compact`` 命令后端）
- ``compact_utils``：``/compact`` 切分辅助（ToolMessage / AIMessage 配对保护）

上下文管理（消息截断 + token 预算）已由 deepagents SummarizationMiddleware
+ PatchToolCallsMiddleware 接管，``context`` 模块已删除。

技能扫描与缓存已由 deepagents ``skills=`` 参数接管；``skills_loader`` 不再
维护 ``load_skills`` / ``get_skills`` / ``reload_skills``。
"""

from __future__ import annotations

from .checkpointer import (
    aclose_checkpointer,
    close_checkpointer,
    get_async_checkpointer,
    get_checkpointer,
)
from .compact_utils import split_messages_for_compact
from .checkpointer_view import (
    ThreadIdInvalid,
    delete_thread,
    get_db_size,
    list_checkpoints,
    list_threads,
    rewind_thread,
)
from .profile_store import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    ProfileSecretDetected,
    ProfileStore,
    ProjectMemoryWithoutWorkspace,
    build_profile_prompt,
)
from .skills_loader import SkillDef
from .skills_store import (
    SkillFileInfo,
    SkillNameInvalid,
    SkillPathEscape,
    delete_skill_file,
    get_skill_file,
    list_skills,
    list_skills_files,
    save_skill_file,
)
from .summarizer import summarize_messages

__all__ = [
    "ProfileCategoryInvalid",
    "ProfileContentTooLong",
    "ProfileEntry",
    "ProfileKeyInvalid",
    "ProfileSecretDetected",
    "ProfileStore",
    "ProjectMemoryWithoutWorkspace",
    "SkillDef",
    "SkillFileInfo",
    "SkillNameInvalid",
    "SkillPathEscape",
    "ThreadIdInvalid",
    "aclose_checkpointer",
    "build_profile_prompt",
    "close_checkpointer",
    "delete_skill_file",
    "delete_thread",
    "get_async_checkpointer",
    "get_checkpointer",
    "get_db_size",
    "get_skill_file",
    "list_checkpoints",
    "list_skills",
    "list_skills_files",
    "list_threads",
    "rewind_thread",
    "save_skill_file",
    "split_messages_for_compact",
    "summarize_messages",
]
