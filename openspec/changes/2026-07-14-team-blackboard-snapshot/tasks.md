# 任务追踪 — team-blackboard-snapshot

## 预期修改文件

### 后端（2 文件）
- [ ] `backend/app/team/blackboard.py` — 新增 `_serialize_findings_for_sse(findings) -> list[dict]`
- [ ] `backend/app/team/nodes.py` — 3 个 `team_done` 发射点追加 `blackboard` payload

### 前端（4 文件）
- [ ] `frontend/renderer/lib/api/blackboard.ts` — 新建 Finding / BlackboardSnapshot 类型
- [ ] `frontend/renderer/stores/chat/index.ts` — MessagePart.team.blackboard + upsertTeamNode updater
- [ ] `frontend/renderer/hooks/useChatStream.ts` — team_done.blackboard 处理
- [ ] `frontend/renderer/components/chat/parts/TeamNodeCard.tsx` — BlackboardPanel 升级

### 文档（1 文件）
- [ ] `docs/agents/02-sse-event-contract.md` — team_done payload 增补 blackboard 字段

### 测试（2 文件新增）
- [ ] 后端单测：aggregate_node blackboard 序列化
- [ ] 前端单测：BlackboardPanel blackboardSnapshot 优先

## OpenSpec Tasks
| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T2.1 | blackboard.py 新增 _serialize_findings_for_sse | blackboard.py | Finding → dict 含 7 字段 | ⬜ |
| T2.2 | nodes.py line 530 空 findings 路径追加 blackboard | nodes.py | payload.blackboard.findings == [] | ⬜ |
| T2.3 | nodes.py line 544 abort 路径追加 blackboard | nodes.py | payload.blackboard 含部分 findings | ⬜ |
| T2.4 | nodes.py line 612 正常路径追加 blackboard | nodes.py | payload.blackboard 含全部 findings | ⬜ |
| T2.5 | 后端单测：正常路径 blackboard 序列化 | tests/python/ | findings 含 task_id/retries/error | ⬜ |
| T2.6 | 后端单测：空 findings 路径 | tests/python/ | findings == [], errors 含值 | ⬜ |
| T3.1 | 新建 blackboard.ts 类型文件 | lib/api/blackboard.ts | Finding + BlackboardSnapshot 导出 | ⬜ |
| T3.2 | MessagePart.team 加 blackboard 字段 | stores/chat/index.ts | blackboard?: BlackboardSnapshot | ⬜ |
| T4.1 | upsertTeamNode updater 加 blackboard | stores/chat/index.ts | 创建+更新路径都写入 | ⬜ |
| T5.1 | useChatStream team_done.blackboard 处理 | useChatStream.ts | 调用 upsertTeamNode({blackboard}) | ⬜ |
| T5.2 | 前端单测：SSE team_done.blackboard | tests/renderer/ | store 中 team part blackboard 被填充 | ⬜ |
| T6.1 | BlackboardPanel 升级消费 blackboardSnapshot | TeamNodeCard.tsx | 优先 snapshot, fallback agents | ⬜ |
| T6.2 | TeamNodeCardImpl 传 blackboardSnapshot prop | TeamNodeCard.tsx | 有 blackboard 时传 prop | ⬜ |
| T6.3 | 前端单测：blackboardSnapshot 渲染 | tests/renderer/ | 显示 task_id/retries/error | ⬜ |
| T6.4 | 前端单测：fallback 聚合 | tests/renderer/ | 无 snapshot 时走 agents | ⬜ |
| T7.1 | 更新 SSE 事件契约文档 | docs/agents/ | team_done 含 blackboard 字段 | ⬜ |
| T8.1 | pytest 全绿 | — | exit 0 | ⬜ |
| T8.2 | vitest 全绿 | — | 524 + 2 新 = 526 | ⬜ |
| T8.3 | typecheck 全绿 | — | node + web exit 0 | ⬜ |

## 规模判定
- 涉及文件数: 7（含文档+测试） → 规模: **L**
- 涉及模块数: 2（backend/team + frontend/renderer）
- 跨模块: 是 → 全流程（Worktree + TDD + 双轨 Review + 部署验证 + 归档）
