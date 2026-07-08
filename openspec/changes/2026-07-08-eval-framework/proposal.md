# Proposal: AgentX 评测框架 — deepagents RubricMiddleware 集成

## Why

AgentX 已有评测框架的基础设施（Phase 1-8 已实现：models/mocks/judges/reporters/runner/CLI/pytest 插件），
但 L2 评分层用的是自研 `LlmJudge`——手写 4 维度 0-5 打分 + JSON 解析 + markdown 代码块剥离。
这违反 AGENTS.md §3 R1"禁止自己造轮子"。

deepagents 0.6.12 原生提供 `RubricMiddleware`（`deepagents.middleware.rubric`），是 LangChain 官方的
评测中间件，提供：

| deepagents 原生能力 | 自研 LlmJudge 差距 |
|---|---|
| `GraderResponse` 结构化输出（pydantic） | 自研版手写 JSON 解析 + markdown 剥离，脆弱 |
| `GRADER_SYSTEM_PROMPT`（含 prompt 注入防护） | 自研版一句话 system prompt，无防护 |
| `_build_grader_transcript`（30 条 + 4000 字符截断） | 自研版无 transcript 边界控制 |
| Per-criterion `passed`/`gap` 结构化裁决 | 自研版仅 4 维度分数，无 gap 描述 |
| `RubricMiddleware` in-loop 自纠循环 | 自研版无自纠能力（仅事后评分） |
| `RubricResult` 5 种状态（satisfied/needs_revision/failed/max_iterations_reached/grader_error） | 自研版仅 pass/fail |

本次改造用 deepagents 原生评测能力替换自研 LlmJudge，并新增 L3 自纠层（in-loop self-correction）。

## What Changes

### 阶段一：L2 RubricJudge 替换 LlmJudge（事后 rubric 评分）

- 新建 `backend/app/eval/judges/rubric_judge.py`：用 deepagents `GraderResponse` + `GRADER_SYSTEM_PROMPT`
  做事后 rubric 评分（agent 跑完后，grader 子代理评审 transcript）
- 删除 `backend/app/eval/judges/llm_judge.py`（自研版整体替换）
- `CaseExpect.llm_judge: LlmJudgeConfig | None` → `CaseExpect.rubric: str | None`（自由文本完成标准）
- YAML schema：`llm_judge:` → `rubric: "完成标准文本"`
- 降级策略不变：无 API key / `--no-rubric` / grader 异常 → skipped（passed=True, score=5.0）

### 阶段二：L3 SelfCorrectionRunner（in-loop 自纠，新能力）

- 新建 `backend/app/eval/judges/self_correction.py`：用 `RubricMiddleware` 注入 `create_deep_agent`，
  agent 执行时 grader 实时评审，`needs_revision` 则注入反馈让 agent 迭代
- `CaseExpect.self_correct: bool = False`：case 声明是否启用自纠
- 自纠模式下 `EvalRunner` 不走 `run_router`，而是自建 `create_deep_agent` + `RubricMiddleware`
- 最终 `_rubric_status`（satisfied/max_iterations_reached/failed）→ JudgeResult
- `max_iterations` 默认 3（hard cap 20），可配

### 阶段三：YAML 评测集 + 集成验证

- 5 个 suite（smoke/router/tools/team/cli），用 `rubric` 替换 `llm_judge`
- smoke suite 离线跑通（MockChatModel + L1 only）
- 集成验证：pytest 插件 + CLI + 门禁脚本

## Capabilities

### New Capabilities

- `eval-rubric-judge`: deepagents GraderResponse 驱动的事后 rubric 评分
- `eval-self-correction`: RubricMiddleware 驱动的 in-loop 自纠评测

### Modified Capabilities

- `eval-framework`: L2 层从 LlmJudge 改为 RubricJudge，新增 L3 自纠层

## Impact

- **后端**:
  - 新建 2 文件（`rubric_judge.py` / `self_correction.py`）
  - 删除 1 文件（`llm_judge.py`）
  - 修改 ~8 文件（models.py / judges/__init__.py / runner.py / cli.py / pytest_plugin.py / reporters）
  - 修改 YAML suite（用 rubric 替换 llm_judge）
- **依赖**: `deepagents>=0.6.12`（已声明，本次消费）
- **测试**: 更新 test_eval_judges.py + 新增 test_eval_rubric_judge.py + test_eval_self_correction.py
- **文档**: 更新 design.md / spec.md / tasks.md

## Future Extensibility

- P2: deepagents-migration 完成后，L3 SelfCorrectionRunner 可复用主 agent 的 `create_deep_agent` 构建
- P3: 探索 `RubricEvaluation` 流式事件（`rubric_evaluation_start`/`end`）的 SSE 桥接
