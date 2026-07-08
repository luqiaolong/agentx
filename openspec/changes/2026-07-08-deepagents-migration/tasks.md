# 任务追踪 — DeepAgents 0.2+ 特性充分利用迁移

## 阶段零:依赖升级

- [x] T0.1 `pyproject.toml` 升级 `deepagents>=0.6.12`,运行 `uv sync` 验证依赖树
- [x] T0.2 确认 deepagents 0.6.12 的 `create_deep_agent` 签名与项目 agent 构建函数兼容
- [x] T0.3 验证 `langchain`/`langgraph` 传递依赖版本不冲突,必要时一并升级

## 阶段一(P0):核心迁移

### P0.1: create_deep_agent 替换 create_react_agent

- [x] T1.1 新建 `backend/app/deep/harness.py`:`build_deepagent_config()` 封装
  `excluded_tools`(隐藏内置 fs 工具)+ `HarnessProfile` 注册(禁用默认 subagent)+
  `build_interrupt_config()`(从 DANGEROUS_TOOLS 生成 interrupt_on)+
  `resolve_backend()`(返回 `FilesystemBackend(root_dir=workspace_path)`)+
  `resolve_memory_paths()`(返回 AGENTS.md + rules 路径列表)+
  `resolve_skills_dir()`(返回 data/skills/ 路径)
- [x] T1.2 [deep/agent.py](../../../backend/app/deep/agent.py) `build_deep_agent`:
  `create_react_agent` → `create_deep_agent`,保留 checkpointer,`prompt=` → `system_prompt=`,
  移除 `_DEEP_SYSTEM_PROMPT` 中 JSON plan 指令(改用 `write_todos` 工具)
- [x] T1.3 [agents/supervisor/work_supervisor.py](../../../backend/app/agents/supervisor/work_supervisor.py)
  `build_work_supervisor`:`create_react_agent` → `create_deep_agent`
- [x] T1.4 [agents/expert/coding.py](../../../backend/app/agents/expert/coding.py):
  验证通过 `build_deep_agent` 间接迁移正常
- [x] T1.5 验证 `write_todos` 工具正确注册,LLM 可调用,tool_call SSE 事件正常流式传输
- [x] T1.6 验证 `excluded_tools` 生效:agent 工具列表不含 `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`/`task`
- [x] T1.7 验证 `backend=FilesystemBackend(root_dir=workspace_path)` 启用 Context Offloading,
  大工具结果自动卸载到虚拟文件系统

### P0.2: interrupt_on 替换 interrupt_before

- [x] T2.1 `build_deepagent_config()` 添加 `interrupt_on` 生成逻辑:
  从 `DANGEROUS_TOOLS` 动态生成 `dict[str, True]`
- [x] T2.2 [deep/agent.py](../../../backend/app/deep/agent.py) 审批循环:
  移除 `interrupt_before=["tools"]`,适配 `interrupt_on` 语义
- [x] T2.3 [agents/supervisor/work_supervisor.py](../../../backend/app/agents/supervisor/work_supervisor.py)
  审批循环同步适配
- [x] T2.4 调整 `_should_skip_interrupt`(工作区自动放行)逻辑:
  中断后直接走审批,工作区放行下沉到审批循环内部
- [x] T2.5 写集成测试:验证 `edit_file` 中断 → 审批 → 恢复流程
- [x] T2.6 写集成测试:验证 `read_file`(非危险工具)不中断,直接执行

### P0.3: 删除自研消息历史修复

- [x] T3.1 [deep/agent.py](../../../backend/app/deep/agent.py):移除
  `_inject_tool_error_messages` / `_sanitize_message_history` 调用
- [x] T3.2 [deep/recovery.py](../../../backend/app/deep/recovery.py):删除上述两个函数,
  保留 `_to_serializable`
- [x] T3.3 验证 `PatchToolCallsMiddleware` 自动修复悬空 tool_calls
- [x] T3.4 写测试:构造悬空 tool_calls 消息历史,验证中间件自动修复

## 阶段二(P1):高价值能力启用

### P1.1: AnthropicPromptCachingMiddleware

- [x] T4.1 验证 `create_deep_agent` 在 Anthropic 模型上自动启用 Prompt Caching
- [x] T4.2 验证非 Anthropic 模型(如 OpenAI)不受影响

### P1.2: SummarizationMiddleware + Context Offloading 替换自研 summarizer

