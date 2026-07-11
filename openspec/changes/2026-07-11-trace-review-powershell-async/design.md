# Design: 执行轨迹复盘改为 PowerShell 异步执行

## Context

AgentX 的执行轨迹复盘功能当前实现链路：

1. 前端 `TraceAnalysisButtons` 点击「复盘」→ `useTraceAnalysis.review(runId)`
2. `useTraceAnalysis` 创建 pending assistant 消息 + `setSessionRunning(true)`
3. `streamTraceReview()` 发起 `POST /api/observation/review` SSE 连接
4. 后端 `observation.py::review_trace()` 读取 observation DB → `_build_trace_summary()` → `_build_review_prompt()` → `ClaudeCliRunner.run_stream()` spawn claude CLI 子进程
5. `ClaudeCliRunner` 解析 stream-json → 转 agentx SSE 事件（reasoning / token / done）
6. 前端 `consumeSse()` 消费 SSE → 写入 chat store parts
7. done 时标记 reasoning done + `setSessionRunning(false)` + 显示「执行优化」按钮

执行优化流程类似，用户点击「执行优化」→ `applyOptimization(runId, reviewText)` → `POST /api/observation/apply-optimization` SSE → claude CLI `acceptEdits` 模式执行代码修改。

**问题**：claude CLI 本身是交互式终端工具，被 `-p` + stream-json 管道化后失去了交互能力。复盘报告输出后用户无法直接追问或让 Claude 执行优化，必须回到 GUI 点击「执行优化」按钮触发第二次 SSE 流。整个链路复杂且脆弱（子进程生命期管理、空闲心跳超时、npm wrapper 解析等）。

## Goals / Non-Goals

**Goals:**
- 点击「复盘」按钮后弹出一个新的 PowerShell 窗口，claude CLI 以交互模式运行
- 复盘报告在 PowerShell 终端展示，用户可直接在终端继续对话执行优化
- 聊天窗口不再渲染任何 trace 内容（不创建 pending message、不写 parts、不 setSessionRunning）
- 删除旧的 SSE 复盘/执行优化链路（后端 2 端点 + 前端 SSE 消费代码）
- 按钮点击后显示「✓ 已发送到 PowerShell」状态

**Non-Goals:**
- 不做 PowerShell 进程管理（不跟踪 PID、不做终止、不做多窗口并发控制）
- 不做复盘结果回传聊天窗口
- 不做 trace 文件自动清理
- 不修改 observation DB 的数据结构或写入逻辑
- 不修改 `claude_cli_runner.py`（它被旧 SSE 端点使用，旧端点删除后此模块可保留但不再被调用，后续 PR 清理）

## Decisions

### Decision 1: 后端导出 prompt 文件而非前端拼装

**选择**: 新增 `POST /api/observation/export-trace/{run_id}` 端点，后端复用 `_build_trace_summary()` + `_build_review_prompt()` 生成完整 prompt 并落盘到 `data/traces/<run_id>_<timestamp>.md`，返回文件绝对路径。前端拿到路径后传给 Tauri command。

**理由**:
- `_build_trace_summary()` 逻辑复杂（token_rollback 处理、碎片合并、截断），前端重新实现违反 DRY
- observation 数据在后端 SQLite 中，前端无法直接访问
- prompt 模板集中后端，便于统一维护

**替代方案**:
- 前端从 chat store 读取 trace 数据自行拼装 → 拒绝，trace 摘要逻辑复杂，且 chat store 中的数据已丢失 observation DB 中的 seq/payload 等结构化信息
- PowerShell 脚本直接调后端 API 取 JSON → 拒绝，PowerShell 中做 JSON 解析 + prompt 拼接维护成本高

### Decision 2: Claude CLI 交互模式而非 `-p` 非交互

**选择**: PowerShell 脚本中通过 stdin 管道将 prompt 传给 claude CLI，不带 `-p` 和 `--output-format stream-json`。Claude 处理完初始 prompt 后进入交互式会话。

