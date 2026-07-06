## ADDED Requirements

### Requirement: GitPanel 文件操作按钮可用

[GitPanel.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/workspace/GitPanel.tsx) 中 StatusRow 的 hover 操作按钮 SHALL 正常渲染并响应点击。

#### Scenario: hover 显示操作按钮

- **WHEN** 用户 hover 某个 git status 行
- **THEN** 该行右侧显示 stage / unstage / discard 三个按钮
- **AND** 按钮点击分别调用 `onStage / onUnstage / onDiscard` props

#### Scenario: 已 staged 的行只显示 unstage + discard

- **GIVEN** 某文件状态为 staged
- **WHEN** hover 该行
- **THEN** 只显示 unstage + discard 按钮

---

### Requirement: SubagentsSettings allToolsOff 校验生效

[SubagentsSettings.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/SubagentsSettings.tsx) 中 `allToolsOff` 警告逻辑 SHALL 正常工作。

#### Scenario: 所有工具关闭时显示警告

- **GIVEN** 某个子代理的所有工具都被关闭
- **WHEN** 渲染该卡片
- **THEN** 显示"未启用任何工具"警告
- **AND** 不再使用 `{} as ToolsConfig` 空对象断言

---

### Requirement: WorkspacePanel 文件按钮可点击

[WorkspacePanel.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/workspace/WorkspacePanel.tsx) ContextTabPanel 中的文件按钮 SHALL 响应点击。

#### Scenario: 点击文件按钮

- **WHEN** 用户点击 ContextTabPanel 中的文件按钮
- **THEN** 调用 `onFileClick(file)` 回调
- **AND** 父组件可决定打开文件 / 预览 / 复制路径等行为

---

### Requirement: CodeBlock 高亮与显示内容同步

[CodeBlock.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/CodeBlock.tsx) 的语法高亮 SHALL 在展开/折叠切换时重新生成。

#### Scenario: 展开后高亮更新

- **GIVEN** CodeBlock 处于折叠状态显示截断代码
- **WHEN** 用户点击展开
- **THEN** 高亮 HTML 基于完整代码重新生成
- **AND** 不再显示折叠时的高亮

---

### Requirement: StatusIndicator 定时健康检查

[StatusIndicator.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/StatusIndicator.tsx) SHALL 定时轮询后端健康状态。

#### Scenario: 定时轮询

- **WHEN** 组件挂载
- **THEN** 立即执行一次健康检查
- **AND** 之后每 60 秒执行一次（`/api/health` 可能 5s+ 耗时，间隔不宜过短）

#### Scenario: 后端故障恢复后状态更新

- **GIVEN** 后端曾故障，StatusIndicator 显示异常
- **WHEN** 后端恢复
- **THEN** 60 秒内 StatusIndicator 自动恢复为健康状态

#### Scenario: 组件卸载清理

- **WHEN** 组件卸载
- **THEN** clearInterval，不残留定时器

---

### Requirement: useChatStream 会话切换安全

[useChatStream.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/useChatStream.ts) SHALL 在会话切换后正确处理 done 事件。

#### Scenario: 切换会话后 done 不误标

- **GIVEN** 用户在会话 A 发送消息，流式响应未完成
- **WHEN** 用户切换到会话 B
- **THEN** 会话 A 的 done 事件不会标记会话 B 为已完成
- **AND** done/error 事件用 `useChatStore.getState().currentId` 实时读取当前会话

---

### Requirement: 公共工具层 format.ts

系统 SHALL 提供 `frontend/renderer/lib/format.ts` 统一时间/大小格式化函数。

#### Scenario: formatTime 支持三种模式

- **WHEN** 调用 `formatTime(iso, "absolute")` 返回 `YYYY-MM-DD HH:mm`
- **AND** 调用 `formatTime(iso, "relative")` 返回 "3 分钟前"
- **AND** 调用 `formatTime(iso, "hhmm")` 返回 `HH:mm`

#### Scenario: formatSize 统一含 GB 分支

