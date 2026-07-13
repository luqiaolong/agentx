"""AgentTeam v2 StateGraph 构建（T6）。

构建 LangGraph ``StateGraph`` 拓扑，连接 ``nodes.py`` 的 6 个节点函数 +
``dispatcher._dispatch_router`` 条件边 + 3 个路由函数：

拓扑（D3/D16）::

    START → plan → dispatch → (Send fan-out) → execute → barrier
                                                           │
                                                  _route_after_barrier
                                                  /              \
                                            有 wave             无 wave
                                              ↓                   ↓
                                          dispatch            aggregate
                                                          │
                                                 _route_after_aggregate
                                                 /              \
                                        质量门通过 → END    质量门失败 → replan
                                                                           │
                                                                 _route_after_replan
                                                                 /              \
                                                           有 wave           无 wave
                                                             ↓                 ↓
                                                         dispatch             END

关键设计：
- ``dispatch`` 节点返回空 dict，``_dispatch_router`` 返回 ``list[Send]`` fan-out
- ``execute`` 是统一执行节点（替代旧 6 个分类节点）
- ``barrier`` 是 join 节点（无状态更新）
- ``aggregate`` 后的条件边是 D16 新增（质量门通过 → END / 失败 → replan）
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import START, StateGraph

from app.team.dispatcher import _dispatch_router, dispatch_node
from app.team.nodes import (
    aggregate_node,
    barrier_node,
    execute_node,
    plan_node,
    _route_after_aggregate,
    _route_after_barrier,
    _route_after_replan,
    replan_node,
)
from app.team.state import TeamState

__all__ = ["build_team_graph", "get_team_graph"]


def build_team_graph() -> Any:
    """构建 v2 Team StateGraph（D3/D16 拓扑）。

    Returns:
        已编译的 ``CompiledGraph``，可通过 ``graph.astream(state, stream_mode=...)`` 消费。
    """
    graph: Any = StateGraph(TeamState)

    # 6 个节点
    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("execute", execute_node)
    graph.add_node("barrier", barrier_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("replan", replan_node)

    # 边
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")
    # dispatch 节点返回空 dict，_dispatch_router 返回 list[Send] fan-out 到 execute
    graph.add_conditional_edges("dispatch", _dispatch_router)
    # execute → barrier（join 节点，等当前 wave 所有子任务完成）
    graph.add_edge("execute", "barrier")
    # barrier 条件边：有 wave → dispatch；无 → aggregate（D16：不再路由到 replan）
    graph.add_conditional_edges("barrier", _route_after_barrier)
    # D16 新增：aggregate 条件边（质量门通过 → END / 失败 → replan）
    graph.add_conditional_edges("aggregate", _route_after_aggregate)
    # replan 条件边：有 pending_waves → dispatch；无 → END
    graph.add_conditional_edges("replan", _route_after_replan)

    return graph.compile()


# 单例缓存（与旧 orchestrator._team_graph 同策略）
_team_graph: Any = None


def get_team_graph() -> Any:
    """获取已编译的 Team StateGraph（单例缓存）。

    首次调用时构建，后续返回缓存。LangGraph 编译开销较大，单例避免重复编译。
    """
    global _team_graph
    if _team_graph is None:
        _team_graph = build_team_graph()
    return _team_graph
