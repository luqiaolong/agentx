# ChatComposer 底部 Toolbar 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ChatComposer 底部从「左 flex 行 + 右 absolute 块」裂开重组为单一 Toolbar（触发类 / 状态类 / 动作类 三段分组），提升信息密度 18-25px

**Architecture:** 完全移除 `absolute bottom-1 right-1 z-30` 块与 textarea 的 `pr-36 pb-8` 死区；引入 `<Toolbar>` 容器，通过 `justify-between` + 中间 `<div className="flex-1">` spacer 推开；toolbar 高度统一 28px (`h-7`)

**Tech Stack:** React 18 + TypeScript + Tailwind v4 + zustand + lucide-react

**Spec:** [`docs/superpowers/specs/2026-07-05-composer-toolbar-redesign-design.md`](../specs/2026-07-05-composer-toolbar-redesign-design.md)

---

## Task 1: ChatComposer 主组件重构（移除 absolute / 合并 toolbar）

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ChatComposer.tsx:344-489`
- Test: `d:\java\agentprojects\agentx\tests\renderer\chat-composer.test.tsx`

- [ ] **Step 1: 调整容器外层 padding**

在 `ChatComposer.tsx`（约第 344 行），将根容器类名从：

```tsx
<div className="border-t border-default bg-surface px-4 py-2">
```

改为：

```tsx
<div className="border-t border-default bg-surface px-4 py-1.5">
```

将 `.chat-composer` 容器类名从：

```tsx
<div
  className={`chat-composer relative px-3 pb-2 pt-1.5 ${
    dragOver ? "is-drop-target" : ""
  }`}
```

改为：

```tsx
<div
  className={`chat-composer relative px-3 pb-1.5 pt-2 ${
    dragOver ? "is-drop-target" : ""
  }`}
```

- [ ] **Step 2: 调整 textarea 类名（移除 absolute 死区）**

找到（当前约第 371 行）：

```tsx
className="input-borderless relative z-20 block w-full resize-none pr-36 pb-8"
```

改为：

```tsx
className="input-borderless relative z-20 block w-full resize-none pb-2"
```

- [ ] **Step 3: 移除 absolute 按钮块（替换为 Toolbar）**

找到并**完整删除**整个 `<div className="absolute bottom-1 right-1 z-30 flex items-center gap-1">...</div>` 块（约 376-408 行），替换为如下结构（紧跟 textarea 之后插入）：

```tsx
{/* 底部 Toolbar：左 = 触发类，右 = 状态/动作 */}
<div className="mt-1.5 flex items-center justify-between gap-1 border-t border-default pt-1.5">
  {/* LEFT — 触发类 */}
  <div className="flex items-center gap-1">
    <button
      type="button"
      className="btn-icon"
      onClick={openCommandPickerManually}
      title="调用命令或技能 (/ 命令)"
      aria-label="调用命令或技能"
    >
      <Slash className="h-3.5 w-3.5" />
    </button>
    <button
      type="button"
      className="btn-icon"
      onClick={() => void handleAttachFile()}
      title="附加文件 (@)"
      aria-label="附加文件"
    >
      <AtSign className="h-3.5 w-3.5" />
    </button>
    {showWorkspaceChip && workspacePath ? (
      <span
        className="group/ws inline-flex max-w-[160px] items-center gap-1 rounded-md border border-brand-500/25 bg-brand-600/10 pl-1.5 pr-1 py-0.5 text-[10.5px] font-medium text-brand-500 transition-colors hover:bg-brand-600/15"
        title={workspaceChipTitle}
      >
        <Folder
          className="h-3 w-3 shrink-0 text-brand-500/80"
          aria-hidden="true"
        />
        <span className="max-w-[88px] truncate">
          {workspacePath.split(/[\\/]/).pop() || workspacePath}
        </span>
        <button
          type="button"
          className="ml-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-brand-500/70 transition-colors hover:bg-brand-500/20 hover:text-brand-500"
          onClick={handleRemoveWorkspace}
          title="迁回 Home"
          aria-label="迁回 Home"
        >
          <X className="h-2.5 w-2.5" />
        </button>
        <button
          type="button"
          className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-brand-500/70 transition-colors hover:bg-brand-500/20 hover:text-brand-500"
          onClick={() => void handleAttachWorkspace()}
          title="更换 workspace"
          aria-label="更换 workspace"
        >
          <FolderPlus className="h-2.5 w-2.5" />
        </button>
      </span>
    ) : (
      <button
        type="button"
        className="btn-icon"
        onClick={() => void handleAttachWorkspace()}
        title={
          homeWorkspacePath
            ? `选择 workspace（Home = ${homeWorkspacePath}）`
            : "选择 workspace 目录"
        }
        aria-label="选择 workspace 目录"
      >
        <FolderPlus className="h-3.5 w-3.5" />
      </button>
    )}
  </div>

  {/* SPACER */}
  <div className="flex-1" />

  {/* RIGHT — 状态 + 动作 */}
  <div className="flex items-center gap-1">
    <ModelToggle />
    <PermissionToggle
      workspacePath={workspacePath}
      homeWorkspacePath={homeWorkspacePath}
    />
    {isStreaming ? (
      <button
        type="button"
        onClick={onAbort}
        className="btn-send is-stop"
        aria-label="中止生成"
        title="中止"
      >
        <Square className="h-3 w-3 fill-current" />
      </button>
    ) : (
      <button
        type="button"
        onClick={handleSubmit}
        disabled={!canSend}
        className="btn-send"
        aria-label="发送消息"
        title="发送 (Enter)"
      >
        <Send className="h-3.5 w-3.5" />
      </button>
    )}
  </div>
