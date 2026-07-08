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

# 所有模板的映射，供 generator 使用
TEMPLATES: dict[str, str] = {
    "AGENTS.md": AGENTS_MD_TEMPLATE,
    "mcp.json": MCP_JSON_TEMPLATE,
    "subagents.json": SUBAGENTS_JSON_TEMPLATE,
    "tools.json": TOOLS_JSON_TEMPLATE,
    "system_prompt.md": SYSTEM_PROMPT_TEMPLATE,
    "rules/README.md": RULES_README_TEMPLATE,
}
