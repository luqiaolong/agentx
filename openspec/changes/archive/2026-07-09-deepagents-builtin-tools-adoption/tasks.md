# 任务追踪 — 采用 deepagents 0.6.12 内置工具替代自研 fs/git 工具

## 阶段 A：fs 工具迁移（用内置替代自研）

### A.0 基础设施

- [ ] A.0.1 新建 `backend/app/deepagent/context.py`：`current_thread_id: contextvars.ContextVar[str]`
- [ ] A.0.2 [session_sandbox.py](../../../backend/app/sandbox/session_sandbox.py) 新增
      `check_read_sync`/`check_write_sync` 同步方法（复用核心检查逻辑，不加 asyncio.Lock）
- [ ] A.0.3 新建 `backend/app/deepagent/authorized_backend.py`：
      `AuthorizedLocalShellBackend(SafeLocalShellBackend)`，override 6 个 fs 方法
      （ls/read/write/edit/glob/grep）注入 SessionSandbox 动态授权

### A.1 启用内置 fs 工具

- [ ] A.1.1 [factory.py:46-50](../../../backend/app/deepagent/factory.py#L46-L50) 删除
      `_EXCLUDED_BUILTIN_TOOLS`（全部启用内置 fs 工具）
- [ ] A.1.2 [factory.py:123-138](../../../backend/app/deepagent/factory.py#L123-L138)
      `resolve_backend` 改用 `AuthorizedLocalShellBackend(root_dir=workspace_path, virtual_mode=True)`
- [ ] A.1.3 agent 入口（run_deep_path / run_work_supervisor / run_coding_expert / run_coding_team）
      invoke 前 `current_thread_id.set(thread_id)`

### A.2 删除自研 fs 工具

- [ ] A.2.1 [subagents/base.py:49-93](../../../backend/app/subagents/base.py#L49-L93)
      `make_fs_tools` / `_make_fs_tools`：删除
- [ ] A.2.2 [tool_assembly.py:67-80](../../../backend/app/deepagent/tool_assembly.py#L67-L80)
      `_make_deep_tools` 中的 write_file/edit_file：删除
- [ ] A.2.3 [filesystem.py](../../../backend/app/tools/filesystem.py) read_file/list_dir/glob/grep/
      write_file/edit_file：删除
- [ ] A.2.4 [filesystem.py](../../../backend/app/tools/filesystem.py) 幂等保护逻辑
      （`_recent_writes`/`_content_hash`/`_gc_recent_writes`）：删除
- [ ] A.2.5 保留 `list_workspace`/`read_workspace_file`（前端 API 用）
- [ ] A.2.6 [tool_assembly.py:61](../../../backend/app/deepagent/tool_assembly.py#L61)
      `_make_fs_tools` 调用：删除；`_TOOL_NAME_MAP` 中 glob/grep 映射：删除

### A.3 适配内置语义

- [ ] A.3.1 更新 LLM system prompt（deep/work/coding/team）：引导 write_file 创建新文件、
      edit_file 修改已存在文件、多处匹配用 replace_all=true、grep 用字面量搜索

### A.4 验证

- [ ] A.4.1 验证 `agent.get_graph()` 工具列表含 ls/read_file/write_file/edit_file/glob/grep
- [ ] A.4.2 验证 AuthorizedLocalShellBackend 授权注入：未授权路径被拒、授权路径放行、
      白名单路径放行

## 阶段 B：git 工具用 execute 替代（与 A 并行）

### B.1 删除 make_git_tools

- [ ] B.1.1 [subagents/base.py:96-263](../../../backend/app/subagents/base.py#L96-L263)
      整个 `make_git_tools` 函数删除
- [ ] B.1.2 [tool_assembly.py](../../../backend/app/deepagent/tool_assembly.py) 删除
      `make_git_tools` 调用

### B.2 git 写命令拦截

- [ ] B.2.1 [command_filter.py](../../../backend/app/security/command_filter.py) 新增
      `is_git_write_command(command)` 检测函数 + `_GIT_WRITE_SUBCOMMANDS` 集合：
      用 `shlex.split` 解析，检查首 token 是 `git` 且第二 token 在
      `{commit, push, checkout, clone, pull, add}` 中
- [ ] B.2.2 [safe_shell_backend.py](../../../backend/app/deepagent/safe_shell_backend.py)
      `SafeLocalShellBackend.execute` 新增 git 写命令拦截：
      在 blocklist + 元字符过滤之后，调 `is_git_write_command`，
      命中返回 `ExecuteResponse(output="git 写操作需审批...", exit_code=126, truncated=False)`

### B.3 更新配置

- [ ] B.3.1 [dangerous_tools.py](../../../backend/app/security/dangerous_tools.py)
      `DANGEROUS_TOOLS` 删除 git_* 条目（仅保留 edit_file/write_file）
- [ ] B.3.2 [dangerous_tools.py](../../../backend/app/security/dangerous_tools.py)
      `FORBIDDEN_SUBAGENT_TOOLS` 删除 git_* 条目
- [ ] B.3.3 [config/subagents.py:64-65](../../../backend/app/config/subagents.py#L64-L65)
      删除 git_* 工具开关
- [ ] B.3.4 [config/agents.py:42-43](../../../backend/app/config/agents.py#L42-L43)
      删除 git_* 工具开关

### B.4 验证

- [ ] B.4.1 验证 `git status`/`git diff`/`git log`/`git branch` 放行（只读不拦截）
- [ ] B.4.2 验证 `git commit`/`git clone`/`git pull`/`git checkout`/`git add`/`git push`
      命中 command_filter 拦截（exit_code=126 + 提示需审批）

## 阶段 C：自研 delete_file 补齐（依赖 A）

### C.1 实现 delete_file

- [ ] C.1.1 [tool_assembly.py](../../../backend/app/deepagent/tool_assembly.py) 新增自研 `delete_file`：
      `@tool async def delete_file(path: str, recursive: bool = False) -> str`
- [ ] C.1.2 授权：从 contextvar 取 thread_id，调 `SessionSandbox.check_write`
- [ ] C.1.3 安全护栏：禁止删除沙箱白名单根目录（data/workspace, data/uploads 本身），
      仅允许删其下内容
- [ ] C.1.4 `recursive=False`：仅删文件，遇目录返回错误字符串
- [ ] C.1.5 `recursive=True`：用 `shutil.rmtree` 递归删目录，捕获 `OSError` 返回错误字符串

### C.2 审批注册

- [ ] C.2.1 [dangerous_tools.py](../../../backend/app/security/dangerous_tools.py)
      `DANGEROUS_TOOLS` 添加 `"delete_file"`
- [ ] C.2.2 [dangerous_tools.py](../../../backend/app/security/dangerous_tools.py)
      `FORBIDDEN_SUBAGENT_TOOLS` 添加 `"delete_file"`

### C.3 验证

- [ ] C.3.1 验证 delete_file 文件删除成功
- [ ] C.3.2 验证 delete_file recursive=True 递归删目录
- [ ] C.3.3 验证 delete_file 白名单根目录拒绝
- [ ] C.3.4 验证 delete_file 未授权拒绝
- [ ] C.3.5 验证 delete_file 触发 interrupt_on 审批

## 阶段 D：测试与验证

### D.1 单元测试

- [ ] D.1.1 新建 `tests/python/unit/test_authorized_backend.py`：
      AuthorizedLocalShellBackend 授权注入测试（未授权拒绝、授权放行、白名单放行）
- [ ] D.1.2 新建 `tests/python/unit/test_delete_file.py`：
      delete_file 测试（文件删除、递归目录、白名单根拒绝、未授权拒绝、recursive=False 遇目录报错）
- [ ] D.1.3 扩展 `tests/python/unit/test_security_command_filter.py`：
      git 写命令拦截测试（commit/clone/pull/checkout/add/push 拦截，status/diff/log/branch 放行）

### D.2 内置语义测试

- [ ] D.2.1 新建 `tests/python/unit/test_builtin_fs_semantics.py`：
      write_file 已存在报错、edit_file 多匹配报错、edit_file replace_all、read_file 分片、
      grep 字面量搜索

### D.3 回归测试

- [ ] D.3.1 扩展 `tests/python/unit/test_deep_approval.py`：
      验证 interrupt_on 对 write_file/edit_file/delete_file 生效
- [ ] D.3.2 验证子代理工具集不含 write_file/edit_file/delete_file/execute
      （FORBIDDEN_SUBAGENT_TOOLS 过滤）
- [ ] D.3.3 更新现有 fs/git 工具测试（删除自研工具测试，适配内置工具）

### D.4 全量验证

- [ ] D.4.1 `uv run pytest tests/python/unit -m "not integration"` 全绿
- [ ] D.4.2 `uv run ruff check backend/app/deepagent/ backend/app/tools/ backend/app/security/ backend/app/subagents/` 无新增错误

## 预期修改文件

### 后端新建文件
- `backend/app/deepagent/context.py` — contextvar 传递 thread_id
- `backend/app/deepagent/authorized_backend.py` — AuthorizedLocalShellBackend

### 后端修改文件
- `backend/app/deepagent/factory.py` — 删除 _EXCLUDED_BUILTIN_TOOLS，resolve_backend 改用 AuthorizedLocalShellBackend
- `backend/app/deepagent/tool_assembly.py` — 删除 write_file/edit_file/make_fs_tools/make_git_tools 调用，新增 delete_file
- `backend/app/deepagent/safe_shell_backend.py` — execute 新增 git 写命令拦截（调 is_git_write_command）
- `backend/app/deepagent/agent.py` — agent 入口 set thread_id contextvar
- `backend/app/scenarios/work/agent.py` — agent 入口 set thread_id contextvar
- `backend/app/scenarios/coding/agent.py` — agent 入口 set thread_id contextvar
- `backend/app/scenarios/coding_team/agent.py` — agent 入口 set thread_id contextvar
- `backend/app/subagents/base.py` — 删除 make_fs_tools + make_git_tools
- `backend/app/tools/filesystem.py` — 删除 6 个自研 fs 工具 + 幂等保护，保留 list_workspace/read_workspace_file
- `backend/app/sandbox/session_sandbox.py` — 新增 check_read_sync/check_write_sync
- `backend/app/security/dangerous_tools.py` — DANGEROUS_TOOLS/FORBIDDEN_SUBAGENT_TOOLS 删 git_*，增 delete_file
- `backend/app/security/command_filter.py` — 新增 is_git_write_command 检测函数 + _GIT_WRITE_SUBCOMMANDS
- `backend/app/config/agents.py` — 删除 git_* 工具开关
- `backend/app/config/subagents.py` — 删除 git_* 工具开关

### 后端测试文件
- `tests/python/unit/test_authorized_backend.py` — 新增
- `tests/python/unit/test_delete_file.py` — 新增
- `tests/python/unit/test_builtin_fs_semantics.py` — 新增
- `tests/python/unit/test_security_command_filter.py` — 扩展 git 拦截测试
- `tests/python/unit/test_deep_approval.py` — 扩展 interrupt_on 回归
- `tests/python/unit/test_filesystem_tools.py` — 删除/适配（自研工具已删）
- `tests/python/unit/test_git_tools.py` — 删除/适配（自研工具已删）

## 规模判定

- 涉及文件数: ~20（2 新建 + 12 修改 + 6 测试）
- 涉及模块数: 6（deepagent/ + tools/ + subagents/ + security/ + sandbox/ + config/）
- 规模: **L（大改）** — 5+ 文件且跨模块，需全流程执行

## OpenSpec Tasks

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| A.0 | AuthorizedLocalShellBackend 基础设施 | context.py(新), authorized_backend.py(新), session_sandbox.py | check_*_sync 可用，fs 方法注入授权 | ⏳ |
| A.1 | 启用内置 fs 工具 | factory.py, agent 入口 | 工具列表含 ls/read/write/edit/glob/grep | ⏳ |
| A.2 | 删除自研 fs 工具 | base.py, tool_assembly.py, filesystem.py | 自研 fs 函数删除，幂等保护删除 | ⏳ |
| A.3 | 适配内置语义 | system prompts | LLM 引导新行为 | ⏳ |
| B.1 | 删除 make_git_tools | base.py, tool_assembly.py | make_git_tools 删除 | ⏳ |
| B.2 | command_filter git 拦截 | command_filter.py | git 写命令 exit_code=126 | ⏳ |
| B.3 | 更新配置 | dangerous_tools.py, config/agents.py, config/subagents.py | git_* 开关删除 | ⏳ |
| C.1 | 自研 delete_file | tool_assembly.py | delete_file 可用 | ⏳ |
| C.2 | delete_file 审批注册 | dangerous_tools.py | DANGEROUS_TOOLS 含 delete_file | ⏳ |
| D.1-D.4 | 测试与验证 | tests/ | pytest 全绿，ruff 无错 | ⏳ |
