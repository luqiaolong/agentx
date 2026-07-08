# Tasks: 智能体架构重构 — Supervisor + Expert 场景化体系

## 1. 配置体系和 agents 包骨架（Phase 1）

- [x] 1.1 创建 `backend/app/config/agents.py`，定义 `SupervisorSettings` / `ExpertSettings` / `ScenarioTeamSettings` 模型
- [x] 1.2 扩展 `backend/app/config/settings.py`，新增 `agents_config` 字段和 `agents` property，读取 `AGENTX_AGENTS_CONFIG` 环境变量
- [x] 1.3 创建 `backend/app/config/prompts/agent.py`，定义 `_DEFAULT_SUPERVISOR_SYSTEM_PROMPT` 和 `_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT`
- [x] 1.4 创建 `backend/app/agents/` 包骨架：`__init__.py` / `supervisor/__init__.py` / `expert/__init__.py` / `team/__init__.py`
- [x] 1.5 编写配置体系的单元测试（模型校验、环境变量读取、默认值）

## 2. 实现 supervisor（work 全能 agent）（Phase 2）

- [x] 2.1 创建 `backend/app/agents/supervisor/work_supervisor.py`，实现 `build_work_supervisor()` 和 `run_work_supervisor()`
- [x] 2.2 基于 `create_react_agent` 构建，集成完整工具集（fs 读写 + cli + git + rag + web），复用 `app.tools` 和 `app.subagents.base`
- [x] 2.3 实现 `delegate_to_expert` 工具（参数：`expert_name` / `task` / `context`），委派 coding expert
- [x] 2.4 实现 `delegate_to_subagent` 工具（参数：`agent_name` / `task`），委派 rag/web 子代理
- [x] 2.5 配置 `interrupt_before` 为危险工具（write_file / edit_file / cli_execute / git_write）触发审批流
- [x] 2.6 定义 supervisor system prompt（全能 agent 角色，明确各 expert/子代理职责边界，引导 LLM 自主决策自己执行还是委派 expert）
- [x] 2.7 实现 @mention 解析逻辑（`@coding` / `@rag` / `@web` 强制委派，覆盖自主决策）
- [x] 2.8 确保 `invoke_agent_team` 工具不存在于 supervisor
- [x] 2.9 编写 supervisor 的单元测试（简单问答直接回复、文件写操作审批、expert 委派、子代理委派、@mention 解析）

## 3. 实现 coding expert（专家 agent）（Phase 3）

- [x] 3.1 创建 `backend/app/agents/expert/coding.py`，实现 `build_coding_expert()` 和 `run_coding_expert()`
- [x] 3.2 基于 `build_deep_agent` 框架构建，替换 system prompt 为 coding 专用
- [x] 3.3 集成代码专用工具集（fs 读写 + cli + git + rag + web），复用 `app.deep.tools` 和 `app.subagents.base`
- [x] 3.4 实现 `delegate_to_subagent` 工具（coding expert 内部调用 rag/web）
- [x] 3.5 配置 `interrupt_before` 审批流（复用 DeepAgent 机制）
- [x] 3.6 确保 `delegate_to_expert` 和 `invoke_agent_team` 工具不存在于 coding expert（expert 不可委派其他 expert，也不可触发 team）
- [x] 3.7 创建 `backend/app/agents/expert/__init__.py`，导出 `run_coding_expert` 和 `build_coding_expert`
- [x] 3.8 编写 coding expert 的单元测试（工具集构建、审批流触发、子代理调用、无 team 触发）

## 4. 改造 coding team 为场景级（Phase 4）

- [x] 4.1 创建 `backend/app/agents/team/coding_team.py`，实现 `run_coding_team()`，复用 `app.team` 框架
- [x] 4.2 调整 `backend/app/team/orchestrator.py`，移除对旧 `agent_team` 顶层模式的依赖
- [x] 4.3 调整 `backend/app/team/scheduler.py`，将 `agent_name == "code"` 分支映射到 coding expert
- [x] 4.4 调整 `backend/app/team/orchestrator.py` 降级逻辑，移除对旧 CHAT 路径的回退（改为建议用户切换模式）
- [x] 4.5 创建 `backend/app/agents/team/__init__.py`，导出 `run_coding_team`
- [x] 4.6 编写 coding team 的单元测试（入口调用、角色映射、降级逻辑）

## 5. 改造 Router 为场景分发（Phase 5）

- [x] 5.1 改造 `backend/app/router/graph.py` 的 `run_router()`，改为场景+模式直接分发：
  - `agent_mode == "work"` → `run_work_supervisor()`
  - `agent_mode == "coding"` → `run_coding_expert()`
  - `agent_mode == "coding_team"` → `run_coding_team()`
- [x] 5.2 删除 `backend/app/router/classifier.py` 的 CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径分类逻辑
- [x] 5.3 更新 `backend/app/router/state.py` 的 `RouterState`，添加 `agent_mode` 字段
- [x] 5.4 更新 `backend/app/router/__init__.py`，移除已删除的分类器导出
- [x] 5.5 编写 Router 场景分发的集成测试（三种模式分发、无效模式报错）

