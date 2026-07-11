# 沙箱管控策略优化 — 诊断与候选方案对比

> 状态：对比文档（不动代码，待拍板）
> 触发问题：执行 `powershell -Command "Get-Process | Sort-Object ..."` 被元字符拦
> 附带问题：write_file 路径认知错位（C:\... 拦 → /workspace/ 也拦）
> 目标：分层风险分级（命令级 + 元字符级 + 路径级 + 上下文）

---

## 一、现状诊断

### 1.1 沙箱分层现状

AgentX 沙箱目前由 4 层组成，每层独立工作，没有统一调度：

| 层 | 文件 | 职责 | 拦截面 |
|---|---|---|---|
| **L1 命令黑名单** | `app/security/command_filter.py` | 拦截 rm/del/format/sudo 等极度危险命令（`DEFAULT_BLOCKLIST`，27 条） | 命令名级 |
| **L2 元字符过滤** | `command_filter.has_forbidden_args` | 拦截 `; & \| ` \` $ < > \r \n` | 字符串级（含 `respect_quotes=True` 引号感知） |
| **L3 Git 写操作** | `command_filter.is_git_write_command` | 拦截 commit/push/checkout/... | 命令前缀级 |
| **L4 路径白名单** | `app/sandbox/session_sandbox.py` | 检查路径是否在 `DEFAULT_WHITELIST` 或 thread 授权目录内 | 路径级 |

**两套执行入口**：

| 入口 | 走 L1+L2 | 走 L3 | 走 L4 | 子进程模式 |
|---|---|---|---|---|
| `cli_execute`（自研工具） | ✅ | ❌ | ✅ | **argv 列表，shell=False** |
| `execute`（deepagents `LocalShellBackend`） | ✅ | ✅ | ❌（root_dir） | **shell=True** |

→ **核心问题**：`cli_execute` 走 argv 列表本来就不需要元字符过滤，但仍然被拦了 6 次（每次参数都过一遍 `has_forbidden_args`），元字符过滤在这里是**冗余且有害**的。

### 1.2 三大 Bug 清单

#### Bug A：元字符误伤 PowerShell 合法语法

**复现**：
```
powershell -Command "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, @{Name='Memory(MB)';Expression={[math]::Round($_.WorkingSet64/1MB,2)}}, Id | Format-Table -AutoSize"
```

**根因**：
- `safe_shell_backend.py:242` 调用 `has_forbidden_args(command, respect_quotes=True)`
- `_has_forbidden_respecting_quotes`（`command_filter.py:132-167`）的引号感知规则太粗：
  - 单引号内：完全放行 ✅
  - 双引号内：仅放过 `$` 和反引号 ❌（漏了 `$_` 这种合法用法 + 单 `|` 在 PS 是管道语法）
  - 实际上即使 `respect_quotes=True`，整个正则路径对 PS 仍然错位

**影响面**：所有用 PS 写管道的 agent 都会撞墙。

#### Bug B：write_file 路径认知错位（用户当前最痛的点）

**复现**（用户 trace）：
```
write_file(C:\Users\luqia\Desktop\memory_analyze.py)
→ 沙箱：路径未授权（绝对路径走 C: 盘符，不在任何白名单/授权目录里）

write_file(/workspace/memory_analyze.py)
→ 沙箱：路径未授权（"/workspace/" 解析为 C:\workspace，not under any whitelist）

ls(D:\java\book\book)
→ 沙箱：路径未授权（D:\java\book\book 也没授权）
```

**根因**：
1. `path_guard.normalize_path` 对**绝对路径**只做 `resolve()`，不做语义改写；LLM 输出的"虚拟路径" `/workspace/` 在 OS 层根本不存在
2. `session_sandbox.check_read` 报错信息**没有列出当前 thread 的所有可用白名单/授权目录**，LLM 不知道该改为什么路径
3. 没有 `data/workspace/.scratch` 的"温柔引导"——`data/workspace/.scratch/` 才是 LLM 应该写的兜底，但错误提示里只在提示中提了一句

**影响面**：所有想写文件的 agent 都会卡 2-3 轮重试。

#### Bug C：`cli_execute` 误拦（设计 bug）

`tools/cli.py:140-156` 在 argv 列表模式（`shell=False`）下，仍然对每个参数跑 `has_forbidden_args`。
- argv 列表是**不会**经过 shell 解析的，`;` `|` 都不会被解释
- 唯一真危险的是参数值会被拼回日志/审批面板显示（注入日志阅读器）
- 但 `redact_args` 已经处理了 token/password 脱敏，元字符的"日志注入"风险其实可以收口

**影响面**：用户写 `python -c "import time; print(time.time())"` 这种合法 Python 也会被拦。

### 1.3 根因：分层之间没有协调

