# 任务追踪 — execution-trace-optimization

## 预期修改文件

### 修改
- [ ] `frontend/renderer/stores/chat/index.ts` — debounce 持久化 + messageIndex + 移除 content
- [ ] `frontend/renderer/stores/chat/messageOps.ts` — 移除 deriveContent
- [ ] `frontend/renderer/stores/chat/migrations.ts` — v5→v6 迁移
- [ ] `frontend/renderer/hooks/useChatStream.ts` — team_plan 单次 upsert + reasoning/tool_call 写入时间戳
- [ ] `frontend/renderer/components/chat/AssistantUIThread.tsx` — 拆分 + 取消 text 重排 + 虚拟化
- [ ] `frontend/renderer/components/chat/parts/ReasoningBlock.tsx` — startedAt + 流式预览
- [ ] `frontend/renderer/components/chat/parts/ToolCallCard.tsx` — 复制 + 展开 + source + 耗时
- [ ] `frontend/renderer/components/chat/parts/TeamNodeCard.tsx` — key 稳定
- [ ] `frontend/renderer/components/chat/ChatView.tsx` — 导航条进度 + useAutoScroll 参数
- [ ] `frontend/renderer/hooks/useAutoScroll.ts` — 流式 auto
- [ ] `tests/renderer/parts-rendering.test.tsx` — 更新覆盖
- [ ] `frontend/package.json` — 新增 @tanstack/react-virtual

### 新建
- [ ] `frontend/renderer/stores/chat/messageIndex.ts` — 反向索引
- [ ] `frontend/renderer/components/chat/UserMessageBubble.tsx` — user 编辑气泡
- [ ] `frontend/renderer/components/chat/ToolSystemMessage.tsx` — tool 系统提示
- [ ] `frontend/renderer/components/chat/AssistantMessageParts.tsx` — assistant parts 渲染
- [ ] `frontend/renderer/components/chat/parts/ToolCallGroup.tsx` — 批量折叠
- [ ] `tests/renderer/store-perf.test.ts` — store 性能测试
- [ ] `tests/renderer/message-index.test.ts` — 反向索引测试

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | store 持久化 debounce + messageIndex 反向索引 | messageIndex.ts, index.ts, messageOps.ts, message-index.test.ts | appendPartText 用 O(1) 查找；流式期间不写 localStorage | ⬜ |
| T2 | team_plan 单次 upsert | index.ts, useChatStream.ts, store-perf.test.ts | team_plan N 个 agent 只触发 1 次 set | ⬜ |
| T3 | buildRenderItems 缓存 + 卡片 memo | ToolCallCard.tsx, ReasoningBlock.tsx, TeamNodeCard.tsx, DelegationCard.tsx | token 追加到 text part 不触发 tool-call 卡片重渲 | ⬜ |
| T4 | 消息列表虚拟化 | AssistantUIThread.tsx, package.json | 100+ 消息 60fps；overscan=5 | ⬜ |
| T5 | text parts 按真实顺序渲染 | AssistantUIThread.tsx, parts-rendering.test.tsx | text→tool-call→text 顺序保留 | ⬜ |
| T6 | ReasoningBlock 增强 | ReasoningBlock.tsx, useChatStream.ts, index.ts, parts-rendering.test.tsx | 流式预览 + startedAt 计算耗时 | ⬜ |
| T7 | ToolCallCard 增强 | ToolCallCard.tsx, useChatStream.ts, index.ts, parts-rendering.test.tsx | 复制 + 展开 + source + 耗时 | ⬜ |
| T8 | ToolCallGroup 批量折叠 | ToolCallGroup.tsx, AssistantUIThread.tsx, parts-rendering.test.tsx | 连续 ≥3 同类自动折叠 | ⬜ |
| T9 | ChatView 导航条 + useAutoScroll | ChatView.tsx, useAutoScroll.ts | 进度条 rAF 更新；流式 auto | ⬜ |
| T10 | MessageParts 拆分 | UserMessageBubble.tsx, ToolSystemMessage.tsx, AssistantMessageParts.tsx, AssistantUIThread.tsx | 三组件独立职责 | ⬜ |
| T11 | TeamNodeCard key + sessionStorage 清理 | TeamNodeCard.tsx, ReasoningBlock.tsx | key 用 agent 名；卸载清理 sessionStorage | ⬜ |
| T12 | 移除 content 兼容字段 | index.ts, migrations.ts, messageOps.ts, parts-rendering.test.tsx | v5→v6 迁移；无 .content 引用 | ⬜ |

## 规模判定

- 涉及文件数: 19（12 修改 + 7 新建）
- 涉及模块数: 1（renderer，跨 store/hooks/components/parts/tests 多层）
- 规模: **L（大改）**
- 流程: 全流程（Worktree + TDD + 双轨 Review + 部署 + 归档）

## 执行顺序

按依赖关系分批：

**批次 1（基础设施，无依赖）**: T1（messageIndex） → T2（team_plan 单次 upsert） → T12（移除 content）
**批次 2（数据模型扩展，依赖 T1）**: T6.1-T6.3（reasoning startedAt） → T7.1-T7.2（tool-call startedAt/completedAt）
**批次 3（卡片增强，依赖批次 2）**: T3（memo） → T6.4-T6.6（ReasoningBlock） → T7.3-T7.7（ToolCallCard） → T8（ToolCallGroup） → T11（key + sessionStorage）
**批次 4（渲染层重构，依赖批次 3）**: T5（text 顺序） → T10（拆分） → T4（虚拟化）
**批次 5（容器层，依赖批次 4）**: T9（导航条 + useAutoScroll）
