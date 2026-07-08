# 任务追踪 — AgentX 评测框架 deepagents RubricMiddleware 集成

## 阶段一：数据模型重构

- [ ] T1.1 `backend/app/eval/models.py`:
  - 删除 `LlmJudgeConfig` 类
  - `CaseExpect.llm_judge` → `CaseExpect.rubric: str | None`
  - `CaseExpect` 新增 `self_correct: bool = False`
  - `CaseExpect` 新增 `self_correct_max_iterations: int = 3`
  - `JudgeLayer` 扩展为 `Literal["L1", "L2", "L3"]`
- [ ] T1.2 更新 `tests/python/unit/test_eval_models.py`:
  - 移除 LlmJudgeConfig 测试
  - 新增 rubric / self_correct / self_correct_max_iterations 字段测试
  - 新增 JudgeLayer="L3" 测试

## 阶段二：L2 RubricJudge 实现

- [ ] T2.1 新建 `backend/app/eval/judges/rubric_judge.py`:
  - `RubricJudge` 类，`__init__(no_rubric: bool = False, grader_model=None)`
  - `async evaluate(events, case) -> JudgeResult`
  - 用 `langchain.agents.create_agent` + `GraderResponse` + `GRADER_SYSTEM_PROMPT` 构建 grader
  - 用 `_build_grader_transcript` 做 transcript 边界控制
  - GraderResponse → JudgeResult 映射（satisfied/needs_revision/failed）
  - 降级：no rubric / no API key / grader 异常 → skipped
- [ ] T2.2 删除 `backend/app/eval/judges/llm_judge.py`
- [ ] T2.3 更新 `backend/app/eval/judges/__init__.py`:
  - 移除 `LlmJudge` 导出
  - 新增 `RubricJudge` 导出
- [ ] T2.4 新建 `tests/python/unit/test_eval_rubric_judge.py`:
  - 测试 satisfied → passed=True, score=5.0
  - 测试 needs_revision → passed=False, score=3.0
  - 测试 failed → passed=False, score=0.0
  - 测试降级（no rubric / no API key / grader 异常）
  - 测试 transcript 构建
  - 测试 grader payload 构建
  - 用 MockChatModel 模拟 grader 响应

## 阶段三：L3 SelfCorrectionRunner 实现

- [ ] T3.1 扩展 `backend/app/eval/mocks/llm.py` MockChatModel:
  - 支持 `response_format` 参数（当 agent 以 `response_format=GraderResponse` 调用时）
  - 返回预设的 `structured_response` dict（而非纯文本）
  - 支持 `grader_responses` 队列：按调用次数返回不同的 grader 响应（模拟 needs_revision → satisfied 迭代）
- [ ] T3.2 新建 `backend/app/eval/judges/self_correction.py`:
  - `SelfCorrectionRunner` 类，`__init__(chat_model=None, grader_model=None, max_iterations=3, checkpointer=None)`
  - `async run_case(case, tools=None) -> CaseResult`
  - 降级检查在构造 `RubricMiddleware` 之前（无 model + 无 API key → skipped）
  - 用 `create_deep_agent(middleware=[RubricMiddleware(...)], checkpointer=...)` 构建 agent
  - 调用传入 `{"messages": [...], "rubric": case.expect.rubric}`
  - 读取 `_rubric_status` + `_rubric_evaluations`（依赖 checkpointer）
  - RubricResult → JudgeResult 映射
- [ ] T3.3 更新 `backend/app/eval/judges/__init__.py`:
  - 新增 `SelfCorrectionRunner` 导出
- [ ] T3.4 新建 `tests/python/unit/test_eval_self_correction.py`:
  - 测试 satisfied 一次通过
  - 测试 needs_revision → satisfied 二次通过
  - 测试 max_iterations_reached
  - 测试 grader_error
  - 测试 mock 模式（MockChatModel for both agent + grader）
  - 测试 self_correct=false 时不触发 L3
  - 测试降级：无 grader_model + 无 API key → skipped

## 阶段四：EvalRunner 集成

- [ ] T4.1 修改 `backend/app/eval/runner.py`:
  - `__init__` 新增 `grader_model` 参数（可选），供 L3 透传
  - `__init__` 新增 `no_rubric` 参数（默认 False），控制 L3 分支是否触发
  - `run_case` 添加 L3 分支：`case.expect.self_correct and case.expect.rubric and not self.no_rubric` 时走 SelfCorrectionRunner
  - `apply_judge_results` 扩展：L3 结果参与 passed 计算，avg_score 取 L2+L3 均值
- [ ] T4.2 更新 `tests/python/unit/test_eval_runner.py`:
  - 新增 L3 分支测试（self_correct=true 走 SelfCorrectionRunner）
  - 新增 `--no-rubric` 时 self_correct=true 不进入 L3 的测试
  - 更新 apply_judge_results 测试（含 L3 场景）

## 阶段五：CLI + pytest 插件适配

- [ ] T5.1 修改 `backend/app/eval/cli.py`:
  - `--no-llm-judge` → `--no-rubric`
  - `LlmJudge(no_llm_judge=...)` → `RubricJudge(no_rubric=...)`
- [ ] T5.2 修改 `backend/app/cli.py`:
  - eval parser 的 `--no-llm-judge` → `--no-rubric`
- [ ] T5.3 修改 `backend/app/eval/pytest_plugin.py`:
  - mock 模式：`RubricJudge(no_rubric=True)`
  - live 模式：`RubricJudge()`
