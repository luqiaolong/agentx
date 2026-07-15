"""eval CLI 子命令：``agentx eval run|list|show|export-feedback|run-trace|export-trace``。

由 ``app.cli.main`` 在 ``args.command == "eval"`` 时调用 ``run_eval_command`` 分发。
退出码（FR-7.8）：全部通过=0，有失败=1，suite 不存在=2。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.eval.judges import AssertJudge, RubricJudge
from app.eval.mocks.llm import MockChatModel
from app.eval.models import EvalCase, EvalResult, EvalSuite
from app.eval.reporters import ConsoleReporter, JsonReporter, MarkdownReporter
from app.eval.runner import EvalRunner

# 默认 rubric：当用户点 👎 但未填写 comment 时填的兜底期望（FR-10.5）
_DEFAULT_RUBRIC = "回复应满足用户期望"

# suites 目录：worktree root 下的 tests/eval/suites/
# __file__ = backend/app/eval/cli.py → parents[3] = worktree root
_SUITES_DIR = Path(__file__).resolve().parents[3] / "tests" / "eval" / "suites"
# mock fixtures 目录：backend/app/eval/mocks/fixtures/
_FIXTURES_DIR = Path(__file__).resolve().parent / "mocks" / "fixtures"
# 报告输出目录：worktree root 下的 data/eval/reports/
_REPORTS_DIR = Path(__file__).resolve().parents[3] / "data" / "eval" / "reports"


def _load_suite(name: str) -> EvalSuite:
    """从 ``tests/eval/suites/{name}.yaml`` 加载 ``EvalSuite``。

    文件不存在时打印错误并 ``sys.exit(2)``（FR-7.8 suite 不存在=2）。
    """
    path = _SUITES_DIR / f"{name}.yaml"
    if not path.exists():
        print(f"错误：suite '{name}' 不存在（路径：{path}）", file=sys.stderr)
        sys.exit(2)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return EvalSuite(**data)


def _list_suites() -> list[str]:
    """列出所有可用 suite 名（按字母序）；目录不存在时返回空列表。"""
    if not _SUITES_DIR.exists():
        return []
    return sorted(p.stem for p in _SUITES_DIR.glob("*.yaml"))


def _make_reporter(format_name: str):
    """按格式名构造 Reporter 实例。"""
    if format_name == "console":
        return ConsoleReporter()
    if format_name == "md":
        return MarkdownReporter()
    if format_name == "json":
        return JsonReporter()
    raise ValueError(
        f"未知 format: {format_name!r}，合法值为 console/md/json"
    )


def _ext_for(format_name: str) -> str:
    """``md`` / ``json`` → 报告文件扩展名。

    ``console`` 永远走 ``print(content)`` 不会进此函数，因此不映射。
    """
    return {"md": "md", "json": "json"}[format_name]


async def _run_suite_async(
    suite: EvalSuite, live: bool, no_rubric: bool
) -> EvalResult:
    """异步执行 suite + Judge 打分，返回回填后的 ``EvalResult``。

    - ``live=True`` 时 chat_model=None（用真实 LLM）；否则用 ``MockChatModel``
    - ``no_rubric=True`` 时只跑 L1 ``AssertJudge``，跳过 L2 ``RubricJudge``，
      且 L3 自纠分支不触发
    - 评分逻辑已合并到 ``EvalRunner.run_suite(judges=...)`` 内部，
      本函数不再二次循环 ``case_results``
    """
    chat_model = None if live else MockChatModel.from_fixtures(_FIXTURES_DIR)
    judges: list = [AssertJudge()]
    if not no_rubric:
        judges.append(RubricJudge(no_rubric=no_rubric))

    runner = EvalRunner(chat_model=chat_model, no_rubric=no_rubric)
    return await runner.run_suite(suite, judges=judges)


async def _run_all_suites_async(
    suites: list[tuple[str, EvalSuite]], live: bool, no_rubric: bool
) -> list[tuple[str, EvalResult]]:
    """在单个 event loop 中顺序执行所有 suite，避免 checkpointer 跨 event loop 问题。

    ``get_async_checkpointer()`` 返回单例，其内部 asyncio.Lock 绑定到首次调用的
    event loop。若每个 suite 用独立 ``asyncio.run()``，后续 suite 会因 lock 跨
    event loop 报 RuntimeError。本函数把所有 suite 放在同一个 ``asyncio.run()``
    中执行，确保 lock 始终在同一个 event loop 上。
    """
    results: list[tuple[str, EvalResult]] = []
    for name, suite in suites:
        eval_result = await _run_suite_async(
            suite, live=live, no_rubric=no_rubric
        )
        results.append((name, eval_result))
    return results


def _cmd_run(args: argparse.Namespace) -> int:
    """执行 ``agentx eval run``。返回退出码（0=全过，1=有失败，2=suite 不存在）。"""
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    if not formats:
        formats = ["console"]

    # 解析要跑的 suite 列表
    if args.suite == "all":
        suite_names = _list_suites()
        if not suite_names:
            print("错误：未找到任何 suite", file=sys.stderr)
            return 2
    else:
        path = _SUITES_DIR / f"{args.suite}.yaml"
        if not path.exists():
            print(f"错误：suite '{args.suite}' 不存在", file=sys.stderr)
            return 2
        suite_names = [args.suite]

    # 加载所有 suite
    suites: list[tuple[str, EvalSuite]] = []
    for name in suite_names:
        suites.append((name, _load_suite(name)))

    # 在单个 asyncio.run() 中执行所有 suite，避免 checkpointer 跨 event loop 问题
    all_results = asyncio.run(
        _run_all_suites_async(suites, live=args.live, no_rubric=args.no_rubric)
    )

    overall_passed = True
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for name, eval_result in all_results:
        # 渲染每种格式
        for fmt in formats:
            reporter = _make_reporter(fmt)
            content = reporter.render(eval_result)
            if fmt == "console":
                print(content)
            else:
                report_path = _REPORTS_DIR / f"eval-{name}-{timestamp}.{_ext_for(fmt)}"
                report_path.write_text(content, encoding="utf-8")
                print(f"[{name}] {fmt} 报告已写入：{report_path}")

        if not all(cr.passed for cr in eval_result.case_results):
            overall_passed = False

    return 0 if overall_passed else 1


def _cmd_list() -> int:
    """执行 ``agentx eval list``，列出所有可用 suite。"""
    names = _list_suites()
    if not names:
        print("（无可用 suite）")
        return 0
    print("可用 suite：")
    for n in names:
        try:
            suite = _load_suite(n)
            print(f"  - {n}: {suite.name}（{len(suite.cases)} cases）")
        except SystemExit:
            # _load_suite 在不存在时会 sys.exit(2)，这里已经被 _list_suites 列出
            # 理论上不会触发，但防御性处理
            print(f"  - {n}: 加载失败")
        except Exception as exc:  # noqa: BLE001
            print(f"  - {n}: 加载失败 - {exc}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """执行 ``agentx eval show <name>``，打印 suite 的 case 列表。"""
    suite = _load_suite(args.suite_name)
    print(f"Suite: {suite.id} — {suite.name}")
    print(f"描述: {suite.description}")
    print(f"Cases ({len(suite.cases)}):")
    for i, c in enumerate(suite.cases, 1):
        print(f"  {i}. [{c.id}] mode={c.agent_mode} tags={c.tags}")
        preview = c.user_message[:80] + ("..." if len(c.user_message) > 80 else "")
        print(f"     user_message: {preview}")
        expect = c.expect
        parts: list[str] = []
        if expect.events:
            parts.append(f"events={len(expect.events)}")
        if expect.tools_called:
            parts.append(f"tools_called={expect.tools_called}")
        if expect.tools_not_called:
            parts.append(f"tools_not_called={expect.tools_not_called}")
        if expect.assertions:
            parts.append(f"assertions={len(expect.assertions)}")
        if expect.rubric:
            parts.append("rubric=enabled")
        if expect.self_correct:
            parts.append("self_correct=enabled")
        if parts:
            print(f"     expect: {', '.join(parts)}")
    return 0


def _cmd_export_feedback(args: argparse.Namespace) -> int:
    """执行 ``agentx eval export-feedback``（FR-10）。

    把最近 N 天的 thumb_down 反馈导成 EvalSuite YAML 写到
    ``tests/eval/suites/feedback-YYYYMMDD.yaml``，可直接被
    ``agentx eval run --suite feedback-YYYYMMDD --mock`` 消费。

    数据流：
    1. ``get_observation_sink().list_thumb_down_feedback_sync(days)``
       → JOIN observation_feedback + observation_run
    2. 每条 feedback 转 EvalCase：user_message / agent_mode / expect.rubric
       （rubric 优先用 feedback.comment，缺失时 _DEFAULT_RUBRIC）
    3. cases 数 == 0 时退出码 0 但不写文件（无 👎 可导出）
    """
    days = args.days
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 延迟 import：避免 cli 启动时强制加载 observation sink（可能需要 lifespan）
    from app.observability.observation import get_observation_sink
    sink = get_observation_sink()

    # list_thumb_down_feedback_sync 是同步方法，避免在 cli 中混入 asyncio.run
    rows = sink.list_thumb_down_feedback_sync(days=days)
    if not rows:
        print(f"最近 {days} 天无 thumb_down 反馈可导出。", file=sys.stderr)
        return 0

    cases: list[EvalCase] = []
    for row in rows:
        feedback_id = row.get("feedback_id") or "unknown"
        run_id = row.get("run_id", "unknown")
        user_message = row.get("user_message") or ""
        agent_mode = row.get("agent_mode") or "work"
        comment = (row.get("comment") or "").strip()
        rubric = comment if comment else _DEFAULT_RUBRIC
        cases.append(
            EvalCase(
                id=f"feedback-{feedback_id}-{run_id[:8]}",
                user_message=user_message,
                agent_mode=str(agent_mode),
                workspace_path=row.get("workspace_path"),
                expect={"rubric": rubric},
                tags=["feedback", "thumb_down"],
                timeout=120.0,
            )
        )

    suite = EvalSuite(
        id=f"feedback-{datetime.now().strftime('%Y%m%d')}",
        name=f"用户反馈（最近 {days} 天 thumb_down）",
        description=(
            f"由 agentx eval export-feedback 自动生成；"
            f"{len(cases)} 个 case，对应 observation_feedback 表中 "
            f"kind=thumb_down 且 created_at >= now()-{days} days 的行。"
        ),
        cases=cases,
    )

    yaml_path = output_dir / f"{suite.id}.yaml"
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            suite.model_dump(mode="json"),
            f,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"已导出 {len(cases)} 个 case 到 {yaml_path}")
    return 0


# ============================================================
# trace 评测：从 observation DB 导出已执行的轨迹 → EvalCase → Judge 打分
# ============================================================


def _db_events_to_sse_events(db_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 observation_event 表的行转换为 Judge 可消费的 SSE 事件列表。

    observation DB 存 ``{event_type, payload_json}``，Judge 期望：
    - AssertJudge：读 ``e["event"]`` + ``e["data"]``（JSON 字符串，解析后取 ``name``）
    - RubricJudge：读 ``e["tool"]`` / ``e["args"]`` / ``e["tool_call_id"]`` /
      ``e["result"]``（顶层字段）

    本函数产出**同时满足两者**的扁平格式：
    - token → ``{"event": "token", "data": "content string"}``
    - tool_call → ``{"event": "tool_call", "data": "{...}", "tool": "name",
      "args": {...}, "tool_call_id": "id"}``
    - tool_result → ``{"event": "tool_result", "data": "{...}", "tool_call_id": "id",
      "result": "..."}``
    - 其他 → ``{"event": event_type, "data": payload_json}``

    跳过 LangChain callback 事件（llm_start/llm_end/tool_start/tool_end/chain_*
    等），Judge 不消费这些。
    """
    # Judge 不消费的 LangChain callback 事件类型
    _SKIP_TYPES = frozenset({
        "llm_start", "llm_end", "tool_start", "tool_end",
        "chain_start", "chain_end",
    })

    sse_events: list[dict[str, Any]] = []
    for row in db_rows:
        et = row.get("event_type", "")
        if et in _SKIP_TYPES:
            continue

        payload_str = row.get("payload_json", "")
        try:
            payload = json.loads(payload_str) if payload_str else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        if et == "token":
            # payload = {"content": "...", "live": true}
            content = payload.get("content", "")
            sse_events.append({"event": "token", "data": content})
        elif et == "tool_call":
            # payload = {"id": "...", "name": "...", "args": {...}, "source": "..."}
            sse_events.append({
                "event": "tool_call",
                "data": json.dumps(payload, ensure_ascii=False, default=str),
                "tool": payload.get("name", ""),
                "args": payload.get("args", {}),
                "tool_call_id": payload.get("id", ""),
            })
        elif et == "tool_result":
            # payload = {"id": "...", "name": "...", "result": "...", "source": "..."}
            sse_events.append({
                "event": "tool_result",
                "data": json.dumps(payload, ensure_ascii=False, default=str),
                "tool_call_id": payload.get("id", ""),
                "result": payload.get("result", ""),
            })
        else:
            # reasoning / reasoning_delta / token_rollback / todo_update / done / error 等
            sse_events.append({"event": et, "data": payload_str})

    return sse_events


