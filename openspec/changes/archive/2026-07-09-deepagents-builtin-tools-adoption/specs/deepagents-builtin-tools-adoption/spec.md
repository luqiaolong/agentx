# Spec: deepagents 内置工具采用层（deepagents-builtin-tools-adoption）

## 概述

`backend/app/deepagent/authorized_backend.py` 提供 deepagents 0.6.12 内置 fs 工具的授权注入层。
通过自定义 `AuthorizedLocalShellBackend(SafeLocalShellBackend)`，override fs 方法注入
`SessionSandbox` 动态授权，突破 0.6.12 `FilesystemMiddleware._permissions` 与
`SandboxBackendProtocol` 互斥的限制。删除 6 个自研 fs 工具 + 9 个自研 git 工具，
改用 deepagents 内置 fs 工具 + execute（git 操作）+ 自研 delete_file。

## 包结构

```
backend/app/deepagent/
├── context.py              ← 新建：contextvar 传递 thread_id
├── authorized_backend.py   ← 新建：AuthorizedLocalShellBackend（fs 方法注入授权）
├── factory.py              ← 修改：删除 _EXCLUDED_BUILTIN_TOOLS，resolve_backend 改用 AuthorizedLocalShellBackend
├── tool_assembly.py        ← 修改：删除自研 fs/git 工具注册，新增 delete_file
├── safe_shell_backend.py   ← 修改：execute 新增 git 写命令拦截（调 is_git_write_command）
└── agent.py                ← 修改：agent 入口 set thread_id contextvar

backend/app/sandbox/
└── session_sandbox.py      ← 修改：新增 check_read_sync/check_write_sync

backend/app/security/
├── dangerous_tools.py      ← 修改：DANGEROUS_TOOLS/FORBIDDEN_SUBAGENT_TOOLS 删 git_*，增 delete_file
└── command_filter.py       ← 修改：新增 git 写命令拦截

backend/app/tools/
└── filesystem.py           ← 修改：删除 6 个自研 fs 工具 + 幂等保护，保留 list_workspace/read_workspace_file

backend/app/subagents/
└── base.py                 ← 修改：删除 make_fs_tools + make_git_tools

backend/app/config/
├── agents.py               ← 修改：删除 git_* 工具开关
└── subagents.py            ← 修改：删除 git_* 工具开关
```

## 公共 API

### `context.py`

```python
import contextvars

# 在 agent 入口设置，backend 方法内读取
current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "thread_id", default=""
)
```

### `authorized_backend.py`

