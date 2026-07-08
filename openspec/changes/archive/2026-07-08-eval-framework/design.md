# Design: AgentX 评测框架 — deepagents RubricMiddleware 集成

## 1. Context

AgentX 评测框架 Phase 1-8 已实现（models/mocks/judges/reporters/runner/CLI/pytest 插件），但 L2 评分层
用自研 `LlmJudge`（手写 4 维度 0-5 打分 + JSON 解析）。deepagents 0.6.12 提供原生 `RubricMiddleware`，
含 `GraderResponse` 结构化输出 + `GRADER_SYSTEM_PROMPT` + transcript 边界控制 + in-loop 自纠循环。

本次改造分两层：
- **L2 RubricJudge**（事后评分，替换 LlmJudge）：agent 跑完后，grader 子代理评审 transcript + rubric
- **L3 SelfCorrectionRunner**（in-loop 自纠，新能力）：agent 执行时 grader 实时评审，`needs_revision` 则迭代

## 2. 数据模型变更（models.py）

### 2.1 CaseExpect 字段变更

```python
class CaseExpect(BaseModel):
    """case 期望结果。"""
    # L1 断言（不变）
    events: list[EventAssertion] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    tools_not_called: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)

    # L2 rubric（替换 llm_judge）
    rubric: str | None = None  # 自由文本完成标准，如"回复必须包含代码示例且语言正确"

    # L3 自纠（新增）
    self_correct: bool = False  # 是否启用 in-loop 自纠
    self_correct_max_iterations: int = 3  # 自纠最大迭代次数（hard cap 20）
```

### 2.2 删除 LlmJudgeConfig

```python
# 删除（被 rubric: str | None 替换）
class LlmJudgeConfig(BaseModel):
    criteria: list[str] = ...
    reference_answer: str | None = None
```

### 2.3 JudgeLayer 扩展

```python
# 之前
JudgeLayer = Literal["L1", "L2"]

# 之后
JudgeLayer = Literal["L1", "L2", "L3"]
```

### 2.4 JudgeResult 不变

`JudgeResult` 结构不变（case_id/passed/score/reason/details/layer），L2 和 L3 都复用。
- L2 RubricJudge: `details` 含 `grader_result`（satisfied/failed）+ `criteria[]`
- L3 SelfCorrectionRunner: `details` 含 `rubric_status` + `evaluations[]` + `iterations`

## 3. L2 RubricJudge 设计（rubric_judge.py）

### 3.1 核心逻辑

```python
from deepagents.middleware.rubric import (
    GRADER_SYSTEM_PROMPT,
    GraderResponse,
    _build_grader_transcript,  # 复用 transcript 边界控制（模块级函数）
)
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
import secrets

# 助手函数定义（模块级）
def _has_api_key() -> bool:
    """检查是否存在任一 AGENTX_*_API_KEY 环境变量。"""
    import os
    return any(os.environ.get(k) for k in (
        "AGENTX_OPENAI_API_KEY", "AGENTX_DEEPSEEK_API_KEY",
        "AGENTX_KIMI_API_KEY", "AGENTX_GLM_API_KEY",
    ))

def _get_grader_model():
    """获取 grader 用的 ChatModel（temperature=0）。失败抛 ValueError。"""
    from app.llm import get_chat_model
    return get_chat_model(temperature=0)

class RubricJudge:
    """L2 rubric 评分器：用 deepagents GraderResponse 做事后 rubric 评分。"""

    def __init__(self, no_rubric: bool = False, grader_model=None):
        self.no_rubric = no_rubric
        self.grader_model = grader_model  # None 时用 _get_grader_model()

    async def evaluate(self, events: list[dict], case: EvalCase) -> JudgeResult:
        # 1. 降级检查（无 rubric / --no-rubric / 无 API key）
        if self.no_rubric or case.expect.rubric is None:
            return _skipped(case, "no rubric")

        if not _has_api_key():
            return _skipped(case, "no API key")

        # 2. 获取 grader model（失败降级）
        try:
            model = self.grader_model or _get_grader_model()
        except ValueError as exc:
            return _skipped(case, f"get_chat_model error: {exc}")

        # 3. 构建 grader agent（用 langchain.agents.create_agent + GraderResponse）
        grader = create_agent(
            model=model,
            system_prompt=GRADER_SYSTEM_PROMPT,
            tools=[],
            response_format=GraderResponse,
        )

        # 4. 构建 grader payload（rubric + transcript）
        transcript = _build_transcript_from_events(events)
        payload = _build_grader_payload(case.expect.rubric, transcript)

        # 5. 调用 grader（用 .get() 避免 KeyError，与 RubricMiddleware._extract_graded 一致）
        try:
            result = await grader.ainvoke({"messages": [HumanMessage(content=payload)]})
            structured = result.get("structured_response")
            if structured is None:
                return _skipped(case, "grader returned no structured_response")
            graded = GraderResponse.model_validate(structured)
        except Exception as exc:
            return _skipped(case, f"grader error: {exc}")

        # 6. 映射 GraderResponse → JudgeResult
        return _map_grader_response(graded, case)
```