**命令**: `$prompt | claude --model sonnet --permission-mode plan --max-turns 25`

**为什么用 stdin 管道而非位置参数**:
- Windows `CreateProcess` 命令行长度上限 32767 字符，而 trace 摘要可达 200KB（`_TRACE_SUMMARY_LIMIT = 200000`），位置参数会超限
- stdin 管道无长度限制，claude CLI 从 stdin 读取初始 prompt 后切换到 console 交互输入

**理由**:
- 交互模式下用户可阅读复盘报告后直接输入「按方案 1 执行修改」，Claude 在同一会话上下文中执行，无需第二次 API 调用
- 用户可通过 `/permission-mode acceptEdits` 切换到可编辑模式执行代码修改
- 交互模式是 claude CLI 的原生设计，体验远优于 `-p` 管道模式

**Fallback**: 如果 claude CLI 的 stdin 管道在交互模式下不工作（stdin 被 pipe 占用导致无法切换到 console 输入），则改为：
1. 先 `claude -p` 处理初始 prompt（stdin 管道模式）
2. 脚本自动启动 `claude --continue` 进入交互模式继续该会话
此 fallback 在实现阶段验证后决定是否启用。

**替代方案**:
- 两次 `-p` 非交互调用（先 plan 后 acceptEdits）→ 拒绝，两次调用无上下文关联，用户无法在复盘基础上追问
- 只做复盘不执行优化 → 拒绝，用户已确认两个都在 PowerShell 做

### Decision 3: Tauri Rust spawn PowerShell 而非 tauri-plugin-shell

**选择**: 使用 `std::process::Command::new("powershell.exe").spawn()`，与现有 `shell_reveal_in_folder` 一致。Tauri command 只接收 `prompt_file` 参数，`project_root` 从 `env!("CARGO_MANIFEST_DIR")` 推导（与 `resolve_backend_cwd` 同源模式）。

**理由**:
- `tauri-plugin-shell` 的 `open()` 方法用于打开文件/URL，不支持传参运行脚本
- `Command::new` + `spawn()` 是 Rust 标准库，项目已有使用先例
- `spawn()` 返回立即，不阻塞 Tauri 主线程，PowerShell 窗口独立于 AgentX 主窗口
- `project_root` 在 Tauri 端推导，前端无需传递，减少参数耦合

**替代方案**:
- `tauri-plugin-shell::ShellExt::shell().command()` → 拒绝，API 更复杂且无额外收益
- 前端传 `project_root` → 拒绝，前端不感知项目根目录（由 Tauri 进程管理）

### Decision 4: PowerShell 脚本放 `scripts/` 目录

**选择**: `scripts/analysis-run.ps1`，与现有 `start.ps1` / `stop.ps1` / `health-check.ps1` 同级。

**理由**:
- 对齐 AGENTS.md §15 的启停脚本约定
- Tauri Rust 端可通过项目根目录拼接 `scripts/analysis-run.ps1` 定位

### Decision 5: trace 文件带时间戳不复盖

**选择**: 文件名 `<run_id>_<YYYYMMDD_HHMMSS>.md`，每次复盘生成新文件。

**理由**:
- 同一 run_id 可能被多次复盘，保留历史便于对比
- 文件体积小（<200KB），不构成存储压力

## Architecture

### 数据流

```
ChatView 复盘按钮
   │
   ├─ 1. useTraceAnalysis.review(runId)
   │     └─ POST /api/observation/export-trace/{run_id}
   │          → 后端读 observation DB (run + events)
   │          → _build_trace_summary() + _build_review_prompt()
   │          → 写 data/traces/<run_id>_<ts>.md
   │          → 返回 { ok: true, prompt_file: "<abs_path>" }
   │
   ├─ 2. invoke("analysis_launch_powershell", { promptFile, projectRoot })
   │     └─ Tauri Rust: spawn powershell.exe -NoExit -File scripts/analysis-run.ps1
   │        -PromptFile <path> -ProjectRoot <path>
   │          → 独立 PowerShell 窗口打开
   │
   └─ 3. 按钮状态 → "✓ 已发送到 PowerShell"
                     （聊天窗口不创建任何消息）
```

