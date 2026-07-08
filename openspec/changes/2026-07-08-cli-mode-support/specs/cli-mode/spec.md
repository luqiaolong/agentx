# Spec: CLI 模式（cli-mode）

## 概述

CLI 模式为 AgentX 提供终端命令行交互能力。用户在 PowerShell / Terminal 中输入 `agentx` 即可与 AI 助手对话，无需启动 GUI。

## 使用形态

### 形态 A：REPL 交互模式

```powershell
agentx
```

启动后进入持续对话会话：

```
AgentX CLI v0.2.0 | model: deepseek-chat | mode: coding | thread: a1b2c3d4e5f6
> 帮我写一个快速排序
[思考中...]
好的，这是一个 Python 实现...
> /mode work
模式已切换为 work
> /quit
```

### 形态 B：One-shot 单次任务

```powershell
agentx "帮我把这段代码改成异步的"
```

直接输出结果后退出，返回码 0（成功）或 1（错误）。

### 形态 C：管道输入

```powershell
cat .\error.log | agentx "分析这个错误日志"
git diff | agentx "写 commit message"
```

stdin 内容作为上下文附加到用户消息中。

### 形态 D：JSON 输出（脚本友好）

```powershell
agentx "列出当前目录下的 .py 文件" --json
```

输出结构化 JSON 数组：

```json
[
  {"event": "token", "data": "当前目录下有以下 .py 文件:"},
  {"event": "token", "data": "\n- main.py\n- utils.py"},
  {"event": "done", "data": "{}"}
]
```

## 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `message` | 位置参数（可选） | — | One-shot 模式的用户提问 |
| `--thread` | `str` | 自动生成 | 复用已有会话 ID |
| `--work` | flag | — | 使用 work 模式（Supervisor 全能 agent） |
| `--coding` | flag | **默认** | 使用 coding 模式（Coding Expert） |
| `--coding-team` | flag | — | 使用 coding_team 模式（AgentTeam 协作） |
| `--workspace` / `-w` | `str` | 当前目录 | 绑定 workspace 绝对路径 |
| `--verbose` / `-v` | flag | `False` | 显示 reasoning 和完整 tool_result |
| `--json` | flag | `False` | JSON 结构化输出（不渲染彩色文本） |
| `--version` | flag | — | 打印版本并退出 |

**模式选择规则**：`--work` / `--coding` / `--coding-team` 互斥，后指定者覆盖。均未指定时默认 `--coding`。

## 内建命令（REPL 模式）

以 `/` 开头的输入被解析为内建命令，不发送给 LLM：

| 命令 | 说明 |
|---|---|
| `/reset` | 清空当前 thread 的 checkpointer 和沙箱授权 |
| `/mode <work\|coding\|coding_team>` | 切换 agent_mode（默认 coding） |
| `/quit` / `/q` | 退出 REPL |
| `/help` | 显示可用命令列表 |

## 配置加载

启动时按以下优先级加载配置：

1. **Tauri store**：读取 `%APPDATA%/agentx/config.json`，解析并写入 `os.environ`
2. **环境变量**：已有的 `AGENTX_*` 直接复用
3. **交互式提示**：若以上均无 API Key，终端询问用户输入

## 审批交互

当事件流中出现 `approval_request` 时，CLI 暂停生成器并打印：

```
⚠️  审批请求
工具: write_file
参数: {"path": "D:\\project\\test.py", "content": "..."}
Approve? [y=批准 / n=拒绝 / o=仅一次 / s=会话级]:
```

用户输入后，CLI 调用 `submit_approval()` 写入决定，恢复生成器。

## 事件渲染

| 事件 | 默认渲染 | `--verbose` | `--json` |
|---|---|---|---|
| `token` | 增量 print | 增量 print | 原样输出 |
| `reasoning` | 隐藏 | dim 灰色显示 | 原样输出 |
| `tool_call` | `[调用: {name}]` | `[调用: {name}]` + 参数 | 原样输出 |
| `tool_result` | 截断 2000 字符 | 截断 10000 字符 | 原样输出 |
| `approval_request` | 高亮显示 + 提示输入 | 同默认 | 原样输出 |
| `team_plan` | `[团队计划: N 个子任务]` | 显示完整 plan JSON | 原样输出 |
| `team_progress` | `[{agent}] {status}` | 同默认 | 原样输出 |
| `delegation` | `[委派: {target}]` | 同默认 | 原样输出 |
| `done` | 换行 | 换行 | 原样输出 |
| `error` | 红色输出 | 红色输出 | 原样输出 |

## 错误处理

| 场景 | 行为 |
|---|---|
| Tauri store 不存在 | 降级到环境变量/交互提示，打印 warning |
| API Key 缺失 | 交互式询问，用户可输入或 Ctrl+C 退出 |
| `run_router` 抛异常 | 打印错误信息，REPL 模式下回到提示符；One-shot 返回码 1 |
| 审批超时 | 打印 "审批超时"，中断当前请求 |
| 终端编码非 UTF-8 | 启动时尝试切换代码页，失败则降级到纯 ASCII 输出 |

## 依赖

- `colorama>=0.4.0`（Windows 终端颜色兼容）
- 现有后端依赖（`langgraph`, `fastapi`, `pydantic-settings` 等）全部复用

## 边界

- 不支持图片/文件上传（终端限制）
- 不支持点击式审批（终端只有键盘输入）
- 多模态输入需等待终端图像协议支持
