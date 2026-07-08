# Spec: DeepAgents 集成层(deepagents-integration)

## 概述

`backend/app/deep/harness.py` 提供 deepagents 0.6.12 的集成封装,管理 `create_deep_agent`
的配置:工具冲突排除(`excluded_tools`)、子代理禁用、危险工具中断配置(`interrupt_on` 生成)、
HarnessProfile 注册、`memory=` 参数(AGENTS.md 自动加载)、`skills=` 参数(SKILL.md 自动加载 + 渐进式披露)。
作为 `create_deep_agent` 与项目自研工具/安全约束之间的适配层。

## 包结构

```
backend/app/deep/
├── harness.py    ← 新建:deepagents 配置封装(excluded_tools + interrupt_on + memory= + skills=)
├── agent.py      ← 修改:调用 harness 配置 + create_deep_agent + 移除自研 prompt 注入
├── tools.py      ← 不修改:DANGEROUS_TOOLS 导出供 harness 使用
└── recovery.py   ← 修改:删除自研修复函数,保留 _to_serializable

backend/app/memory/
├── skills_loader.py  ← 修改:删除自研加载逻辑,保留 parse_mention 标签解析
├── summarizer.py     ← 修改:禁用自动触发,保留手动函数
└── profile_store.py  ← 修改:简化,保留 profile_extractor 画像提取

backend/app/project_config/
└── loader.py         ← 修改:移除 prompt 注入逻辑(改由 memory= 接管)
```

## 公共 API

### `harness.py`

```python
from deepagents import create_deep_agent, HarnessProfile, register_harness_profile
from langgraph.checkpoint.base import BaseCheckpointSaver
from pathlib import Path

# 需要用 excluded_tools 隐藏的 deepagents 内置 fs 工具
# (task 工具通过 harness profile 禁用 auto-added subagent + 不传 subagents= 实现,
#  不在此列表中)
_EXCLUDED_BUILTIN_TOOLS: frozenset[str] = frozenset({
    "ls", "read_file", "write_file", "edit_file", "glob", "grep",
})

def build_interrupt_config() -> dict[str, bool]:
    """从 DANGEROUS_TOOLS 集合动态生成 interrupt_on 配置。
    
    DANGEROUS_TOOLS 定义在 deep/tools.py:31:
      {"edit_file", "write_file", "shell_exec", "cli_execute",
       "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit"}
    
    Returns:
        dict[str, bool]: 工具名 → True,用于 create_deep_agent(interrupt_on=...)
    """

def ensure_harness_profile(model_name: str) -> None:
    """注册 HarnessProfile,排除内置 fs 工具 + 禁用默认 subagent。
    
    幂等:重复调用不会重复注册。
    保留 write_todos 工具(项目缺失的规划能力)。
    
    注意:register_harness_profile 的第一个参数是模型标识符
    (如 "anthropic:claude-sonnet-4-6"),不是 profile 名。
    """

def resolve_memory_paths(workspace_path: str | None) -> list[str]:
    """解析 AGENTS.md + rules 文件路径列表,供 create_deep_agent(memory=) 使用。
    
    接管原 project_config/loader.py 的 context_prompt(AGENTS.md + rules)加载:
    1. 项目级 .agentx/AGENTS.md(若存在)
    2. 项目级 .agentx/rules/*.md(若存在,最多 10 个,按文件名排序)
    
    注意:原 Router graph.py 的 project_context_prompt 拼接逻辑将被移除,
    AGENTS.md + rules 改由 memory= 参数自动加载到 system prompt。
    
    Returns:
        list[str]: 文件绝对路径列表(AGENTS.md 在前,rules 按文件名排序在后,可能为空)
    """

def resolve_skills_dir() -> str | None:
    """解析技能目录路径,供 create_deep_agent(skills=) 使用。
    
    Returns:
        str | None: data/skills/ 绝对路径(若存在),否则 None
    """

def resolve_backend(workspace_path: str | None):
    """构建 FilesystemBackend 供 create_deep_agent(backend=) 使用。
    
    启用 Context Offloading:大工具结果(>20k token)自动卸载到虚拟文件系统。
    
    注意:excluded_tools 隐藏了内置 fs 工具(模型不可见),但 FilesystemMiddleware
    (处理 offloading 的中间件)仍保留并使用 backend 内部虚拟文件系统。
    不与项目自研 fs 工具冲突(自研工具操作真实文件系统,backend 操作虚拟文件系统)。
    
    Returns:
        FilesystemBackend | None: 若 workspace_path 非空则返回 FilesystemBackend(root_dir=workspace_path),
        否则 None
    """

def create_agent(
    model,
    tools: list,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    thread_id: str | None = None,
    workspace_path: str | None = None,
) -> "CompiledStateGraph":
    """封装 create_deep_agent,注入项目配置。
    
    1. ensure_harness_profile(model_name)
    2. interrupt_on = build_interrupt_config()
    3. memory_paths = resolve_memory_paths(workspace_path)
    4. skills_dir = resolve_skills_dir()
    5. backend = resolve_backend(workspace_path)
    6. return create_deep_agent(
           model, tools,
           system_prompt=system_prompt,
           interrupt_on=interrupt_on,
           checkpointer=checkpointer,
           memory=memory_paths or None,   # AGENTS.md + rules 自动加载
           skills=skills_dir,             # SKILL.md 自动加载 + 渐进式披露
           backend=backend,               # FilesystemBackend 启用 Context Offloading
           # 不传 subagents= → 禁用 task 工具
       )
    
    Returns:
        CompiledStateGraph: 与 create_react_agent 返回类型一致,
        astream/aget_state/aupdate_state API 完全兼容
    """
```