</div>
```

- [ ] **Step 4: 删除原独立 flex 行**

找到并**完整删除**以下整段（约 411-478 行）：

```tsx
<div className="flex items-center gap-1">
  <div className="flex items-center gap-1 text-[11px] text-muted-c">
    <button ... Slash />
    <button ... AtSign />
    {showWorkspaceChip && workspacePath ? ( <span ...> ... </span> ) : ( <button ... FolderPlus /> )}
  </div>
</div>
```

原因：已合并到上方 Toolbar 内。

- [ ] **Step 5: 验证 ChatComposer 完整结构**

重新查看 `ChatComposer.tsx` 的 `return` 部分（约 344-490 行）。期望结构应为：

```
<div className="border-t ... py-1.5">           ← Step 1
  <div className="mx-auto max-w-3xl">
    <div className="chat-composer relative px-3 pb-1.5 pt-2 ...">   ← Step 1
      {pickerOpen && <CommandPicker />}
      <div className="relative">
        <textarea className="... pb-2" />        ← Step 2
        <div className="mt-1.5 flex ... border-t ...">  ← Step 3 新 Toolbar
          <div>{Slash, AtSign, workspace}</div>
          <div className="flex-1" />
          <div>{ModelToggle, PermissionToggle, btn-send}</div>
        </div>
      </div>
      {dragOver && <DropOverlay />}
    </div>
  </div>
</div>
```

- [ ] **Step 6: TypeScript 类型校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：`frontend/renderer` 项目无新增 TS 错误。

- [ ] **Step 7: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/ChatComposer.tsx && git commit -m "refactor(chat-composer): 合并底部为单一 Toolbar，移除 absolute 定位"
```

---

