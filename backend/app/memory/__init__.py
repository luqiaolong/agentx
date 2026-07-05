"""记忆与持久化：checkpointer（会话状态）+ skills_loader（技能加载）。

新增模块：
- ``skills_store``：技能文件 CRUD（``data/skills/*.md``）
- ``profile_store``：长期用户画像 CRUD（``data/config/profile.json``）
- ``checkpointer_view``：checkpointer 只读视图 + 单会话清理
- ``context``：上下文管理 — 滑动窗口消息截断 + token 预算
- ``summarizer``：消息摘要压缩（``/compact`` 命令后端）
"""

from __future__ import annotations

from .checkpointer import close_checkpointer, get_async_checkpointer, get_checkpointer
from .checkpointer_view import (
    ThreadIdInvalid,
    delete_thread,
    get_db_size,
    list_threads,
)
from .context import trim_messages_with_budget
from .profile_store import (
    ProfileCategoryInvalid,
    ProfileContentTooLong,
    ProfileEntry,
    ProfileKeyInvalid,
    ProfileStore,
    build_profile_prompt,
)
from .sandbox_store import SandboxStore, get_sandbox_store
from .skills_loader import SkillDef, get_skills, load_skills, reload_skills
from .skills_store import (
    SkillFileInfo,
    SkillNameInvalid,
    SkillPathEscape,
    delete_skill_file,
    get_skill_file,
    list_skills_files,
    save_skill_file,
)
from .summarizer import summarize_messages

__all__ = [
    "ProfileCategoryInvalid",
    "ProfileContentTooLong",
    "ProfileEntry",
    "ProfileKeyInvalid",
    "ProfileStore",
    "SandboxStore",
    "SkillDef",
    "SkillFileInfo",
    "SkillNameInvalid",
    "SkillPathEscape",
    "ThreadIdInvalid",
    "build_profile_prompt",
    "close_checkpointer",
    "delete_skill_file",
    "delete_thread",
    "get_async_checkpointer",
    "get_checkpointer",
    "get_db_size",
    "get_sandbox_store",
    "get_skill_file",
    "get_skills",
    "list_skills_files",
    "list_threads",
    "load_skills",
    "reload_skills",
    "save_skill_file",
    "summarize_messages",
    "trim_messages_with_budget",
]