### PowerShell 窗口内流程

```
==== AgentX 执行轨迹复盘 ====
项目根目录: D:\java\agentprojects\agentx
Prompt 文件: D:\...\data\traces\abc123_20260711_143000.md

提示: 复盘完成后如需执行优化，输入 /permission-mode acceptEdits
提示: 输入 /quit 退出 Claude CLI

[Claude CLI 交互模式启动]
> [Claude 输出复盘报告...]
> [用户可继续对话追问/执行优化]
> /quit
```

## Components

### 1. 后端：`POST /api/observation/export-trace/{run_id}`

**文件**: `backend/app/api/observation.py`

**请求**: 路径参数 `run_id`

**响应**:
```json
{
  "ok": true,
  "prompt_file": "D:\\java\\agentprojects\\agentx\\data\\traces\\abc123_20260711_143000.md"
}
```

**逻辑**:
1. `sink.get_run_sync(run_id)` + `sink.list_events_sync(run_id)` 读取数据
2. run 和 events 都不存在 → 返回 `{ ok: false, error: "未找到轨迹数据" }`
3. `trace_summary = _build_trace_summary(run, events)`
4. `prompt = _build_review_prompt(trace_summary)`
5. 创建 `DATA_DIR / "traces"` 目录（幂等）
6. 写文件 `DATA_DIR / "traces" / f"{run_id}_{timestamp}.md"`
7. 返回绝对路径

### 2. Tauri：`analysis_launch_powershell` 命令

**文件**: `src-tauri/src/commands/analysis.rs`（新建）

```rust
#[tauri::command]
pub fn analysis_launch_powershell(
    prompt_file: String,
) -> Result<(), String>
```

**逻辑**:
1. 从 `env!("CARGO_MANIFEST_DIR")` 推导项目根目录（`CARGO_MANIFEST_DIR` 指向 `src-tauri`，其 parent 即项目根）
2. 拼接脚本路径 `format!("{}/scripts/analysis-run.ps1", project_root)`
3. `Command::new("powershell.exe")` + `.args(["-NoExit", "-ExecutionPolicy", "Bypass", "-File", &script_path, "-PromptFile", &prompt_file, "-ProjectRoot", &project_root])` + `.spawn()`
4. spawn 失败 → `Err(String)`，前端显示错误
5. 成功 → `Ok(())`，前端设按钮 dispatched 状态

### 3. PowerShell 脚本

**文件**: `scripts/analysis-run.ps1`（新建）

**参数**: `-PromptFile`（必填）、`-ProjectRoot`（必填）

**逻辑**:
1. `Set-Location $ProjectRoot`
2. 打印 banner（标题 + 路径 + 操作提示）
3. `$prompt = Get-Content -Raw $PromptFile`
4. `$prompt | claude --model sonnet --permission-mode plan --max-turns 25`（stdin 管道传 prompt，避免命令行长度限制）
5. claude 不存在时 PowerShell 自身报错，`-NoExit` 保证窗口不关闭

### 4. 前端：`useTraceAnalysis` 重写

**文件**: `frontend/renderer/hooks/useTraceAnalysis.ts`

**变更**: 从 ~220 行简化到 ~50 行

**保留**:
- `review(runId: string)` 函数签名

**删除**:
- `applyOptimization` / `abort` / `analyzingKind` / `lastReviewPendingId`
- `_abortController` / `_pendingId` / `_analyzingThreadId` / `_lastReviewText` / `_lastReviewRunId`
- SSE 监听 / pending message 创建 / sessionRunning / chat store parts 操作

