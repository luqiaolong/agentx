# agent-team-v2-refactor

AgentTeam 模块全量重构（v2）：将 `backend/app/team/orchestrator.py`（约 780 行单文件）
拆为 11 个单一职责模块，并一次性落地 P0+P1+P2 共 13 项改动，覆盖 DAG 依赖编排、
结构化任务通信、并发限流、危险任务 LLM 分类、未知 agent fallback、abort 响应 LLM 长调用、
失败重试、迭代式 replan、静默降级消除、token 透传一致性、findings key 区分。

本 change **supersede** 两个未落地的 prior change：

- `agent-team-dag-orchestration`（DAG 依赖编排，原 P0 子集，未落地）
- `2026-07-12-agent-team-architecture-cleanup`（架构清理，原 Phase 2，未落地）

理由：两个 prior change 的工作高度耦合（DAG 编排依赖架构清理引入的统一节点 / 派发表），
分两步落地会增加跨 change 的硬冲突。本 change 合二为一，直接推倒重来，删除旧代码。

项目约定：开发阶段无需考虑灰度和兼容老版本，直接推倒重来。
