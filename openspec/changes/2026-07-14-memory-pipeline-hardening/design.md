# Design: 记忆链路硬化与一致性修复

## Context

当前记忆链路包含七类状态源：

1. 会话历史：LangGraph `AsyncSqliteSaver`，存于 `data/agentx.db`
2. 全局画像：`data/config/profile.json`
3. 工作区记忆：`<workspace>/.agentx/memory/*.md`
4. 旧工作区画像：`<workspace>/.agentx/profile.json`
5. DeepAgents `MemoryMiddleware` 注入的 skills / AGENTS / rules / memory files
6. 持久化自动抽取队列：`profile_extract_queue`
7. Dream LLM 整理任务

这些源头的所有权不同：checkpoint 属于 LangGraph，长期记忆属于 profile/workspace store，上下文注入属于 DeepAgents middleware，抽取队列属于后台 worker。现在的问题主要来自跨边界手写状态：手工写 checkpoint、把 LLM 输出直接套用为最终记忆状态、用 rowid 推断消息语义、把异常吞成空结果。

## Goals

- 让 Dream 只能在可预览、可校验、可回滚时修改记忆
- 让 chat compact/rewind/reset 遵守 LangGraph checkpoint 语义
- 让抽取队列的失败、重试、空结果成为显式状态
- 让全局/工作区/项目记忆的作用域可解释、可测试
- 让 profile prompt 在 Work、Coding、Team 链路中一致生效
- 让记忆 API 遵守 workspace 授权和 secret filtering
- 让注入上下文从“全部塞入”逐步转为“核心记忆 + 相关检索”

## Non-Goals

- 不自研新的 agent memory 框架
- 不绕过 DeepAgents/LangGraph 去维护另一套 checkpoint store
- 不在本提案中实现完整向量记忆系统；TEI/Milvus Top-K 作为 P2 增强
- 不删除旧数据；旧 workspace profile 走迁移/只读兼容

## Decisions

### D1. Dream 从“全量替换”改为“受控 patch”

Dream 当前结构让 LLM 返回“整理后的全部条目”，再通过集合差异推断删除。这对 LLM 输出过于信任：漏一条就是删除，target 错一条就是跨作用域移动。

新的 Dream 输出应是 patch 列表：

```text
MemoryPatch
- operation: upsert | delete | move | keep
- key
- source_scope?: global | workspace
- target_scope?: global | workspace
- entry?: structured memory entry
- reason
```

应用流程：

1. 加载当前 global/workspace snapshot
2. 调 LLM 生成 patch
3. 纯函数计算 preview diff
4. 校验 key、scope、target、重复操作、delete 显式性
5. 写入前持久化 snapshot
6. 事务化应用 patch
7. 任一步失败则恢复 snapshot 并记录错误

### D2. checkpoint 压缩与回退不再直接拼 SQL 语义

LangGraph saver 的 checkpoint parent、namespace、channel version、writes 语义都由 saver 管理。后续实现必须按优先级选择：

1. DeepAgents 官方 summarization/context management
2. LangGraph 官方 state update / time travel / checkpoint APIs
3. saver 提供的 `aput` / `adelete_thread` / `alist(config)` 等正式接口
4. raw SQL 只允许作为只读诊断或经过隔离的迁移脚本

`compact` 的最小正确语义：

- 必须带 `configurable.thread_id`
- 必须带 `configurable.checkpoint_ns`
- parent 必须来自旧 checkpoint 的 config，而不是把新 `checkpoint_id` 当 parent
- `new_versions` 必须是 saver 期待的 mapping
- 活跃 run 期间不得 compact 同一 thread

`rewind` 的目标语义：

- 前端传入要回退到的真实 message id 或 checkpoint id
- 后端通过 checkpoint metadata / messages 查找分支点
- 创建新分支或删除后续状态必须保持 writes/checkpoints 一致
- 前端必须 await 后端结果后再 resend

### D3. 抽取队列以“任务状态机”建模

`[]` 既可能代表“没有可抽取记忆”，也可能代表“LLM 失败后被吞异常”。这两个状态必须拆开。

建议状态：

```text
pending -> leased -> completed_empty
                  -> completed_written
                  -> retryable_failed
                  -> dead_letter
```

队列表字段：

```text
id
message
assistant_reply
workspace_path
status
attempts
last_error
leased_until
created_at
updated_at
```

worker 用 SQL 原子 claim 获取任务，任务完成后按结果更新状态。shutdown drain 只能处理未被其他 worker lease 的任务，避免重复消费。

### D4. 记忆作用域必须由内容分类决定

当前“有 workspace 就写 workspace”的规则会把用户长期偏好困在项目内。新的分类策略：

- `preference`: 用户表达的长期偏好，默认 global，可带 workspace override
- `fact`: 用户个人事实，默认 global，敏感事实需过滤或标记
- `project`: 当前 workspace 的技术/约定/任务记忆，必须 workspace
- `ephemeral`: 对当前线程有用但不应持久化，默认不入库

分类结果必须包含 `scope`、`confidence`、`sensitivity`。低置信度不自动写入，或进入候选队列。

### D5. profile prompt 注入成为执行契约

`profile_prompt` 不能只在 router 层构建后部分链路使用。后续实现必须覆盖：

- Work supervisor
- Coding expert
- Coding team planner
- Team role subtask
- Team aggregator

每条链路测试应能验证 profile prompt 被传入最终 prompt/context。对 DeepAgents `MemoryMiddleware` 的缓存，应在工作区 memory 文件变更时基于 version/mtime 重新加载，而不是永久复用线程内 `memory_contents`。

### D6. 记忆 API 进入安全边界

所有接受 `workspace_path` 的读写接口必须先验证授权根目录。存储前增加敏感信息扫描：

- API key / token / password / private key
- 连接串与带凭证 URL
- 个人身份敏感字段

过滤策略分层：

- 明确 secret：拒绝入库并返回可解释错误
- 可能敏感：标记 `sensitivity=private`，默认不注入 prompt
- 普通记忆：可按 scope 注入或检索

## Delivery Sequence

### Phase 1: P0 数据安全与 checkpoint 正确性

- Dream 默认关闭并修复明显 async/import 错误
- compact/CLI compact 切到正确 saver 语义或暂时 fail-fast
- rewind 改为 await 后端完成，禁止不一致 resend
- 抽取失败不再删除队列任务

### Phase 2: 作用域与执行一致性

- 引入 `scope/confidence/sensitivity`
- Work/Coding/Team 统一 profile 注入和抽取触发
- workspace_path 授权校验
- secret filtering

### Phase 3: 记忆质量与检索

- Dream patch preview/rollback
- DeepAgents MemoryMiddleware version/mtime reload
- legacy workspace profile migration
- keywords/scenarios 进入 Top-K 检索

## Risks & Mitigations

### Risk 1: checkpoint 改造触及历史核心链路

缓解：先补真实 `AsyncSqliteSaver` 回归测试；实现时优先用官方 API；无法安全 compact 时宁可返回明确错误。

### Risk 2: scope 分类可能误判

缓解：新增 `confidence`，低置信度不自动写入；前端可展示候选；测试覆盖偏好、项目事实、敏感内容。

### Risk 3: secret filtering 误杀正常文本

缓解：分级处理，先 warn-only 观察，再对高置信 secret 阻断。

### Risk 4: MemoryMiddleware reload 影响性能

缓解：只比较 workspace memory/rules 的 mtime/version，变更才刷新；不在每 token 路径读取磁盘。
