# Design: 采用 deepagents 0.6.12 内置工具替代自研 fs/git 工具

## Context

[2026-07-08-deepagents-migration](../2026-07-08-deepagents-migration/proposal.md) 的 P0/P1 已完成，
P2 Future「引入 FilesystemBackend 抽象，用 deepagents 内置 fs 工具 + permissions= 替换自研 fs 工具」未实施。
当前项目用 `_EXCLUDED_BUILTIN_TOOLS` 排除 6 个内置 fs 工具，改用自研实现（能力弱于内置）。
9 个自研 git 工具是 subprocess 包装，与 execute 功能等价。0.6.12 无内置 delete，项目也未自研。

## Goals / Non-Goals

**Goals:**
- 用 deepagents 0.6.12 内置 fs 工具（ls/read_file/write_file/edit_file/glob/grep）替代 6 个自研 fs 工具
- 用 AuthorizedLocalShellBackend 注入 SessionSandbox 动态授权（突破 0.6.12 permissions+sandbox 互斥限制）
- 用 execute 替代 9 个自研 git 工具 + command_filter 拦截 git 写命令
- 自研 delete_file 补齐 0.6.12 无内置 delete 的缺口
- 采用内置语义（write_file 不覆盖、edit_file 唯一匹配、grep 字面量搜索）
- 删除 ~568 行自研代码，净减少 ~430 行

**Non-Goals:**
- 不升级 deepagents 版本（锁定 0.6.12，用户明确要求）
- 不迁移到 deepagents 内置 delete（需 0.7.a1+，本 change 自研补齐）
- 不改 delegate_to_expert（业务封装，消费 run_coding_expert 事件流，不适合作为 compiled subagent）
- 不改 rag_retrieve / web_search（内置无对应，Tavily SDK 已成熟）
- 不改 SafeLocalShellBackend.execute（已用内置，AuthorizedLocalShellBackend 继承保留）
- 不改 SSE 事件契约（tool_call/tool_result/approval_request 格式不变）
- 不改前端（工具名不变，SSE 事件不变）

## Decisions

### Decision 1: 用 AuthorizedLocalShellBackend 注入动态授权（突破 0.6.12 限制）

**问题**: 0.6.12 的 `FilesystemMiddleware._permissions` 禁止与 `SandboxBackendProtocol` 同用
（[filesystem.py:864-872](../../../.venv/Lib/site-packages/deepagents/middleware/filesystem.py)）。
项目需要 `thread_id` 级**动态**授权（运行时用户通过 dialog 授权目录），
而 `permissions` 是**静态**声明式规则（创建 agent 时固定）。

**选项**:
- A. 等待 0.7+ backend policy hooks → 用户要求不升级，不可行
- B. 用 `FilesystemMiddleware(tools=...)` 白名单 → 0.7.0a4+ 特性，0.6.12 不支持
- C. 自定义 backend 子类，方法级 override 注入授权 → 可行

**选择**: C — 创建 `AuthorizedLocalShellBackend(SafeLocalShellBackend)`

`LocalShellBackend` 多重继承 `FilesystemBackend` + `SandboxBackendProtocol`，已有全部 fs 方法。
创建子类 override fs 方法，在方法体内调 `SessionSandbox.check_*`，**不传 `_permissions`**
（绕过互斥限制）。

```
AuthorizedLocalShellBackend(SafeLocalShellBackend)
    └── SafeLocalShellBackend(LocalShellBackend)  ← execute 已有安全过滤
        └── LocalShellBackend(FilesystemBackend, SandboxBackendProtocol)
            ├── ls / read / write / edit / glob / grep  ← 来自 FilesystemBackend，override 注入授权
            └── execute  ← 来自 SandboxBackendProtocol，SafeLocalShellBackend 已 override
```

### Decision 2: contextvars 传递 thread_id

**问题**: backend 方法签名无 thread_id 参数，但 `SessionSandbox.check_*` 需要 thread_id。

**选择**: 用 `contextvars.ContextVar` 在 agent 入口设置 thread_id，backend 方法内读取。