**新增**:
- `dispatched` 状态（标记已发送）

**`review` 逻辑**:
```typescript
const review = useCallback(async (runId: string) => {
  // 1. 调后端导出 trace
  const res = await fetch(`${API_BASE}/api/observation/export-trace/${runId}`, {
    method: "POST",
  });
  const data = await res.json();
  if (!data.ok) { setError(data.error); return; }
  // 2. 调 Tauri 弹 PowerShell（project_root 由 Tauri 端推导）
  await invoke("analysis_launch_powershell", {
    promptFile: data.prompt_file,
  });
  // 3. 标记已发送（仅成功时设 dispatched=true）
  setDispatched(true);
}, []);
```

### 5. 前端：`TraceAnalysisButtons` 简化

**文件**: `frontend/renderer/components/chat/TraceAnalysisButtons.tsx`

**删除**:
- 「执行优化」按钮
- 「停止」按钮（isAnalyzingSession 分支）
- `Loader2` / `Square` 图标 import
- `sessionRunning` / `isAnalyzingSession` / `showApplyOptimization` 逻辑
- `applyOptimization` / `abort` / `lastReviewPendingId` 从 hook 解构

**保留**:
- 「复盘」按钮（`RotateCcw` 图标）

**新增**:
- `dispatched` 状态：点击后按钮变灰 + 显示「✓ 已发送」
- tooltip: "复盘已发送到 PowerShell 窗口"

### 6. 前端：`observation.ts` 清理

**文件**: `frontend/renderer/lib/api/observation.ts`

**删除**:
- `streamTraceReview()`
- `streamApplyOptimization()`
- `consumeSse()`
- `AnalysisStreamHandlers` / `AnalysisDoneMeta` / `StreamOpts` / `ApplyOptimizationOpts` 类型

**保留**: 文件本身（如果变为空文件则删除整个文件）

## Error Handling

**核心原则**: `dispatched` 状态仅在整条链路成功时（后端导出 + Tauri spawn 都成功）设为 `true`。任何错误都不设 `dispatched`，按钮保持可点击，并通过 `error` 状态在 tooltip 显示错误信息。

| 场景 | 处理 |
|---|---|
| trace run_id 不存在 | 后端返回 `{ ok: false, error: "未找到轨迹数据" }`，前端不设 dispatched，tooltip 显示错误 |
| trace 文件写入失败 | 后端返回 500 + 错误信息，前端不设 dispatched，tooltip 显示错误 |
| Tauri spawn PowerShell 失败 | invoke reject，前端不设 dispatched，tooltip 显示错误 |
| claude CLI 未安装 | PowerShell 窗口内显示 `'claude' 不是可识别的命令`，`-NoExit` 保证窗口不关闭（前端已设 dispatched，用户可自行关闭窗口） |
| PowerShell 窗口被用户关闭 | 不影响 AgentX 主窗口（独立进程），trace 文件已落盘 |
| 网络请求失败 | 前端 catch，不设 dispatched，tooltip 显示「网络请求失败」 |

## Testing

### 后端单元测试

- `test_export_trace_success`：mock observation DB 返回 run + events → 验证返回 `prompt_file` 路径 + 文件存在 + 内容包含 prompt 模板
- `test_export_trace_not_found`：run_id 不存在 → 验证返回 `{ ok: false }`
- `test_export_trace_file_write`：验证文件名格式 `<run_id>_<timestamp>.md`

### 前端测试

- `useTraceAnalysis.review` 调用 fetch + invoke 的正确性
- `dispatched` 状态切换

### 手动验证

- 点击复盘按钮 → PowerShell 窗口弹出 → claude CLI 启动 → 复盘报告输出
- 在 PowerShell 中继续对话 → Claude 响应
- 输入 `/permission-mode acceptEdits` → 切换到可编辑模式 → 执行代码修改