- [x] T5.1 [memory/summarizer.py](../../../backend/app/memory/summarizer.py):
  禁用自动触发路径,保留 `summarize_messages` 函数(供 `/api/chat/compact` 手动调用)
- [x] T5.2 [memory/context.py](../../../backend/app/memory/context.py):
  删除 `trim_messages_with_budget` + `_ensure_tool_call_pairing`
  (由 `SummarizationMiddleware` + `PatchToolCallsMiddleware` 接管),
  若无其他引用则删除整个文件
- [x] T5.3 [deep/agent.py](../../../backend/app/deep/agent.py):`max_iterations` 从 50 放宽到 100
- [x] T5.4 验证 `SummarizationMiddleware` 在 85% token 阈值自动触发
- [x] T5.5 验证 Context Offloading:大工具结果(>20k token)自动卸载到虚拟文件系统,返回前 10 行预览
- [x] T5.6 写测试:构造超长对话历史,验证自动摘要 + offloading 触发后上下文被压缩

### P1.3: skills= 参数替换自研 skills_loader.py

- [x] T6.1 迁移现有 `data/skills/*.md` 到 `data/skills/<name>/SKILL.md` 目录结构
- [x] T6.2 frontmatter 字段对齐 agentskills.io 规范:`name` + `description` 必填,
  `trigger`/`tools` 保持可选
- [x] T6.3 [memory/skills_loader.py](../../../backend/app/memory/skills_loader.py):
  用 deepagents `skills=` 参数替换自研加载逻辑,删除渐进式披露自研代码
- [x] T6.4 保留 `@skill:<name>` 标签解析(`parse_mention` 函数)供前端交互
- [x] T6.5 写测试:验证 skills= 参数加载 SKILL.md + 渐进式披露 + @skill: 标签解析

### P1.4: memory= 参数替换自研 prompt 注入

- [x] T8.1 [deep/harness.py](../../../backend/app/deep/harness.py) `create_agent`:
  添加 `memory=` 参数,`resolve_memory_paths(workspace_path)` 返回
  `[.agentx/AGENTS.md] + [.agentx/rules/*.md]` 文件路径列表
- [x] T8.2 [router/graph.py](../../../backend/app/router/graph.py):
  简化注入逻辑——去掉 `project_context_prompt`(AGENTS.md + rules)拼接到 `profile_prompt`,
  保留 `project_system_prompt`(.agentx/system_prompt.md)注入
- [x] T8.3 [deep/agent.py](../../../backend/app/deep/agent.py) `build_deep_agent`:
  调用 `create_agent(memory=memory_paths, system_prompt=profile_prompt + workspace_suffix)`,
  `profile_prompt` 不再包含 AGENTS.md + rules(由 `memory=` 接管)
- [x] T8.4 [agents/supervisor/work_supervisor.py](../../../backend/app/agents/supervisor/work_supervisor.py):
  同步适配 `memory=` 参数
- [x] T8.5 保留 `project_config/loader.py` 和 `merger.py`(仍加载 mcp.json/subagents.json/tools.json/system_prompt.md)
- [x] T8.6 保留 `profile_store.build_profile_prompt()`(用户画像仍通过 `system_prompt` 注入)
- [x] T8.7 验证 `memory=` 加载结果与原 `context_prompt`(AGENTS.md + rules)等价
- [x] T8.8 写测试:验证 memory= 加载 AGENTS.md + rules + profile 画像注入

## 阶段三:测试与文档

- [x] T7.1 更新 [tests/python/unit/](../../../tests/python/unit/) 现有测试:
  适配 `create_deep_agent` 构建方式
- [x] T7.2 新增 `tests/python/unit/test_deepagents_integration.py`:
  验证 excluded_tools / interrupt_on / write_todos / 自动摘要 / offloading / memory= / skills=
- [x] T7.3 更新 [AGENTS.md](../../../AGENTS.md) §10 技术栈速查:
  deepagents 0.2 → 0.6.12 + 新增能力说明
- [x] T7.4 更新 [AGENTS.md](../../../AGENTS.md) §11 文件地图:
  新增 `deep/harness.py`,标注 recovery.py / skills_loader.py / summarizer.py 变更
- [x] T7.5 端到端冒烟测试:完整对话流 + 审批 + 规划 + 长会话摘要 + skills 加载 + memory 加载

## 预期修改文件

### 后端新建文件
- `backend/app/deep/harness.py` — deepagents 配置封装(excluded_tools + interrupt_on + memory= + skills= + backend= 生成)