```
┌──────────────────────────────────────────────────────┐
│  L1 命令黑名单    (27 条硬编码)                      │
│  L2 元字符过滤    (字符串级一刀切)                   │
│  L3 Git 写操作    (子命令级)                         │
│  L4 路径白名单    (路径级)                           │
│      ↓ 各自独立判定，没有"风险等级"概念              │
│      ↓ 错误信息互不相通                              │
│      ↓ 不区分 cli_execute / execute 两种执行模型    │
└──────────────────────────────────────────────────────┘
```

**核心缺失**：
- 没有 **RiskLevel** 概念（低 / 中 / 高 / 严重）
- 没有 **执行上下文** 概念（argv 模式 / shell 模式 / read-only / full-trust）
- 没有 **错误聚合 + 智能建议**（命中多条规则时只报第一条，且不告诉 LLM 怎么改）

---

## 二、修复目标

按 `agentx-architect` 的分层标准 + `clean-architecture` 的端口-适配器，把沙箱重构为：

```
┌──────────────────────────────────────────────────────┐
│  RiskClassifier (新) — 风险评估中心                  │
│  ├─ CommandRisk     (rm/format/sudo → severe)       │
│  ├─ MetacharRisk    (在双引号内 / argv 模式 → low)  │
│  ├─ GitWriteRisk    (commit/push → medium)          │
│  └─ PathRisk        (不在白名单 → medium)           │
│      ↓ 汇总                                          │
│  ExecutionContext (新) — 执行上下文                  │
│  ├─ exec_mode:    argv_list | shell_string          │
│  ├─ trust_mode:   workspace | full_trust            │
│  └─ thread_id + 已授权目录列表                      │
│      ↓ 驱动                                          │
│  现有四层规则按上下文选择性生效                       │
└──────────────────────────────────────────────────────┘
```

**收益**：
- argv 列表模式下，元字符过滤**直接关闭**（Bug C 解决）
- 双引号内 PowerShell `$_` `|` `@{}` **直接放行**（Bug A 解决）
- write_file 错误信息**列出当前 thread 所有可用目录 + 推荐 scratch**（Bug B 解决）
- 给未来接 macOS App Sandbox / Windows Job Object 留端口

---

## 三、三套候选方案对比

### 方案 A：最小修补（Quick Fix）

**做什么**：
- A1：`has_forbidden_args` 取消 `respect_quotes=True` 在双引号内对 `|` 的拦截（保留 `$` `` ` ``）
- A2：把 `cli_execute` 的 `has_forbidden_args` 调用删除（argv 模式不需要）
- A3：`session_sandbox.check_read/write` 报错时，附上当前 thread 的"已授权目录列表 + 建议 scratch 路径"
- A4：把 `DEFAULT_BLOCKLIST` 移到配置项，支持通过 settings 关闭单条规则

**代码量**：约 80-120 行

**代价**：
- ✅ 零架构风险
- ✅ 3 个 bug 全部解决
- ❌ 没解决"分层无协调"的根问题，未来加新规则继续重复同样的乱贴
- ❌ 没有 RiskLevel 概念，无法做"分级提示"和"分级审计"

**适合场景**：你想 1 天内能上线，不动架构

---

### 方案 B：风险分级 + 上下文感知（推荐）✅

**做什么**：

**B1 - 抽出 `RiskLevel` 枚举**（在 `app/security/risk.py`）：
```python
class RiskLevel(IntEnum):
    NONE = 0      # 完全安全
    LOW = 1       # 元字符在引号内/argv 模式 — 放行
    MEDIUM = 2    # 路径未授权 / Git 写 — 走审批
    HIGH = 3      # 命令在黑名单 — 拒绝
    SEVERE = 4    # 系统关键目录 / 路径穿越 — 拒绝 + 审计
```

**B2 - 抽出 `ExecutionContext`**（在 `app/security/context.py`）：
```python
@dataclass(frozen=True)
class ExecutionContext:
    exec_mode: Literal["argv_list", "shell_string"]
    trust_mode: Literal["workspace", "full_trust"]
    thread_id: str
    authorized_paths: tuple[Path, ...]
    scratch_path: Path
```

**B3 - 重写 `CommandFilter` 为策略链**（`app/security/filter.py`）：
```python
class CommandFilter(Protocol):
    def assess(self, command: str, ctx: ExecutionContext) -> RiskAssessment: ...