### `agent.py` 变更

```python
# 之前
from langgraph.prebuilt import create_react_agent

def build_deep_agent(...):
    # Router 已把 AGENTS.md + rules + system_prompt.md + profile + skill 拼到 profile_prompt
    base_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT, scene_prompt=scene_prompt, skill_extra=profile_prompt or None)
    system_prompt = base_prompt + _workspace_prompt_suffix(workspace_path)
    agent = create_react_agent(
        model, tools, name="deep_agent", prompt=system_prompt,
        interrupt_before=["tools"], checkpointer=checkpointer,
    )
    return agent

# 之后
from app.deep.harness import create_agent

def build_deep_agent(...):
    # profile_prompt 不再包含 AGENTS.md + rules(由 memory= 接管)
    # 仍包含 system_prompt.md + profile + skill(由 Router 注入)
    base_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT, scene_prompt=scene_prompt, skill_extra=profile_prompt or None)
    system_prompt = base_prompt + _workspace_prompt_suffix(workspace_path)
    agent = create_agent(
        model, tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        thread_id=thread_id,
        workspace_path=workspace_path,  # harness 内部调用 resolve_memory_paths
    )
    return agent
```

### `recovery.py` 变更

```python
# 删除
def _inject_tool_error_messages(messages: list) -> list: ...
def _sanitize_message_history(messages: list) -> list: ...

# 保留(其他模块仍使用)
def _to_serializable(obj) -> Any: ...
```

### `context.py` 变更

```python
# 删除(由 deepagents SummarizationMiddleware + PatchToolCallsMiddleware 接管)
def trim_messages_with_budget(messages, max_messages=20, max_tokens=16000): ...
def _ensure_tool_call_pairing(messages): ...

# 若无其他引用则删除整个文件
```

### `_DEEP_SYSTEM_PROMPT` 变更

```python
# 之前(删除 JSON plan 指令)
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
    "\n\n对于需要多步执行的复杂任务，请先输出 JSON 计划，格式："
    '{"plan": [{"id": "1", "title": "步骤标题", "status": "pending"}, ...]}'
    "；执行过程中每次完成一步输出："
    '{"plan_update": {"id": "...", "status": "done"}}'
    "。"
)

# 之后(移除 JSON plan 指令,由 write_todos 工具接管规划)
_DEEP_SYSTEM_PROMPT = (
    "你是一个强大的个人助理。你可以读写文件、搜索知识库、搜索网页。"
    "执行危险操作（写文件、执行命令）前需要用户审批。"
    "请根据用户任务规划步骤，调用合适的工具完成。"
    # write_todos 工具由 deepagents 自动注册,LLM 可调用它创建结构化待办清单
)
```

### `skills_loader.py` 变更

```python
# 删除(由 deepagents skills= 参数接管)
def load_skills(skills_dir: str) -> list[Skill]: ...
def get_skill_content(skill_name: str) -> str | None: ...

# 保留(前端 @skill: 标签交互需要)
def parse_mention(text: str) -> list[str]: ...
```

### `project_config/loader.py` 变更

```python
# 删除(由 deepagents memory= 参数接管)
def load_agents_md_as_prompt(workspace_path: str) -> str: ...
def inject_project_config(prompt: str, workspace_path: str) -> str: ...

# 保留(项目配置管理仍需要)
def load_project_config(workspace_path: str) -> dict: ...
def merge_configs(global_config: dict, project_config: dict) -> dict: ...
```

## 行为规格

### 工具冲突管理

