# 输入框权限开关设计（两档简化）

> 日期：2026-07-05
> 主题：ChatComposer 底部两档权限开关 + 运行时扩展授权
> 状态：已确认设计，待实现

## 1. 产品结论

在 ChatComposer 底部、发送按钮左侧新增一个紧凑的两档 segmented control：**标准** / **完全信任**。

- **标准**（默认）：等价于当前 workspace 行为。写操作 / shell_exec 仍走 ApprovalDialog 审批；只读 fs 工具访问未授权目录时，自动弹"扩展授权"弹窗请求用户决策（本次 / 会话内 / 拒绝）。
- **完全信任**：当前会话内读写 + shell 命令全部自动批准，不再弹任何审批弹窗；fs 工具访问未授权目录时自动放行（系统关键目录仍拒绝）。

模式为**会话级**：切换会话时重置为"标准"，不持久化到 localStorage / electron-store。

## 2. 用户工作流

```
用户打开新会话
  → 默认"标准"模式
  → AI 调用 write_file（危险操作）
    → ApprovalDialog 弹窗（双按钮：拒绝 / 批准）
  → AI 调用 read_file 访问未授权目录
    → ApprovalDialog 弹窗（三按钮：拒绝 / 本次允许 / 会话内允许）
  → 用户觉得频繁授权烦
    → 点击"完全信任"开关
    → 后续所有操作自动放行，不再弹窗
  → 用户切换到另一个会话
    → 自动重置回"标准"模式
```

## 3. 数据模型与状态

### 3.1 前端 store：`frontend/renderer/stores/permission.ts`

```ts
type PermissionMode = "standard" | "full_trust";

interface PermissionState {
  mode: PermissionMode;
  setMode: (m: PermissionMode) => void;
  reset: () => void;  // 切换会话时调用，重置为 standard
}
```

- **不接入 persist 中间件**：会话级状态，不跨会话/不跨重启保留。
- 通过 `reset()` 在 ChatComposer 的 `useEffect(..., [currentId])` 中调用，复用现有的会话切换钩子。

### 3.2 ApprovalRequest 类型扩展（`frontend/shared/api-types.ts`）

```ts
type ApprovalKind = "dangerous_tool" | "directory_extension";

interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
  kind?: ApprovalKind;              // 缺省 = dangerous_tool（向后兼容）
  requestedPath?: string;           // directory_extension 时填
  writable?: boolean;               // directory_extension 时填
}
```

### 3.3 后端审批决策扩展

`backend/app/main.py` 的 `_pending_approvals` 升级为存储决策 dict：

```python
# 旧：_pending_approvals: dict[str, bool]
# 新：
@dataclass
class ApprovalDecision:
    approved: bool
    decision: str = "approve"   # "approve" | "once" | "session" | "deny"
    path: str | None = None     # directory_extension 时填
    writable: bool = False

_pending_approvals: dict[str, ApprovalDecision] = {}
```

`ApproveRequest` Pydantic 模型增加可选字段：

```python
class ApproveRequest(BaseModel):
    thread_id: str
    approval: bool
    decision: str = "approve"   # "approve" | "once" | "session" | "deny"
    path: str | None = None
    writable: bool = False
```

## 4. UI 组件

### 4.1 新增 `PermissionToggle.tsx`

位置：`frontend/renderer/components/chat/PermissionToggle.tsx`

紧贴发送按钮左侧的 segmented control：

```tsx
<div className="inline-flex items-center rounded-md border border-default bg-subtle p-0.5 text-[11px]">
  <button
    className={mode === "standard" ? activeCls : inactiveCls}
    onClick={() => setMode("standard")}
    title="标准模式：危险操作与目录访问需审批"
  >
    <Lock className="h-3 w-3" /> 标准
  </button>
  <button
    className={mode === "full_trust" ? activeAmberCls : inactiveCls}
    onClick={() => setMode("full_trust")}
    title="完全信任：当前会话内所有操作自动放行"
  >
    <Zap className="h-3 w-3" /> 完全信任
  </button>
</div>
```