```python
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import (
    ReadResult, WriteResult, EditResult, LsResult, GrepResult, GlobResult,
)
from app.deepagent.safe_shell_backend import SafeLocalShellBackend
from app.deepagent.context import current_thread_id
from app.sandbox.session_sandbox import SessionSandbox


class AuthorizedLocalShellBackend(SafeLocalShellBackend):
    """在 fs 操作前注入 SessionSandbox 动态授权。

    继承 SafeLocalShellBackend（保留 execute 的 blocklist + 元字符过滤），
    override 6 个 fs 方法注入 thread_id 级动态授权。
    不传 _permissions（绕过 0.6.12 permissions+sandbox 互斥限制）。

    继承链（已核实 deepagents 0.6.12 源码）：
        AuthorizedLocalShellBackend(SafeLocalShellBackend)
          └── SafeLocalShellBackend(LocalShellBackend)  ← execute 已有安全过滤
              └── LocalShellBackend(FilesystemBackend, SandboxBackendProtocol)
                  ├── ls/read/write/edit/glob/grep  ← 来自 FilesystemBackend（BackendProtocol 定义）
                  └── execute  ← 来自 SandboxBackendProtocol，SafeLocalShellBackend 已 override
    """

    def _check_auth(self, path: str, write: bool) -> None:
        """从 contextvar 取 thread_id，调 SessionSandbox 同步校验。

        Raises:
            PathNotAuthorized: 未授权时抛出，由调用方转为 backend 错误返回值。
        """
        thread_id = current_thread_id.get()
        sandbox = SessionSandbox()
        if write:
            sandbox.check_write_sync(thread_id, path, base=str(self.cwd))
        else:
            sandbox.check_read_sync(thread_id, path, base=str(self.cwd))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读文件（注入授权）。未授权时返回 ReadResult(error=...)。

        签名来源：BackendProtocol.read（protocol.py:381-402）
        默认 limit=2000 行，offset 0-indexed，返回 cat -n 格式带行号。
        """
        try:
            self._check_auth(file_path, write=False)
        except PathNotAuthorized as exc:
            return ReadResult(error=str(exc))
        return super().read(file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """写文件（注入授权）。未授权时返回 WriteResult(error=...)。

        签名来源：BackendProtocol.write（protocol.py:542-558）
        内置语义：已存在文件报错（强制先 read 再 edit）。
        """
        try:
            self._check_auth(file_path, write=True)
        except PathNotAuthorized as exc:
            return WriteResult(error=str(exc))
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """编辑文件（注入授权）。未授权时返回 EditResult(error=...)。

        签名来源：BackendProtocol.edit（protocol.py:568-593）
        内置语义：replace_all=False 时 old_string 必须唯一匹配，多处匹配报错；
                  replace_all=True 时全局替换。
        """
        try:
            self._check_auth(file_path, write=True)
        except PathNotAuthorized as exc:
            return EditResult(error=str(exc))
        return super().edit(file_path, old_string, new_string, replace_all)

    def ls(self, path: str) -> LsResult:
        """列目录（注入授权）。未授权时返回 LsResult(error=...)。

        签名来源：BackendProtocol.ls（protocol.py:353-375）
        返回 LsResult(entries=[FileInfo(path, is_dir, size, modified_at)])。
        """
        try:
            self._check_auth(path, write=False)
        except PathNotAuthorized as exc:
            return LsResult(error=str(exc))
        return super().ls(path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        """内容检索（注入授权）。未授权时返回 GrepResult(error=...)。

        签名来源：BackendProtocol.grep（protocol.py:413-470）
        内置语义：字面量搜索（NOT regex，protocol 注释明确）。
        """
        try:
            self._check_auth(path or str(self.cwd), write=False)
        except PathNotAuthorized as exc:
            return GrepResult(error=str(exc))
        return super().grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """文件名匹配（注入授权）。未授权时返回 GlobResult(error=...)。

        签名来源：BackendProtocol.glob（protocol.py:501-536）
        """
        try:
            self._check_auth(path or str(self.cwd), write=False)
        except PathNotAuthorized as exc:
            return GlobResult(error=str(exc))
        return super().glob(pattern, path)

    # execute 不 override：继承 SafeLocalShellBackend.execute（blocklist + 元字符过滤）
```

### `session_sandbox.py` 变更

```python
# 新增（同步版，复用核心检查逻辑，不加 asyncio.Lock）
def check_read_sync(
    self, thread_id: str, path: str, base: str | None = None,
    parent_thread_id: str | None = None,
) -> None:
    """同步版 check_read。

    复用 check_read 的 resolved/is_critical/whitelist/_authorized_dirs 检查，
    仅省去 asyncio.Lock（授权目录变更不频繁，dict 读操作 GIL 保护）。

    Raises:
        PathNotAuthorized: 未授权时抛出。
    """

def check_write_sync(
    self, thread_id: str, path: str, base: str | None = None,
    parent_thread_id: str | None = None,
) -> None:
    """同步版 check_write。语义同 check_read_sync。"""
```

### `tool_assembly.py` 变更

```python
# 删除
# - _make_fs_tools 调用
# - _make_deep_tools 中的 write_file/edit_file 注册
# - make_git_tools 调用
# - _TOOL_NAME_MAP 中 glob/grep 映射

# 新增 delete_file
from langchain_core.tools import tool
import shutil

@tool
async def delete_file(path: str, recursive: bool = False) -> str:
    """删除文件或目录。

    Args:
        path: 文件或目录路径。
        recursive: True 时递归删除目录；False 时仅删文件，遇目录返回错误。

    Returns:
        成功返回确认消息，失败返回错误描述字符串。
    """
    thread_id = current_thread_id.get()
    sandbox = SessionSandbox()
    await sandbox.check_write(thread_id, path, base=workspace_path)

    # 白名单根目录禁止删（data/workspace, data/uploads 本身）
    full_path = Path(workspace_path) / path
    _GUARDED_ROOTS = {"data/workspace", "data/uploads"}
    if any(str(full_path) == str(Path(workspace_path) / root) for root in _GUARDED_ROOTS):
        return f"错误：禁止删除沙箱根目录 {path}"

    if full_path.is_dir():
        if not recursive:
            return f"错误：{path} 是目录，需 recursive=True 才能删除"
        try:
            shutil.rmtree(full_path)
            return f"已递归删除目录 {path}"
        except OSError as exc:
            return f"删除目录失败：{exc}"
    else:
        try:
            full_path.unlink()
            return f"已删除文件 {path}"
        except OSError as exc:
            return f"删除文件失败：{exc}"
```

