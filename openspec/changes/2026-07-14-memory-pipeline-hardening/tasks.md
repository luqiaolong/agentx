# 任务追踪 — memory-pipeline-hardening

## 预期产物

- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/proposal.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/design.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/tasks.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/specs/memory-consolidation-lifecycle/spec.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/specs/checkpoint-memory-lifecycle/spec.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/specs/profile-extraction-scope/spec.md`
- [x] `openspec/changes/2026-07-14-memory-pipeline-hardening/specs/memory-safety-contract/spec.md`

## 建议实施顺序

### Phase 1: P0 止血

- [ ] T1.1 将 `dream_enabled` 默认值改为 `False`，避免不安全整理任务自动运行
- [ ] T1.2 修复 `dream.py` 对不存在 `profile_store.delete_entry` 的导入，并补直接导入测试
- [ ] T1.3 修复 Dream global delete 未 `await` 的 async 调用
- [ ] T1.4 为 Dream 增加最小 dry-run preview，未通过 schema 校验时不得写入/删除
- [ ] T1.5 修复 `/api/chat/compact` 的 saver config：补 `checkpoint_ns`，纠正 parent config 和 `new_versions`
- [ ] T1.6 修复 CLI `/compact` 的同类 saver config 与 `new_versions` 问题
- [ ] T1.7 在无法安全 compact 时 fail-fast 返回明确错误，禁止写出坏 checkpoint
- [ ] T1.8 修改前端删除/重发流程：`deleteMessagesAfter` 必须 await 后端 rewind 成功后才允许 resend
- [ ] T1.9 修复抽取器异常语义：LLM 失败抛出/返回 failure，不再伪装成 `[]`
- [ ] T1.10 队列 worker 对 failure 不删除任务，记录 `last_error`

### Phase 2: checkpoint 与历史编辑重构

- [ ] T2.1 为真实 `AsyncSqliteSaver` compact 写回归测试，覆盖 namespace、parent、versions
- [ ] T2.2 替换低层 checkpoint 手工构造，优先接入 DeepAgents/LangGraph summarization 或 state update API
- [ ] T2.3 `rewind_thread` 改为基于真实 message/checkpoint id，不再按 `keep_messages_count * 2` 猜测
- [ ] T2.4 删除 `writes.rowid > checkpoints.rowid` 这类跨表 rowid 比较
- [ ] T2.5 修复 `/reset` 子 thread 枚举：使用 `alist(config)` / `CheckpointTuple.config` / `adelete_thread`
- [ ] T2.6 前端首条消息也执行一致的 rewind/branch 流程，不跳过
- [ ] T2.7 补回归测试：编辑中间消息、编辑首条消息、回退后 resend、compact 后继续对话

### Phase 3: 抽取队列与作用域

- [ ] T3.1 扩展 `profile_extract_queue`：`status`、`attempts`、`last_error`、`leased_until`
- [ ] T3.2 worker 使用原子 claim/lease，shutdown drain 不重复消费已 lease 任务
- [ ] T3.3 区分 `completed_empty`、`completed_written`、`retryable_failed`、`dead_letter`
- [ ] T3.4 自动抽取结果增加 `scope`、`confidence`、`sensitivity`
- [ ] T3.5 分类规则：长期用户偏好默认 global，项目知识默认 workspace，低置信度不自动写
- [ ] T3.6 `coding_team` 主链接入自动抽取，或显式记录不抽取原因
- [ ] T3.7 补测试：LLM 失败重试、空结果成功、workspace 偏好升 global、项目事实写 workspace

### Phase 4: 注入一致性与 DeepAgents memory reload

- [ ] T4.1 Work supervisor prompt 验证 `profile_prompt` 注入
- [ ] T4.2 Coding expert prompt 验证 `profile_prompt` 注入
- [ ] T4.3 Team planner/aggregator/role subtask 统一消费 `profile_prompt`
- [ ] T4.4 修复 `_run_team_role_subtask` 接收但忽略 profile prompt 的问题
- [ ] T4.5 为 DeepAgents `MemoryMiddleware` 增加 workspace memory/rules version 或 mtime 感知 reload
- [ ] T4.6 补测试：线程内修改 `.agentx/memory/*.md` 后下一轮能看到新内容

### Phase 5: 安全、兼容与检索

- [ ] T5.1 所有 memory API 的 `workspace_path` 通过 `SessionSandbox` 授权校验
- [ ] T5.2 入库前增加 secret/credential detector
- [ ] T5.3 敏感记忆默认不注入 prompt，并在 API 返回中标记 `sensitivity`
- [ ] T5.4 修复旧 `.agentx/profile.json` 与新 markdown store 的更新/删除一致性，提供迁移或只读兼容策略
- [ ] T5.5 前端 memory API 全部补 `assertOk`
- [ ] T5.6 禁止无 workspace 时写入 project memory
- [ ] T5.7 `last_updated` 字段从 checkpoint UUID 改为真实时间，或前端不再格式化为时间
- [ ] T5.8 使用 TEI/Milvus 对 `keywords` / `scenarios` 做 Top-K 相关记忆检索

## 验证建议

- [ ] V1. `uv run pytest tests/python/unit -m "not integration" -q`
- [ ] V2. 新增 Dream 单测：import、dry-run、invalid target、delete preview、rollback
- [ ] V3. 新增 checkpoint 单测：真实 `AsyncSqliteSaver.aput`、compact、rewind、reset child cleanup
- [ ] V4. 新增 extract queue 单测：failure retry、dead letter、lease、shutdown drain
- [ ] V5. 新增 scope/security 单测：workspace authorization、secret filtering、scope classification
- [ ] V6. `pnpm typecheck`
- [ ] V7. 前端单测：delete/resend await、memory API assertOk、无 workspace 禁止 project write

## 规模判定

- 涉及模块：memory、workspace、api/chat、cli、router、team、frontend chat store、frontend memory API
- 风险等级：**L（大改）**
- 建议拆分提交：P0 止血、checkpoint 重构、抽取队列、作用域/安全、检索优化