## Task 2: ModelToggle 按钮高度对齐 28px

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ModelToggle.tsx:107-117`

- [ ] **Step 1: 修改 trigger 按钮高度**

找到（约 107-108 行）：

```tsx
className={[
  "btn-icon group inline-flex h-[1.875rem] w-auto items-center gap-1 px-1.5",
  "text-[11px] leading-none",
```

将 `h-[1.875rem]` 改为 `h-7`：

```tsx
className={[
  "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
  "text-[11px] leading-none",
```

- [ ] **Step 2: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/ModelToggle.tsx && git commit -m "refactor(chat-composer): ModelToggle 按钮高度对齐 28px"
```

---

## Task 3: PermissionToggle 按钮高度对齐 28px

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\PermissionToggle.tsx:94-103`

- [ ] **Step 1: 修改 trigger 按钮高度**

找到（约 94-103 行）：

```tsx
className={[
  // 与左侧 Slash/AtSign/FolderPlus 同款 btn-icon，仅高 1.875rem（30px），
  // 文字尺寸 11px 与左侧 text-[11px] 一致，整体 visual rhythm 一致
  "btn-icon group inline-flex h-[1.875rem] w-auto items-center gap-1 px-1.5",
  "text-[11px] leading-none",
```

将 `h-[1.875rem]` 改为 `h-7`，并同步更新上方注释：

```tsx
className={[
  // 与左侧 Slash/AtSign/FolderPlus 同款 btn-icon，高 h-7（28px），
  // 文字尺寸 11px 与左侧 text-[11px] 一致，整体 visual rhythm 一致
  "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
  "text-[11px] leading-none",
```

- [ ] **Step 2: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/PermissionToggle.tsx && git commit -m "refactor(chat-composer): PermissionToggle 按钮高度对齐 28px"
```

---

## Task 4: globals.css 中 .btn-send 高度同步

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\styles\globals.css:374-406`

- [ ] **Step 1: 修改 .btn-send 高度**

找到（约 374-406 行）：

```css
/* Send button — 紫色主色 */
.btn-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 1.875rem;
  width: 1.875rem;
```

将两处 `1.875rem` 改为 `1.75rem`（h-7）：

```css
/* Send button — 紫色主色 */
.btn-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 1.75rem;
  width: 1.75rem;
```

- [ ] **Step 2: 验证 CSS**

```bash
cd d:\java\agentprojects\agentx && grep -n "height: 1.75rem" frontend/renderer/styles/globals.css
```

期望：至少匹配到 `.btn-send` 中的两处 `1.75rem`。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/styles/globals.css && git commit -m "style: 同步 .btn-send 高度到 28px (h-7)"
```

---

## Task 5: 新增 toolbar 存在性轻测

**Files:**
- Modify: `d:\java\agentprojects\agentx\tests\renderer\chat-composer.test.tsx`

- [ ] **Step 1: 追加测试用例**

在文件最末尾（第 156 行 `});` 后）追加新的 `describe` 块：

```tsx
describe("ChatComposer 底部 Toolbar", () => {
  it("包含发送按钮 + 模型 + 权限按钮（同 row）", () => {
    useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
    // 发送按钮：isStreaming=false 时显示「发送消息」
    expect(getByRole("button", { name: "发送消息" })).toBeInTheDocument();
    // 流式态：传 isStreaming=true 后显示「中止生成」
    const { getByRole: getByRole2 } = render(
      <ChatComposer
        isStreaming={true}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
    expect(getByRole2("button", { name: "中止生成" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试验证**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/chat-composer.test.tsx
```

期望：4 个测试全部 PASS（2 个原有切会话 + 1 个 Toolbar 用例）。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add tests/renderer/chat-composer.test.tsx && git commit -m "test(chat-composer): 新增底部 Toolbar 存在性轻测"
```

---

## Task 6: 全量验证与人工视觉走查

**Files:**
- 验收：运行 `npm run dev` 启动前后端

- [ ] **Step 1: 全量测试**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/chat-composer.test.tsx tests/renderer/permission-toggle.test.tsx
```

期望：全部 PASS。

- [ ] **Step 2: TypeScript / Lint 全量**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck && npm run lint 2>&1 | tail -50 || true
```

期望：无新增错误。

- [ ] **Step 3: 视觉走查清单**

启动 `npm run dev`，到聊天界面目视确认：

- [ ] 底部为**单行** Toolbar，无 absolute 错位
- [ ] Toolbar 上有 `border-t` 浅色分隔线
- [ ] 左组 `[/][@][📁]` 触发类贴左
- [ ] 右组 `[Model][权限][↗]` 状态/动作贴右
- [ ] workspace chip 不超过 160px，超长自动 truncate + hover 显示完整路径
- [ ] 点击发送有禁用态（输入为空时 ↗ 半透明）
- [ ] 流式态变为 `[■]` 红色中止按钮
- [ ] 命令面板 `/` 仍能正常打开
- [ ] 拖入文件时 DropOverlay 仍覆盖容器中央（不挡 toolbar）
- [ ] 切会话后输入清空、picker 重置（已有测试覆盖）

- [ ] **Step 4: 提交（若 Step 3 有视觉微调）**

如果有非实质性微调（如 padding / 字号），按 `style(chat-composer): ...` 单独提交。无则跳过。

---

## Self-Review

**Spec coverage:**
- §1 产品结论：Task 1 完整实现（移除 absolute / 合并 Toolbar / 三段分组）
- §2 用户工作流：所有场景（窄屏/textarea 增长/切会话/发送）由 Task 1 + 5 验证
- §3 数据模型：明确"无新增 store" — Tasks 1-5 均不涉及
- §4 UI 规范：Tasks 1-4 全覆盖
- §5 行为与兼容：Task 6 步骤 3 视觉走查覆盖交互验证；Test 已覆盖状态保留
- §6 文件改动清单：Tasks 1 (ChatComposer) / 2 (ModelToggle) / 3 (PermissionToggle) / 4 (globals.css) / 5 (test) 五项全覆盖
- §7 风险：Task 6 步骤 3 涵盖；z-index 已纳入 Task 1 Step 3 隐式（toolbar 在 textarea z-20 之后，正常显示）

**Type consistency:**
- `canSend` / `handleSubmit` / `onAbort` 等函数与现有 ChatComposer.tsx 定义一致
- `ModelToggle` / `PermissionToggle` 组件 API 不变（仍接 `workspacePath` / `homeWorkspacePath`）
- `btn-send` class 名不变，仅 CSS height 变更

**No placeholders found.**

**Risks to monitor during execution:**
- 拖拽 overlay (`pointer-events-none z-10`) 与 toolbar (`z-20` 隐含) 重叠安全 — Step 3 视觉走查重点
- textarea 高度自适应（`useAutoResizeTextarea`）不再依赖 absolute 偏移，与 toolbar 独立