### `command_filter.py` 变更

```python
import shlex

# git 写命令集合（命中时由 SafeLocalShellBackend.execute 返回 exit_code=126）
_GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset({
    "commit", "push", "checkout", "clone", "pull", "add",
})

def is_git_write_command(command: str) -> bool:
    """检测是否为 git 写命令。

    用 shlex.split 解析，检查首 token 是 git 且第二 token 在写命令集合中。
    防御包含这些子串的非 git 命令（如 `echo "git commit"`）。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    return tokens[1] in _GIT_WRITE_SUBCOMMANDS
```

### `safe_shell_backend.py` 变更

在 `SafeLocalShellBackend.execute` 的 blocklist 检查之后、元字符过滤之后，
新增 git 写命令拦截：

```python
from app.security.command_filter import has_forbidden_args, is_command_blocked, is_git_write_command

class SafeLocalShellBackend(LocalShellBackend):
    def execute(self, command: str, **kwargs) -> ExecuteResponse:
        # ... 现有 blocklist + 元字符过滤 ...
        
        # 新增：git 写命令拦截（exit_code=126 + 提示需审批）
        if is_git_write_command(command):
            return ExecuteResponse(
                output="git 写操作需审批，请通过审批流授权",
                exit_code=126,
                truncated=False,
            )
        
        return super().execute(command, **kwargs)
```

**注意**：`AuthorizedLocalShellBackend` 继承 `SafeLocalShellBackend`，
因此 git 拦截逻辑自动继承，无需在 `AuthorizedLocalShellBackend` 重复。

### `dangerous_tools.py` 变更

```python
# DANGEROUS_TOOLS：删除 git_*，新增 delete_file
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "edit_file",
        "write_file",
        "delete_file",  # 新增
    }
)

# FORBIDDEN_SUBAGENT_TOOLS：删除 git_*，新增 delete_file
FORBIDDEN_SUBAGENT_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "delete_file",  # 新增
        "execute",
        "cli_execute",  # 旧名保留过滤
    }
)
```

### `filesystem.py` 变更

```python
# 删除（改用 deepagents 内置）
async def read_file(...): ...
async def list_dir(...): ...
async def glob(...): ...
async def grep(...): ...
async def write_file(...): ...
async def edit_file(...): ...

# 删除（幂等保护逻辑）
_recent_writes: dict = ...
_content_hash = ...
def _gc_recent_writes(...): ...

# 保留（前端 API 用，非 LLM 工具）
def list_workspace(...): ...
def read_workspace_file(...): ...
```

### `subagents/base.py` 变更

```python
# 删除
def make_fs_tools(thread_id: str) -> list: ...
def _make_fs_tools(...): ...
def make_git_tools(thread_id: str) -> list: ...

# 保留
def make_rag_tools(thread_id: str) -> list: ...  # 内置无对应
def make_web_tools(thread_id: str) -> list: ...   # 内置无对应（Tavily SDK）
```

## 行为规格

### 工具冲突管理（更新）

| deepagents 内置工具 | 处理方式 | 理由 |
|---|---|---|
| `write_todos` | **保留** | 项目缺失的结构化规划能力（2026-07-08-migration 已启用） |
| `ls` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 list_dir（能力升级：元数据） |
| `read_file` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 read_file（能力升级：分片+行号+多模态） |
| `write_file` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 write_file（采用内置语义：不覆盖） |
| `edit_file` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 edit_file（采用内置语义：唯一匹配+replace_all） |
| `glob` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 glob_files（ripgrep 引擎） |
| `grep` | **启用**（AuthorizedLocalShellBackend 注入授权） | 替代自研 grep_files（ripgrep+三态输出，字面量搜索） |
| `execute` | **保留**（SafeLocalShellBackend 已用） | shell 命令执行（含 git 操作） |
| `task` | **harness profile 禁用** | 项目有 delegate_to_expert（三层架构+安全约束） |
| `delete` | ❌ 0.6.12 无 | 自研 delete_file 补齐 |

