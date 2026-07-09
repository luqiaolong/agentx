r"""SafeLocalShellBackend: 继承 deepagents LocalShellBackend，添加 blocklist + 元字符过滤。

deepagents 的 ``LocalShellBackend`` 提供 ``execute`` 工具用 ``subprocess.run(shell=True)``
执行命令——无 blocklist、无元字符过滤。本模块继承并 override ``execute`` 方法，
复用 ``app.security.command_filter`` 的安全层：

- **blocklist**: ``DEFAULT_BLOCKLIST``（rm/del/format/shutdown/sudo...）+ 用户配置合并
- **元字符过滤**: ``FORBIDDEN_ARG_PATTERN`` 拦截 ``; & | ` $ < >``，阻断 shell 注入
- **root_dir**: 限制工作目录（由父类 ``LocalShellBackend.__init__`` 处理）
- **审批**: ``execute`` 不再属于 ``DANGEROUS_TOOLS``，其审批通过 ``directory_extension``
  机制处理（workspace 之外未授权时触发审批）

虽然 ``shell=True`` 允许管道/重定向/命令链，但元字符过滤使注入面收窄到与
``shell=False`` 相近——所有 shell 元字符在到达 ``subprocess.run`` 前被拦截。
"""

from __future__ import annotations

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from app.security.command_filter import has_forbidden_args, is_command_blocked

__all__ = ["SafeLocalShellBackend"]


class SafeLocalShellBackend(LocalShellBackend):
    """LocalShellBackend with blocklist + metachar filtering.

    继承 ``LocalShellBackend`` 并 override ``execute`` 方法，在调用父类执行前：
    1. 提取命令名，检查是否在 blocklist 中
    2. 检查整个命令字符串是否包含 shell 元字符

    安全层：
    - blocklist: 复用 ``app.security.command_filter.DEFAULT_BLOCKLIST`` + 用户配置
    - 元字符过滤: 复用 ``has_forbidden_args``，拦截 ``; & | ` $ < >``
    - root_dir: 父类 ``LocalShellBackend`` 限制工作目录
    - 审批: execute 不再属于 DANGEROUS_TOOLS；其审批通过 directory_extension
      机制处理（workspace 之外未授权时触发审批）。

    契约：
    - ``execute`` 必须返回 ``ExecuteResponse``，下游 ``FilesystemMiddleware.async_execute``
      在 ``result.output`` / ``result.exit_code`` 上直接属性访问（参见
      ``deepagents/middleware/filesystem.py:1839``）。安全拦截分支不能返回 ``str``，
      否则触发 ``AttributeError: 'str' object has no attribute 'output'``，并导致
      ``run_agent_with_approval`` 的初始流崩溃（trace 案例 ``ace5a9740dd543bd``）。
    """

    def execute(self, command: str, **kwargs) -> ExecuteResponse:
        """执行 shell 命令，带 blocklist + 元字符过滤。

        Args:
            command: 完整命令字符串（``shell=True`` 模式）。
            **kwargs: 透传给父类 ``LocalShellBackend.execute`` 的额外参数。

        Returns:
            父类或安全拦截均返回 ``ExecuteResponse``：
            - 父类返回：含 stdout/stderr/exit_code/truncated。
            - 空命令：``ExecuteResponse(output="command 不能为空", exit_code=1, truncated=False)``
            - blocklist 命中：``ExecuteResponse(output=..., exit_code=126, truncated=False)``
            - 元字符过滤命中：``ExecuteResponse(output=..., exit_code=126, truncated=False)``

        Raises:
            ValueError: 透传父类在非法 timeout 时的异常。
        """
        command = command.strip()
        if not command:
            return ExecuteResponse(
                output="command 不能为空",
                exit_code=1,
                truncated=False,
            )

        # 1. 提取命令名（shell=True 下 command 是完整命令字符串，取第一个 token）
        cmd_name = command.split()[0] if command else ""

        # 2. blocklist 检查（exit_code=126 沿用 shell "command cannot execute" 语义）
        if is_command_blocked(cmd_name):
            return ExecuteResponse(
                output=(
                    f"命令 '{cmd_name}' 在黑名单中，禁止执行"
                    "（删除/格式化/提权等极度危险操作）"
                ),
                exit_code=126,
                truncated=False,
            )

        # 3. 元字符过滤（阻断 shell 注入：; & | ` $ < >）
        if has_forbidden_args(command):
            return ExecuteResponse(
                output=f"命令包含非法 shell 元字符: {command!r}",
                exit_code=126,
                truncated=False,
            )

        # 4. 调用父类执行（root_dir 限制工作目录）
        # 父类 LocalShellBackend.execute 已始终返回 ExecuteResponse，无需再做包装
        return super().execute(command, **kwargs)