- 新建 `backend/app/deepagent/context.py`：`current_thread_id: ContextVar[str]`
- 在 agent 入口（run_deep_path / run_work_supervisor / run_coding_expert / run_coding_team）
  invoke 前 `current_thread_id.set(thread_id)`
- LangGraph 工具执行在同 asyncio task 内，contextvar 自动传播
- LangGraph `Send` 并行分支需显式 `copy_context()`（在子代理分发处处理）

**理由**: contextvar 是 Python 标准的上下文传递机制，不污染函数签名，asyncio task 自动传播。

### Decision 3: SessionSandbox 新增同步校验方法

**问题**: `SessionSandbox.check_read/check_write` 是 `async`（用 `asyncio.Lock`），
但 `FilesystemBackend` 的 fs 方法是同步的，无法直接 `await`。

**选择**: SessionSandbox 新增 `check_read_sync`/`check_write_sync` 同步方法。

- 复用核心检查逻辑（`_authorized_dirs` 等 dict 读取，GIL 保护，线程安全）
- 仅省去 `asyncio.Lock`（授权目录变更不频繁，可接受极小竞态）
- 异常处理：`PathNotAuthorized` 需转为 backend 的错误返回值
  （`ReadResult(error=...)` / `WriteResult(error=...)`），与 FilesystemBackend 契约一致

### Decision 4: 用 execute 替代 git 工具 + command_filter 拦截

**问题**: 9 个自研 git 工具是 subprocess 包装，与 execute 功能等价。但 git 写操作
（clone/pull/checkout/stage/commit）在 `DANGEROUS_TOOLS` 中有独立审批粒度——
子代理可只读、DeepAgent 写操作触发 interrupt_on。用 execute 替代会丢失这个粒度
（execute 不在 DANGEROUS_TOOLS，走 directory_extension 机制）。

**用户决策**: 用 execute 替代（用户已澄清）。

**选择**: 在 [command_filter.py](../../../backend/app/security/command_filter.py) 新增 `is_git_write_command` 检测函数，
在 [safe_shell_backend.py](../../../backend/app/deepagent/safe_shell_backend.py) 的 `SafeLocalShellBackend.execute` 中调用。

- `command_filter.py` 新增 `is_git_write_command(command)` + `_GIT_WRITE_SUBCOMMANDS` 集合
- 检测逻辑：`shlex.split` 解析，检查首 token 是 `git` 且第二 token 在 `{commit, push, checkout, clone, pull, add}` 中
- `SafeLocalShellBackend.execute` 在 blocklist + 元字符过滤之后调用检测，命中返回 `ExecuteResponse(output="git 写操作需审批...", exit_code=126, truncated=False)`
- 触发 directory_extension 审批流（workspace 之外未授权时触发审批）
- 只读 git 命令（status/diff/log/branch）不拦截，走 directory_extension 机制
- `AuthorizedLocalShellBackend` 继承 `SafeLocalShellBackend`，git 拦截自动继承

**DANGEROUS_TOOLS 更新**: 删除 git_* 条目（仅保留 edit_file/write_file，新增 delete_file）。

### Decision 5: 采用内置 write_file/edit_file 语义

**用户决策**: 采用内置语义（用户已澄清）。

**变更**:
- 删除幂等保护逻辑（`_recent_writes`/`_content_hash`/`_gc_recent_writes`，~110 行）
- write_file：已存在文件报错（强制先 read 再 edit）
- edit_file：old_text 在文件中**唯一匹配**才替换，多处匹配报错；支持 `replace_all=True` 全局替换
- 更新 LLM system prompt 引导新行为

### Decision 6: 接受内置 grep 字面量搜索语义

**用户决策**: 接受字面量搜索（用户已澄清）。

**变更**:
- 内置 grep 基于 ripgrep，默认做字面量搜索（`-F`，非正则）
- 自研 grep 用 Python `re` 做正则匹配
- 迁移到内置后，现有正则模式（如 `def\s+\w+`）将失效
- LLM prompt 引导用字面量搜索；正则需求由 execute 跑 `rg` 替代

### Decision 7: 自研 delete_file（0.6.12 无内置）

**问题**: 0.6.12 无内置 delete（需 0.7.a1+），项目需要文件删除能力。

**选择**: 自研 `delete_file` 工具。