- "标准"激活：subtle 灰底 + Lock 图标。
- "完全信任"激活：amber 警示色 + Zap 图标 + 闪动小圆点。
- 切换时立即调用 `usePermissionStore.setMode`，并通过 chat request 传递给后端。

### 4.2 ChatComposer 集成

在 `ChatComposer.tsx` 底部右侧按钮容器（`div.flex.items-center.gap-1.5`）中，发送按钮之前插入 `<PermissionToggle />`。

在 `useEffect(..., [currentId])` 中增加 `reset()` 调用：

```ts
useEffect(() => {
  setInput("");
  resetPicker();
  usePermissionStore.getState().reset();  // 新增
  textareaRef.current?.focus();
}, [currentId]);
```

### 4.3 ApprovalDialog 扩展

检测 `approvalRequest.kind === "directory_extension"` 时：

- 标题改为「授权新目录」
- icon 改用 `FolderPlus`（amber 色）
- 按钮改为三选一：「拒绝」「本次允许」「会话内允许」
- 既有 `dangerous_tool` 形态保持不变（向后兼容，kind 缺省）

submit 时根据按钮调用不同的 decision：
- 拒绝 → `decision: "deny"`
- 本次允许 → `decision: "once"`
- 会话内允许 → `decision: "session"`

## 5. 通信链路

### 5.1 chat.send 扩展

`ElectronAPI.chat.send` 的 opts 增加 `permissionMode`：

```ts
send: (msg, opts?: { threadId?: string; permissionMode?: PermissionMode }) => Promise<void>;
```

preload 的 `streamChat` 将 `permissionMode` 写入 `/api/chat` body：

```ts
body: JSON.stringify({
  message: msg.content,
  thread_id: opts?.threadId ?? "",
  permission_mode: opts?.permissionMode ?? "standard",
}),
```

### 5.2 approve.submit 扩展

`ElectronAPI.approve.submit` 改为支持 decision：

```ts
submit: (
  threadId: string,
  approval: boolean,
  decision?: "approve" | "once" | "session" | "deny",
  path?: string,
  writable?: boolean,
) => Promise<void>;
```

preload 调 `/api/chat/approve` body 增加 `decision` / `path` / `writable`。

### 5.3 后端 ChatRequest 扩展

```python
class ChatRequest(BaseModel):
    message: str
    thread_id: str
    permission_mode: str = "standard"  # "standard" | "full_trust"
```

### 5.4 SessionSandbox 扩展

`backend/app/utils/security.py` 的 `SessionSandbox` 增加 full_trust 标志：

```python
class SessionSandbox:
    def __init__(self) -> None:
        self.authorized_dirs: dict[str, set[tuple[Path, bool]]] = {}
        self.full_trust_threads: set[str] = set()  # 新增
        self._temp_authorized: dict[str, set[tuple[Path, bool]]] = {}  # 新增：once 决策临时授权

    def set_full_trust(self, thread_id: str, enabled: bool) -> None:
        if enabled:
            self.full_trust_threads.add(thread_id)
        else:
            self.full_trust_threads.discard(thread_id)

    def is_full_trust(self, thread_id: str) -> bool:
        return thread_id in self.full_trust_threads

    def clear_temp(self, thread_id: str) -> None:
        """清空 once 决策的临时授权（每次工具调用完成后调用）。"""
        self._temp_authorized.pop(thread_id, None)
```

> **越界定义**：路径不在 `_DEFAULT_WHITELIST`、不在 `authorized_dirs[thread_id]`、不在 `_temp_authorized[thread_id]` 中，且非系统关键目录。

> **清理时机**：`run_deep_path` 在每次工具调用恢复执行后（`_stream_agent_events` yield 完成后），调用 `sandbox.clear_temp(thread_id)` 清理 once 临时授权，避免累积。

`check_read` / `check_write` 在 full_trust 模式下跳过授权检查（仍拒绝系统关键目录）：