### 授权注入流程

```
LLM 调用 read_file(path="src/main.py")
  → AuthorizedLocalShellBackend.read("src/main.py")
    → _check_auth("src/main.py", write=False)
      → current_thread_id.get() → "session-abc"
      → SessionSandbox.check_read_sync("session-abc", "src/main.py", base=workspace)
        → 路径解析 + 白名单检查 + _authorized_dirs 查询
        → 授权通过 → return None
      → super().read("src/main.py") → FilesystemBackend.read
        → 返回 ReadResult(content="...", line_numbers=True)
    → 未授权 → PathNotAuthorized → ReadResult(error="路径未授权")
```

### git 命令执行流程

```
LLM 调用 execute(command="git status")
  → SafeLocalShellBackend.execute("git status")
    → command_filter 检查：_is_git_write_command("git status") → False（status 不在写集合）
    → blocklist + 元字符过滤 → 通过
    → subprocess.run("git status") → 返回 ExecuteResult(stdout=...)

LLM 调用 execute(command="git commit -m 'fix'")
  → SafeLocalShellBackend.execute("git commit -m 'fix'")
    → command_filter 检查：_is_git_write_command("git commit -m 'fix'") → True
    → 返回 ExecuteResult(exit_code=126, stderr="git 写操作需审批")
  → 触发 directory_extension 审批流（workspace 之外未授权时）
```

### delete_file 行为

```
LLM 调用 delete_file(path="temp/old.log", recursive=False)
  → SessionSandbox.check_write(thread_id, "temp/old.log") → 授权
  → 白名单根检查：非 data/workspace/data/uploads 本身 → 通过
  → full_path.is_dir() → False
  → full_path.unlink() → "已删除文件 temp/old.log"

LLM 调用 delete_file(path="temp/old_dir", recursive=True)
  → SessionSandbox.check_write → 授权
  → 白名单根检查 → 通过
  → full_path.is_dir() → True，recursive=True
  → shutil.rmtree(full_path) → "已递归删除目录 temp/old_dir"

LLM 调用 delete_file(path="data/workspace", recursive=True)
  → 白名单根检查：命中 _GUARDED_ROOTS → "错误：禁止删除沙箱根目录 data/workspace"

LLM 调用 delete_file(path="/etc/passwd")
  → SessionSandbox.check_write → PathNotAuthorized → 返回错误字符串
```

## 兼容性约束

1. **SSE 事件契约不变**：tool_call/tool_result/approval_request 事件格式不变
2. **工具名兼容**：ls/read_file/write_file/edit_file/glob/grep/execute 名称不变
3. **delete_file 新增**：新工具名，前端按通用 tool_call 渲染（无需特殊适配）
4. **checkpointer 兼容**：AuthorizedLocalShellBackend 不影响 checkpointer
5. **SessionSandbox 兼容**：新增 check_*_sync 方法，原有 async check_* 保留
6. **子代理安全约束不变**：FORBIDDEN_SUBAGENT_TOOLS 仍过滤写工具（write_file/edit_file/delete_file/execute）
7. **deepagents 版本不变**：锁定 0.6.12，不升级
8. **delegate_to_expert 保留**：业务封装，不适合作为 compiled subagent
9. **rag_retrieve / web_search 保留**：内置无对应，Tavily SDK 已成熟

## 与 2026-07-08-deepagents-migration 的衔接

| 2026-07-08 P0/P1（已完成） | 本 change |
|---|---|
| `_EXCLUDED_BUILTIN_TOOLS` 排除 6 个内置 fs 工具 | **删除** _EXCLUDED_BUILTIN_TOOLS，启用内置 fs 工具 |
| 自研 read_file/list_dir/glob/grep/write_file/edit_file | **删除**，改用内置（AuthorizedLocalShellBackend 注入授权） |
| 自研 git_* 工具（9 个） | **删除**，改用 execute + command_filter 拦截 |
| DANGEROUS_TOOLS 含 git_* | **精简**：删除 git_*，新增 delete_file |
| 无 delete 能力 | **新增** delete_file 自研工具 |
| write_file 覆盖+幂等保护 | **采用内置语义**：不覆盖，删除幂等保护 |
| edit_file 首次匹配 | **采用内置语义**：唯一匹配 + replace_all |
| grep 正则匹配 | **采用内置语义**：字面量搜索 |
