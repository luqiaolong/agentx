"""blackboard._serialize_findings_for_sse 单测 — T2.5/T2.6。

验证 findings 序列化为 SSE payload：
- 单个 Finding → 展平为 list[dict]
- list[Finding] → 展平为多项
- error 字段仅在 finding.error 不为 None 时加入
- 空 findings → 返回 []
"""
from app.team.blackboard import _serialize_findings_for_sse
from app.team.state import Finding


def test_serialize_single_finding():
    """单个 Finding 应展平为 list 含 1 项，含全部字段。"""
    findings = {
        "code:t1:0": Finding(
            agent="code",
            task_id="t1",
            wave_index=0,
            content="result",
            success=True,
            retries=0,
        )
    }
    result = _serialize_findings_for_sse(findings)
    assert isinstance(result, list)
    assert len(result) == 1
    item = result[0]
    assert item["agent"] == "code"
    assert item["task_id"] == "t1"
    assert item["wave_index"] == 0
    assert item["content"] == "result"
    assert item["success"] is True
    assert item["retries"] == 0
    # success=True 且 error 未设置时不应含 error 字段
    assert "error" not in item


def test_serialize_list_findings_flattened():
    """list[Finding] 应展平为多项（BE-N 修复后的混合形态）。"""
    findings = {
        "code:t1:0": Finding(
            agent="code", task_id="t1", wave_index=0, content="r1"
        ),
        "web:t2:0": [
            Finding(agent="web", task_id="t2", wave_index=0, content="r2"),
            Finding(agent="web", task_id="t2b", wave_index=1, content="r3"),
        ],
    }
    result = _serialize_findings_for_sse(findings)
    assert len(result) == 3
    agents = [item["agent"] for item in result]
    contents = [item["content"] for item in result]
    assert "code" in agents
    assert contents.count("r2") == 1
    assert contents.count("r3") == 1


def test_serialize_finding_with_error_field():
    """finding.error 不为 None 时应加入 error 字段，retries 也保留。"""
    findings = {
        "code:t1:0": Finding(
            agent="code",
            task_id="t1",
            wave_index=0,
            content="partial",
            success=False,
            error="timeout",
            retries=2,
        )
    }
    result = _serialize_findings_for_sse(findings)
    assert len(result) == 1
    item = result[0]
    assert item["error"] == "timeout"
    assert item["retries"] == 2
    assert item["success"] is False


def test_serialize_empty_findings_returns_empty_list():
    """空 findings 应返回空 list（T2.6，对应空 findings 路径）。"""
    assert _serialize_findings_for_sse({}) == []
