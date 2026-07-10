# Proposal: 采用 deepagents 0.6.12 内置工具替代自研 fs/git 工具

## Why

[2026-07-08-deepagents-migration](../2026-07-08-deepagents-migration/proposal.md) 的 P0/P1 已完成
（create_deep_agent 替换 create_react_agent、interrupt_on、SummarizationMiddleware、memory=、skills= 等），
但其 Future P2「引入 FilesystemBackend 抽象，用 deepagents 内置 fs 工具 + permissions= 替换自研 fs 工具」
**从未实施**。当前项目仍通过 `_EXCLUDED_BUILTIN_TOOLS`（[factory.py:46-50](../../../backend/app/deepagent/factory.py#L46-L50)）
排除 6 个内置 fs 工具，改用自研实现，存在以下问题：

### 问题 1：自研 fs 工具能力弱于内置

| 能力 | deepagents 0.6.12 内置 | 项目自研 | 差距 |
|---|---|---|---|
| `read_file` | offset/limit 分片 + 行号 + 多模态（图片/pdf） | 一次性 `read_text` | 大文件 OOM 风险，无分片 |
| `grep` | ripgrep 引擎 + 三态输出（content/files/counts）+ context 行 | Python `re` 正则，单模式，截断 200 条 | 检索能力弱 |
| `ls` | 元数据（size/mtime/type） | 仅名称 | 信息不足 |
| `edit_file` | replace_all 全局模式 | 仅首次匹配 | 无法批量替换 |
| `glob` | ripgrep 模式匹配 | Python `rglob` | 等价 |

### 问题 2：delete 工具完全缺失

0.6.12 无内置 `delete`（需 0.7.a1+），项目也未自研。当前 LLM 无法通过工具调用删除文件——
shell `rm`/`del` 又在 [command_filter.py](../../../backend/app/security/command_filter.py) 的 blocklist 中
（exit_code=126 拦截）。**文件删除能力完全关闭**。

### 问题 3：9 个自研 git 工具是 subprocess 包装，与 execute 功能等价

[base.py:96-263](../../../backend/app/subagents/base.py#L96-L263) 的 9 个 git 工具全部是
`asyncio.create_subprocess_exec("git", ...)` 包装，输出 stdout 字符串。与 `execute` 跑
`git status` **功能完全等价**，属于重复造轮子（违反 AGENTS.md §1.1 + §3 R1）。

## What Changes

### 阶段 A（P0）：fs 工具迁移——用内置替代自研

#### A.1 创建 AuthorizedLocalShellBackend

新建 `backend/app/deepagent/authorized_backend.py`，继承 `SafeLocalShellBackend`，
override 6 个 fs 方法（ls/read/write/edit/glob/grep）注入 `SessionSandbox` 动态授权。
用 `contextvars.ContextVar` 在 agent 入口传递 thread_id。

突破 0.6.12 限制：`FilesystemMiddleware._permissions` 禁止与 `SandboxBackendProtocol` 同用，
但 AuthorizedLocalShellBackend 通过**方法级 override**注入授权（不传 `_permissions`），
绕过互斥限制。

#### A.2 启用内置 fs 工具

[factory.py:46-50](../../../backend/app/deepagent/factory.py#L46-L50) 删除 `_EXCLUDED_BUILTIN_TOOLS`，
`resolve_backend` 改用 `AuthorizedLocalShellBackend`。

#### A.3 删除自研 fs 工具

- [subagents/base.py:49-93](../../../backend/app/subagents/base.py#L49-L93) `make_fs_tools`：删除
- [tool_assembly.py:67-80](../../../backend/app/deepagent/tool_assembly.py#L67-L80) write_file/edit_file 注册：删除
- [filesystem.py](../../../backend/app/tools/filesystem.py) read_file/list_dir/glob/grep/write_file/edit_file +
  幂等保护逻辑（`_recent_writes`/`_content_hash`/`_gc_recent_writes`）：删除
- 保留 `list_workspace`/`read_workspace_file`（前端 API 用，非 LLM 工具）

#### A.4 采用内置语义

- 删除幂等保护（~110 行），全面采用内置 write_file（已存在报错）+ edit_file（唯一匹配）语义
- 更新 LLM system prompt：write_file 创建新文件、edit_file 修改已存在文件、多处匹配用 replace_all=true、
  grep 用字面量搜索

### 阶段 B（P0）：git 工具用 execute 替代

#### B.1 删除 make_git_tools

[subagents/base.py:96-263](../../../backend/app/subagents/base.py#L96-L263) 整个 `make_git_tools` 函数删除
（~168 行）。git 操作通过 `execute` 完成。

#### B.2 重新设计 git 审批策略

用户选择「用 execute 替代」，需处理审批粒度丢失。方案：在 [command_filter.py](../../../backend/app/security/command_filter.py)
增加 git 写命令检测（`git commit`/`git push`/`git checkout`/`git clone`/`git pull`/`git add`），
命中时返回 exit_code=126 + 提示「需审批」，触发 directory_extension 审批流。

更新 [dangerous_tools.py](../../../backend/app/security/dangerous_tools.py) `DANGEROUS_TOOLS`：
删除 git_* 条目（仅保留 edit_file/write_file）。

#### B.3 更新配置

- [config/subagents.py:64-65](../../../backend/app/config/subagents.py#L64-L65) 删除 git_* 工具开关
- [config/agents.py:42-43](../../../backend/app/config/agents.py#L42-L43) 删除 git_* 工具开关
- `FORBIDDEN_SUBAGENT_TOOLS` 删除 git_* 条目

### 阶段 C（P0）：自研 delete_file 补齐

0.6.12 无内置 delete。在 [tool_assembly.py](../../../backend/app/deepagent/tool_assembly.py) 新增自研
`delete_file`（带 `SessionSandbox.check_write` 授权 + 白名单根目录保护 + `recursive` 参数 +
`shutil.rmtree`）。加入 `DANGEROUS_TOOLS` + `FORBIDDEN_SUBAGENT_TOOLS`。

### 阶段 D（P0）：测试与验证

- `AuthorizedLocalShellBackend` 授权注入测试
- 内置语义测试（write_file 已存在报错、edit_file 多匹配报错、read_file 分片、grep 字面量）
- `delete_file` 测试（文件删除、递归目录、白名单根拒绝、未授权拒绝）
- git via execute 测试（`git status` 放行、`git commit` 命中 command_filter 拦截）
- 审批流回归测试（interrupt_on 对 write_file/edit_file/delete_file 生效）

## Capabilities

### Modified Capabilities

- `deepagents-integration`: 从「排除内置 fs 工具 + 自研」改为「启用内置 fs 工具 + AuthorizedLocalShellBackend 授权注入」
- `filesystem-tools`: 删除 6 个自研 fs 工具，改用 deepagents 0.6.12 内置（能力升级：分片/元数据/ripgrep）
- `git-tools`: 删除 9 个自研 git 工具，改用 execute + command_filter 拦截
- `dangerous-tools`: DANGEROUS_TOOLS 集合精简（删除 git_*，新增 delete_file）

### New Capabilities

- `file-delete`: 自研 delete_file 工具补齐 0.6.12 无内置 delete 的能力缺口

## Impact

- **后端**:
  - 新建 1 文件（`authorized_backend.py`）
  - 修改 ~8 文件（factory.py / tool_assembly.py / base.py / filesystem.py / dangerous_tools.py /
    command_filter.py / config/agents.py / config/subagents.py）
  - 删除 ~568 行自研代码（6 fs 工具 + 幂等保护 + 9 git 工具 + make_fs_tools 工厂）
  - 新增 ~140 行（AuthorizedLocalShellBackend + delete_file + command_filter git 拦截）
  - 净减少 ~430 行
- **前端**: 无改动（工具名不变，SSE 事件契约不变）
- **API**: 无新增端点
- **测试**: 新增 AuthorizedLocalShellBackend + delete_file 测试，更新 fs/git 工具测试
- **依赖**: 无新增（deepagents 保持 0.6.12，不升级）

## Future Extensibility

- 升级到 deepagents 0.7+ 后，评估用内置 `delete` 替换自研 `delete_file`
- 评估 `FilesystemMiddleware(tools=...)` 白名单限制子代理只读（0.7.0a4+ 特性）
- 评估 `fetch_url` 工具补齐（web 子代理能力缺口，本 change 范围外）

## 决策来源

本 proposal 基于本会话的多轮分析：
1. 核查 deepagents 0.6.12 实际内置工具（官方文档 + 本地源码 grep 双重验证）
2. 与用户澄清 3 个关键决策（git 替代策略、grep 语义、write/edit 语义）
3. 对照 `2026-07-08-deepagents-migration` 的 Future P2，确认本 change 为其落地 + 扩展