## 6. 删除旧逻辑（Phase 6）

- [x] 6.1 删除 `backend/app/subagents/code_agent.py` 文件
- [x] 6.2 从 `backend/app/subagents/__init__.py` 移除 `code_agent` 导出
- [x] 6.3 从 `backend/app/subagents/dispatch.py` 移除 code 子代理的路由逻辑
- [x] 6.4 更新 `llm_select_subagent()` 和 `keyword_select_subagent()`，排除 code 选项
- [x] 6.5 从 `backend/app/config/subagents.py` 移除 code 子代理默认配置，`BUILTIN_SUBAGENT_KEYS` 改为 `{"rag", "web"}`
- [x] 6.6 从 `backend/app/config/prompts/builtin.py` 移除 code 相关 prompt
- [x] 6.7 删除 `backend/app/deep/` 中 DEEP_TASK 路径分发逻辑（保留 `build_deep_agent` 框架供 coding expert 复用）
- [x] 6.8 清理 `backend/app/api/chat.py` 中对旧 `agent` / `agent_team` 模式的引用
- [x] 6.9 删除 code 子代理相关测试文件
- [x] 6.10 编写子代理精简后的回归测试（rag/web 正常工作、code 不可达）

## 7. API 层扩展（Phase 7 - 后端部分）

- [x] 7.1 扩展 `backend/app/api/schemas.py` 的 `ChatRequest.agent_mode`，枚举改为 `"work" / "coding" / "coding_team"`，移除 `"agent"` / `"agent_team"`
- [x] 7.2 更新 `backend/app/api/chat.py` 的 `_event_generator()`，透传新的 `agent_mode` 值
- [x] 7.3 新增 `GET /api/agents/mentionable` 端点，返回可用 @mention agent 列表（expert+子代理）
- [x] 7.4 添加 `AGENTX_AGENTS_CONFIG` 到 `.env.example`
- [x] 7.5 编写 API 层测试（新枚举值验证、无效值 400 报错、mentionable 端点）

## 8. 前端模式扩展（Phase 7 - 前端部分）

- [x] 8.1 扩展 `frontend/shared/api-types.ts` 的 `AgentMode` 类型为 `"work" | "coding" | "coding_team"`
- [x] 8.2 更新 `frontend/renderer/stores/agentMode.ts`，支持三模式存储和持久化，旧值重置为 `work`
- [x] 8.3 扩展模式选择器 UI 组件，按场景分组 `[Work] | [Coding Agent] [Coding Team]`
- [x] 8.4 实现 `coding_team_enabled=false` 时隐藏 Coding Team 按钮
- [x] 8.5 实现模式指示器（非 work 模式下显示当前模式标识）
- [x] 8.6 实现输入框 `@` 语法自动补全（仅 work 模式生效，从 `/api/agents/mentionable` 获取列表）
- [x] 8.7 实现 @mention 文本高亮样式（expert 蓝色、子代理绿色）
- [x] 8.8 实现 @mention 解析逻辑，在 work 模式下包含目标 agent 在请求中
- [x] 8.9 实现 coding/coding_team 模式下 @mention 剥离逻辑
- [x] 8.10 更新前端 SSE 事件处理，支持 `source="work"` / `"coding"` / `"coding_team"` 新标识，移除 `"code"` / `"deep"` 处理
- [x] 8.11 编写前端单元测试（模式切换、@mention 解析、自动补全、旧值重置）

## 9. 测试与验证（Phase 8）

- [x] 9.1 端到端测试：work 模式简单问答直接回复
- [x] 9.2 端到端测试：work 模式文件写操作审批流（supervisor 自己执行）
- [x] 9.3 端到端测试：work 模式委派 coding expert（含审批流）
- [x] 9.4 端到端测试：work 模式调用 rag 子代理
- [x] 9.5 端到端测试：work 模式 @coding 强制路由到 expert
- [x] 9.6 端到端测试：coding 模式直接处理代码任务（expert 独立执行）
- [x] 9.7 端到端测试：coding expert 内部调用 rag 子代理
- [x] 9.8 端到端测试：coding_team 模式多 expert 协作
- [x] 9.9 端到端测试：无效 agent_mode 返回 400 错误
- [x] 9.10 端到端测试：coding/coding_team 模式下 @mention 被剥离
- [x] 9.11 冒烟测试：前端三模式切换、@mention 自动补全
- [x] 9.12 性能测试：work 模式路由延迟 < 500ms

## 10. 文档更新（Phase 8）

- [x] 10.1 更新 `AGENTS.md` 的架构章节，描述 Supervisor + Expert 场景化架构
- [x] 10.2 更新 `AGENTS.md` 的 Router 路径说明，改为场景+模式分发
- [x] 10.3 更新 `AGENTS.md` 的 SSE 事件契约，添加新 source 标识
- [x] 10.4 更新 `.env.example`，添加 `AGENTX_AGENTS_CONFIG` 示例
- [x] 10.5 更新项目 README（如有架构图）