### 3.2 GraderResponse → JudgeResult 映射

| GraderResponse.result | JudgeResult.passed | JudgeResult.score | JudgeResult.reason |
|---|---|---|---|
| `satisfied` | True | 5.0 | "rubric satisfied" |
| `needs_revision` | False | 3.0 | "rubric needs revision" + gaps |
| `failed` | False | 0.0 | "rubric failed (malformed/impossible)" |

`JudgeResult.details`:
```python
{
    "grader_result": graded.result,
    "explanation": graded.explanation,
    "criteria": [{"name": c["name"], "passed": c["passed"], "gap": c.get("gap", "")} for c in graded.criteria],
}
```

### 3.3 Transcript 构建

`_build_transcript_from_events(events)`：把 SSE 事件列表转为 LangChain messages 列表，
然后调 deepagents `_build_grader_transcript(messages)` 做边界控制（30 条 + 4000 字符/条）。

事件 → messages 映射规则：
- `event=token` → 拼接所有 token data 为一条 `AIMessage(content=...)`（连续 token 合并）
- `event=tool_call` → `AIMessage(content="", tool_calls=[{"name": ..., "args": ..., "id": ...}]`)
- `event=tool_result` → `ToolMessage(content=..., tool_call_id=...)`
- `event=done` → 忽略（终止信号，非消息）
- 其他事件 → 忽略

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

def _build_transcript_from_events(events: list[dict]) -> str:
    """把 SSE 事件列表转为 transcript 文本（经 deepagents 边界控制）。"""
    messages: list[AnyMessage] = [HumanMessage(content="(original user message not available in eval events)")]
    current_ai_text: list[str] = []
    for e in events:
        evt = e.get("event")
        if evt == "token":
            data = e.get("data", "")
            if isinstance(data, str):
                current_ai_text.append(data)
        elif evt == "tool_call":
            if current_ai_text:
                messages.append(AIMessage(content="".join(current_ai_text)))
                current_ai_text = []
            messages.append(AIMessage(content="", tool_calls=[{
                "name": e.get("tool", ""),
                "args": e.get("args", {}),
                "id": e.get("tool_call_id", ""),
            }]))
        elif evt == "tool_result":
            if current_ai_text:
                messages.append(AIMessage(content="".join(current_ai_text)))
                current_ai_text = []
            messages.append(ToolMessage(
                content=str(e.get("result", "")),
                tool_call_id=e.get("tool_call_id", ""),
            ))
    if current_ai_text:
        messages.append(AIMessage(content="".join(current_ai_text)))
    return _build_grader_transcript(messages)
```

> **注意**：eval 框架收集的 SSE 事件不含原始 user_message（由 `run_router` 内部消费），
> transcript 中用占位符替代。L3 SelfCorrectionRunner 直接构建 `HumanMessage(content=case.user_message)`，无此问题。

### 3.4 Grader Payload 构建

复用 `RubricMiddleware._build_grader_payload` 的逻辑（nonce 标签 + sanitize），
但因为不是中间件内部调用，直接实现等价逻辑：

```python
def _build_grader_payload(rubric: str, transcript: str) -> str:
    nonce = secrets.token_hex(8)
    safe_rubric = _sanitize_for_payload(rubric.strip())
    safe_transcript = _sanitize_for_payload(transcript)
    return (
        f"Evaluate whether the agent transcript below satisfies every criterion "
        f"in the rubric.\n\n"
        f"<rubric-{nonce}>\n{safe_rubric}\n</rubric-{nonce}>\n\n"
        f"<transcript-{nonce}>\n{safe_transcript}\n</transcript-{nonce}>\n\n"
        "Return a GraderResponse."
    )