- **WHEN** 字节数 >= 1GB
- **THEN** 返回 `X.XX GB`
- **AND** 不再出现各文件实现不一致

---

### Requirement: 公共工具层 validators.ts

系统 SHALL 提供 `frontend/renderer/lib/validators.ts` 统一 key/name 校验。

#### Scenario: KEY_RE 正则单一来源

- **WHEN** 任何组件需要校验 key 格式
- **THEN** 从 `lib/validators.ts` 导入 `KEY_RE`
- **AND** 不再有 6 处重复定义

---

### Requirement: UI 组件 ErrorBanner

系统 SHALL 提供 `components/ui/ErrorBanner.tsx` 统一错误提示 UI。

#### Scenario: 替换 10+ 处内联错误 UI

- **WHEN** 组件需要展示错误信息
- **THEN** 使用 `<ErrorBanner message={errMsg} />`
- **AND** 不再内联 `border-rose-200 bg-rose-50 + AlertCircle` JSX

---

### Requirement: UI 组件 ConfirmButton

系统 SHALL 提供 `components/ui/ConfirmButton.tsx` 统一确认删除按钮。

#### Scenario: 替换 9 处确认删除按钮

- **WHEN** 组件需要确认删除
- **THEN** 使用 `<ConfirmButton onConfirm={...} />`
- **AND** 不再各自实现 confirmDelete state + 按钮组

---

### Requirement: usePopover hook

系统 SHALL 提供 `components/ui/hooks/usePopover.ts` 统一 popover 行为。

#### Scenario: 4 个 Toggle 复用

- **WHEN** ModeToggle / ModelToggle / PermissionToggle / ContextUsage 渲染
- **THEN** 共用 usePopover 管理 open / ESC / clickOutside
- **AND** 每个组件减少 ~20 行重复 useEffect

---

### Requirement: useModalDialog hook

系统 SHALL 提供 `components/ui/hooks/useModalDialog.ts` 统一 modal 行为。

#### Scenario: 3 个 Modal 复用

- **WHEN** SettingsModal / LogsModal / SubagentEditModal 渲染
- **THEN** 共用 useModalDialog 管理 triggerRef / ESC / body overflow / focus trap / focus restore
- **AND** 消除 ~100 行重复 useEffect

#### Scenario: SubagentEditModal 补齐 a11y

- **WHEN** SubagentEditModal 打开
- **THEN** ESC 可关闭
- **AND** 焦点被 trap 在 modal 内
- **AND** 关闭后焦点恢复到 trigger

---

### Requirement: HTTP 边界统一 request.ts

系统 SHALL 提供 `frontend/renderer/lib/api/request.ts` 统一 HTTP 请求。

#### Scenario: 15 个 API 方法复用

- **WHEN** http.ts / chat.ts / settings.ts / app.ts / git.ts / logs.ts 发起请求
- **THEN** 调用 `apiGet / apiPost / apiPut / apiDelete`
- **AND** 不再各自 `fetch + headers + body`

#### Scenario: HTTP 错误抛出 ApiError

- **WHEN** 后端返回 4xx / 5xx
- **THEN** 抛出 `ApiError(status, body)`
- **AND** 调用方可 `catch (e) { if (e instanceof ApiError) ... }`

---

### Requirement: 错误处理可观测

所有 `catch {}` 静默吞错 SHALL 改为 `logger.warn + setErrMsg`。

#### Scenario: 12 处吞错修复

- **WHEN** ApprovalSettings / MilvusCredentialsForm / McpSettings / ModelProviderSettings / SubagentsSettings / SystemPromptSettings / ToolsSettings / SandboxSettings / FileTree / CodeBlock 发生异常
- **THEN** 调用 `logger.warn(context, e)`
- **AND** UI 显示 `humanizeError(e)` 友好信息

---

### Requirement: 超大文件拆分

系统 SHALL 拆分 5 个超大文件到 < 400 行。

#### Scenario: ModelProviderSettings 拆分

