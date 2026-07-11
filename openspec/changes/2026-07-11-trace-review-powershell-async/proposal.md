# Proposal: 执行轨迹复盘改为 PowerShell 异步执行

## Why

当前复盘/执行优化流程：前端点击「复盘」按钮 → 后端 spawn claude CLI 子进程 → stream-json 转 SSE → 前端在聊天窗口渲染 reasoning/token 事件。存在三个问题：

1. **聊天窗口污染**：复盘输出作为 assistant 消息混入对话历史，干扰正常对话上下文
2. **Claude CLI 能力浪费**：claude CLI 本身是交互式终端工具，被后端 `-p` 非交互模式 + stream-json 管道化后，用户无法在复盘后直接对话追问或执行优化
3. **SSE 链路过重**：后端维护 claude 子进程生命期 + stream-json 解析 + SSE 转换 + 前端 SSE 消费 + pending message 状态管理，全为把终端输出塞进聊天窗口

## What Changes

### 执行载体迁移

将复盘/执行优化的执行载体从「后端 SSE 流 → 聊天窗口渲染」改为「后端导出 prompt 文件 → Tauri 弹 PowerShell 窗口 → claude 交互模式」。聊天窗口不再展示任何执行轨迹内容。

### 后端层

- **新增** `POST /api/observation/export-trace/{run_id}`：从 observation DB 读取 run + events，复用现有 `_build_trace_summary()` + `_build_review_prompt()` 生成完整 prompt，落盘到 `data/traces/<run_id>_<timestamp>.md`，返回文件绝对路径
- **删除** `POST /api/observation/review`（旧 SSE 复盘端点）
- **删除** `POST /api/observation/apply-optimization`（旧 SSE 执行优化端点）
- **保留** `_build_trace_summary()` / `_build_review_prompt()`（被新端点复用）
- **删除** `_build_apply_optimization_prompt()`（交互模式下用户直接在 PowerShell 对话，不需要后端拼 prompt）

### Tauri 层

- **新增** `commands/analysis.rs`：`analysis_launch_powershell` 命令，spawn `powershell.exe -NoExit -File scripts/analysis-run.ps1` 并传入 prompt 文件路径和项目根目录
- **新增** `scripts/analysis-run.ps1`：PowerShell 脚本，cd 到项目根目录，读取 prompt 文件，以交互模式启动 `claude` CLI

### 前端层

- **重写** `hooks/useTraceAnalysis.ts`：删除全部 SSE 监听 / pending message / sessionRunning / AbortController 逻辑，`review(runId)` 改为调 export-trace 端点 + Tauri command
- **简化** `components/chat/TraceAnalysisButtons.tsx`：删除执行优化按钮、停止按钮、流式 loading 状态，只保留 1 个复盘按钮，点击后显示「✓ 已发送」
- **删除** `lib/api/observation.ts` 中的 `streamTraceReview` / `streamApplyOptimization` / `consumeSse`

## Capabilities

### New Capabilities

- `trace-review-powershell`：复盘任务通过独立 PowerShell 窗口异步执行，claude CLI 以交互模式运行

### Modified Capabilities

- `observation-api`：删除 2 个 SSE 端点，新增 1 个导出端点

### Removed Capabilities

- `trace-review-sse`：旧的 SSE 流式复盘/执行优化链路整体废弃

## Impact

- **后端**: 修改 `observation.py`（删 2 端点 + 加 1 端点），新增 traces 目录
- **Tauri**: 新增 `commands/analysis.rs`（~30 行），修改 `lib.rs` 注册命令，新增 `scripts/analysis-run.ps1`（~20 行）
- **前端**: 重写 `useTraceAnalysis.ts`（从 ~220 行简化到 ~50 行），简化 `TraceAnalysisButtons.tsx`（从 ~150 行简化到 ~60 行），删除 `observation.ts` 中 SSE 相关函数
- **API**: 删 2 端点加 1 端点
- **测试**: 后端新端点单元测试
- **文档**: 无（内部架构变更，用户可见行为仅为按钮反馈变化）

## Future Extensibility

- 未来可在 PowerShell 脚本中支持 `--model opus` 参数切换模型
- 未来可支持用户自定义复盘 prompt 模板（从 `.agentx/` 读取）
- 未来可把 trace 文件作为可分享的调试素材
