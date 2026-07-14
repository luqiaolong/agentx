# Proposal: 记忆链路硬化与一致性修复

## Why

本次审计确认，AgentX 的“记忆”并不是单一模块，而是由会话 checkpoint、全局画像、工作区 `.agentx/memory/*.md`、DeepAgents memory、自动抽取队列、Dream 整理、前端历史编辑共同组成的一条链路。当前主路径可用，但多处关键环节缺少生命周期、作用域和失败语义，导致记忆可能丢失、误删、污染作用域，或与聊天历史状态不一致。

### 关键问题

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | Dream 默认开启但会启动即失败 | [settings.py:177](../../../backend/app/config/settings.py#L177), [dream.py:115](../../../backend/app/memory/dream.py#L115) | 导入不存在的 `delete_entry`，整理任务无法运行 |
| 2 | Dream 把 LLM 输出当完整最终状态应用 | [dream.py:158](../../../backend/app/memory/dream.py#L158), [dream.py:231](../../../backend/app/memory/dream.py#L231) | target 缺失/错误、LLM 漏项都可能触发误删或跨作用域移动 |
| 3 | `/api/chat/compact` 手工构造 checkpoint 不符合 LangGraph saver 语义 | [chat.py:650](../../../backend/app/api/chat.py#L650), [chat.py:671](../../../backend/app/api/chat.py#L671) | 缺少 `checkpoint_ns`、parent 关系错误，可能破坏历史链 |
| 4 | CLI `/compact` 存在同类问题 | [commands.py:252](../../../backend/app/cli/commands.py#L252), [commands.py:296](../../../backend/app/cli/commands.py#L296) | CLI 压缩路径与后端路径都可能写出坏 checkpoint |
| 5 | 历史回退按“每条消息两个 checkpoint”猜测 | [checkpointer_view.py:191](../../../backend/app/memory/checkpointer_view.py#L191), [checkpointer_view.py:256](../../../backend/app/memory/checkpointer_view.py#L256) | `writes.rowid` 与 `checkpoints.rowid` 不可比较，容易删错 writes/checkpoints |
| 6 | 前端删除后立即重发，未等待后端 rewind 完成 | [index.ts:1156](../../../frontend/renderer/stores/chat/index.ts#L1156), [ChatView.tsx:623](../../../frontend/renderer/components/chat/ChatView.tsx#L623) | UI 状态与 checkpoint 状态竞争，造成重发串线 |
| 7 | 自动抽取失败被吞成空结果 | [profile_extractor.py:96](../../../backend/app/memory/profile_extractor.py#L96), [extract_queue.py:193](../../../backend/app/memory/extract_queue.py#L193) | LLM 调用失败会被当成功处理并删除队列任务 |
| 8 | 作用域、安全和注入不一致 | `profile_store` / `memory_store` / `team` / `frontend` | 工作区内学到的用户偏好可能只写入项目；Team 未稳定消费 profile；workspace_path 缺少授权校验；敏感信息可能进入记忆 |

## What Changes

### A1. Dream 整理链路默认安全化

- 默认关闭 Dream，直到其具备预览、校验、事务化应用和回滚能力
- Dream 输出必须使用严格枚举与 schema 校验，`target` 只能是 `global` 或 `workspace`
- Dream 不得把 LLM 漏掉的条目自动视为删除；删除必须来自显式 `delete` 操作并通过 diff preview
- Dream 应用采用 snapshot + transaction：任一写入/删除失败则回滚，不产生半整理状态
- 所有全局/工作区删除操作必须正确 `await` 并复用现有 store API

### A2. checkpoint/历史编辑链路回到框架语义

- 移除手工构造 checkpoint 的压缩实现，优先使用 DeepAgents/LangGraph 官方 summarization、state update 或 time-travel API
- 后端 `/api/chat/compact` 与 CLI `/compact` 必须使用正确 `checkpoint_ns`、parent config、channel versions 语义
- 历史回退从“消息数量猜测”改为“基于真实 message/checkpoint id 的语义分支”
- 删除/重发必须等待后端 rewind/branch 完成，失败则不进入 resend
- reset/child thread cleanup 使用 `adelete_thread` / `alist(config)` 等官方接口，不再混用 raw SQL 与不匹配的 rowid

### A3. 自动抽取队列具备失败语义与作用域策略

- `extract_profile_via_llm` 区分“成功但无可抽取记忆”和“抽取失败”
- 队列表增加 `attempts`、`last_error`、`status`、`leased_until`，支持重试、死信、恢复
- 队列消费采用原子 claim/lease，避免 shutdown drain 与 worker 并发重复消费
- 自动作用域不再只看是否存在 workspace，而是对“用户偏好/事实/项目知识”做显式分类
- `work`、`coding`、`coding_team` 三条主链都必须一致触发抽取或明确声明不抽取

### A4. 记忆安全、注入与前端 API 一致性

- 所有带 `workspace_path` 的记忆 API 必须通过 `SessionSandbox` 或等价授权校验
- 入库前增加 secret/credential 过滤与敏感级别标记，敏感记忆默认不注入 prompt
- `profile_prompt` 必须在 Work、Coding、Team planner、aggregator、role agent 中一致注入
- DeepAgents `MemoryMiddleware` 的工作区 memory reload 要具备 version/mtime 感知，避免线程内缓存陈旧
- 前端 memory API 统一 `assertOk`，禁止 project memory 在无 workspace 时写入 global project entry
- `keywords` / `scenarios` 进入检索策略：先注入 pinned/core，再按当前请求 Top-K 检索相关记忆

## Capabilities

### New Capabilities

- `memory-consolidation-lifecycle`
- `checkpoint-memory-lifecycle`
- `profile-extraction-scope`
- `memory-safety-contract`

## Impact

- **后端**：`backend/app/memory/`、`backend/app/workspace/memory_store.py`、`backend/app/api/chat.py`、`backend/app/api/memory.py`、`backend/app/cli/commands.py`、`backend/app/router/graph.py`、`backend/app/team/`
- **前端**：`frontend/renderer/stores/chat/index.ts`、`frontend/renderer/components/chat/ChatView.tsx`、`frontend/renderer/lib/api/http.ts`、记忆设置/工作面板相关组件
- **测试**：新增 Dream dry-run/rollback、AsyncSqliteSaver compact、rewind resend、extract queue failure/retry、scope classification、workspace authorization、secret filtering 回归测试
- **兼容性**：默认关闭 Dream 属于风险收敛；checkpoint 压缩和历史编辑需保持现有 API 路由，但内部实现切换到框架语义

## Rollback Plan

1. Dream 默认关闭可以独立回滚为配置项变更，但不建议在无事务校验前重新默认开启。
2. checkpoint compact/rewind 改造按后端 API 与 CLI 分阶段提交；若新路径失败，保留旧 API 但返回明确错误，不回退到已知不安全写法。
3. 抽取队列表结构扩展采用兼容迁移；旧 pending 任务默认进入 `pending` 状态，`attempts=0`。
4. 安全过滤与作用域分类先以 warn-only + telemetry 落地，再切换为阻断/改写。

## Out of Scope

- 本变更只生成 OpenSpec artifacts，不直接实现代码
- 不替换 DeepAgents/LangGraph；相反，后续实现必须优先回归 DeepAgents/LangGraph 官方 API
- 不一次性重做 UI，只修复记忆相关 API 行为与必要的状态等待