```

### 3.5 降级策略（与 LlmJudge 一致）

- `--no-rubric` 标志 → skipped
- 无 `AGENTX_*_API_KEY` → skipped
- `get_chat_model()` 抛 ValueError → skipped
- grader 调用失败 / structured_response 解析失败 → skipped
- skipped = passed=True, score=5.0（不惩罚用例）

## 4. L3 SelfCorrectionRunner 设计（self_correction.py）

### 4.1 核心逻辑

```python
from deepagents import RubricMiddleware, create_deep_agent
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver  # 或 InMemorySaver（langgraph 版本相关）
import uuid

class SelfCorrectionRunner:
    """L3 in-loop 自纠评测：用 RubricMiddleware 驱动 agent 自我迭代。"""

    def __init__(self, chat_model=None, grader_model=None, max_iterations: int = 3,
                 checkpointer=None):
        self.chat_model = chat_model
        self.grader_model = grader_model
        self.max_iterations = max_iterations
        # checkpointer 必须传入，否则 aget_state 无法工作
        # EvalRunner 默认传 MemorySaver()
        self.checkpointer = checkpointer or MemorySaver()

    async def run_case(self, case: EvalCase, tools: list = None) -> CaseResult:
        # 0. 降级检查：无 grader_model 且无 API key → skipped（避免 RubricMiddleware(model=None) 抛 ValueError）
        try:
            model = self.chat_model or _get_chat_model()
        except ValueError as exc:
            return _skipped_case(case, f"L3 skipped: no chat model: {exc}")

        try:
            grader_model = self.grader_model or _get_grader_model()
        except ValueError as exc:
            return _skipped_case(case, f"L3 skipped: no grader model: {exc}")

        # 1. 构建 agent with RubricMiddleware
        rubric_middleware = RubricMiddleware(
            model=grader_model,
            max_iterations=self.max_iterations,
        )

        agent = create_deep_agent(
            model=model,
            tools=tools or [],
            middleware=[rubric_middleware],
            system_prompt="你是 AgentX 评测目标 agent。",
            checkpointer=self.checkpointer,  # 必须传，否则 aget_state 失败
        )

        # 2. 调用 agent，传入 rubric（rubric 是 RubricState 的顶层字段）
        thread_id = f"eval-sc-{case.id}-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}

        events: list[dict] = []
        start = time.perf_counter()
        try:
            async for event in agent.astream(
                {"messages": [HumanMessage(content=case.user_message)], "rubric": case.expect.rubric},
                config=config,
            ):
                events.append(_normalize_event(event))
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return CaseResult(case=case, events=events, error=str(exc),
                              duration_ms=duration_ms)

        # 3. 读取最终 rubric 状态（依赖 checkpointer 持久化）
        final_state = await agent.aget_state(config)
        rubric_status = final_state.values.get("_rubric_status", "grader_error")
        rubric_evaluations = final_state.values.get("_rubric_evaluations", [])

        # 4. 映射 → CaseResult + JudgeResult
        duration_ms = int((time.perf_counter() - start) * 1000)
        return _build_self_correction_result(
            case, events, rubric_status, rubric_evaluations, duration_ms
        )
```

> **关键约束**：
> - `checkpointer` 是 `create_deep_agent` 的必传参数（L3 需要读 `_rubric_status`）。
>   `EvalRunner` 默认传 `MemorySaver()`，与 `run_router` 的 checkpointer 解耦。
> - `RubricMiddleware(model=None)` 会抛 `ValueError`，必须在构造前检查 `grader_model` 可用性。
> - `rubric` 是 `RubricState` 的顶层输入字段（非嵌套在 messages 里），通过 `astream` 的输入 dict 传入。

### 4.2 RubricResult → JudgeResult 映射

| _rubric_status | JudgeResult.passed | JudgeResult.score | JudgeResult.reason |
|---|---|---|---|
| `satisfied` | True | 5.0 | "self-correction satisfied" |
| `max_iterations_reached` | False | 3.0 | "self-correction exhausted {N} iterations" |
| `failed` | False | 0.0 | "rubric malformed/impossible" |
| `grader_error` | False | 0.0 | "grader error: {explanation}" |
| None（未触发） | False | 0.0 | "rubric middleware not triggered" |

`JudgeResult.details`:
```python
{
    "rubric_status": rubric_status,
    "iterations": len(rubric_evaluations),
    "evaluations": [
        {
            "iteration": ev["iteration"],
            "result": ev["result"],
            "explanation": ev["explanation"],
            "criteria": ev["criteria"],
        }
        for ev in rubric_evaluations
    ],
}
```

### 4.3 与 EvalRunner 的集成

`EvalRunner.run_case` 逻辑变更：
```python
async def run_case(self, case: EvalCase) -> CaseResult:
    # L3 自纠模式：走 SelfCorrectionRunner
    if case.expect.self_correct and case.expect.rubric:
        sc_runner = SelfCorrectionRunner(
            chat_model=self.chat_model,
            grader_model=self.grader_model,  # 新增字段，None 时 SelfCorrectionRunner 内部 _get_grader_model()
            max_iterations=case.expect.self_correct_max_iterations,
            checkpointer=self.checkpointer,  # 透传，L3 需要读状态
        )
        return await sc_runner.run_case(case)

    # 默认模式：走 run_router（L1 + L2 事后评分）
    return await self._run_case_via_router(case)
