# 输入框底部按钮布局重构（提高信息密度）

> 日期：2026-07-05
> 主题：ChatComposer 底部按钮从「左 flex 行 + 右 absolute 块」裂开重组为单行 Toolbar
> 状态：已确认设计，待实现

## 1. 产品结论

**问题现状**

当前 `ChatComposer` 底部按钮散落两处：

```
┌─ ChatComposer (.chat-composer) ────────────────────┐
│  [textarea: pr-36 + pb-8]                           │
│                                                    │  ← 右侧 absolute block
│  / [@] [📁 workspace-chip]      [Model][盾][↗]      │  ← 左 flex 行（独立）
└────────────────────────────────────────────────────┘
```

带来三类问题：

1. **布局脆弱**：右侧 [Model][权限][发送] 走 `absolute bottom-1 right-1 z-30` 配合 textarea 的 `pr-36 / pb-8` 留位，跨分辨率 / 主题容易错位
2. **信息密度低**：外层 `py-2` + 内层 `pb-2` 重复堆叠；workspace chip `max-w-[220px]` 把整条底边撑开；textarea 底部 `pb-8` 留 32px 死区
3. **按钮分组无层次**：左是触发类 [/][@][workspace]，右是状态类 [Model][权限] + 动作类 [↗]，但视觉未做分组切割

**改后效果**

```
┌─ ChatComposer (.chat-composer) ────────────────────┐
│  [textarea: pb-2，去掉 pr-36 / pb-8]               │
│  ─────────────────────────────────────             │  ← border-t 分隔
│  / [@] [📁 chip]      [Model▼][盾▼][↗]             │  ← 一行 28px
└────────────────────────────────────────────────────┘
```

**核心改动**

- 移除 `absolute` 块与 textarea 的 `pr-36 / pb-8` 死区
- 引入单一 `<Toolbar>` 容器，三类按钮分组：`justify-between`
  - 左（触发类）：`Slash`、`AtSign`、workspace chip（`ml-auto` 推到右侧由 spacer 撑开）
  - 右（状态+动作）：`ModelToggle`、`PermissionToggle`、`btn-send`（或 `btn-send.is-stop`）
- Toolbar 高度 28px (`h-7`)，与 textarea 自适应最小高度形成统一节奏
- 容器整体下塌约 18–25px

## 2. 用户工作流

```
用户拖动窗口到 600px 窄屏
  → toolbar 仍单行；workspace chip 自动 truncate 到 160px 后省略
  → 模型 / 权限 trigger 自带 truncate（已有 max-w-[140px]）

用户输入到 textarea 撑大
  → toolbar 仍在 textarea 下方，无 absolute 重叠风险
  → textarea 可增长到任意行，toolbar 跟随底部自动布局

用户切换会话
  → useEffect 清理 input + picker + permission mode（无变化）
  → toolbar 重新计算时无闪烁（flex 流式）

用户点击发送 / 中止
  → 与现状一致；按钮位于 toolbar 末尾
```

## 3. 数据模型与状态

**无新增 store / 接口**

- `useChatStore` / `usePermissionStore` / `useModelStore` / `useCommandPickerStore` / `useSkillsStore` 全部不变
- toolbar 布局不引入额外 React state
- 拖拽 / 命令面板 / 权限 / 模型 / workspace IPC 全部零改动

## 4. UI 规范

### 4.1 容器结构（`ChatComposer.tsx`）

```tsx
<div className="chat-composer relative px-3 pb-1.5 pt-2 ...">
  {pickerOpen && <CommandPicker ... />}

  <div className="relative">
    <textarea
      ref={textareaRef}
      value={input}
      rows={2}
      className="input-borderless relative z-20 block w-full resize-none pb-2"
      style={{ height: `${textareaHeight}px` }}
    />

    {/* ▼ 替换原 absolute 块 + 独立 flex 行 ▼ */}
    <div className="mt-1.5 flex items-center justify-between gap-1 border-t border-default pt-1.5">
      {/* LEFT — 触发类 */}
      <div className="flex items-center gap-1">
        <button className="btn-icon" onClick={openCommandPickerManually} ...> <Slash /> </button>
        <button className="btn-icon" onClick={() => void handleAttachFile()} ...> <AtSign /> </button>
        {showWorkspaceChip && workspacePath ? (
          <span className="...max-w-[160px] text-[10.5px] ..."> {/* workspace chip 收敛 */} </span>
        ) : (
          <button className="btn-icon" onClick={() => void handleAttachWorkspace()} ...> <FolderPlus /> </button>
        )}
      </div>

      {/* SPACER */}
      <div className="flex-1" />

      {/* RIGHT — 状态 + 动作 */}
      <div className="flex items-center gap-1">
        <ModelToggle />
        <PermissionToggle workspacePath={workspacePath} homeWorkspacePath={homeWorkspacePath} />
        {isStreaming ? (
          <button className="btn-send is-stop" onClick={onAbort} ...> <Square /> </button>
        ) : (
          <button className="btn-send" onClick={handleSubmit} disabled={!canSend} ...> <Send /> </button>
        )}
      </div>
    </div>
  </div>

  {dragOver && <DropOverlay ... />}
</div>
```