- [ ] T5.4 更新 `tests/python/unit/test_eval_cli.py`:
  - `--no-llm-judge` → `--no-rubric`
- [ ] T5.5 更新 `tests/python/unit/test_eval_pytest_plugin.py`:
  - 适配 RubricJudge

## 阶段六：YAML 评测集

- [ ] T6.1 创建 `tests/eval/suites/smoke.yaml`（5-8 个 case，含 rubric + self_correct）
- [ ] T6.2 创建 `tests/eval/suites/router.yaml`（3 个 case，含 rubric）
- [ ] T6.3 创建 `tests/eval/suites/tools.yaml`（4 个 case，含 rubric）
- [ ] T6.4 创建 `tests/eval/suites/team.yaml`（3 个 case，含 rubric）
- [ ] T6.5 创建 `tests/eval/suites/cli.yaml`（4 个 case，含 rubric）
- [ ] T6.6 新增/更新 mock fixtures（`backend/app/eval/mocks/fixtures/`）支持 rubric grader 响应:
  - fixture 格式：`{"structured_response": {"result": "satisfied", "explanation": "...", "criteria": [...]}}`
  - 支持 `grader_responses` 队列（模拟 needs_revision → satisfied 迭代）
  - smoke suite 的 self_correct case 必须能在 mock 模式下降级通过（无 grader_model → skipped）

## 阶段七：发版门禁脚本

- [ ] T7.1 创建 `scripts/eval-gate.ps1`:
  - 调用 `agentx eval run --suite smoke --format=console,md`
  - 退出码非 0 打印"评测门禁失败，禁止发版"
  - 通过打印"评测门禁通过"

## 阶段八：集成验证

- [ ] T8.1 smoke suite 离线跑通：`agentx eval run --suite smoke`（无 API key，30 秒内完成）
- [ ] T8.2 现有测试不破坏：`pytest tests/python/unit/ -q`
- [ ] T8.3 eval 专项测试：`pytest tests/python/unit/test_eval_*.py -q`
- [ ] T8.4 pytest 插件集成：`pytest tests/eval/suites/ -q`（YAML 自动收集）
- [ ] T8.5 门禁脚本可用：`.\scripts\eval-gate.ps1`

## 预期修改文件

### 新建文件
- `backend/app/eval/judges/rubric_judge.py` — L2 RubricJudge
- `backend/app/eval/judges/self_correction.py` — L3 SelfCorrectionRunner
- `tests/python/unit/test_eval_rubric_judge.py` — L2 测试
- `tests/python/unit/test_eval_self_correction.py` — L3 测试
- `tests/eval/suites/smoke.yaml` — 冒烟评测集
- `tests/eval/suites/router.yaml` — Router 分发评测集
- `tests/eval/suites/tools.yaml` — 工具调用评测集
- `tests/eval/suites/team.yaml` — AgentTeam 协作评测集
- `tests/eval/suites/cli.yaml` — CLI 闭环评测集
- `scripts/eval-gate.ps1` — 发版门禁脚本

### 修改文件
- `backend/app/eval/models.py` — 数据模型变更
- `backend/app/eval/judges/__init__.py` — 导出变更
- `backend/app/eval/runner.py` — L3 分支 + grader_model + no_rubric 参数 + apply_judge_results 扩展
- `backend/app/eval/cli.py` — --no-rubric + RubricJudge
- `backend/app/cli.py` — --no-rubric
- `backend/app/eval/pytest_plugin.py` — RubricJudge 适配
- `backend/app/eval/mocks/llm.py` — MockChatModel 扩展支持 response_format + grader_responses 队列
- `tests/python/unit/test_eval_models.py` — 模型测试更新
- `tests/python/unit/test_eval_judges.py` — LlmJudge → RubricJudge 测试
- `tests/python/unit/test_eval_runner.py` — L3 分支测试
- `tests/python/unit/test_eval_cli.py` — --no-rubric
- `tests/python/unit/test_eval_pytest_plugin.py` — RubricJudge 适配

### 删除文件
- `backend/app/eval/judges/llm_judge.py` — 自研 LlmJudge（被 RubricJudge 替换）

## 规模判定

- 涉及文件数: ~21（10 新建 + 10 修改 + 1 删除）
- 涉及模块数: 3（eval/ + tests/ + scripts/）
- 规模: **L（大改）** — 10+ 文件且跨模块，需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 数据模型重构 | models.py, test_eval_models.py | rubric/self_correct 字段可用 | ⬜ |
| T2 | L2 RubricJudge | rubric_judge.py, test_eval_rubric_judge.py | GraderResponse 映射正确 + 降级 | ⬜ |
| T3 | L3 SelfCorrectionRunner | self_correction.py, test_eval_self_correction.py | 5 种 RubricResult 状态覆盖 | ⬜ |
| T4 | EvalRunner 集成 | runner.py, test_eval_runner.py | L3 分支 + apply_judge_results 扩展 | ⬜ |
| T5 | CLI + pytest 适配 | cli.py, pytest_plugin.py | --no-rubric + RubricJudge | ⬜ |
| T6 | YAML 评测集 | tests/eval/suites/*.yaml | 5 suite, 20+ case | ⬜ |
| T7 | 门禁脚本 | scripts/eval-gate.ps1 | smoke 通过 | ⬜ |
| T8 | 集成验证 | 全量测试 | smoke 离线 + 现有测试不破坏 | ⬜ |