def _load_trace_from_observation(run_id: str) -> tuple[dict | None, list[dict[str, Any]]]:
    """从 observation DB 读取 run 详情 + 事件流，转换为 Judge 可消费的格式。

    Returns:
        (run_dict, sse_events)。run_dict 为 None 表示 run 不存在。
    """
    from app.observability.observation import get_observation_sink

    sink = get_observation_sink()
    run = sink.get_run_sync(run_id)
    db_events = sink.list_events_sync(run_id)
    sse_events = _db_events_to_sse_events(db_events)
    return run, sse_events


def _build_trace_eval_case(
    run: dict[str, Any],
    sse_events: list[dict[str, Any]],
    rubric: str | None = None,
) -> EvalCase:
    """从 observation run + sse_events 构造 EvalCase（含 trace_events）。

    rubric 优先用传入值，否则用 run 的 feedback comment（若有），最终兜底 _DEFAULT_RUBRIC。
    """
    # rubric 优先级：CLI 传入 > run 关联的 thumb_down comment > 默认
    final_rubric = rubric
    if final_rubric is None:
        # 尝试从 run 的 feedback 读取 thumb_down comment 作为 rubric
        from app.observability.observation import get_observation_sink

        sink = get_observation_sink()
        feedbacks = sink.list_feedback_sync(run.get("run_id", ""))
        thumb_down = next(
            (f for f in feedbacks if f.get("kind") == "thumb_down"), None
        )
        if thumb_down and thumb_down.get("comment"):
            final_rubric = thumb_down["comment"]
        else:
            final_rubric = _DEFAULT_RUBRIC

    run_id = run.get("run_id", "unknown")
    return EvalCase(
        id=f"trace-{run_id[:8]}",
        user_message=run.get("user_message", ""),
        agent_mode=str(run.get("agent_mode", "work")),
        workspace_path=run.get("workspace_path"),
        expect={"rubric": final_rubric},
        tags=["trace", "observation"],
        trace_events=sse_events,
    )