```

> `EvalRunner.__init__` 新增 `grader_model` 参数（可选），供 L3 透传。默认 None 时
> `SelfCorrectionRunner` 内部调 `_get_grader_model()`。

### 4.4 Mock 模式与降级

L3 的降级决策在 `SelfCorrectionRunner.run_case` 开头，**在构造 `RubricMiddleware` 之前**：

| 场景 | 行为 |
|---|---|
| `chat_model=MockChatModel` + `grader_model=MockChatModel`（有 grader fixtures） | 正常执行 L3，grader 返回 mock 的 GraderResponse |
| `chat_model=MockChatModel` + 无 `grader_model` + 无 API key | `_get_grader_model()` 抛 ValueError → L3 skipped（passed=True, score=5.0） |
| 无 `chat_model` + 无 API key | `_get_chat_model()` 抛 ValueError → L3 skipped |
| `--no-rubric` 标志 | `EvalRunner` 不进入 L3 分支（`case.expect.self_correct` 仍可为 true，但 runner 跳过） |

> **关键**：`RubricMiddleware(model=None)` 会抛 `ValueError`，所以降级检查必须在构造中间件之前。
> MockChatModel 的 grader fixtures 需返回 `GraderResponse` 格式的 structured_response。

## 5. 变更影响

### 5.1 models.py
- 删除 `LlmJudgeConfig`
- `CaseExpect.llm_judge` → `CaseExpect.rubric: str | None`
- `CaseExpect` 新增 `self_correct: bool = False` + `self_correct_max_iterations: int = 3`
- `JudgeLayer` 扩展为 `Literal["L1", "L2", "L3"]`

### 5.2 judges/
- 删除 `llm_judge.py`
- 新建 `rubric_judge.py`（L2）
- 新建 `self_correction.py`（L3）
- `__init__.py` 更新导出：`LlmJudge` → `RubricJudge` + `SelfCorrectionRunner`
- `composite.py` 不变（已支持任意 Judge 列表）

### 5.3 runner.py
- `__init__` 新增 `grader_model` 参数（可选），供 L3 `SelfCorrectionRunner` 透传
- `run_case` 添加 L3 分支：`case.expect.self_correct and case.expect.rubric and not self.no_rubric` 时走 `SelfCorrectionRunner`
  - `--no-rubric` 时即使 `self_correct=true` 也不进入 L3（避免无 API key 时构造 RubricMiddleware 失败）
- `apply_judge_results` 扩展：L3 结果参与 `passed` 计算（all judges passed），`avg_score` 取 L2+L3 均值

### 5.4 cli.py
- `--no-llm-judge` → `--no-rubric`
- `_run_suite_async` 中 `LlmJudge(no_llm_judge=...)` → `RubricJudge(no_rubric=...)`

### 5.5 pytest_plugin.py
- mock 模式：`RubricJudge(no_rubric=True)`（跳过 L2）
- live 模式：`RubricJudge()`（正常执行 L2）

### 5.6 reporters/
- `ConsoleReporter` / `MarkdownReporter` / `JsonReporter`：无需修改（已通过 `JudgeResult` 抽象）
- 报告中 L2/L3 区分通过 `layer` 字段展示

### 5.7 YAML suite 变更
```yaml
# 之前
expect:
  llm_judge:
    criteria: [relevance, accuracy]
    reference_answer: "..."

# 之后
expect:
  rubric: "回复必须包含可运行的代码示例，且语言与用户请求一致"
  self_correct: false  # 可选，默认 false