```python
def check_read(self, thread_id: str, path: str | Path) -> None:
    resolved = self._normalize(path)
    # 系统关键目录永远拒绝
    if self._is_critical(resolved):
        raise PathNotAuthorized(f"路径 {path} 是系统关键目录，不可访问")
    # full_trust 模式跳过授权检查
    if self.is_full_trust(thread_id):
        return
    # ... 现有白名单 + 授权目录检查
```

新增 `authorize_temp` 方法（用于 once 决策，不写入 checkpoint）：

```python
def authorize_temp(self, thread_id: str, path: str | Path, writable: bool = False) -> Path:
    """临时授权（不持久化到 checkpoint）。用于 directory_extension 的 once 决策。"""
    resolved = self._normalize(path)
    if self._is_critical(resolved):
        raise ValueError(f"路径 {path} 是系统关键目录，不可授权")
    # 存到单独的 temp 集合，不写入 authorized_dirs
    self._temp_authorized.setdefault(thread_id, set()).add((resolved, writable))
    return resolved
```

并在 `check_read` / `check_write` 中额外检查 `_temp_authorized`。

## 6. 后端流程改动

### 6.1 run_router 传递 permission_mode

`backend/app/router/graph.py` 的 `run_router` 接收 `permission_mode` 参数，传递给 `_run_deep_path`。

### 6.2 run_deep_path 改动

`backend/app/paths/deep_path.py` 的 `run_deep_path` 接收 `permission_mode`：

1. **full_trust 模式**：
   - `sandbox.set_full_trust(thread_id, True)` 在进入 agent 循环前设置
   - interrupt 循环中，对所有 pending_calls（包括危险工具）直接放行，不 yield approval_request
   - 流结束后 `sandbox.set_full_trust(thread_id, False)` 清理（防御性）

2. **standard 模式**：
   - 危险工具（write_file/edit_file/shell_exec）：保持现有审批流（yield approval_request, kind=dangerous_tool）
   - 只读 fs 工具（read_file/list_dir/glob/grep）：在 interrupt 处预检查参数中的路径是否已授权
     - 已授权：放行
     - 未授权：yield approval_request（kind=directory_extension, requestedPath=路径, writable=False），等待决策
       - decision="once"：`sandbox.authorize_temp(tid, path, writable=False)`，放行
       - decision="session"：`sandbox.authorize(tid, path, writable=False)`，放行
       - decision="deny"：工具调用标记为失败（"用户拒绝访问该目录"），DeepAgent 继续规划

### 6.3 路径提取辅助函数

在 `deep_path.py` 新增 `_extract_paths_from_tool_call(tool_call)` 函数，从工具参数中提取路径：

```python
def _extract_paths_from_tool_call(tool_call: dict) -> list[str]:
    """从工具调用参数中提取路径字符串。
    
    支持的工具：
    - read_file / write_file / edit_file: args["path"]
    - list_dir: args["path"]
    - glob: args["pattern"] → 取 _glob_base
    - grep: args["path"]
    """
    name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        return []
    if name in ("read_file", "write_file", "edit_file", "list_dir", "grep"):
        p = args.get("path")
        return [str(p)] if p else []
    if name == "glob":
        pattern = args.get("pattern", "")
        return [_glob_base(str(pattern))] if pattern else []
    return []
```

### 6.4 _make_approval_event 扩展

新增 `kind` 参数，支持生成 directory_extension 事件：

```python
def _make_approval_event(
    tool_call: dict, thread_id: str, kind: str = "dangerous_tool",
    requested_path: str | None = None, writable: bool = False,
) -> dict[str, str]:
    # ... 既有逻辑
    data = {
        "thread_id": thread_id,
        "tool_name": name,
        "args": redacted_args,
        "preview": preview,
        "kind": kind,
    }
    if kind == "directory_extension":
        data["requestedPath"] = requested_path or ""
        data["writable"] = writable
        data["preview"] = f"AI 想访问目录: {requested_path}"
    # ...
```