- **WHEN** 重构完成
- **THEN** 原 1208 行文件拆为 `model-provider/index.tsx + ModelRow.tsx + ModelEditor.tsx + TokenField.tsx`
- **AND** 每个文件 < 400 行

#### Scenario: SubagentsSettings 拆分

- **WHEN** 重构完成
- **THEN** 原 999 行文件拆为 `subagents/index.tsx + SubagentCard.tsx + constants.ts`
- **AND** BuiltinCard 与 CustomCard 合并为 `SubagentCard variant="builtin"|"custom"`

#### Scenario: McpSettings 拆分

- **WHEN** 重构完成
- **THEN** 原 767 行文件拆为 `mcp/index.tsx + ServerRow.tsx + ServerEditor.tsx + utils.ts`

#### Scenario: WorkspacePanel 拆分

- **WHEN** 重构完成
- **THEN** 原 474 行文件拆为 `WorkspacePanel.tsx + CompactTaskList.tsx + ContextTabPanel.tsx + extractFiles.ts`

---

### Requirement: 列表项 React.memo

8 个列表项组件 SHALL 用 `React.memo` 包裹。

#### Scenario: 父组件 state 变化不触发全量重渲染

- **WHEN** 父组件的非 props state 变化
- **THEN** 列表项不重渲染
- **AND** 只有 props 变化的项重渲染

---

### Requirement: memory 子组件合并

5 个 memory 子组件 SHALL 合并为 1 个共享组件 + 5 个薄包装。

#### Scenario: useCrudList 共享 CRUD 逻辑

- **WHEN** PreferenceManager / ProfileManager / ProjectMemoryManager / SessionManager / SkillsManager 渲染
- **THEN** 共用 `useCrudList(category)` hook
- **AND** 共用 `<MemoryList>` 组件
- **AND** 每个薄包装 < 30 行

---

### Requirement: 表单 RHF + zod 统一

8 个表单组件 SHALL 用 `react-hook-form + zodResolver`。

#### Scenario: schema 集中管理

- **WHEN** 表单需要校验
- **THEN** schema 从 `lib/schemas/` 导入
- **AND** 不再手写 useState + 手写校验

#### Scenario: MilvusCredentialsForm 修复不彻底用法

- **WHEN** MilvusCredentialsForm 渲染
- **THEN** 所有 8 个字段（host/port/db/collection/embeddingUrl/authEnabled/user/password）走 RHF
- **AND** 不再混合 useState

---

### Requirement: chat store 拆分

`frontend/renderer/stores/chat.ts` (936 行) SHALL 拆分为 4 个文件。

#### Scenario: 拆分后对外 API 不变

- **WHEN** 重构完成
- **THEN** `import { useChatStore } from "@/stores/chat"` 仍可用
- **AND** 内部拆为 `chat/index.ts + migrations.ts + quotaStorage.ts + messageOps.ts`
- **AND** 每个文件 < 400 行

---

### Requirement: 配置保存流程统一

7 个 settings 组件 SHALL 用 `useConfigSave` hook。

#### Scenario: 热更新流程复用

- **WHEN** ApprovalSettings / McpSettings / ModelProviderSettings / SubagentEditModal / SubagentsSettings / SystemPromptSettings / ToolsSettings 保存配置
- **THEN** 调用 `useConfigSave(saveFn)` 返回的 `save(v)`
- **AND** 自动执行 `saveFn → reloadBackendConfig → setSaved + setTimeout`
- **AND** 不再各自重复 5 行模板

---

### Requirement: 向后不兼容

本次重构 SHALL 不保留任何兼容代码。

#### Scenario: 旧文件直接删除

- **WHEN** 拆分超大文件
- **THEN** 原文件直接删除
- **AND** 不保留 re-export shim
- **AND** 不写 `@Deprecated`

#### Scenario: 旧实现直接替换

- **WHEN** 抽取公共工具
- **THEN** 各文件本地实现直接删除
- **AND** 改为从公共模块导入
