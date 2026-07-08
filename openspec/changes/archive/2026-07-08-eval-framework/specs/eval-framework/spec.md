# Spec: AgentX 评测框架 — deepagents RubricMiddleware 集成

## 功能需求

### FR-1: L2 RubricJudge（替换 LlmJudge）

**FR-1.1** `backend/app/eval/judges/rubric_judge.py` 必须实现 `RubricJudge` 类，用 deepagents
`GraderResponse` + `GRADER_SYSTEM_PROMPT` 做事后 rubric 评分。

**FR-1.2** `RubricJudge.evaluate(events, case)` 必须在 `case.expect.rubric is None` 时返回 skipped。

**FR-1.3** `RubricJudge` 必须用 `langchain.agents.create_agent` 构建 grader 子代理，
`response_format=GraderResponse`，`system_prompt=GRADER_SYSTEM_PROMPT`。

**FR-1.4** `RubricJudge` 必须复用 deepagents `_build_grader_transcript` 做 transcript 边界控制
（30 条消息 + 4000 字符/条）。

**FR-1.5** GraderResponse → JudgeResult 映射：
- `satisfied` → passed=True, score=5.0
- `needs_revision` → passed=False, score=3.0
- `failed` → passed=False, score=0.0

**FR-1.6** `JudgeResult.details` 必须含 `grader_result`/`explanation`/`criteria[]`，
每个 criterion 含 `name`/`passed`/`gap`。

**FR-1.7** 降级策略（任一触发即 skipped，passed=True, score=5.0）：
- `--no-rubric` 标志
- 无 `AGENTX_*_API_KEY` 环境变量
- `get_chat_model()` 抛 ValueError
- grader 调用失败或 `structured_response` 解析失败

### FR-2: L3 SelfCorrectionRunner（in-loop 自纠）

**FR-2.1** `backend/app/eval/judges/self_correction.py` 必须实现 `SelfCorrectionRunner` 类，
用 `RubricMiddleware` 注入 `create_deep_agent` 驱动 agent 自我迭代。

**FR-2.2** `SelfCorrectionRunner.run_case(case)` 必须用 `create_deep_agent(middleware=[RubricMiddleware(...)])`
构建 agent，调用时传入 `{"messages": [...], "rubric": case.expect.rubric}`。

**FR-2.3** `RubricMiddleware` 的 `max_iterations` 默认 3，可由 `case.expect.self_correct_max_iterations`
覆盖（hard cap 20）。

**FR-2.4** agent 执行完成后必须从 `agent.aget_state(config).values` 读取 `_rubric_status` 和
`_rubric_evaluations`。

**FR-2.5** RubricResult → JudgeResult 映射：
- `satisfied` → passed=True, score=5.0
- `max_iterations_reached` → passed=False, score=3.0
- `failed` → passed=False, score=0.0
- `grader_error` → passed=False, score=0.0
- None（未触发） → passed=False, score=0.0

**FR-2.6** `JudgeResult.details` 必须含 `rubric_status`/`iterations`/`evaluations[]`，
每个 evaluation 含 `iteration`/`result`/`explanation`/`criteria[]`。

**FR-2.7** mock 模式下（`chat_model=MockChatModel`），grader 也必须用 MockChatModel；
无 grader fixtures 时 L3 降级为 skipped（passed=True, score=5.0）。
降级检查必须在构造 `RubricMiddleware` **之前**执行（`RubricMiddleware(model=None)` 会抛 ValueError）。

### FR-3: 数据模型变更

**FR-3.1** `CaseExpect.llm_judge: LlmJudgeConfig | None` 必须替换为 `CaseExpect.rubric: str | None`。

**FR-3.2** `CaseExpect` 必须新增 `self_correct: bool = False` 和
`self_correct_max_iterations: int = 3`。

**FR-3.3** 必须删除 `LlmJudgeConfig` 类。

**FR-3.4** `JudgeLayer` 必须扩展为 `Literal["L1", "L2", "L3"]`。

### FR-4: EvalRunner 集成

**FR-4.1** `EvalRunner.run_case(case)` 必须在 `case.expect.self_correct and case.expect.rubric`
且 `not self.no_rubric` 为 True 时走 `SelfCorrectionRunner`，否则走 `run_router`（默认路径）。
`--no-rubric` 时即使 `self_correct=true` 也不进入 L3（避免无 API key 时构造 `RubricMiddleware` 失败）。

**FR-4.2** `EvalRunner.apply_judge_results` 必须将 L3 结果纳入 `passed` 计算（all judges passed），
`avg_score` 取 L2+L3 均值（无 L2/L3 时 L1 全 passed → 5.0）。