# 实现：
# - BlocklistFilter（命令黑名单）
# - MetacharFilter（按 ctx.exec_mode 决定是否启用）
# - GitWriteFilter
# - PathFilter（白名单 + scratch 引导）
# 串联为 RiskClassifier.classify(command, ctx) -> list[RiskAssessment]
```

**B4 - `cli_execute` 改造**：
- 取消元字符过滤（argv 模式天然免疫）
- 路径走 `PathFilter`，错误信息引导到 `.scratch/` 或当前已授权目录
- 仍然 redact token/password（日志安全）

**B5 - `safe_shell_backend` 改造**：
- `respect_quotes=True` 升级为 `MetacharFilter(respect_quotes=True, allow_powershell=True)`
- PowerShell 的 `$_` `$var` `@{...}` 在双引号内**字面量放行**
- shell 模式下管道 `|` **仍然拦截**（管道是 shell 注入主战场）
- 错误信息聚合所有命中规则 + 给出"如何重写"的建议

**B6 - `session_sandbox` 错误信息增强**：
- `_unauthorized_read_hint` 改成"列出当前所有可选目录"的动态提示
- 增加"建议改写到 scratch 路径"分支

**代码量**：约 400-600 行（含测试 + 文档）

**代价**：
- ✅ 解决 3 个 bug 的同时，建立分层协调
- ✅ 给后续加 macOS App Sandbox / Windows Job Object 留接口
- ✅ 错误信息聚合 + 智能建议（用户体验最大提升）
- ⚠️ 需要改 4 个文件 + 1 个新文件，跨多个模块
- ⚠️ 测试需要重写一批

**适合场景**：你愿意花 1 周做一次正经的重构，把"沙箱"作为基础设施长期维护

---

### 方案 C：端口-适配器化（Clean Architecture 全套重构）

**做什么**：
- 把 `BlocklistFilter` / `MetacharFilter` / `GitWriteFilter` / `PathFilter` 都做成可插拔的 `RiskStrategy`
- 增加 `SandboxPolicy` 抽象端口（`Protocol`）
- 适配器：`DefaultSandboxPolicy`（默认） / `StrictSandboxPolicy`（CI/生产用） / `LooseSandboxPolicy`（开发用）
- 配合 `@ConditionalOnProperty` 能力门控

**代码量**：约 800-1200 行

**代价**：
- ✅ 长期可维护性最佳
- ✅ 策略可以远程下发（未来接 Nacos config）
- ❌ 当前场景**过度设计**——还没到需要远程下发策略的时候
- ❌ 学习成本高，新人接手需要理解分层

**适合场景**：你有明确的"未来要做策略热更新"诉求

---

## 四、方案对比矩阵

| 维度 | A 最小修补 | B 风险分级（推荐） | C 端口-适配器化 |
|---|---|---|---|
| **修复 3 个 bug** | ✅ 全部 | ✅ 全部 | ✅ 全部 |
| **解决根因（分层无协调）** | ❌ | ✅ | ✅✅ |
| **代码量** | 80-120 行 | 400-600 行 | 800-1200 行 |
| **工作量** | 1 天 | 1 周 | 2-3 周 |
| **架构风险** | 极低 | 中 | 中-高 |
| **可维护性** | 1-3 月 | 1-2 年 | 3+ 年 |
| **可扩展性** | 差 | 良 | 优 |
| **测试改造** | 增量 | 半数重写 | 大量重写 |
| **是否值得现在做** | 痛点紧急 | ✅ 性价比最优 | 暂不需要 |

---

## 五、风险与依赖

### 5.1 通用风险
- `cli_execute` 取消元字符过滤后，必须**保留 `redact_args`** 的 token/password 脱敏（否则日志会被注入凭证）
- PowerShell 双引号放宽后，需要在测试中**新增一批 PS 合法用法的 case** 防止回归
- 路径报错信息增强后，前端 `ApprovalDialog` 的展示需要回归测试

### 5.2 方案 B 额外风险
- 新增 `risk.py` / `context.py` 会被 deepagent / tools / sandbox 三个包依赖，需谨慎处理 import 方向
- `ExecutionContext` 的 frozen 语义要求调用方在变化时重建，避免 shared state 污染
- 错误信息聚合需要新增"建议改写"的格式化模块，初期建议只做英文+中文两种语言

### 5.3 依赖项
- 现有 9 个测试文件需要更新：`test_security_command_filter.py` / `test_safe_shell_backend.py` / `test_session_sandbox.py` / 等
- 文档更新：`docs/agents/03-key-conventions.md` 的"沙箱"章节 + 新增 `docs/agents/06-sandbox-policy.md`

---

## 六、推荐与下一步

**推荐方案 B**：
- 解决根因的同时，1 周内能落地
- 给未来的 macOS 沙箱 / 策略热更新留好接口
- 测试与文档配套推进

**下一步（待你拍板后）**：
1. 选定方案 B
2. 写完整的 OpenSpec 提案（`proposal.md` + `design.md` + `tasks.md` + `spec.md`）
3. 在 `docs/agents/06-sandbox-policy.md` 起草分层契约
4. 按"风险评估层 → 上下文层 → 过滤策略链 → 错误聚合"的顺序实施

**当前状态**：等待你确认走方案 B（或 A / C），然后落 spec 文档。