| deepagents 内置工具 | 处理方式 | 理由 |
|---|---|---|
| `write_todos` | **保留** | 项目缺失的结构化规划能力 |
| `ls` | `excluded_tools` | 项目有 `list_dir`(沙箱授权绑定) |
| `read_file` | `excluded_tools` | 项目有 `read_file`(名称冲突,沙箱授权绑定) |
| `write_file` | `excluded_tools` | 项目有 `write_file`(名称冲突,危险工具审批) |
| `edit_file` | `excluded_tools` | 项目有 `edit_file`(名称冲突,危险工具审批) |
| `glob` | `excluded_tools` | 项目有 `glob_files`(沙箱授权绑定) |
| `grep` | `excluded_tools` | 项目有 `grep_files`(沙箱授权绑定) |
| `task` | **harness profile 禁用** | 项目有 `delegate_to_expert`/`delegate_to_subagent`(三层架构 + 安全约束) |

**`task` 工具禁用方式**: 根据 deepagents 文档,不能通过 `excluded_middleware` 移除
`SubAgentMiddleware`。正确方式是:(1) 通过 harness profile 禁用 auto-added subagent;
(2) 不传 `subagents=` 参数。`excluded_tools` 仅隐藏 model-visible tool surface,
不移除中间件——但 `task` 工具的禁用需通过 harness profile 的 subagent 配置实现。

### interrupt_on 配置生成

```python
# deep/tools.py:31 中的 DANGEROUS_TOOLS(已有,不修改)
# CLI_TOOL_NAME = "cli_execute"(定义在 app/tools/cli.py:30)
DANGEROUS_TOOLS: set[str] = {
    "edit_file", "write_file", "shell_exec", "cli_execute",
    "git_clone", "git_pull", "git_checkout", "git_stage", "git_commit",
}

# harness.py 中的 build_interrupt_config()
def build_interrupt_config() -> dict[str, bool]:
    return {tool: True for tool in DANGEROUS_TOOLS}
```

### 审批循环适配

**之前**(`interrupt_before=["tools"]`):
1. 所有工具调用前中断
2. `_should_skip_interrupt`:检查工作区 → 放行非危险工具
3. 危险工具走审批流程

**之后**(`interrupt_on={...}`):
1. 只有 `DANGEROUS_TOOLS` 中的工具调用前中断
2. 中断后直接走审批流程(无需 skip 逻辑)
3. 工作区放行逻辑下沉到审批循环内部(检查 `is_path_authorized`)

**注意**: `interrupt_on` 是"工具名 → 是否中断"的静态映射,不支持按参数条件中断。
对于 `cli_execute`(命令内容决定危险性),仍需在审批循环中做 runtime 判断:
- `interrupt_on` 包含 `cli_execute`(所有 CLI 调用都中断)
- 审批循环中用 `runtime_dangerous` 判断是否需要审批(白名单命令自动放行)

### memory= 参数(AGENTS.md + rules 自动加载)

**之前**(Router 层自研 prompt 注入):
1. Router `graph.py` 调用 `load_project_config(workspace_path)` → `ProjectConfig`
2. `ProjectConfig.context_prompt` property 拼接 AGENTS.md + rules 为字符串
3. Router 将 `context_prompt` + `system_prompt.md` + `profile` + `skill` 拼到 `profile_prompt`
4. `build_deep_agent(profile_prompt=...)` → `resolve_system_prompt(skill_extra=profile_prompt)`
5. `create_react_agent(prompt=system_prompt, ...)`

**之后**(deepagents memory= 参数):
1. `harness.py` `resolve_memory_paths(workspace_path)` 返回
   `[.agentx/AGENTS.md] + [.agentx/rules/*.md]` 文件路径列表
2. `create_deep_agent(memory=paths)` 自动加载文件内容到 system prompt
3. Router `graph.py` 简化注入:`profile_prompt` 只保留 `system_prompt.md` + `profile` + `skill`
4. `build_deep_agent` → `create_agent(memory=..., system_prompt=profile_prompt + workspace_suffix)`

**保留不修改**:
- `project_config/loader.py` — 仍加载 mcp.json/subagents.json/tools.json/system_prompt.md
- `project_config/merger.py` — 配置合并功能不变
- `profile_store.build_profile_prompt()` — 用户画像仍通过 `system_prompt` 注入
- `ProjectConfig.context_prompt` property — 保留但不再用于 prompt 注入(deepagents memory= 接管)

### skills= 参数(SKILL.md 自动加载 + 渐进式披露)