- 位置：[tool_assembly.py](../../../backend/app/deepagent/tool_assembly.py) 注册
- 授权：`SessionSandbox.check_write`（异步，从 contextvar 取 thread_id）
- 安全护栏：禁止删除沙箱白名单根目录（`data/workspace`、`data/uploads` 本身），仅允许删其下内容
- `recursive=False`：仅删文件，遇目录返回错误
- `recursive=True`：用 `shutil.rmtree` 递归删目录
- 审批：加入 `DANGEROUS_TOOLS` + `FORBIDDEN_SUBAGENT_TOOLS`
- 仅 DeepAgent 暴露，子代理不暴露

## Risks

### Risk 1: contextvar 在 LangGraph 并行子代理中传播

**风险**: LangGraph `Send` 并行分支可能不自动传播 contextvar，导致子代理 thread_id 丢失。

**缓解**: 在子代理分发处显式 `copy_context()`。Phase D 测试验证子代理 thread_id 可用。

### Risk 2: SessionSandbox 同步方法竞态

**风险**: `check_read_sync`/`check_write_sync` 不加 `asyncio.Lock`，授权目录变更时可能竞态。

**缓解**: 授权目录变更不频繁（用户通过 dialog 授权），dict 读操作 GIL 保护。
极小竞态窗口最坏情况是读到旧授权状态，不会导致数据损坏（写入仍经 backend 校验）。

### Risk 3: 内置 write_file 不覆盖破坏现有 LLM 行为

**风险**: LLM 习惯用 write_file 覆盖已存在文件，迁移后已存在文件报错，LLM 可能反复重试。

**缓解**: Phase A.4 更新 LLM system prompt 明确引导「write_file 创建新文件，edit_file 修改已存在文件」。
Phase D 测试覆盖。

### Risk 4: command_filter git 拦截误判

**风险**: command_filter 检测 `git commit` 等命令时，可能误判包含这些子串的非 git 命令
（如 `echo "git commit"`）。

**缓解**: 检测逻辑用 `shlex.split` 解析命令，检查首个 token 是 `git` 且第二个 token 在
写命令集合中。Phase D 测试覆盖边界情况。

### Risk 5: 子代理工具集变更

**风险**: 删除 make_fs_tools 后，rag/web 子代理需通过 `create_deep_agent` 的
`FilesystemMiddleware` 自动注入内置 fs 工具。需确保子代理只读。

**缓解**: 子代理通过 `FORBIDDEN_SUBAGENT_TOOLS` 过滤写工具（write_file/edit_file/delete_file/execute）。
Phase D 验证子代理工具集不含写工具。

## Migration Strategy

### 分阶段迁移（A/B 并行）

```
Phase A (fs 迁移) ──┐
                    ├──► Phase C (delete_file) ──► Phase D (测试)
Phase B (git 替代) ─┘
```

- Phase A 和 Phase B **相互独立**，可并行实施
- Phase C 依赖 Phase A（AuthorizedLocalShellBackend 授权机制复用）
- Phase D 贯穿，每个 Phase 完成后运行对应测试

### 回滚策略

- Phase A/B/C 各自独立提交，出问题可单独 revert
- 自研 fs/git 工具代码保留在 git 历史中，可作为 fallback
- AuthorizedLocalShellBackend 出问题时可回退到 `_EXCLUDED_BUILTIN_TOOLS` + 自研工具

## 与 2026-07-08-deepagents-migration 的关系

本 change 是 `2026-07-08-deepagents-migration` Future P2 的落地 + 扩展：

| 维度 | 原 P2 设想 | 本 change 实际 |
|---|---|---|
| fs 工具 | 用 FilesystemBackend + permissions= | 用 AuthorizedLocalShellBackend（突破 permissions+sandbox 互斥） |
| 授权 | 静态 permissions | 动态 SessionSandbox.check_*（方法级 override） |
| git 工具 | 未提及 | 扩展：用 execute + command_filter 替代 |
| delete | 未提及 | 扩展：自研 delete_file 补齐 |
| 语义 | 未提及 | 扩展：采用内置 write/edit/grep 语义 |
| 版本 | 未限定 | 锁定 0.6.12（不升级） |