### 6.5 chat_approve 端点扩展

```python
@app.post("/api/chat/approve")
async def chat_approve(req: ApproveRequest) -> dict[str, Any]:
    decision = ApprovalDecision(
        approved=req.approval,
        decision=req.decision,
        path=req.path,
        writable=req.writable,
    )
    _pending_approvals[req.thread_id] = decision
    # ... trace 逻辑
```

### 6.6 _await_approval 改动

返回 `ApprovalDecision` 而非 `bool`：

```python
async def _await_approval(thread_id: str, ...) -> ApprovalDecision | None:
    # ... 轮询 _pending_approvals
    # 返回 ApprovalDecision 或 None
```

## 7. 错误处理与边界

| 场景 | 处理 |
|------|------|
| 用户在 full_trust 模式下再点一次 | 切回 standard，`sandbox.set_full_trust(tid, False)` |
| directory_extension 弹窗超时 | 等同 deny，工具返回"用户拒绝访问该目录" |
| 后端 reloadBackendConfig 后 | permission_mode 通过 chat request body 传递，不依赖 env，无需 reload |
| Python 重启 | sandbox 状态丢失，full_trust_threads 清空；前端 store 保留 mode，下条消息重新传递 |
| 系统关键目录（C:/Windows 等） | full_trust 模式仍拒绝，由 `SessionSandbox._is_critical` 兜底 |
| subagent 路径 B 越界 | 不触发扩展授权（subagent 无 interrupt 流），工具返回错误字符串（保持现有行为） |
| full_trust 模式 + MCP untrusted 工具 | 仍跳过审批（full_trust 全量放行） |

## 8. 测试矩阵

### 8.1 前端单元测试（vitest）

- `PermissionToggle` 组件渲染、点击切换 mode
- `usePermissionStore` 的 setMode / reset 行为
- `ApprovalDialog` 在 kind=directory_extension 时渲染三按钮
- `ApprovalDialog` 在 kind=dangerous_tool（缺省）时保持双按钮
- ChatComposer 在 currentId 变化时调用 reset

### 8.2 后端单元测试（pytest）

- `SessionSandbox.set_full_trust` / `is_full_trust`
- `SessionSandbox.check_read` 在 full_trust 模式下放行未授权目录
- `SessionSandbox.check_read` 在 full_trust 模式下仍拒绝系统关键目录
- `SessionSandbox.authorize_temp` 写入临时授权且不污染 authorized_dirs
- `_extract_paths_from_tool_call` 对各工具参数的路径提取

### 8.3 集成测试

- `test_approval_flow.py` 增加用例：
  - `test_full_trust_skips_approval`：full_trust 模式下 write_file 不触发 approval_request
  - `test_directory_extension_flow`：standard 模式下 read_file 越界触发 directory_extension，submit once/session/deny 后行为正确

## 9. 实现顺序

1. 后端：SessionSandbox 扩展 + ApprovalDecision + ApproveRequest 扩展
2. 后端：deep_path.py 改动（full_trust 跳过审批 + directory_extension 流程）
3. 后端：main.py chat_approve 端点 + ChatRequest 扩展
4. 后端：run_router 传递 permission_mode
5. 前端：api-types.ts 类型扩展
6. 前端：preload chat.send / approve.submit 扩展
7. 前端：stores/permission.ts
8. 前端：PermissionToggle.tsx
9. 前端：ChatComposer 集成 + reset
10. 前端：ApprovalDialog 三按钮扩展
11. 测试：前端 vitest + 后端 pytest
12. 验证：手动启动应用验证完整流程

## 10. 不做项

- 不做"全部"以外的其他档位（如 Home / Workspace / Ask）
- 不做跨会话持久化（会话级，切换会话重置）
- 不做 subagent 路径 B 的扩展授权（保持现有错误字符串行为）
- 不做 permission_mode 的 env 注入（通过 chat request body 传递）
- 不做 full_trust 模式的视觉警告弹窗（仅在 toggle 上用 amber 色警示）