### 后端修改文件
- `pyproject.toml` — deepagents>=0.6.12
- `backend/app/deep/agent.py` — create_deep_agent + interrupt_on + 移除 recovery 调用 + max_iterations + memory= + 移除 JSON plan 指令
- `backend/app/deep/recovery.py` — 删除 _inject_tool_error_messages / _sanitize_message_history
- `backend/app/agents/supervisor/work_supervisor.py` — create_deep_agent + interrupt_on + memory=
- `backend/app/agents/expert/coding.py` — 验证间接迁移
- `backend/app/deep/tools.py` — DANGEROUS_TOOLS 保留,interrupt_on 配置生成
- `backend/app/memory/skills_loader.py` — 删除自研加载逻辑,保留 parse_mention 标签解析
- `backend/app/memory/skills_store.py` — 适配 `data/skills/<name>/SKILL.md` 目录结构
- `backend/app/memory/summarizer.py` — 禁用自动触发,保留手动函数
- `backend/app/memory/context.py` — 删除 trim_messages_with_budget + _ensure_tool_call_pairing(或删除整个文件)
- `backend/app/router/graph.py` — 简化注入逻辑(去掉 project_context_prompt 拼接,保留 project_system_prompt)
- `backend/app/memory/profile_store.py` — 保留 build_profile_prompt()(用户画像仍通过 system_prompt 注入)

### 保留不修改的文件
- `backend/app/project_config/loader.py` — 保留(仍加载 mcp.json/subagents.json/tools.json/system_prompt.md)
- `backend/app/project_config/merger.py` — 保留(配置合并功能不变)

### 后端测试文件
- `tests/python/unit/test_deep_approval.py` — 适配 create_deep_agent
- `tests/python/unit/test_coding_expert.py` — 适配 create_deep_agent
- `tests/python/unit/test_supervisor.py` — 适配 create_deep_agent
- `tests/python/unit/test_deepagents_integration.py` — 新增
- `tests/python/unit/test_memory_skills.py` — 适配 skills 目录结构
- `tests/python/unit/test_memory.py` — 适配 skills_loader 清理
- `tests/python/unit/test_skills_api.py` — 适配 skills 目录结构
- `tests/python/unit/test_router_graph.py` — 适配 @skill 标签解析

### 文档文件
- `AGENTS.md` — §10 技术栈 + §11 文件地图

## 规模判定

- 涉及文件数: ~16(1 新建 + 10 修改 + 5 测试)
- 涉及模块数: 5(deep/ + agents/supervisor/ + agents/expert/ + memory/ + project_config/)
- 规模: **L(大改)** — 5+ 文件且跨模块,需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T0 | 依赖升级 deepagents>=0.6.12 | pyproject.toml | uv sync 无冲突 | ✅ |
| T1 | create_deep_agent 替换 + harness.py + backend= | deep/harness.py(新), deep/agent.py, work_supervisor.py | write_todos 可用, excluded_tools 生效, offloading 启用 | ✅ |
| T2 | interrupt_on 替换 interrupt_before | deep/agent.py, work_supervisor.py | 危险工具中断, 非危险工具直通 | ✅ |
| T3 | 删除自研消息修复 + 删除 trim_messages_with_budget | deep/recovery.py, deep/agent.py, memory/context.py | PatchToolCallsMiddleware + SummarizationMiddleware 接管 | ✅ |
| T4 | Prompt Caching 验证 | (零配置) | Anthropic 模型自动缓存 | ✅ |
| T5 | SummarizationMiddleware + Offloading 替换 summarizer | memory/summarizer.py, deep/agent.py | 85% 阈值自动摘要 + 大结果 offloading | ✅ |
| T6 | skills= 参数替换 skills_loader.py | memory/skills_loader.py, memory/skills_store.py, data/skills/* | 渐进式披露可用, @skill: 标签解析保留 | ✅ |
| T7 | 测试与文档 | tests/, AGENTS.md | 全部测试通过 | ✅ |
| T8 | memory= 参数替换自研 prompt 注入 | deep/harness.py, deep/agent.py, router/graph.py | AGENTS.md 自动加载, profile_extractor 保留 | ✅ |
| T9 | 删除 _DEEP_SYSTEM_PROMPT JSON plan | deep/agent.py | write_todos 替代, plan SSE 事件弃用 | ✅ |
