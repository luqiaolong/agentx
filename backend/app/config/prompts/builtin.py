"""内置子代理（rag/web）角色 prompt 与触发条件描述。

场景化架构（Supervisor + Expert）下，code 子代理已被 coding Expert 取代，
本模块仅保留 rag/web 子代理的默认配置。
"""

from __future__ import annotations

__all__ = [
    "_DEFAULT_RAG_SYSTEM_PROMPT",
    "_DEFAULT_WEB_SYSTEM_PROMPT",
    "_DEFAULT_RAG_TRIGGER_DESCRIPTION",
    "_DEFAULT_WEB_TRIGGER_DESCRIPTION",
    "_DEFAULT_RAG_TOOLS",
    "_DEFAULT_WEB_TOOLS",
]


# ---- 子代理默认工具 ----
_DEFAULT_RAG_TOOLS = ["rag_retrieve"]
_DEFAULT_WEB_TOOLS = ["web_search"]


# 内置子代理默认角色定义（合并原 description + system_prompt）
_DEFAULT_RAG_SYSTEM_PROMPT = (
    "你是知识库检索专家。你的职责是帮助用户从向量知识库中检索信息：\n"
    "1. 使用 rag_retrieve 工具检索与用户问题相关的文档片段\n"
    "2. 基于检索结果给出准确、有依据的回答\n"
    "3. 如果检索结果不足，明确告知用户知识库中未找到相关内容\n"
    "4. 引用检索到的文档内容时保持原文含义，不随意扩展\n"
    "5. 优先回答技术文档、API 文档、内部规范等知识库类型的问题"
)
_DEFAULT_WEB_SYSTEM_PROMPT = (
    "你是联网搜索专家。你的职责是帮助用户获取互联网上的实时信息：\n"
    "1. 使用 web_search 工具搜索最新的外部信息\n"
    "2. 回答新闻、资料、技术动态、产品信息等需要实时数据的问题\n"
    "3. 搜索结果需注明信息来源和时间\n"
    "4. 对于时效性强的信息（如版本号、价格、事件），优先使用搜索而非依赖训练数据\n"
    "5. 如果搜索无结果，明确告知用户并建议调整查询词"
)

# 内置子代理默认触发条件描述（短句，供 LLM 语义路由和降级关键词匹配）
_DEFAULT_RAG_TRIGGER_DESCRIPTION = "用户问题需要引用内部知识库、技术文档、API手册、产品规范或历史资料时触发。"
_DEFAULT_WEB_TRIGGER_DESCRIPTION = "用户问题需要获取互联网实时信息、最新新闻、当前版本号、市场价格、事件动态或外部资料时触发。"
