"""AgentX CLI 包。

与 ``app.team`` 包同构，每个文件单一职责：
- ``app``：主入口 + argparse + 模式分发
- ``repl``：REPL 交互循环
- ``one_shot``：One-shot 单次任务
- ``approval``：终端审批交互
- ``commands``：CommandResult + REPL 命令分发与实现
- ``renderer``：SSE 事件终端渲染器
- ``store``：Tauri store 配置读取 + 凭证解密
"""

from app.cli.app import _build_eval_parser, _build_parser, main

__all__ = ["main", "_build_parser", "_build_eval_parser"]
