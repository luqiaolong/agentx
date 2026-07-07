# Tasks: Agent Team 多代理协作

## Phase 1: OpenSpec 文档

- [x] 创建 `openspec/changes/2026-07-06-agent-team/.openspec.yaml`
- [x] 编写 `proposal.md`
- [x] 编写 `design.md`
- [x] 编写 `tasks.md`（本文件）
- [x] 编写 `specs/agent-team/spec.md`

## Phase 2: 前端状态与 UI

- [x] 新增 `frontend/renderer/stores/agentMode.ts`
  - 定义 `AgentMode = "agent" | "agent_team"`
  - zustand store，`mode` 默认 `"agent"`，跨会话持久化
- [x] 新增 `frontend/renderer/components/chat/ModeToggle.tsx`
  - 复刻 PermissionToggle 结构
  - trigger 展示当前模式图标 + 短名 + chevron
  - popover 底部弹出，含选项说明
  - `agent_team` 激活态用 brand 紫色
- [x] 修改 `frontend/renderer/components/chat/ChatComposer.tsx`
  - 导入 ModeToggle
  - 在底部 toolbar 左侧 workspace chip 前插入 `<ModeToggle />`
  - 保持现有布局不溢出

## Phase 3: 前后端 API 契约

- [x] 修改 `frontend/shared/api-types.ts`
  - 新增 `AgentMode` 类型
  - `ElectronAPI.chat.send` opts 增加 `agentMode?: AgentMode`
  - `ChatEvent` 增加 Team 事件：`team_plan` / `team_progress` / `team_result`
- [x] 修改 `frontend/preload/index.ts`
  - `streamChat` opts 增加 `agentMode`
  - POST body 增加 `agent_mode` 字段（默认 `"agent"`）
  - 解析新 SSE 事件类型并加入 `ChatEvent`
- [x] 修改 `frontend/renderer/components/chat/ChatView.tsx`
  - `handleSend` 读取 `useAgentModeStore.getState().mode`
  - 调用 `window.api.chat.send` 时传入 `agentMode`
- [x] 修改 `frontend/renderer/hooks/useChatStream.ts`
  - 新增 Team 事件分支处理
- [x] 修改 `frontend/renderer/stores/chat.ts` 与 `AssistantUIThread.tsx`
  - `MessagePart` 增加 team 类型
  - 渲染层展示团队计划、进度、结果卡片

## Phase 4: 后端配置与状态

- [x] 修改 `backend/app/config.py`
  - 新增 Team 配置项：`agent_team_enabled`、`agent_team_max_tasks`、`agent_team_max_parallel`、`agent_team_result_max_chars`
  - 字段来源 `AGENTX_AGENT_TEAM_*` env
- [x] 修改 `backend/app/router/state.py`
  - 新增可选字段：`agent_mode`、`team_plan`、`team_blackboard`

## Phase 5: 后端 Team 路径

- [x] 新增 `backend/app/paths/team_path.py`
  - 定义 `TeamPlanTask`、`TeamSubtaskResult`、`Blackboard` 数据结构
  - 实现 `_build_orchestrator_prompt`
  - 实现 `_parse_plan`（JSON 提取 + 校验 + 截断 + 危险任务强制改写 `deep`）
  - 实现 `_run_subtask`：async generator，根据 agent 名调用对应 runner，
    deep 子任务实时透传 approval_request 等事件，最后产出 `_subtask_done` 哨兵
  - 实现 `run_team_path`：队列驱动的并行调度（asyncio.Queue + Semaphore），
    子任务事件实时透传，Orchestrator → 调度 → Aggregator 流式输出
  - 危险任务强制走 `deep` agent
- [x] 修改 `backend/app/router/graph.py`
  - `run_router` 增加 `agent_mode` 参数
  - 分类后判断：若 `agent_mode == "agent_team"` 则调用 `run_team_path`
  - 否则保持原有三路径逻辑

## Phase 6: 后端端点

- [x] 修改 `backend/app/main.py`
  - `ChatRequest` 增加 `agent_mode: str = "agent"`
  - `_event_generator` 透传 `agent_mode` 到 `run_router`
  - 总开关 `agent_team_enabled=false` 时强制降级为 `agent` 模式

## Phase 7: SSE 事件对齐

- [x] 后端 `team_path.py` 在关键节点 yield SSE：
  - `team_plan`：Orchestrator 完成后
  - `team_progress`：每个子任务开始/完成/失败
  - `team_result`：每个子任务完成后摘要
  - `token` / `reasoning`：Aggregator 流式输出
- [x] 前端 `useChatStream.ts` 解析 Team 事件并写入 parts

## Phase 8: 测试

- [x] 后端新增 `tests/python/unit/test_team_path.py`
  - Orchestrator JSON 解析
  - 子任务并行调度
  - 黑板汇总
  - 危险任务强制 deep
  - run_team_path 完整链路（plan / progress / result / aggregator）
  - Bug 回归测试：token 纯字符串、approval_request 透传、_make_team_event token 行为
- [x] 修复 `test_chat_endpoint.py` 中 fake_run_router 签名以接受 `agent_mode`
- [x] 新增前端测试：ModeToggle 渲染、agentMode store 持久化
- [x] 运行 `npm run typecheck`（通过）
- [x] 运行 `uv run pytest tests/python/unit -m "not integration"`（423 passed, 1 skipped, 2 xfailed）

## Phase 9: 文档与收尾

- [x] 更新 `AGENTS.md` §13 SSE 事件表（新增 team 事件）
- [x] 增强 `_parse_plan`：支持 3 种 LLM 输出形态（纯 JSON / markdown 代码块 / 混杂文本）
- [x] HTTP 端到端自测：`/api/chat agent_mode=agent_team` 完整链路打通
  （team_plan → team_progress → team_result → reasoning → token → done）
- [x] 提交 commit