```

## 6. YAML Schema 示例

```yaml
id: smoke
name: 冒烟测试
description: P0 核心路径离线验证
cases:
  - id: smoke-001
    user_message: "你好"
    agent_mode: work
    expect:
      events:
        - type: done
          count_min: 1
      tools_called: []
      assertions:
        - "len([e for e in events if e.get('event')=='token']) > 0"
    timeout: 30
    tags: [p0, offline]

  - id: smoke-002
    user_message: "帮我写一个 Python hello world"
    agent_mode: coding
    expect:
      events:
        - type: done
          count_min: 1
      rubric: |
        回复必须包含一个可运行的 Python hello world 代码块，
        代码使用 print 函数输出 "Hello, World!"。
    timeout: 60
    tags: [p0, rubric]

  - id: smoke-003
    user_message: "分析这段代码的性能问题并修复"
    agent_mode: coding
    expect:
      events:
        - type: done
          count_min: 1
      rubric: |
        1. 必须识别出至少一个性能问题
        2. 必须提供修复后的代码
        3. 修复后的代码必须保持原有功能
      self_correct: true
      self_correct_max_iterations: 2
    timeout: 120
    tags: [p0, self-correction]
```

## 7. 测试策略

### 7.1 test_eval_rubric_judge.py（新增）
- 测试 `satisfied` → passed=True, score=5.0
- 测试 `needs_revision` → passed=False, score=3.0
- 测试 `failed` → passed=False, score=0.0
- 测试降级：no rubric / no API key / grader 异常 → skipped
- 测试 transcript 构建（事件 → messages → transcript 文本）
- 测试 grader payload 构建（nonce 标签 + sanitize）
- 用 MockChatModel 模拟 grader 响应（返回 GraderResponse JSON）

### 7.2 test_eval_self_correction.py（新增）
- 测试 `satisfied` 一次通过 → passed=True, score=5.0, iterations=1
- 测试 `needs_revision` → `satisfied` 二次通过 → passed=True, iterations=2
- 测试 `max_iterations_reached` → passed=False, score=3.0
- 测试 `grader_error` → passed=False, score=0.0
- 测试 mock 模式（MockChatModel for both agent + grader）
- 测试 `self_correct: false` 时不触发 L3（走 run_router）
- 测试降级：无 `grader_model` + 无 API key → L3 skipped（passed=True, score=5.0）

#### Mock grader fixtures 格式

MockChatModel 用于 grader 时，返回的 `structured_response` 必须是 `GraderResponse` 兼容的 dict：
```python
# MockChatModel 响应格式（grader 模式）
{
    "structured_response": {
        "result": "satisfied",  # 或 "needs_revision" / "failed"
        "explanation": "All criteria met.",
        "criteria": [
            {"name": "code-runnable", "passed": True},
            {"name": "language-correct", "passed": True},
        ]
    }
}
```
`MockChatModel` 需扩展支持 `response_format` 参数，当 agent 以 `response_format=GraderResponse`
调用时，mock 返回预设的 structured_response dict（而非纯文本）。

### 7.3 test_eval_judges.py（更新）
- 移除 LlmJudge 相关测试
- 保留 AssertJudge + CompositeJudge 测试
- 新增 CompositeJudge 含 RubricJudge 的集成测试

### 7.4 test_eval_models.py（更新）
- 移除 LlmJudgeConfig 测试
- 新增 rubric / self_correct 字段测试

## 8. 降级容错

| 场景 | L2 RubricJudge | L3 SelfCorrectionRunner |
|---|---|---|
| mock 模式（MockChatModel） | skipped（无 API key） | skipped（无 grader fixtures） |
| 无 API key | skipped | 不触发（走 run_router + L1 only） |
| `--no-rubric` | skipped | 不触发 |
| grader 调用失败 | skipped | grader_error → passed=False |
| rubric 为 None | skipped | 不触发（走默认 run_router 路径） |

## 9. 与 deepagents-migration 的关系

- **不依赖**：eval 框架自建 `create_deep_agent`（L3）或 `create_agent`（L2 grader），独立于主 agent 迁移
- **dogfooding**：eval 框架消费 `create_deep_agent` + `RubricMiddleware`，验证 deepagents 能力
- **迁移完成后**：L3 SelfCorrectionRunner 可复用主 agent 的 `build_deep_agent`（加 RubricMiddleware），减少重复构建
