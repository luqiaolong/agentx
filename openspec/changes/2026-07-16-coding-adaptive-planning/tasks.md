# 任务追踪 — coding-adaptive-planning

## 预期修改文件

### 后端（5 文件：1 新增 + 4 修改）
- [ ] `backend/app/team/complexity_classifier.py` — **新增** ComplexityClassifier + ComplexityResult + prompts
- [ ] `backend/app/deepagent/factory.py` — AggressiveTodoMiddleware + force_todo 参数 + ensure_harness_profile 扩展
- [ ] `backend/app/scenarios/coding/agent.py` — _build_subagents 团队角色 + build_coding_expert + run_coding_expert 入口
- [ ] `backend/app/config/settings.py` — 4 个 coding_complexity_* 配置字段
- [ ] `backend/app/config/prompts/agent.py` — coding Expert prompt 强化任务规划引导

### 测试（4 文件新增）
- [ ] `tests/python/unit/test_complexity_classifier.py` — ComplexityClassifier 单测
- [ ] `tests/python/unit/test_factory_force_todo.py` — force_todo + AggressiveTodoMiddleware 单测
- [ ] `tests/python/unit/test_coding_subagents_team_roles.py` — 团队角色注入单测
- [ ] `tests/python/unit/test_run_coding_expert_complex.py` — run_coding_expert 集成测试

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1.1 | 新建 complexity_classifier.py 骨架 | complexity_classifier.py | ComplexityResult 模型 + ComplexityClassifier 类 + __init__ | ⬜ |
| T1.2 | 实现 LLM 路径 | complexity_classifier.py | make_structured_llm + ainvoke + 缓存 | ⬜ |
| T1.3 | 实现启发式降级 | complexity_classifier.py | 4 信号检测 + _heuristic_fallback | ⬜ |
| T1.4 | LLM prompt 模板 | complexity_classifier.py | _COMPLEXITY_PROMPT 含判断规则 | ⬜ |
| T2.1 | factory.py 新增 _AGGRESSIVE_TODO_SYSTEM_PROMPT | factory.py | 含强制 write_todos + task 并行委派引导 | ⬜ |
| T2.2 | factory.py 新增 _AGGRESSIVE_TODO_TOOL_DESCRIPTION | factory.py | 覆盖默认劝退型描述 | ⬜ |
| T2.3 | factory.py 新增 _build_aggressive_todo_middleware | factory.py | 返回 TodoListMiddleware(system_prompt=..., tool_description=...) | ⬜ |
| T2.4 | factory.py create_agent 新增 force_todo 参数 | factory.py | True 时 excluded_middleware + 注入 AggressiveTodoMiddleware | ⬜ |
| T2.5 | factory.py ensure_harness_profile 支持 excluded_middleware | factory.py | profile key 含 excluded_middleware 哈希；HarnessProfile.excluded_middleware 传入 | ⬜ |
| T3.1 | coding/agent.py _build_subagents 新增 include_team_roles 参数 | coding/agent.py | False 时当前行为不变 | ⬜ |
| T3.2 | coding/agent.py 新增 _build_team_role_subagents | coding/agent.py | 从 settings.team_subagents 构造 SubAgent 列表 | ⬜ |
| T3.3 | coding/agent.py build_coding_expert 透传 force_todo + include_team_roles | coding/agent.py | 参数透传到 create_agent | ⬜ |
| T3.4 | coding/agent.py run_coding_expert 入口集成 ComplexityClassifier | coding/agent.py | 分类 + 异常降级 + 日志 | ⬜ |
| T4.1 | settings.py 新增 4 个配置字段 | settings.py | 默认值 + Field 约束 | ⬜ |
| T4.2 | prompts/agent.py 强化 coding Expert 任务规划引导 | prompts/agent.py | 删除"简单任务可直接执行"劝退文本 | ⬜ |
| T5.1 | 单测：ComplexityClassifier LLM 路径 | test_complexity_classifier.py | mock LLM 返回 is_complex=True/False | ⬜ |
| T5.2 | 单测：ComplexityClassifier 启发式各信号 | test_complexity_classifier.py | 4 信号分别测试 | ⬜ |
| T5.3 | 单测：ComplexityClassifier 缓存命中 | test_complexity_classifier.py | 同 message 第二次不调 LLM | ⬜ |
| T5.4 | 单测：force_todo=True 排除默认 + 注入 Aggressive | test_factory_force_todo.py | middleware 栈断言 | ⬜ |
| T5.5 | 单测：force_todo=False 当前行为不变 | test_factory_force_todo.py | 默认 TodoListMiddleware 存在 | ⬜ |
| T5.6 | 单测：excluded_middleware 生成不同 profile key | test_factory_force_todo.py | key 含 excluded_middleware hash | ⬜ |
| T5.7 | 单测：include_team_roles=True 含团队角色 | test_coding_subagents_team_roles.py | SubAgent 列表含 frontend_dev 等 | ⬜ |
| T5.8 | 单测：include_team_roles=False 不含团队角色 | test_coding_subagents_team_roles.py | 列表与当前一致 | ⬜ |
| T5.9 | 集成：复杂任务 → force_todo=True + include_team_roles=True | test_run_coding_expert_complex.py | mock classifier 验证参数传递 | ⬜ |
| T5.10 | 集成：简单任务 → 当前路径不变 | test_run_coding_expert_complex.py | force_todo=False | ⬜ |
| T5.11 | 集成：coding_complexity_enabled=False 跳过分类 | test_run_coding_expert_complex.py | 不构造 classifier | ⬜ |
| T5.12 | 集成：分类异常降级 | test_run_coding_expert_complex.py | warning 日志 + 简单路径 | ⬜ |
| T6.1 | pytest 全绿 | — | exit 0 | ⬜ |

## 规模判定
- 涉及文件数: 9（含测试） → 规模: **M**
- 涉及模块数: 2（backend/team + backend/scenarios/coding）
- 跨模块: 否（均在 backend 内） → 全流程（Worktree + TDD + Review + 验证）
