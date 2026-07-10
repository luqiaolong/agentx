"""``.agentx/`` 目录各文件的模板内容。

模板设计原则：
- Markdown 文件（AGENTS.md / system_prompt.md / rules/README.md）用引导式结构，
  含 HTML 注释 ``<!-- -->`` 占位，用户填充后自然变成正文。
- JSON 文件（mcp.json / subagents.json / tools.json）用空集合 + 注释说明格式。
- 不含任何凭证字段（安全红线）。
"""

from __future__ import annotations

# AGENTS.md 模板：项目级 AI 规则
AGENTS_MD_TEMPLATE = """\
# Project AGENTS.md

> 本文件是项目级 AI 规则，会自动注入到所有 agent 的上下文。
> 编辑此文件为你的项目添加专属指令。

## 项目简介
<!-- 一句话描述这个项目是做什么的 -->

## 技术栈
<!-- 列出项目使用的语言、框架、工具 -->

## 编码规范
<!-- 列出项目特有的编码规范，例如：
- 使用 4 空格缩进
- 函数名用 snake_case
- 必须写类型注解
-->

## 禁止事项
<!-- 列出 AI 不应该做的事，例如：
- 不要修改 config/ 目录下的文件
- 不要引入新的第三方依赖
-->

## 项目结构
<!-- 描述关键目录结构，帮助 AI 理解项目布局 -->
"""

# mcp.json 模板：项目级 MCP servers
# JSON 不支持注释，用空数组 + 文档说明
MCP_JSON_TEMPLATE = """\
[]
"""

# subagents.json 模板：项目级子代理配置覆盖
SUBAGENTS_JSON_TEMPLATE = """\
{}
"""

# tools.json 模板：项目级工具开关覆盖
TOOLS_JSON_TEMPLATE = """\
{}
"""

# system_prompt.md 模板：项目级系统提示词
SYSTEM_PROMPT_TEMPLATE = """\
# 项目级系统提示词

> 本文件内容会前置到默认系统提示词之前，作为项目专属指令。
> 编辑此文件添加你希望 AI 在本项目遵循的额外指令。
"""

# rules/README.md 模板：说明如何添加规则文件
RULES_README_TEMPLATE = """\
# Rules 目录

本目录下的 ``*.md`` 文件会自动加载为 AI 上下文（按文件名排序，最多 10 个文件）。

## 如何添加规则

1. 在本目录下新建 ``.md`` 文件，如 ``coding-style.md``
2. 写入你的规则内容
3. 下次发送消息时自动生效（无需重启）

## 示例

```markdown
# 编码风格规则

- 使用 TypeScript 严格模式
- 优先使用函数式组件
- 禁止使用 any 类型
```
"""

# skills/README.md 模板：说明如何添加技能文件
SKILLS_README_TEMPLATE = """\
# Skills 目录

本目录用于存放项目级 Agent Skills，遵循 agentskills.io 规范。

## 目录结构

每个技能是一个独立子目录，包含 ``SKILL.md`` 文件：

```text
skills/
├── my-skill/
│   ├── SKILL.md          # 必需：YAML frontmatter + Markdown 指令
│   ├── scripts/            # 可选：可执行脚本
│   ├── references/         # 可选：参考文档
│   └── assets/             # 可选：静态资源
└── another-skill/
    └── SKILL.md
```

## SKILL.md 格式

```markdown
---
name: my-skill
description: 简短描述技能用途和触发时机
---

# My Skill

## When to Use

- 当用户要求...时使用

## Instructions

- 步骤一
- 步骤二
```

## 使用方式

1. 在输入框中通过 ``@skill:<name>`` 显式触发（如 ``@skill:my-skill``）
2. 技能内容会自动注入到当前会话的 system prompt 中
"""

# memory/README.md 模板：说明如何添加工作区记忆文件
MEMORY_README_TEMPLATE = """\
# Memory 目录

本目录用于存放工作区记忆（Workspace Memory），每个记忆条目是一个独立的 ``.md`` 文件。
这些文件会自动加载到 AI 的上下文中，帮助 AI 理解项目背景、技术栈、用户偏好等。

## 文件格式

每个 ``.md`` 文件包含 YAML frontmatter + Markdown 内容：

```markdown
---
key: workspace_tech
category: project
source: manual
updated_at: "2026-07-10T16:00:00+00:00"
---

项目使用 FastAPI + React + Tauri 2.x 技术栈，Python 版本 >= 3.11。
```

## 字段说明

- ``key``: 条目唯一标识（字母、数字、下划线、连字符，1-64 字符）
- ``category``: 分类，可选 ``project`` / ``preference`` / ``fact`` / ``custom``
- ``source``: 来源，``manual``（手动）或 ``llm_extracted``（LLM 自动抽取）
- ``updated_at``: 最后更新时间（ISO 8601 格式）

## 使用方式

1. 在前端「设置 → 记忆 → 工作区记忆」中新增/编辑/删除条目
2. 或手动在本目录下创建 ``.md`` 文件
3. 下次发送消息时自动生效（无需重启）
"""

# 所有模板的映射，供 generator 使用
TEMPLATES: dict[str, str] = {
    "AGENTS.md": AGENTS_MD_TEMPLATE,
    "mcp.json": MCP_JSON_TEMPLATE,
    "subagents.json": SUBAGENTS_JSON_TEMPLATE,
    "tools.json": TOOLS_JSON_TEMPLATE,
    "system_prompt.md": SYSTEM_PROMPT_TEMPLATE,
    "rules/README.md": RULES_README_TEMPLATE,
    "skills/README.md": SKILLS_README_TEMPLATE,
    "memory/README.md": MEMORY_README_TEMPLATE,
}


def _derive_top_level_names() -> list[str]:
    """从 TEMPLATES 键推导 ``.agentx/`` 顶级条目名（保留首次出现顺序）。

    嵌套路径（如 ``rules/README.md``）取顶级目录名（``rules``），
    供 UI 状态展示用（展示目录而非目录内单个文件）。
    """
    seen: list[str] = []
    for key in TEMPLATES:
        top = key.split("/", 1)[0]
        if top not in seen:
            seen.append(top)
    return seen


# .agentx/ 目录下供 UI 状态展示的顶级条目（文件 + 目录）
# 自动与 TEMPLATES 同步：新增模板键即自动出现在此列表
TEMPLATE_FILE_NAMES: list[str] = _derive_top_level_names()
