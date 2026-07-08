# Proposal: DeepAgents 0.2+ 特性充分利用迁移

## Why

AgentX 在 [pyproject.toml:7](../../../pyproject.toml#L7) 声明了 `deepagents>=0.2.0` 依赖,
但全代码库零处调用 `create_deep_agent` 或任何 deepagents 高级 API(已用 grep 验证)。
所有 agent 都用 `langgraph.prebuilt.create_react_agent` 自组装,等于 deepagents 这个依赖
"声明但未消费"——这正是 AGENTS.md §3 反面清单 R1 要求避免的"自己造轮子"。

deepagents 0.6.12 提供了项目当前自研的以下能力的开箱即用实现:

| 项目自研代码 | deepagents 内置等价物 | 自研版本差距 |
|---|---|---|
| `deep/recovery.py` 消息历史修复 | `PatchToolCallsMiddleware` | 自研版仅做兜底清理,框架版自动修复 |
| `memory/summarizer.py` 手动摘要 | `SummarizationMiddleware`(85% 阈值自动) | 自研版仅支持 `/compact` 手动触发 |
| `deep/agent.py:74-79` 纯文本 JSON plan | `write_todos` 工具 + `TodoListMiddleware` | 自研版无结构化状态字段 |
| `interrupt_before=["tools"]` 全工具中断 | `interrupt_on={"edit_file": True}` 精细中断 | 自研版所有工具一起中断,需 runtime_dangerous 计算 |
| 无 Prompt Caching | `AnthropicPromptCachingMiddleware` | 长会话成本不必要地高 |
| `profile_store.py` + `project_config/loader.py` AGENTS.md prompt 注入 | `memory=` 参数 | 自研版手动注入,框架版原生加载 |
| `memory/skills_loader.py` 技能加载 + 渐进式披露 | `skills=` 参数 | 自研版手动扫描,框架版原生支持 |
| `memory/context.py:trim_messages_with_budget` 手动 token 截断 | `SummarizationMiddleware` + `PatchToolCallsMiddleware` | 自研版手动预算截断 + tool_call 配对 hack,决定删除 |
| `_DEEP_SYSTEM_PROMPT` JSON plan 输出指令 | `write_todos` 工具(结构化 todo + state 持久化) | prompt 教 LLM 输出 JSON,无状态字段,决定删除指令 |

本次迁移用 `create_deep_agent` 替换 `create_react_agent`,消除上述重复造轮子,
同时保留项目特有的安全约束(Supervisor/Expert/Team 三层 + 沙箱白名单 + `.agentx/` 项目配置)。

## What Changes

### 阶段一(P0):核心迁移——消除重复造轮子

#### P0.1: `create_deep_agent` 替换 `create_react_agent`

- [deep/agent.py](../../../backend/app/deep/agent.py#L148): `build_deep_agent` 改用 `create_deep_agent`
- [agents/supervisor/work_supervisor.py](../../../backend/app/agents/supervisor/work_supervisor.py#L122): `build_work_supervisor` 同步迁移
- [agents/expert/coding.py](../../../backend/app/agents/expert/coding.py#L216): 通过 `build_deep_agent` 间接迁移
- **工具冲突处理**: 用 `excluded_tools` 隐藏 deepagents 内置 fs 工具
  (`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`),
  保留项目自研工具(沙箱授权绑定 + workspace_path 解析)
- **子代理工具处理**: 禁用 deepagents 默认 `task` 工具(harness profile 禁用 auto-added subagent +
  不传 `subagents=`),项目自有 `delegate_to_expert`/`delegate_to_subagent`
- **Context Offloading 后端**: 传入 `backend=FilesystemBackend(root_dir=workspace_path)`
  启用大工具结果转存虚拟文件系统。`excluded_tools` 仅隐藏内置 fs **工具**(模型不可见),
  但 `FilesystemMiddleware`(处理 offloading 的中间件)仍保留并使用 backend 内部工作。
  不与项目自研 fs 工具冲突(自研工具操作真实文件系统,backend 操作虚拟文件系统用于 offloading)
- **删除 `_DEEP_SYSTEM_PROMPT` JSON plan 指令**: 移除 prompt 中要求 LLM 输出 JSON plan 格式的指令,
  改用 deepagents `write_todos` 工具(结构化 todo + 持久化到 LangGraph state)。
  `write_todos` 工具调用以 `tool_call` SSE 事件流式传输(前端已支持渲染)。
  现有 `plan`/`plan_update` SSE 事件(JSON 解析产生)进入弃用状态(prompt 不再要求输出)

#### P0.2: `interrupt_on` 替换 `interrupt_before=["tools"]`

- 三个 agent 构建函数改为 `interrupt_on={"edit_file": True, "write_file": True, "cli_execute": True, ...}`
- `DANGEROUS_TOOLS` 集合保留(`deep/tools.py`),用于动态生成 `interrupt_on` 配置
- 审批循环逻辑(`_is_interrupted` / `_get_pending_tool_calls`)适配 `interrupt_on` 语义

#### P0.3: 删除自研消息历史修复

- 删除 [deep/recovery.py](../../../backend/app/deep/recovery.py) 的
  `_inject_tool_error_messages` / `_sanitize_message_history`
- `PatchToolCallsMiddleware` 自动修复悬空 tool_calls
- 保留 `_to_serializable` 工具函数(其他模块仍使用)

### 阶段二(P1):高价值能力启用

#### P1.1: 启用 AnthropicPromptCachingMiddleware

- `create_deep_agent` 默认开启,零配置
- 收益: Anthropic/Bedrock 模型 system prompt 静态部分自动缓存

#### P1.2: SummarizationMiddleware + Context Offloading 替换自研 summarizer

- 用 deepagents 内置 85% 阈值自动摘要替换 [memory/summarizer.py](../../../backend/app/memory/summarizer.py)
- 启用 deepagents Context Offloading: 大工具结果(>20k token)自动转存虚拟文件系统,
  仅返回预览摘要,与 SummarizationMiddleware 协同管理上下文
- 保留 `/api/chat/compact` 端点(映射到 `compact_conversation` 工具或保留手动触发)
- `max_iterations` 从 50 放宽到 100(自动摘要后仍保留安全兜底)
- **删除 [memory/context.py](../../../backend/app/memory/context.py) 自研上下文管理**:
  `trim_messages_with_budget()`(手动 token 预算截断)由 `SummarizationMiddleware` 完全替换;
  `_ensure_tool_call_pairing()`(LangGraph 消息修复 hack)由 `PatchToolCallsMiddleware` 接管。
  删除前需检查是否有其他模块从 `context.py` import,若无则整文件删除

#### P1.3: skills= 参数替换自研 skills_loader.py

- 用 deepagents 内置 `skills=` 参数自动加载 SKILL.md 目录 + 渐进式披露,
  替换 [memory/skills_loader.py](../../../backend/app/memory/skills_loader.py)
- 删除 `skills_loader.py`(deepagents `skills=` 原生支持 SKILL.md 扫描 + 渐进式披露)
- 保留 `@skill:<name>` 标签解析机制(前端交互依赖 `parse_mention` 函数)

#### P1.4: memory= 参数替换自研 prompt 注入

- 用 deepagents 内置 `memory=` 参数接管 AGENTS.md + rules 的加载
  (传入文件路径列表 `[.agentx/AGENTS.md, .agentx/rules/*.md]`),
  替换 Router 手动拼接 `project_context_prompt` 到 `profile_prompt` 的自研注入路径
- [router/graph.py](../../../backend/app/router/graph.py) 简化注入逻辑:
  不再拼接 `project_context_prompt` 到 `profile_prompt`,
  仍保留 `project_system_prompt`(`.agentx/system_prompt.md`)注入到 `profile_prompt`
- [project_config/loader.py](../../../backend/app/project_config/loader.py) **保留**
  (仍加载 mcp.json/subagents.json/tools.json/system_prompt.md,
  `context_prompt` 不再用于 prompt 注入)
- [project_config/merger.py](../../../backend/app/project_config/merger.py) **保留**
  (配置合并功能不变)
- [memory/profile_store.py](../../../backend/app/memory/profile_store.py) 的
  `build_profile_prompt()` **保留**(用户画像仍通过 `system_prompt` 注入)
- 保留 `profile_extractor` 自动画像提取(deepagents 不支持该能力,
  输出仍通过 `system_prompt` 注入)

## Capabilities

### New Capabilities

- `deepagents-integration`: deepagents 0.6.12 harness 集成层,提供 create_deep_agent 封装 +
  工具冲突管理 + interrupt_on 配置生成

### Modified Capabilities

- `deep-agent-path`: DeepAgent 路径从 create_react_agent 迁移到 create_deep_agent
- `work-supervisor`: Supervisor 从 create_react_agent 迁移到 create_deep_agent
- `coding-expert`: Coding Expert 通过 build_deep_agent 间接迁移
- `skill-system`: 技能加载对齐 agentskills.io SKILL.md 规范
- `context-management`: 用 deepagents 内置摘要替换自研 summarizer

## Impact

- **后端**:
  - 修改 ~10 个文件(deep/agent.py / deep/tools.py / router/graph.py / agents/supervisor/* / agents/expert/* / memory/skills_loader.py / memory/summarizer.py / memory/profile_store.py)
  - 保留 project_config/loader.py(`context_prompt` 不再用于 prompt 注入,仍加载 mcp.json/subagents.json/tools.json/system_prompt.md)
  - 保留 project_config/merger.py(配置合并功能不变)
  - 删除 deep/recovery.py 核心函数(保留 _to_serializable)
  - 删除 memory/context.py 的 `trim_messages_with_budget` + `_ensure_tool_call_pairing`(或整文件删除)
  - 删除 `_DEEP_SYSTEM_PROMPT` 中 JSON plan 输出指令(改用 `write_todos` 工具)
  - **不替换 `cli_execute`**: `LocalShellBackend` 无安全限制(文档明确"no sandboxing, no process isolation"),
    保留项目自研 `cli_execute`(blocklist + metachar filter + cwd 授权)
  - pyproject.toml 升级 `deepagents>=0.6.12`
- **前端**: 无改动(SSE 事件契约不变,internal API 变化不暴露)
- **API**: 无新增端点
- **测试**: 现有 deep/supervisor/expert 测试适配新构建方式;新增 deepagents 集成测试
- **文档**: 更新 AGENTS.md §10 技术栈速查 + §11 文件地图
- **依赖**: deepagents 0.2 → 0.6.12(Python >=3.11 已满足,无冲突);langchain/langgraph 视依赖树可一并升级

## Future Extensibility

- P2: 引入 `FilesystemBackend` 抽象,用 deepagents 内置 fs 工具 + `permissions=` 替换自研 fs 工具
- P2: 复用 deepagents `subagents=` 参数配置简单子代理委派(当前保留自研 delegate_to_* 工具,
  Supervisor/Expert/Team 三层架构 + `FORBIDDEN_SUBAGENT_TOOLS` + workspace 继承更严格)
- P2: 评估 `MemoryMiddleware`(教 agent 通过 `edit_file` 自动更新 memory 文件),
  当前 P1 仅用 `memory=`(简单文件路径加载到 system prompt)
- P3: 探索 `SandboxBackend`(Modal/Daytona)用于隔离代码执行
