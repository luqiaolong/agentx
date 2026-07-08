# Design: DeepAgents 0.2+ 特性充分利用迁移

## Context

AgentX 在 [pyproject.toml:7](../../../pyproject.toml#L7) 声明了 `deepagents>=0.2.0` 依赖,
但全代码库零处调用 `create_deep_agent`。所有 agent 都用 `langgraph.prebuilt.create_react_agent`
自组装,手动实现了 deepagents 已内置的能力:消息修复、自动摘要、规划工具、精细中断、Prompt 缓存。

当前自研实现的具体差距:

1. **消息历史修复** ([deep/recovery.py](../../../backend/app/deep/recovery.py)):
   `_inject_tool_error_messages` / `_sanitize_message_history` 在每次 astream 前手动扫描,
   仅做兜底清理(补 error tool message / 剥离空 tool_calls)。
   deepagents 的 `PatchToolCallsMiddleware` 在中间件层自动修复,覆盖更全。

2. **上下文压缩** ([memory/summarizer.py](../../../backend/app/memory/summarizer.py)):
   仅支持 `/compact` 手动触发。长会话跑 50 轮 iteration 容易撞
   [deep/agent.py:342](../../../backend/app/deep/agent.py#L342) 的 `max_iterations=50`
   上限被强制停止。deepagents 的 `SummarizationMiddleware` 在 85% token 阈值自动触发。

3. **规划工具** ([deep/agent.py:74-79](../../../backend/app/deep/agent.py#L74-L79)):
   用"在 prompt 里教 LLM 输出 JSON plan"的文本解析方案,需 LLM 严格遵循格式,
   无 pending/in_progress/completed 状态字段。deepagents 的 `write_todos` 是 no-op 工具,
   纯粹让 LLM 把隐式思维外显为结构化 todo 列表,持久化到 LangGraph state。

4. **审批中断** ([deep/agent.py:153](../../../backend/app/deep/agent.py#L153)):
   `interrupt_before=["tools"]` 让所有工具调用前都中断,再用 `DANGEROUS_TOOLS` +
   `runtime_dangerous` 集合判断是否真正危险。deepagents 的 `interrupt_on={"edit_file": True}`
   只对指定工具中断,无需 runtime 计算。

5. **Prompt 缓存**: 0 实现,所有 system prompt 每次重新计算。deepagents 的
   `AnthropicPromptCachingMiddleware` 在 Anthropic/Bedrock 模型上自动开启。

## Goals / Non-Goals

**Goals:**
- 用 `create_deep_agent` 替换 `create_react_agent`,消费已声明的 deepagents 依赖
- 用 `interrupt_on` 实现危险工具精细中断,消除 `interrupt_before=["tools"]` + runtime_dangerous
- 删除 `deep/recovery.py` 自研消息修复,由 `PatchToolCallsMiddleware` 接管
- 删除 `memory/context.py` 的 `trim_messages_with_budget` + `_ensure_tool_call_pairing`,由 `SummarizationMiddleware` + `PatchToolCallsMiddleware` 接管
- 删除 `_DEEP_SYSTEM_PROMPT` 中 JSON plan 输出指令,改用 deepagents `write_todos` 工具
- 启用 `SummarizationMiddleware` 85% 阈值自动摘要 + Context Offloading 大工具结果转存,消除 `max_iterations=50` 硬上限
- 启用 `AnthropicPromptCachingMiddleware` 降低长会话成本
- 用 `memory=` 参数替换自研 prompt 注入(`profile_store.py` + `project_config/loader.py`),保留 `profile_extractor`
- 用 `skills=` 参数替换自研 `skills_loader.py`,保留 `@skill:` 标签解析
- deepagents 升级到 0.6.12

**Non-Goals:**
- 不迁移 Supervisor/Expert/Team 三层架构到 deepagents `subagents=`(业务耦合深,安全约束更严)
- 不用 deepagents 内置 fs 工具替换自研 fs 工具(P2 另开提案)
- 不修改 SSE 事件契约(`plan`/`plan_update`/`approval_request`/`tool_call` 事件格式不变)
- 不修改前端(所有变化在后端 agent 构建层,SSE 事件不暴露 internal API 变化)
- 不修改 `.agentx/` 项目配置覆盖机制(deepagents 不支持项目级覆盖)
- 不修改沙箱白名单授权模型(比 deepagents `permissions=` 更严格)
- 不替换 `cli_execute`(`LocalShellBackend` 无安全限制,文档明确"no sandboxing, no process isolation";
  保留项目自研 `cli_execute` 的 blocklist + metachar filter + cwd 授权)

## Decisions

### Decision 1: 用 `excluded_tools` 隐藏内置 fs 工具,保留项目自研工具

**问题**: `create_deep_agent` 自动注册 `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`
六个 fs 工具,与项目自研的 `read_file`/`write_file`/`edit_file`(名称冲突) +
`list_dir`/`glob_files`/`grep_files`(功能重叠)冲突。

**选项**:
- A. 用 deepagents 内置工具替换自研工具 → 需要把沙箱授权绑定到 `FilesystemBackend`,
  改动大,属 P2 范围
- B. 用 `excluded_tools` 隐藏内置工具,保留自研工具 → 改动最小,P0 先跑通

**选择**: B
- P0 阶段用 `HarnessProfile(excluded_tools=frozenset({"ls","read_file","write_file","edit_file","glob","grep","task"}))`
  隐藏全部内置 fs 工具 + `task` 子代理工具
- 保留 `write_todos`(项目缺失的能力)
- P2 阶段再做 FilesystemBackend 迁移

**理由**: P0 聚焦"消除重复造轮子"(recovery/summarizer/plan/interrupt/cache),
fs 工具迁移涉及沙箱授权适配,风险更高,留到 P2。

### Decision 2: `interrupt_on` 配置从 `DANGEROUS_TOOLS` 动态生成

**问题**: `interrupt_on` 接受 `dict[str, bool]` 或 `set[str]`,需指定哪些工具触发中断。

**选择**: 从现有 `DANGEROUS_TOOLS` 集合动态生成:
```python
interrupt_on = {tool_name: True for tool_name in DANGEROUS_TOOLS}
```
- `DANGEROUS_TOOLS` 集合保留(`deep/tools.py:31`),
  作为 single source of truth
- `runtime_dangerous` 计算逻辑保留(某些工具在特定参数下才危险,如 `cli_execute` 的命令内容)

**注意**: `interrupt_on` 是"工具名 → 是否中断"的静态映射,不支持按参数条件中断。
对于 `cli_execute`(命令内容决定危险性),仍需在审批循环中做 runtime 判断:
- `interrupt_on` 包含 `cli_execute`(所有 CLI 调用都中断)
- 审批循环中用 `runtime_dangerous` 判断是否需要审批(白名单命令自动放行)

### Decision 3: `interrupt_on` 与现有审批循环的兼容性

**问题**: 现有审批循环基于 `interrupt_before=["tools"]`,用 `_is_interrupted(state)` 检测中断,
`_get_pending_tool_calls(state)` 提取待审批工具调用,审批后 `astream(None, config)` 恢复。
`interrupt_on` 机制是否兼容?

**分析**: `interrupt_on` 底层仍是 LangGraph interrupts(通过 checkpoint 暂停-恢复)。
- 暂停后 `aget_state(config)` 返回的 `state.next` 仍包含 `"tools"` 节点
- `state.tasks` 仍包含 pending tool calls
- 恢复方式不变:`astream(None, config)` 续跑

**差异**: `interrupt_before=["tools"]` 在所有工具调用前暂停;
`interrupt_on={"edit_file": True}` 只在 `edit_file` 调用前暂停,其他工具直接执行。

**适配**: 现有 `_is_interrupted` / `_get_pending_tool_calls` 逻辑无需大改,
但 `_should_skip_interrupt`(工作区自动放行)逻辑需调整:
- 之前: 所有工具都中断 → 检查是否工作区内 → 放行
- 之后: 只有危险工具中断 → 中断后直接走审批流程(工作区放行逻辑下沉到审批循环内部)

### Decision 4: 禁用 `task` 子代理工具,保留 `delegate_to_*`

**问题**: `create_deep_agent` 自动注册 `task` 工具用于子代理委派,
与项目的 `delegate_to_expert`/`delegate_to_subagent` 功能重叠。

**选择**: 用 `excluded_tools` 隐藏 `task` 工具,不传 `subagents=` 参数。
- 项目的三层架构(Supervisor → Expert → Subagent)有更严格的安全约束:
  `FORBIDDEN_SUBAGENT_TOOLS` 硬约束 + workspace 授权继承
- deepagents `subagents=` 的安全模型较简单,不适合直接替换

### Decision 5: `write_todos` 与现有 `plan` SSE 事件的桥接

> **已被 Decision 11 取代**: 不再保留 JSON plan 解析,P0 阶段直接删除 `_DEEP_SYSTEM_PROMPT`
> 中的 JSON plan 指令,改用 `write_todos` 工具。详见 Decision 11。

**原方案(已废弃)**: P0 阶段保留现有 prompt JSON plan 解析,`write_todos` 作为补充。
**废弃原因**: 用户要求"优先使用 deepagents 特性",`write_todos` 是结构化工具(有状态字段),
优于 prompt JSON 解析方案。保留两套机制增加复杂度,违反"不要重复造轮子"原则。

### Decision 6: 保留 `max_iterations` 作为安全兜底

**问题**: `SummarizationMiddleware` 自动摘要后,`max_iterations=50` 是否还需要?

**选择**: 保留但放宽到 `max_iterations=100`。
- 自动摘要处理上下文长度,但不限制工具调用轮数
- 极端情况(如死循环)仍需硬上限兜底
- 100 轮足够覆盖任何合理任务,同时防止失控

### Decision 7: `memory=` 替换自研 prompt 注入

**问题**: 项目新增 `project_config` 模块,Router 层([router/graph.py](../../../backend/app/router/graph.py))
加载 `.agentx/AGENTS.md` + `rules/*.md` 并拼接为 `context_prompt` 注入到 `profile_prompt`。
这与 deepagents `memory=` 参数功能重叠。

**选择**: `memory=` 接管 AGENTS.md + rules 的加载(传入文件路径列表),
Router 简化注入逻辑。
- `create_deep_agent` 传入 `memory=[.agentx/AGENTS.md, .agentx/rules/*.md]` 文件路径列表
- Router `graph.py` 去掉 `project_context_prompt` 拼接,保留 `project_system_prompt` 注入

**理由**: deepagents `memory=` 原生支持文件路径列表加载 + system prompt 注入,
消除 Router 层的手动拼接。

**兼容性**:
- [project_config/loader.py](../../../backend/app/project_config/loader.py) 保留
  (仍加载 mcp.json/subagents.json/tools.json/system_prompt.md)
- [project_config/merger.py](../../../backend/app/project_config/merger.py) 保留
  (配置合并功能不变)
- Router [graph.py](../../../backend/app/router/graph.py) 修改:
  去掉 `project_context_prompt` 拼接,保留 `project_system_prompt` 注入
- [profile_store.py](../../../backend/app/memory/profile_store.py) 的 `build_profile_prompt()` 保留
  (用户画像仍通过 `system_prompt` 注入)

**注入链变化**:
- 之前: Router 拼接 AGENTS.md + rules + system_prompt.md + profile + skill → `profile_prompt`
  → `build_deep_agent` → `resolve_system_prompt(skill_extra=profile_prompt)`
  → `create_react_agent(prompt=...)`
- 之后: Router 只拼接 system_prompt.md + profile + skill → `profile_prompt`
  → `build_deep_agent` → `create_deep_agent(memory=[AGENTS.md, rules/*.md],
  system_prompt=profile_prompt + workspace_suffix)`

### Decision 8: `skills=` 替换自研 skills_loader.py

**问题**: 项目自研 [memory/skills_loader.py](../../../backend/app/memory/skills_loader.py)
手动扫描 SKILL.md 目录 + 实现渐进式披露,deepagents 已内置 `skills=` 参数原生支持。

**选择**: 用 `skills=` 参数替换 `skills_loader.py`,删除自研加载器。
- `create_deep_agent` 传入 `skills=["data/skills"]` 自动扫描 + 渐进式披露
- 保留 `@skill:<name>` 标签解析(`parse_mention` 函数)用于前端交互

**理由**: deepagents `skills=` 原生支持 SKILL.md 扫描 + 渐进式披露,
无需项目手动实现;`@skill:` 标签解析是前端交互层,与 deepagents 加载机制正交。

### Decision 9: Context Offloading 纳入 P1 范围

**问题**: deepagents 上下文管理包含 SummarizationMiddleware(摘要)和 Context Offloading
(大工具结果转存)两个机制,后者是否应纳入本次迁移?

**选择**: 纳入 P1.2 范围,与 SummarizationMiddleware 一同启用。
- Context Offloading 自动将大工具结果(>20k token)转存到虚拟文件系统,
  仅返回预览摘要给 LLM
- 与 SummarizationMiddleware 协同:前者处理单次大结果,后者处理累积上下文

**依赖**: Offloading 依赖 `FilesystemMiddleware` 存在(用于虚拟文件系统后端)。
项目虽用 `excluded_tools` 隐藏了内置 fs **工具**,但未排除 `FilesystemMiddleware`
本身(中间件层仍保留),因此 Offloading 可正常工作。

**理由**: 两个机制互补,一起启用才能完整解决长上下文问题;且 Offloading 零配置,
只需 FilesystemMiddleware 存在即可。

### Decision 10: `backend=FilesystemBackend` 启用 Context Offloading

**问题**: Context Offloading 需要一个 backend 来存储大工具结果。Decision 9 假设
FilesystemMiddleware 存在即可,但实际 Offloading 还需传入 `backend=` 参数指定存储后端。

**选择**: 向 `create_deep_agent` 传入 `backend=FilesystemBackend(root_dir=workspace_path)`。
- `create_deep_agent` 接受 `backend=` 参数(类型: `BackendProtocol | Callable`)
- `FilesystemBackend(root_dir=workspace_path)` 将大工具结果 offload 到磁盘

**关键洞察**: `excluded_tools` 隐藏了内置 fs **工具**(模型不可见),
但 `FilesystemMiddleware`(处理 offloading 的中间件)仍然存在并内部使用 backend。
因此 Offloading 与项目自研 fs 工具**不冲突**:自研工具操作真实文件系统,
backend 操作虚拟文件系统(仅用于 offloading 暂存)。

**harness.py 封装**: `backend/app/deep/harness.py` 新增 `resolve_backend()` 函数,
返回 `FilesystemBackend(root_dir=workspace_path)` 或 `None`。
harness.py 完整包结构:
- `_EXCLUDED_BUILTIN_TOOLS` 常量
- `build_interrupt_config()` 函数
- `ensure_harness_profile()` 函数
- `resolve_memory_paths()` 函数
- `resolve_skills_dir()` 函数
- `resolve_backend()` 函数(新增,返回 FilesystemBackend 或 None)
- `create_agent()` 函数(主入口)

### Decision 11: 删除 `_DEEP_SYSTEM_PROMPT` JSON plan,用 `write_todos` 替换

**问题**: 当前 `_DEEP_SYSTEM_PROMPT` 在 prompt 中教 LLM 输出 JSON plan 格式,
由前端解析为 `plan`/`plan_update` SSE 事件。deepagents 提供 `write_todos` 工具,
是结构化 todo + 持久化到 LangGraph state 的方案,无格式依赖。

**选择**: 删除 `_DEEP_SYSTEM_PROMPT` 中的 JSON plan 输出指令,改用 `write_todos` 工具。
- `write_todos` 工具调用以 `tool_call` SSE 事件流式传输(前端已支持渲染 tool_call 事件)
- 前端 `TodoProgress.tsx` 已能渲染 tool_call 事件,可渲染 `write_todos` 调用

**弃用说明**: 现有 `plan`/`plan_update` SSE 事件(JSON 解析产生)进入弃用状态——
prompt 不再要求输出 JSON plan,因此这些事件不再被产生。事件格式本身不变(向后兼容),
仅不再触发。**本决策取代 Decision 5 中"P0 阶段保留现有 prompt JSON plan 解析"的方案**。

**理由**: `write_todos` 是结构化工具(有 pending/in_progress/completed 状态字段),
比 prompt 教 LLM 输出 JSON 更可靠;且 tool_call SSE 事件前端已支持,无需新增事件类型。

### Decision 12: 删除 `trim_messages_with_budget`

**问题**: [memory/context.py](../../../backend/app/memory/context.py) 包含两个自研函数:
- `trim_messages_with_budget()`: 手动 token 预算截断
- `_ensure_tool_call_pairing()`: LangGraph 消息历史修复 hack(补 tool message 配对)

deepagents 的 `SummarizationMiddleware`(85% 阈值自动摘要)替换前者,
`PatchToolCallsMiddleware`(中间件层自动修复)替换后者。

**选择**: 删除 `trim_messages_with_budget` 和 `_ensure_tool_call_pairing`。
- 删除前需检查是否有其他模块从 `context.py` import
- 若无其他引用,整文件删除;若有,仅删除这两个函数

**理由**: 两个自研函数被 deepagents 中间件完全覆盖,保留会造成双机制冲突
(如截断时机重叠、修复逻辑打架)。删除后由 deepagents 中间件单一接管。

## Risks

### Risk 1: deepagents 0.6.12 传递依赖冲突

**风险**: deepagents 0.6.12 可能要求 `langgraph>=0.6` / `langchain>=0.4` 等更高版本,
与项目现有依赖冲突。

**缓解**: 迁移前先运行 `uv pip tree` 检查依赖树,在隔离环境测试升级。
pyproject.toml 已有 `langchain>=0.3.0` 约束,需确认兼容性。

### Risk 2: `interrupt_on` 行为与预期不符

**风险**: `interrupt_on` 的实际中断行为可能与 `interrupt_before` 有细微差异,
导致审批循环逻辑失效。

**缓解**: P0.2 单独作为一个可验证的子任务,先写集成测试验证中断-恢复流程,
再修改生产代码。

### Risk 3: `excluded_tools` 未完全隐藏内置工具

**风险**: 如果 `excluded_tools` 配置不完整,LLM 可能同时看到项目工具和内置工具,
导致工具选择混乱。

**缓解**: 写单元测试验证 `agent.get_graph()` 的工具列表不包含被排除的工具。

### Risk 4: `SummarizationMiddleware` 与项目 `summarizer.py` 冲突

**风险**: 两个摘要机制同时运行可能产生冲突(如摘要触发时机重叠)。

**缓解**: P1.2 中先禁用自研 summarizer 的自动触发路径,
仅保留 `/api/chat/compact` 手动端点(映射为直接调用 summarizer 函数),
确认 deepagents 自动摘要正常工作后再清理。

### Risk 5: `memory=` 与 Router 注入逻辑兼容性

**风险**: 迁移后 Router `graph.py` 注入逻辑变化(去掉 `project_context_prompt` 拼接,
改由 `memory=` 加载 AGENTS.md + rules),可能与现有注入顺序/语义不一致,
导致 system prompt 拼装结果与原行为偏离(如 AGENTS.md 与 rules 的相对顺序、
与 `project_system_prompt`/`profile`/`skill` 的拼接层级)。

**缓解**: P1.4 中先验证 `memory=` 加载的 AGENTS.md + rules 与现有
`project_config.context_prompt` 内容一致(含 rules 排序、symlink 防护、大小截断),
保留 `project_config/loader.py`(仅 `context_prompt` 不再用于 prompt 注入) +
`merger.py` 不变;`project_system_prompt`(`.agentx/system_prompt.md`)与
`profile_store.build_profile_prompt()` 仍通过 `system_prompt` 注入(不经过 `memory=`),
确认注入链等价后再清理 Router 拼接代码。

### Risk 6: `write_todos` 迁移前端适配

**风险**: 删除 `_DEEP_SYSTEM_PROMPT` JSON plan 指令后,`plan`/`plan_update` SSE 事件
不再产生。前端 `TodoProgress.tsx` 若仅解析 JSON plan 事件(而非通用 tool_call 事件)
渲染 todo,迁移后 todo 面板可能空白。

**缓解**: P0.1 迁移前先验证 `TodoProgress.tsx` 能渲染 `write_todos` 的 tool_call 事件
(前端已支持 tool_call 事件流,但需确认 todo 面板的具体渲染逻辑是否覆盖)。
若 `TodoProgress.tsx` 仅消费 `plan`/`plan_update` 事件,需适配为消费 `write_todos` tool_call。
事件格式向后兼容(仅不再触发,不改变格式),可灰度验证。

## Migration Strategy

### 分阶段迁移

1. **P0.1** (最小风险): 仅替换 `create_react_agent` → `create_deep_agent`,
   `interrupt_before` 保持不变,验证基本功能
2. **P0.2** (中等风险): 切换 `interrupt_before` → `interrupt_on`,
   适配审批循环
3. **P0.3** (低风险): 删除 recovery.py 自研修复
4. **P1.1** (零风险): Prompt Caching 自动开启
5. **P1.2** (中等风险): 替换 summarizer + 启用 Context Offloading
6. **P1.3** (低风险): skills= 参数替换 skills_loader.py
7. **P1.4** (中等风险): memory= 参数接管 AGENTS.md + rules 加载,
   Router `graph.py` 简化注入逻辑(去掉 `project_context_prompt` 拼接,
   保留 `project_system_prompt` 注入);保留 `project_config/loader.py` +
   `merger.py` + `profile_store.build_profile_prompt()`

### 回滚策略

- 每个 P0 子任务独立提交,出问题可单独 revert
- `create_react_agent` 调用保留在 git 历史中,可作为 fallback
- P1 阶段的 summarizer 迁移保留自研代码直到确认 deepagents 版本稳定
