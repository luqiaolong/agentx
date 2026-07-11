# 任务追踪 — 执行轨迹复盘改为 PowerShell 异步执行

## 预期修改文件
- [ ] `backend/app/api/observation.py` (修改: 删 2 SSE 端点 + 加 1 导出端点)
- [ ] `src-tauri/src/commands/analysis.rs` (新建)
- [ ] `src-tauri/src/commands/mod.rs` (修改: 注册 analysis 模块)
- [ ] `src-tauri/src/lib.rs` (修改: invoke_handler 注册新命令)
- [ ] `scripts/analysis-run.ps1` (新建)
- [ ] `frontend/renderer/hooks/useTraceAnalysis.ts` (重写)
- [ ] `frontend/renderer/components/chat/TraceAnalysisButtons.tsx` (简化)
- [ ] `frontend/renderer/lib/api/observation.ts` (删除 SSE 函数或整文件)
- [ ] `tests/python/unit/test_observation_export.py` (新建)

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 后端：新增 export-trace 端点 | observation.py | POST /api/observation/export-trace/{run_id} 返回 prompt_file 路径 + 文件落盘 | ⬜ |
| T2 | 后端：删除旧 SSE 端点 | observation.py | 删除 review_trace + apply_optimization 端点 + _build_apply_optimization_prompt + ReviewRequest + ApplyOptimizationRequest + _cli_unavailable_stream + _empty_error_stream | ⬜ |
| T3 | Tauri：新增 analysis_launch_powershell 命令 | commands/analysis.rs, commands/mod.rs, lib.rs | invoke("analysis_launch_powershell") 只接收 prompt_file，从 CARGO_MANIFEST_DIR 推导 project_root，成功 spawn powershell.exe 窗口 | ⬜ |
| T4 | 脚本：新建 analysis-run.ps1 | scripts/analysis-run.ps1 | 接收 -PromptFile -ProjectRoot 参数，cd 项目根，启动 claude 交互模式 | ⬜ |
| T5 | 前端：重写 useTraceAnalysis.ts | hooks/useTraceAnalysis.ts | review(runId) 调 export-trace + invoke，返回 dispatched 状态，无 SSE 逻辑 | ⬜ |
| T6 | 前端：简化 TraceAnalysisButtons.tsx | components/chat/TraceAnalysisButtons.tsx | 只剩 1 个复盘按钮，点击后显示「✓ 已发送」，无执行优化/停止按钮 | ⬜ |
| T7 | 前端：清理 observation.ts SSE 代码 | lib/api/observation.ts | 删除 streamTraceReview/streamApplyOptimization/consumeSse，文件为空则删除 | ⬜ |
| T8 | 测试：后端 export-trace 单元测试 | test_observation_export.py | 覆盖成功/未找到/文件写入 3 个场景 | ⬜ |

## 规模判定
- 涉及文件数: 9 → 规模: M
- 涉及模块数: 4 (backend/api, src-tauri/commands, frontend/hooks+components, scripts)
- 路径: 快速路径（需求明确，有完整 OpenSpec）
