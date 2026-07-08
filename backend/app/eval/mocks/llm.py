"""MockChatModel：从 YAML fixture 加载预录响应的离线 LLM 替身。

继承 ``langchain_core.BaseChatModel``，可被 langchain / langgraph 框架当作普通
ChatModel 使用（bind_tools / invoke / ainvoke / with_structured_output 等协议由基类提供）。

工作原理：
1. ``from_fixtures(dir)`` 遍历目录下所有 ``*.yaml``，每文件为 ``list[dict]``，
   解析为 ``MockFixture`` 列表
2. ``_generate`` 取 messages 中最后一条 HumanMessage，按 ``fixture.match`` 子串
   匹配；命中第一个返回其 ``response`` 与 ``tool_calls``
3. 未命中任何 fixture 时返回 ``default_response``（无 tool_calls）
4. ``with_structured_output(schema)`` 返回 ``_MockStructuredOutput`` Runnable，
   按 ``grader_responses`` 队列顺序返回 ``GraderResponse`` 兼容的 dict
   （用于 L2 RubricJudge 与 L3 SelfCorrectionRunner 的 grader mock）

设计目标：零外部依赖（不连 LLM），用于 CI 与离线评测。见 design.md §Phase 3。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field


class MockFixture(BaseModel):
    """单条预录响应：按 match 子串命中后返回 response + tool_calls。"""

    match: str
    response: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class _MockStructuredOutput:
    """``MockChatModel.with_structured_output`` 返回的 Runnable。

    每次 ``ainvoke`` / ``invoke`` 从绑定的 ``MockChatModel.grader_responses`` 队列
    弹出首个元素作为 ``structured_response`` 返回。队列为空时返回默认 satisfied
    响应（避免测试因队列耗尽报错）。

    返回值与 ``langchain`` 的 ``with_structured_output`` 一致：直接返回 dict
    （``GraderResponse.model_validate`` 兼容的 ``{"result": ..., "explanation": ..., "criteria": ...}``）。
    """

    def __init__(self, mock_model: "MockChatModel") -> None:
        self._mock_model = mock_model

    def _next_response(self) -> dict[str, Any]:
        """弹出队列首个 grader 响应；空队列返回默认 satisfied。"""
        if self._mock_model.grader_responses:
            return self._mock_model.grader_responses.pop(0)
        return {
            "result": "satisfied",
            "explanation": "default mock satisfied",
            "criteria": [],
        }

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步调用：弹出 grader 响应。"""
        return self._next_response()

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步调用：弹出 grader 响应。"""
        return self._next_response()

    def with_config(self, config: Any, **kwargs: Any) -> "_MockStructuredOutput":
        """with_config 兼容：返回 self（mock 不关心 config）。"""
        return self

    @property
    def InputType(self) -> Any:
        return Any

    @property
    def OutputType(self) -> Any:
        return dict


class MockChatModel(BaseChatModel):
    """从 YAML fixture 加载预录响应，按消息内容子串匹配返回。

    用于离线评测模式，零外部依赖（不连 LLM）。

    扩展字段：
    - ``grader_responses``：``GraderResponse`` 兼容的 dict 队列，供
      ``with_structured_output`` 弹出。用于 L2 RubricJudge 和 L3 SelfCorrectionRunner
      的 grader mock（模拟 ``satisfied`` / ``needs_revision`` / ``failed`` 迭代）。
    """

    fixtures: list[MockFixture] = Field(default_factory=list)
    default_response: str = "（mock）暂无预录响应"
    grader_responses: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def from_fixtures(cls, path: str | Path) -> "MockChatModel":
        """从目录加载所有 ``*.yaml`` fixture 文件。

        每个文件内容为 ``list[dict]``，每个 dict 含 ``match`` / ``response`` /
        ``tool_calls`` 字段，构造为 ``MockFixture`` 后合并到 fixtures 列表。

        Args:
            path: 目录路径（str 或 Path）。

        Returns:
            装载好 fixtures 的 MockChatModel 实例。
        """
        dir_path = Path(path)
        fixtures: list[MockFixture] = []
        for yaml_file in sorted(dir_path.glob("*.yaml")):
            with yaml_file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
            if not isinstance(data, list):
                raise ValueError(
                    f"fixture 文件 {yaml_file} 顶层应为 list，实际为 {type(data).__name__}"
                )
            for item in data:
                fixtures.append(MockFixture(**item))
        return cls(fixtures=fixtures)

    def _match_fixture(self, messages: list[BaseMessage]) -> MockFixture | None:
        """从 messages 中提取最后一条 HumanMessage，按 match 子串匹配。

        **防死循环**：若 messages 中已含 ToolMessage（说明工具已执行过），
        则返回 fixture 时剥离 tool_calls，迫使 agent 在下一轮终止工具调用循环。

        Args:
            messages: 对话消息列表。

        Returns:
            命中的第一个 MockFixture，未命中返回 None。
        """
        has_tool_results = any(isinstance(m, ToolMessage) for m in messages)
        last_user_content = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_content = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
        if not last_user_content:
            return None
        for fixture in self.fixtures:
            if fixture.match and fixture.match in last_user_content:
                if has_tool_results and fixture.tool_calls:
                    return MockFixture(
                        match=fixture.match,
                        response=fixture.response,
                        tool_calls=[],
                    )
                return fixture
        return None

    def _build_result(self, fixture: MockFixture | None) -> ChatResult:
        """构造 ChatResult，含 AIMessage + tool_calls。

        fixture 为 None 时用 default_response，无 tool_calls。
        tool_calls 中每项补充唯一 ``id``（格式 ``call_<uuid>``）。
        """
        if fixture is None:
            ai_message = AIMessage(content=self.default_response)
        else:
            tool_calls_with_id = [
                {
                    "name": tc["name"],
                    "args": tc.get("args", {}),
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                }
                for tc in fixture.tool_calls
            ]
            ai_message = AIMessage(content=fixture.response, tool_calls=tool_calls_with_id)
        generation = ChatGeneration(message=ai_message)
        return ChatResult(generations=[generation])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """同步生成（BaseChatModel 抽象方法）。"""
        fixture = self._match_fixture(messages)
        return self._build_result(fixture)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步生成（BaseChatModel 抽象方法，默认委托 _generate）。"""
        return self._generate(messages, stop, run_manager, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockChatModel":
        """Mock bind_tools：返回 self（mock 模式下工具绑定无实际意义）。

        ``create_react_agent`` 会调 ``model.bind_tools(tools)`` 将工具绑定到模型，
        真实 LLM 会据此在响应中产出 tool_calls。MockChatModel 的 tool_calls 由
        fixtures 预录决定，与绑定工具无关，因此直接返回 self 即可。
        """
        return self

    def with_structured_output(
        self,
        schema: Any,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> _MockStructuredOutput:
        """Mock with_structured_output：返回 ``_MockStructuredOutput`` Runnable。

        ``langchain.agents.create_agent(response_format=GraderResponse)`` 与
        ``RubricMiddleware`` 内部均会调 ``model.with_structured_output(schema)``
        获取结构化输出 Runnable。本方法返回的 Runnable 按 ``grader_responses``
        队列顺序弹出 dict（与 ``GraderResponse`` 兼容）。

        Args:
            schema: 结构化输出的 schema（如 ``GraderResponse``），mock 模式忽略。
            include_raw: 是否包含原始输出，mock 模式忽略。

        Returns:
            ``_MockStructuredOutput`` Runnable 实例。
        """
        return _MockStructuredOutput(self)

    @property
    def _llm_type(self) -> str:
        """返回 LLM 类型标识。"""
        return "agentx-mock-chat-model"


__all__ = ["MockChatModel", "MockFixture"]
