# Proposal: Coding 场景按需规划与子代理委派（方案 B）

## Why

traceId `0ea794896ae34593` 暴露的问题：coding 模式（`agent_mode=coding`）下，一个耗时 13.5 分钟、253+ 条消息的复杂任务（Ralph skill 执行 `deepagent-maintainability-refactor`）**全程未生成任何 todo 或 plan**，也**未委派任何子代理**。

根因两层：

1. **TodoListMiddleware 默认 prompt 劝退 LLM 调用 `write_todos`**：deepagents 库 `langchain.agents.middleware.todo.WRITE_TODOS_SYSTEM_PROMPT` 明确写有 *"For simple objectives... it is better to just complete the objective directly and NOT use this tool"*。coding 场景的 system prompt（`_DEFAULT_CODING_EXPERT_SYSTEM_PROMPT` line 83-86）也复述了"简单任务可直接执行，无需创建 todo"。LLM 对复杂任务也不调用 `write_todos`。
2. **coding 场景的子代理范围过窄**：[scenarios/coding/agent.py::_build_subagents](../../../backend/app/scenarios/coding/agent.py#L48) 只声明 rag/web/custom，**不含团队角色**（frontend_dev/backend_dev/tester/architect 等）。LLM 无法通过 `task` 工具委派给领域专家。

基础设施其实已就绪：

- `TodoListMiddleware` 已自动注入 coding 模式 agent（`create_deep_agent` 硬编码）
- `SubAgentMiddleware` + `task` 工具已注入，天然支持一个 AIMessage 多个 tool_calls 并行调度
- `todo_update` / `delegation` SSE 事件已由 streaming.py 支持

缺的是：(a) 替换"劝退型" todo prompt 为"强制型"；(b) 给 coding 模式注入团队角色子代理；(c) 复杂度分类器决定何时启用强制规划。

## What Changes

### C1. 新增 ComplexityClassifier

新文件 [backend/app/team/complexity_classifier.py](../../../backend/app/team/complexity_classifier.py)：

- 模板复用 `DangerousTaskClassifier`：缓存 → LLM 结构化输出 → 启发式降级
- 返回 `ComplexityResult(is_complex, reason, suggested_subagents)`
- 启发式降级信号：消息长度 > 阈值、多领域关键词共现、明确多步骤信号、history_count > 阈值
- LLM prompt：判断任务是否需要 (a) 3 步以上规划 (b) 子代理委派

### C2. 新增 AggressiveTodoMiddleware 构造器

修改 [backend/app/deepagent/factory.py](../../../backend/app/deepagent/factory.py)：

- 新增 `_AGGRESSIVE_TODO_SYSTEM_PROMPT`（强制 write_todos，指导并行 task 委派）
- 新增 `_AGGRESSIVE_TODO_TOOL_DESCRIPTION`（覆盖默认劝退型描述）
- 新增 `_build_aggressive_todo_middleware() -> TodoListMiddleware`
- `create_agent` 新增 `force_todo: bool = False` 参数：True 时通过 `HarnessProfile.excluded_middleware={"TodoListMiddleware"}` 排除默认实例，并经 `middleware=` 注入自定义实例

### C3. coding 模式注入团队角色子代理

修改 [backend/app/scenarios/coding/agent.py](../../../backend/app/scenarios/coding/agent.py)：

- `_build_subagents` 新增 `include_team_roles: bool = False` 参数
- True 时追加团队角色 SubAgent 声明（frontend_dev/backend_dev/tester/architect/devops/ui_designer/product_manager）
- `build_coding_expert` 透传 `include_team_roles` 到 `create_agent(subagents=...)`
- `run_coding_expert` 入口先跑 ComplexityClassifier，复杂时 `include_team_roles=True` + `force_todo=True`

### C4. 配置开关

修改 [backend/app/config/settings.py](../../../backend/app/config/settings.py)：

- `coding_complexity_enabled: bool = True`
- `coding_complexity_message_length: int = 200`（启发式长度阈值）
- `coding_complexity_history_count: int = 10`（启发式历史长度阈值）
- `coding_complexity_team_roles_enabled: bool = True`

### C5. 测试

- 单元：ComplexityClassifier（mock LLM + 启发式各信号）
- 单元：AggressiveTodoMiddleware prompt 内容断言
- 集成：复杂任务 → build_coding_expert 含 force_todo=True + include_team_roles=True
- 集成：简单任务 → 当前路径不变

## Capabilities

### Modified Capabilities

- `agent-mode`（coding 场景）：入口增加复杂度分类层；复杂任务构建增强 agent（强制 todo + 团队角色子代理）；简单任务路径不变

### New Capabilities

- `coding-complexity-classifier`：LLM + 启发式任务复杂度分类器，决定 coding 模式是否启用强制规划

## Impact

- **后端**：修改 4 个文件、新增 1 个文件
  - 新增 [backend/app/team/complexity_classifier.py](../../../backend/app/team/complexity_classifier.py)（ComplexityClassifier + ComplexityResult + prompts）
  - 修改 [backend/app/deepagent/factory.py](../../../backend/app/deepagent/factory.py)（AggressiveTodoMiddleware + force_todo 参数）
  - 修改 [backend/app/scenarios/coding/agent.py](../../../backend/app/scenarios/coding/agent.py)（_build_subagents + build_coding_expert + run_coding_expert）
  - 修改 [backend/app/config/settings.py](../../../backend/app/config/settings.py)（4 个配置字段）
  - 修改 [backend/app/config/prompts/agent.py](../../../backend/app/config/prompts/agent.py)（coding Expert prompt 强化任务规划引导）
- **SSE 事件**：无新事件类型（复用现有 `todo_update` / `delegation` / `classification`）
- **依赖**：无新依赖（TodoListMiddleware/SubAgentMiddleware 已在 deepagents/langchain 中）
- **风险**：MEDIUM
  - LLM 行为依赖 prompt 引导（非代码强制），仍有概率不调用 write_todos
  - 团队角色子代理作为 inline subagent 运行，会增加 token 消耗
  - ComplexityClassifier LLM 路径增加 ~100ms 延迟（缓存命中后 0ms）

## Rollback Plan

1. **代码层**：所有变更集中于单分支，回滚即 `git revert` 单个 merge commit；无数据迁移
2. **配置层**：`coding_complexity_enabled=False` 即关闭整个特性，回退到当前行为
3. **降级安全**：ComplexityClassifier 失败 → 默认简单路径；LLM 不调用 write_todos → 等价当前行为
4. **验证门禁**：合并前必须通过 `pytest tests/python/unit -m "not integration"` + 现有单测全绿

## Out of Scope

- 不引入 DAG 依赖编排（deepagents `Todo` schema 仅 `content + status`，无 `depends_on`）
- 不替换 coding 模式的主体执行模型为 team graph（保留单 agent 主控）
- 不修改 `task` 工具 schema（复用 deepagents 原生 `subagent_type + description`）
- 不做 ComplexityClassifier 的前端 UI 展示（仅后端日志 + `classification` SSE 事件）
- 不动 work / coding_team 模式