**之前**(自研 skills_loader.py):
1. `skills_loader.py` 扫描 `data/skills/*.md` → 解析 frontmatter
2. `@skill:<name>` 标签匹配 → 加载完整文档 → 拼到 system_prompt

**之后**(deepagents skills= 参数):
1. `harness.py` `resolve_skills_dir()` 返回 `data/skills/` 路径
2. `create_deep_agent(skills=dir)` 自动扫描 `<dir>/<name>/SKILL.md`
3. deepagents 内置渐进式披露:启动时只读 frontmatter,任务匹配后自动加载完整文档
4. `@skill:<name>` 标签解析(`parse_mention`)保留供前端交互

**frontmatter 规范**: 对齐 agentskills.io(`name` + `description` 必填,
`trigger`/`tools` 保持可选)。

### Context Offloading

deepagents 的 context management 包含两个机制:
1. **SummarizationMiddleware**: 85% token 阈值自动摘要对话历史
2. **Context Offloading**: 大工具结果(>20k token)自动卸载到虚拟文件系统,返回前 10 行预览

**启用方式**: `create_deep_agent(backend=FilesystemBackend(root_dir=workspace_path))`
- `excluded_tools` 隐藏了内置 fs 工具(模型不可见),但 `FilesystemMiddleware` 本身保留
  (文档明确不能通过 `excluded_middleware` 移除)
- `FilesystemMiddleware` 使用 `backend` 的虚拟文件系统存储 offloaded 内容
- 不与项目自研 fs 工具冲突(自研工具操作真实文件系统,backend 操作虚拟文件系统)

### write_todos 替代 JSON plan

**之前**(自研 JSON plan):
1. `_DEEP_SYSTEM_PROMPT` 指示 LLM 输出 `{"plan": [...]}` JSON
2. 前端解析 JSON → 渲染 `TodoProgress.tsx` → 发送 `plan`/`plan_update` SSE 事件

**之后**(deepagents write_todos 工具):
1. `_DEEP_SYSTEM_PROMPT` 移除 JSON plan 指令
2. `write_todos` 工具由 deepagents 自动注册,LLM 调用它创建结构化待办清单(pending/doing/done/blocked)
3. `write_todos` 的 tool call 通过现有 `tool_call` SSE 事件流式传输
4. `plan`/`plan_update` SSE 事件格式保留但不再触发(prompt 不再要求 JSON plan 输出)

**前端适配**: `TodoProgress.tsx` 已能渲染 tool_call 事件,`write_todos` 调用会自然显示为工具调用。
若前端需要从 `write_todos` 的 tool_call 参数中提取待办列表渲染,需小幅适配。

## 兼容性约束

1. **SSE 事件契约不变**:`plan`/`plan_update`/`approval_request`/`tool_call`/`tool_result` 事件格式不变
2. **返回类型兼容**:`create_deep_agent` 返回 `CompiledStateGraph`,与 `create_react_agent` 一致,
   `astream`/`aget_state`/`aupdate_state` API 完全兼容
3. **checkpointer 兼容**:项目现有 `AsyncPostgresSaver` / `MemorySaver` 直接传入
4. **system_prompt 兼容**:`create_deep_agent` 的 `system_prompt=` 参数与 `create_react_agent` 的
   `prompt=` 参数语义一致(都是 system message 前缀)。区别:`system_prompt` 不再包含
   AGENTS.md + rules 内容(由 `memory=` 接管),仅包含 system_prompt.md + profile 画像 + skill + workspace prompt
5. **Python 版本**:deepagents 0.6.12 要求 `Python >=3.11`,项目 `pyproject.toml` 已声明 `>=3.11`,无冲突
6. **memory= 与 Router 注入兼容**:`resolve_memory_paths()` 返回
   `[.agentx/AGENTS.md] + [.agentx/rules/*.md]` 路径列表,与原 `ProjectConfig.context_prompt`
   加载的文件一致。Router `graph.py` 去掉 `project_context_prompt` 拼接,保留 `project_system_prompt` 注入。
   `project_config/loader.py` 和 `merger.py` 保留(仍加载 mcp.json/subagents.json/tools.json/system_prompt.md)
7. **cli_execute 保留**:不替换为 `LocalShellBackend`(后者无安全限制,文档明确说"no sandboxing,
   no process isolation")。保留项目自研 `cli_execute` 的 blocklist + metachar filter + cwd authorization
8. **SessionSandbox 保留**:不替换为 deepagents `Sandbox`(后者是代码执行沙箱,前者是主机路径授权层,
   功能正交)。保留项目自研 `SessionSandbox` 的 per-thread ACL + approval 集成 + DB 持久化
