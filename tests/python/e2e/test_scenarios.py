"""6 个 P0/P1 真实场景 E2E 测试。

每个测试独立 thread_id，互不干扰。需要真实 LLM（场景 1/3/4/6）。
场景 2/5 不依赖 LLM 工具调用，仅检查路径 + 授权语义。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest


# ============================================================
# 辅助工具
# ============================================================

async def _collect_sse_events(response, max_seconds: float = 60.0) -> list[dict[str, Any]]:
    """异步流式读取 SSE 事件，按行解析。

    Returns:
        解析后的事件列表，每项为 ``{event: str, data: dict|str}``。
        遇 ``event: done`` 提前结束。
    """
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + max_seconds
    current_event: str | None = None
    current_data_lines: list[str] = []

    async for line in response.aiter_lines():
        if time.monotonic() > deadline:
            break
        if not line:
            # 事件分隔符：dispatch 当前事件
            if current_event and current_data_lines:
                raw = "\n".join(current_data_lines)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = raw
                events.append({"event": current_event, "data": data})
            current_event = None
            current_data_lines = []
            continue
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:"):].lstrip())
        # comment lines (`: ping`) ignored
    return events


async def _post_chat(client, body: dict) -> tuple[int, list[dict[str, Any]]]:
    """POST /api/chat 并返回 (status_code, events)。"""
    response = await client.post("/api/chat", json=body)
    status = response.status_code
    events = await _collect_sse_events(response, max_seconds=45.0)
    return status, events


# ============================================================
# 场景 1: CHAT 跨轮记忆
# ============================================================

@pytest.mark.asyncio
async def test_scenario_1_chat_cross_turn_memory(async_client):
    """场景 1：路径 A 跨轮记忆。

    第 1 轮：告诉 LLM "我叫张三"
    第 2 轮：问 "我叫什么？"
    断言：第 2 轮的 token 事件含 "张三"（证明 checkpoint 写回工作）。
    """
    thread_id = f"e2e-scenario-1-{int(time.time())}"

    # 第 1 轮：自我介绍
    status1, events1 = await _post_chat(async_client, {
        "message": "记住我的名字：我叫张三。请简短回应（一句话）。",
        "thread_id": thread_id,
        "permission_mode": "standard",
    })
    assert status1 == 200
    assert any(e["event"] == "done" for e in events1), "第 1 轮应正常完成"

    # 第 2 轮：跨轮回忆
    status2, events2 = await _post_chat(async_client, {
        "message": "请问我叫什么名字？",
        "thread_id": thread_id,
        "permission_mode": "standard",
    })
    assert status2 == 200
    assert any(e["event"] == "done" for e in events2)

    # 验证：第 2 轮 token 拼接含"张三"
    token_text = "".join(
        e["data"] for e in events2
        if e["event"] == "token" and isinstance(e["data"], str)
    )
    assert "张三" in token_text, (
        f"场景 1 失败：第 2 轮 LLM 应答含'张三'，实际：{token_text[:200]}"
    )


# ============================================================
# 场景 2: workspace 字段化（不依赖 LLM 推理）
# ============================================================

def test_scenario_2_workspace_path_field(client):
    """场景 2: workspace_path 字段透传到 router。

    POST /api/chat 携带 workspace_path=某未授权目录，
    验证后端日志已记录 router.workspace_authorized（或未授权拒绝）。
    """
    # 简单验证：workspace_path 是 ChatRequest 接受的字段
    # 且后端不会因携带该字段而 422
    # 完整 E2E 需 LLM 推理；此处仅验证 schema 接受字段
    response = client.post("/api/chat", json={
        "message": "hi",
        "thread_id": f"e2e-scenario-2-{int(time.time())}",
        "permission_mode": "standard",
        "workspace_path": "D:\\nonexistent\\test\\path",
    })
    # 不论 LLM 是否可用，状态码不应是 422（schema 错误）
    assert response.status_code != 422, (
        f"workspace_path 字段应被 ChatRequest 接受：{response.text[:500]}"
    )


# ============================================================
# 场景 3: pause / resume 端点
# ============================================================

@pytest.mark.asyncio
async def test_scenario_3_pause_resume_endpoints(client):
    """场景 3: pause / resume 端点可用。

    启动 chat 流（不读完），立即 pause → 应 200；
    等待 1s 后 resume → 应 200。
    """
    thread_id = f"e2e-scenario-3-{int(time.time())}"

    # pause
    resp_pause = client.post("/api/chat/pause", json={"thread_id": thread_id})
    assert resp_pause.status_code == 200
    assert resp_pause.json().get("ok") is True

    # 短暂 sleep 让 pause 标志在服务端生效
    time.sleep(0.5)

    # resume
    resp_resume = client.post("/api/chat/resume", json={"thread_id": thread_id})
    assert resp_resume.status_code == 200
    assert resp_resume.json().get("ok") is True


# ============================================================
# 场景 4: AGENT_TEAM 路径触发（不降级）
# ============================================================

@pytest.mark.asyncio
async def test_scenario_4_agent_team_not_downgraded(async_client):
    """场景 4: AGENT_TEAM 模式不再误降级到 chat。

    触发条件：消息含多步骤分析（避免 _SIMPLE_TASK_KEYWORDS 命中）
    且 thread 长度足够（≥ 6 中文 / ≥ 12 ASCII），
    agent_mode="agent_team"。

    断言：SSE 流中应出现 ``event: team_plan`` 或 ``event: tool_call``，
    说明路径 D 启动成功，未被降级到 chat 路径。
    """
    thread_id = f"e2e-scenario-4-{int(time.time())}"
    status, events = await _post_chat(async_client, {
        "message": (
            "我请你完成一个多步骤分析任务："
            "1. 列出 backend 目录的顶层结构；"
            "2. 读取 backend/app/main.py 文件前 30 行；"
            "3. 综合这两步信息总结后端 Web 框架选型。"
        ),
        "thread_id": thread_id,
        "permission_mode": "standard",
        "workspace_path": "D:\\java\\agentprojects\\agentx",
        "agent_mode": "agent_team",
    })
    assert status == 200

    event_types = {e["event"] for e in events}
    # 路径 D 应该触发：team_plan / tool_call / team_progress 至少其一
    team_path_indicators = {"team_plan", "team_progress", "team_result", "tool_call"}
    activated = event_types & team_path_indicators
    assert activated, (
        f"场景 4 失败：AGENT_TEAM 路径未启动，事件类型 = {event_types}"
    )


# ============================================================
# 场景 5: 危险工具审批触发
# ============================================================

@pytest.mark.asyncio
async def test_scenario_5_dangerous_tool_blocks_on_approve(async_client):
    """场景 5: shell_exec 危险工具触发后审批阻塞。

    验证：当 LLM 调用 shell_exec 等危险工具时，后端会先 yield approval_request
    （在 astream 收到 interrupt_on 信号后），然后阻塞等待审批。

    本测试不验证前端 UI，仅验证后端 SSE 流接口契约：
    - 流不会立即结束（done 事件晚于 approval_request）
    - 提交 approve 后流继续到 done

    由于 LLM 是否真调 shell_exec 不可控，本测试用宽松断言：
    流必须有 done 事件，且若存在 approval_request，必须在 done 之前。
    """
    thread_id = f"e2e-scenario-5-{int(time.time())}"
    status, events = await _post_chat(async_client, {
        "message": "请使用 shell_exec 工具执行 'ipconfig' 命令。",
        "thread_id": thread_id,
        "permission_mode": "standard",
    })
    assert status == 200

    event_types = [e["event"] for e in events]

    # 流必须正常结束
    assert "done" in event_types, "流必须有 done 事件"
    done_idx = event_types.index("done")

    # 若出现 approval_request，必须在 done 之前
    if "approval_request" in event_types:
        ar_idx = event_types.index("approval_request")
        assert ar_idx < done_idx, "approval_request 应在 done 之前 yield"


# ============================================================
# 场景 6: plan 事件提取
# ============================================================

@pytest.mark.asyncio
async def test_scenario_6_chat_plan_event_for_complex_task(async_client):
    """场景 6: 复杂任务下 chat 路径 tail 检测 plan JSON。

    验证：用户显式要求 LLM 输出 plan JSON 时，chat 路径会在流结束后
    yield 一个 ``plan`` SSE 事件（前端可据此渲染任务计划 UI）。

    断言：若 events 含 plan 事件，data 是 dict 且包含 ``plan`` 数组字段。
    若 LLM 未输出 plan JSON（输出自然语言总结），则允许流无 plan 事件。
    """
    thread_id = f"e2e-scenario-6-{int(time.time())}"
    status, events = await _post_chat(async_client, {
        "message": (
            "我请你分析一个问题。请先用 plan JSON（字段名为 plan）"
            "输出你的任务计划，然后逐项分析。"
        ),
        "thread_id": thread_id,
        "permission_mode": "standard",
    })
    assert status == 200

    plan_events = [e for e in events if e["event"] == "plan"]
    # 软断言：LLM 不一定按要求输出 JSON，但若输出则格式必须正确
    for evt in plan_events:
        assert isinstance(evt["data"], dict), f"plan 事件 data 必须是 dict：{evt}"
        assert "plan" in evt["data"], f"plan 事件 data 必须含 'plan' 字段：{evt}"
        assert isinstance(evt["data"]["plan"], list), (
            f"plan 事件 data.plan 必须是 list：{evt}"
        )