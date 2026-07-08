"""项目级配置目录（``.agentx/``）能力包。

提供 ``.agentx/`` 目录的生成、加载、合并全链路：

- ``templates``：各文件模板内容
- ``generator``：幂等生成 ``.agentx/`` 目录
- ``loader``：从 ``.agentx/`` 加载为 ``ProjectConfig``
- ``merger``：将项目配置合并到全局 ``Settings`` 之上

典型用法::

    from app.project_config import generate_agentx_dir, load_project_config

    # 生成
    result = generate_agentx_dir(Path("/path/to/workspace"))

    # 加载
    config = load_project_config(Path("/path/to/workspace"))
    if config.exists:
        prompt = config.context_prompt  # AGENTS.md + rules 拼接
"""

from app.project_config.generator import GenerationResult, generate_agentx_dir
from app.project_config.loader import ProjectConfig, RuleFile, load_project_config
from app.project_config.merger import MergedConfig, merge_configs

__all__ = [
    "generate_agentx_dir",
    "load_project_config",
    "merge_configs",
    "GenerationResult",
    "ProjectConfig",
    "RuleFile",
    "MergedConfig",
]