def _cmd_run_trace(args: argparse.Namespace) -> int:
    """执行 ``agentx eval run-trace <run_id>``。

    从 observation DB 读取已执行的轨迹 → 构造 EvalCase → EvalRunner.run_trace
    直接交 Judge 打分（不重跑 run_router）。支持 L1 AssertJudge + L2 RubricJudge。
    """
    run_id = args.run_id
    run, sse_events = _load_trace_from_observation(run_id)

    if run is None and not sse_events:
        print(f"错误：未找到 run_id={run_id} 的轨迹数据", file=sys.stderr)
        return 2

    case = _build_trace_eval_case(run or {"run_id": run_id}, sse_events, rubric=args.rubric)

    if not case.trace_events:
        print(f"错误：run_id={run_id} 的事件流为空，无轨迹可评", file=sys.stderr)
        return 2

    # 构造 Judge 链（与 _cmd_run 一致：L1 必跑 + L2 可选）
    no_rubric = args.no_rubric
    judges: list = [AssertJudge()]
    if not no_rubric:
        judges.append(RubricJudge(no_rubric=no_rubric))

    runner = EvalRunner(no_rubric=no_rubric)
    eval_result = asyncio.run(runner.run_trace(case, judges=judges))

    # 渲染报告
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    if not formats:
        formats = ["console"]

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 构造单 case 的 EvalResult 供 reporter 使用
    from app.eval.models import EvalResult as _ER

    eval_result_wrapped = _ER(
        suite_id=f"trace-{run_id[:8]}",
        started_at=datetime.now(),
        case_results=[eval_result],
    )

    for fmt in formats:
        reporter = _make_reporter(fmt)
        content = reporter.render(eval_result_wrapped)
        if fmt == "console":
            print(content)
        else:
            report_path = _REPORTS_DIR / f"eval-trace-{run_id[:8]}-{timestamp}.{_ext_for(fmt)}"
            report_path.write_text(content, encoding="utf-8")
            print(f"[trace-{run_id[:8]}] {fmt} 报告已写入：{report_path}")

    return 0 if eval_result.passed else 1