### 4.2 关键 class 变更表

| 元素 | 当前 | 改后 |
|---|---|---|
| `.chat-composer` 内 padding | `px-3 pb-2 pt-1.5` | `px-3 pb-1.5 pt-2` |
| 外层 `border-t` 容器 | `py-2` | `py-1.5` |
| textarea class | `pr-36 pb-8` | `pb-2` |
| 新 Toolbar | （无 / 独立） | `border-t border-default pt-1.5 mt-1.5 flex items-center justify-between` |
| workspace chip | `max-w-[220px]` `text-[11px]` | `max-w-[160px]` `text-[10.5px]` |
| ModelToggle 按钮 | `h-[1.875rem]` (30px) | `h-7` (28px) （其内部包裹的 `btn-icon` 一致改为 `h-7`） |
| PermissionToggle 按钮 | `h-[1.875rem]` (30px) | `h-7` (28px) （同上） |
| `btn-send` 高度 | `h-[1.875rem]` (30px) | `h-7` (28px) （同步 globals.css） |

### 4.3 视觉规范

- Toolbar 上边框：`border-default`（与 .chat-composer 同色，无新增视觉重量）
- Toolbar 与 textarea 之间距离：`mt-1.5 + pt-1.5`（合计 12px，呼吸感稳定）
- 按钮水平间距：`gap-1`（4px，与现有 workspace chip 内部一致）
- 按钮两侧对齐：左组 `items-start` 自然贴左，右组贴右；中间 spacer 撑开
- 焦点环：`.chat-composer:focus-within` 已存在，整容器高亮，无需针对 toolbar 新增

### 4.4 响应式

- 容器最大宽 `max-w-3xl`（保留）
- 600px 以下：toolbar 仍单行；workspace chip `max-w-[160px] truncate`，超出显示省略号
- 模型名 `max-w-[140px] truncate`（已存在，沿用）

## 5. 行为与兼容

**交互零破坏**

- 键盘：`Enter` 发送 / `Shift+Enter` 换行 / `/` 触发命令 / `↑` 空内容编辑上一条 — 全部不变
- 拖拽：监听仍挂在 `.chat-composer` 容器，DragOverlay 全屏反馈不变
- 命令面板：`/hook` 仍由 store 管理，CommandPicker 仍条件渲染在 container 内
- workspace 切换 / 模型切换 / 权限切换 popover 行为不变
- SSE 流结束自动 focus textarea（已有 useEffect，不变）

**测试影响**

- `tests/renderer/chat-composer.test.tsx`：当前测试用例为「切会话清空输入 / 关闭 picker」，不受 toolbar 样式调整影响
- 新增 1 条 toolbar 存在性轻测：
  ```tsx
  it("底部 toolbar 包含发送按钮 + 模型 + 权限按钮", () => {
    const { getByRole } = render(<ChatComposer ... />);
    expect(getByRole("button", { name: /发送消息|中止生成/ })).toBeInTheDocument();
    expect(getByRole("button", { name: /模型/ })).toBeInTheDocument();
  });
  ```
- `tests/renderer/permission-toggle.test.tsx`：组件零修改，零影响

## 6. 文件改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/renderer/components/chat/ChatComposer.tsx` | 重构 | 移除 `absolute` 块，合并为单一 `<Toolbar>`；调整 padding；chip 收敛；测试用 `getByRole` 选取器变更 |
| `frontend/renderer/components/chat/ModelToggle.tsx` | 微调 | `btn-icon` 高度 `h-[1.875rem]` → `h-7` |
| `frontend/renderer/components/chat/PermissionToggle.tsx` | 微调 | `btn-icon` 高度同上 |
| `frontend/renderer/styles/globals.css` | 微调 | `.btn-send` 高度 `1.875rem` → `1.75rem` (`h-7`)；其他不变 |
| `tests/renderer/chat-composer.test.tsx` | 追加 | 新增 toolbar 存在性轻测 |

## 7. 风险与回退

- **风险 1**：toolbar 高度 28px 与现有触发器 30px 差 2px → 同步 ModelToggle / PermissionToggle / `btn-send` 改为 `h-7`
- **风险 2**：窄屏外层下 workspace chip 仍可能溢出 → 通过 `truncate + max-w-[160px]` 收敛，路径使用 tooltip 展示
- **风险 3**：dragOver overlay 与 toolbar 重叠时遮挡 → DragOverlay 使用 `pointer-events-none` + `z-10`，toolbar 内按钮 `z-` 提升至 `z-20`
- **回退路径**：单文件改动（仅 `ChatComposer.tsx` 与 `globals.css`），可一行 `git revert <commit>` 回退
