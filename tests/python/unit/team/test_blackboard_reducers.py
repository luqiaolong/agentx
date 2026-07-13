"""blackboard reducers 测试 — 验证 _merge_findings 不丢失同 key finding。"""
from app.team.blackboard import _merge_findings


def test_merge_findings_same_key_collects_to_list():
    """同 key 的两个 finding 应都保留（收集到 list），不丢失。"""
    left = {"code:t1:0": {"content": "finding-1"}}
    right = {"code:t1:0": {"content": "finding-2"}}
    result = _merge_findings(left, right)
    # 同 key 应保留两边数据
    val = result["code:t1:0"]
    assert isinstance(val, list), f"同 key 应收集为 list，实际 {type(val)}"
    assert len(val) == 2
    assert val[0]["content"] == "finding-1"
    assert val[1]["content"] == "finding-2"


def test_merge_findings_different_keys_merge():
    """不同 key 的 finding 合并到同一 dict。"""
    left = {"code:t1:0": {"content": "f1"}}
    right = {"web:t2:0": {"content": "f2"}}
    result = _merge_findings(left, right)
    assert len(result) == 2
    assert "code:t1:0" in result
    assert "web:t2:0" in result


def test_merge_findings_empty_right():
    """right 为空时返回 left 副本。"""
    left = {"code:t1:0": {"content": "f1"}}
    result = _merge_findings(left, {})
    assert result == left
    assert result is not left  # 应是副本