def _cmd_export_trace(args: argparse.Namespace) -> int:
    """执行 ``agentx eval export-trace <run_id>``。

    把 observation DB 的 run + events 打包为 EvalSuite YAML（含 trace_events），
    可被 ``agentx eval run --suite <name>`` 加载，或直接被外部 agent 消费。
    """
    run_id = args.run_id
    run, sse_events = _load_trace_from_observation(run_id)

    if run is None and not sse_events:
        print(f"错误：未找到 run_id={run_id} 的轨迹数据", file=sys.stderr)
        return 2

    case = _build_trace_eval_case(run or {"run_id": run_id}, sse_events, rubric=args.rubric)

    suite = EvalSuite(
        id=f"trace-{run_id[:8]}",
        name=f"执行轨迹导出（run_id={run_id[:8]}）",
        description=(
            f"由 agentx eval export-trace 自动生成；"
            f"{len(case.trace_events or [])} 个 SSE 事件，"
            f"对应 observation_run 表中 run_id={run_id} 的会话。"
        ),
        cases=[case],
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = output_dir / f"{suite.id}.yaml"
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            suite.model_dump(mode="json"),
            f,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"已导出 {len(case.trace_events or [])} 个事件到 {yaml_path}")
    return 0


def run_eval_command(args: argparse.Namespace) -> int:
    """eval 子命令分发入口（由 ``app.cli.main`` 调用）。

    根据 ``args.eval_command`` 分发到 ``_cmd_run`` / ``_cmd_list`` /
    ``_cmd_show`` / ``_cmd_export_feedback`` / ``_cmd_run_trace`` / ``_cmd_export_trace``。
    未指定子命令时打印用法并返回 2。
    """
    if args.eval_command == "run":
        return _cmd_run(args)
    if args.eval_command == "list":
        return _cmd_list()
    if args.eval_command == "show":
        return _cmd_show(args)
    if args.eval_command == "export-feedback":
        return _cmd_export_feedback(args)
    if args.eval_command == "run-trace":
        return _cmd_run_trace(args)
    if args.eval_command == "export-trace":
        return _cmd_export_trace(args)
    print(f"错误：未知 eval 子命令 '{args.eval_command}'", file=sys.stderr)
    print("用法：agentx eval run|list|show|export-feedback|run-trace|export-trace ...", file=sys.stderr)
    return 2
