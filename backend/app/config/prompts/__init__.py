"""专家角色 prompt 聚合导出。

- ``builtin``：内置子代理（code/rag/web）
- ``team``：软件开发专家团（7 角色）
"""

from app.config.prompts.builtin import *  # noqa: F401, F403
from app.config.prompts.team import *  # noqa: F401, F403