**FR-4.3** `EvalRunner.__init__` 必须新增 `grader_model` 可选参数，供 L3 `SelfCorrectionRunner` 透传。
默认 None 时 `SelfCorrectionRunner` 内部调 `_get_grader_model()`。

**FR-4.4** `EvalRunner.__init__` 必须新增 `no_rubric` 可选参数（默认 False），用于 `--no-rubric` 标志透传，
控制 L3 分支是否触发。

### FR-5: CLI 变更

**FR-5.1** `--no-llm-judge` 必须重命名为 `--no-rubric`。

**FR-5.2** `agentx eval run --no-rubric` 必须跳过 L2 RubricJudge。

**FR-5.3** `_run_suite_async` 必须用 `RubricJudge(no_rubric=...)` 替换 `LlmJudge(no_llm_judge=...)`。

### FR-6: pytest 插件变更

**FR-6.1** mock 模式下必须用 `RubricJudge(no_rubric=True)` 跳过 L2。

**FR-6.2** live 模式下必须用 `RubricJudge()` 正常执行 L2。

### FR-7: YAML 评测集

**FR-7.1** 必须提供 5 个 suite：`smoke.yaml`/`router.yaml`/`tools.yaml`/`team.yaml`/`cli.yaml`。

**FR-7.2** 总 case 数 ≥ 20。

**FR-7.3** 每个 suite 必须有至少 1 个 case 声明 `rubric`。

**FR-7.4** smoke suite 必须有至少 1 个 case 声明 `self_correct: true`。

**FR-7.5** YAML schema 用 `rubric: "文本"` 替换 `llm_judge: {...}`。

### FR-8: 发版门禁脚本

**FR-8.1** `scripts/eval-gate.ps1` 必须调用 `agentx eval run --suite smoke --format=console,md`。

**FR-8.2** 退出码非 0 时必须打印 "评测门禁失败，禁止发版" 并退出 1。

**FR-8.3** 通过时必须打印 "评测门禁通过"。

## 非功能需求

### NFR-1: 离线可跑

`agentx eval run --suite smoke --mock` 在无 `OPENAI_API_KEY`、无 myserver、无 HTTP 8123 的情况下
必须 30 秒内完成，全部通过。
- L2 RubricJudge 降级为 skipped（无 API key）
- L3 SelfCorrectionRunner：`--mock` 注入 `MockChatModel` 作为 agent model；
  若未注入 grader_model 且无 API key，L3 降级为 skipped（不进入 `RubricMiddleware` 构造）
- smoke suite 的 self_correct case 必须能在 mock 模式下通过（grader 降级为 skipped → passed=True）

### NFR-2: 零外部依赖（mock 模式）

mock 模式下不发起任何网络请求（不连 LLM、不连 Milvus、不连 TEI）。

### NFR-3: 模块隔离

`app.eval` 包不导入 `app.api`（FastAPI 层），不导入 `app.cli`（CLI 层）。
`app.eval.judges.rubric_judge` 和 `app.eval.judges.self_correction` 可导入 `deepagents`。

### NFR-4: deepagents 依赖消费

本次改造必须实际调用 deepagents 的 `RubricMiddleware`/`GraderResponse`/`GRADER_SYSTEM_PROMPT`/
`_build_grader_transcript`/`create_deep_agent`，禁止仅 import 不使用。

### NFR-5: 可测试性

L2 RubricJudge 和 L3 SelfCorrectionRunner 必须有单元测试，覆盖 satisfied/needs_revision/failed/
max_iterations_reached/grader_error 5 种状态 + 降级路径。

## 约束

### CON-1: 不修改 run_router 的现有行为

`run_router` 的 `chat_model` 参数（Phase 5 已添加）不修改。L3 SelfCorrectionRunner 不走 `run_router`，
而是自建 `create_deep_agent`。

### CON-2: 不破坏现有测试

现有 `tests/python/unit/` 必须全部继续通过（除 `test_interrupt_stream.py::test_team_runner_responds_to_abort`
预存在失败）。`test_eval_judges.py` 中 LlmJudge 测试需替换为 RubricJudge 测试。

### CON-3: deepagents 版本

`deepagents>=0.6.12` 已在 `pyproject.toml` 声明，无需修改版本约束。

### CON-4: 不引入新的全局状态

eval 框架不能引入模块级可变全局状态。RubricJudge/SelfCorrectionRunner 实例化时传入配置。

## 依赖变更

- 无新增依赖（`deepagents>=0.6.12` 已声明）
- 无移除依赖
