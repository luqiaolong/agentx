# 标题栏 Work/Coding 场景切换器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在标题栏增加 Work / Coding 分段切换器，切换场景时前端按场景注入内置 system prompt，通过新增的 `ChatRequest.system_prompt` 可选字段透传给后端三路径。

**Architecture:** 方案 B —— 后端 `ChatRequest` 加可选 `system_prompt` 字段；`graph.py` 顶部新增 `resolve_system_prompt(default, scene_prompt, skill_extra)` 工具函数（优先级 skill_extra > scene_prompt > default）；三路径统一调用此函数。前端新建 `useSceneStore`（持久化到 localStorage），标题栏分段按钮绑定 store，`ChatView` 发送消息时从 store 读 prompt 通过 preload `streamChat` 传给后端。

**Tech Stack:** React 18 + TypeScript + zustand（前端）；FastAPI + pydantic + LangGraph（后端）；pytest + vitest（测试）

**Spec:** [docs/superpowers/specs/2026-07-05-titlebar-scene-switcher-design.md](file:///d:/java/agentprojects/agentx/docs/superpowers/specs/2026-07-05-titlebar-scene-switcher-design.md)

---

## File Structure

| 文件 | 责任 |
|---|---|
| `backend/app/router/graph.py` | 新增 `resolve_system_prompt` 工具函数；`run_router` / `_run_chat_path` / `_run_tool_path` / `_run_deep_path` 加 `scene_prompt` 参数 |
| `backend/app/paths/deep_path.py` | `build_deep_agent` / `run_deep_path` 加 `scene_prompt` 参数 |
| `backend/app/main.py` | `ChatRequest` 加 `system_prompt` 字段；`chat()` 端点透传给 `run_router` |
| `frontend/renderer/stores/scene.ts` | 新建：scene store + `SCENE_PROMPTS` 常量 |
| `frontend/renderer/App.tsx` | 标题栏插入 Work/Coding 分段按钮 |
| `frontend/renderer/components/chat/ChatView.tsx` | `chat.send` 调用处传 `systemPrompt` |
| `frontend/preload/index.ts` | `streamChat` 加 `systemPrompt` opt + body 字段 |
| `frontend/shared/api-types.ts` | `chat.send` 类型同步加 `systemPrompt?: string` |
| `tests/python/unit/test_router_graph.py` | 补充 `resolve_system_prompt` 单测 |
| `tests/python/unit/test_chat_endpoint.py` | 新建：验证 `system_prompt` 字段被路径 A 接收 |
| `tests/renderer/stores/scene.test.ts` | 新建：scene store 单测 |
| `tests/renderer/App.test.tsx` | 补充：标题栏分段按钮渲染 + 切换 |

---

## Task 1: 后端 resolve_system_prompt 工具函数 + 单测

**Files:**
- Modify: `backend/app/router/graph.py`（顶部新增函数）
- Modify: `tests/python/unit/test_router_graph.py`（新增测试）

- [ ] **Step 1: 写失败测试**

在 `tests/python/unit/test_router_graph.py` 顶部 import 区追加：

```python
from app.router.graph import build_router_graph, run_router, resolve_system_prompt
```

文件末尾追加：

```python
# ============================================================
# resolve_system_prompt 工具函数
# ============================================================


def test_resolve_system_prompt_default_only() -> None:
    """无 scene_prompt 也无 skill_extra → 返回 default。"""
    assert resolve_system_prompt("default", None, None) == "default"


def test_resolve_system_prompt_scene_overrides_default() -> None:
    """scene_prompt 非空 → 覆盖 default。"""
    assert resolve_system_prompt("default", "coding-prompt", None) == "coding-prompt"


def test_resolve_system_prompt_skill_prepended() -> None:
    """skill_extra 始终拼在最前（即使 scene_prompt 也存在）。"""
    result = resolve_system_prompt("default", "coding-prompt", "skill-content")
    assert result == "skill-content\ncoding-prompt"


def test_resolve_system_prompt_skill_only() -> None:
    """只有 skill_extra → 拼到 default 前。"""
    result = resolve_system_prompt("default", None, "skill-content")
    assert result == "skill-content\ndefault"


def test_resolve_system_prompt_empty_scene_falls_back() -> None:
    """scene_prompt 为空字符串（非 None）→ 视为未设置，回退 default。

    防御 pydantic 把 "" 当 falsy 处理的边界情况。
    """
    assert resolve_system_prompt("default", "", None) == "default"
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run pytest tests/python/unit/test_router_graph.py::test_resolve_system_prompt_default_only -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_system_prompt'`

- [ ] **Step 3: 实现工具函数**

在 `backend/app/router/graph.py` 顶部 import 之后、`build_router_graph` 之前插入：

```python
def resolve_system_prompt(
    default: str,
    scene_prompt: str | None,
    skill_extra: str | None,
) -> str:
    """合并三层 system prompt，优先级：skill_extra > scene_prompt > default。

    - ``scene_prompt`` 非空时覆盖 ``default``（场景切换器注入）。
    - ``skill_extra`` 非空时拼在最前（画像 / @skill content，遵循 spec R9 画像优先约定）。
    - ``scene_prompt`` 为空字符串视为未设置（防御 pydantic 边界）。

    Examples:
        >>> resolve_system_prompt("d", None, None)
        'd'
        >>> resolve_system_prompt("d", "scene", None)
        'scene'
        >>> resolve_system_prompt("d", "scene", "skill")
        'skill\\nscene'
    """
    base = scene_prompt if scene_prompt else default
    return f"{skill_extra}\n{base}" if skill_extra else base
```

- [ ] **Step 4: 跑测试看通过**

Run: `uv run pytest tests/python/unit/test_router_graph.py -k "resolve_system_prompt" -v`
Expected: PASS（5 个测试全过）

- [ ] **Step 5: commit**

```bash
git add backend/app/router/graph.py tests/python/unit/test_router_graph.py
git commit -m "feat(router): add resolve_system_prompt helper for scene/skill/default layering"
```

---

## Task 2: 后端 ChatRequest 加 system_prompt 字段 + run_router 加参数

**Files:**
- Modify: `backend/app/main.py:194-196`（ChatRequest）
- Modify: `backend/app/main.py:534`（chat 端点）
- Modify: `backend/app/router/graph.py:445-449`（run_router 签名）

- [ ] **Step 1: ChatRequest 加字段**

在 `backend/app/main.py` 找到 `class ChatRequest(BaseModel):`（约 L194），改为：

```python
class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")
    system_prompt: str | None = Field(
        None, description="可选场景 prompt；非空时覆盖 default_system_prompt"
    )
```

- [ ] **Step 2: run_router 加 scene_prompt 参数**

在 `backend/app/router/graph.py` 找到 `async def run_router(`（约 L445），改为：

```python
async def run_router(
    message: str,
    thread_id: str,
    checkpointer: Any = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
```

并在 docstring 的 `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt（前端场景切换器注入），非空时覆盖
            ``default_system_prompt``（路径 A/B）或 ``_DEEP_SYSTEM_PROMPT``（路径 C）。
```

- [ ] **Step 3: chat 端点透传**

在 `backend/app/main.py` 找到 `@app.post("/api/chat")` 的 `async def chat(req: ChatRequest)`（约 L534），在调用 `run_router` 的地方追加 `scene_prompt=req.system_prompt`。

定位 `run_router` 调用（在 `chat()` 函数体内，搜索 `run_router(`），改为：

```python
async for event in run_router(
    req.message,
    req.thread_id,
    checkpointer=checkpointer,
    scene_prompt=req.system_prompt,
):
```

> 注：实际变量名/参数顺序以源码为准；只新增 `scene_prompt=req.system_prompt` 这一个 kwarg。

- [ ] **Step 4: 跑现有测试确保无 regression**

Run: `uv run pytest tests/python/unit/test_router_graph.py tests/python/unit/test_chat_control_api.py -v`
Expected: PASS（所有现有测试通过，证明加可选参数不破坏契约）

- [ ] **Step 5: commit**

```bash
git add backend/app/main.py backend/app/router/graph.py
git commit -m "feat(chat): accept optional system_prompt in ChatRequest and thread through run_router"
```

---

## Task 3: 后端路径 A _run_chat_path 使用 scene_prompt

**Files:**
- Modify: `backend/app/router/graph.py:194-216`（_run_chat_path 签名 + system_prompt 计算）
- Modify: `backend/app/router/graph.py:516-529`（run_router 内 CHAT 分支调用）

- [ ] **Step 1: _run_chat_path 加 scene_prompt 参数**

找到 `async def _run_chat_path(`（约 L194），改为：

```python
async def _run_chat_path(
    message: str,
    thread_id: str,
    system_prompt_extra: str | None = None,
    history: list | None = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
```

docstring 的 `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt，非空时覆盖 default_system_prompt。
```

- [ ] **Step 2: 替换 system_prompt 计算逻辑**

找到 `_run_chat_path` 内的（约 L213-216）：

```python
    system_prompt = get_settings().default_system_prompt
    if system_prompt_extra:
        # 画像/skill 在前，default 在后（spec R9：画像优先于默认 prompt，与路径 C 一致）
        system_prompt = f"{system_prompt_extra}\n{system_prompt}"
```

改为：

```python
    system_prompt = resolve_system_prompt(
        default=get_settings().default_system_prompt,
        scene_prompt=scene_prompt,
        skill_extra=system_prompt_extra,
    )
```

- [ ] **Step 3: run_router 内 CHAT 分支透传 scene_prompt**

找到 `run_router` 内 `if classification == "CHAT":` 分支（约 L516-529），把 `_run_chat_path(...)` 调用改为：

```python
            async for sse in _run_chat_path(
                cleaned_message,
                thread_id,
                system_prompt_extra=system_prompt_extra,
                history=history,
                scene_prompt=scene_prompt,
            ):
                yield sse
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/python/unit/test_router_graph.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add backend/app/router/graph.py
git commit -m "feat(router): path A (chat) consumes scene_prompt via resolve_system_prompt"
```

---

## Task 4: 后端路径 B _run_tool_path 透传 scene_prompt

**Files:**
- Modify: `backend/app/router/graph.py:248-275`（_run_tool_path 签名 + 回退调用）
- Modify: `backend/app/router/graph.py:530-537`（run_router 内 SINGLE_TOOL 分支）

- [ ] **Step 1: _run_tool_path 加 scene_prompt 参数**

找到 `async def _run_tool_path(`（约 L248），改为：

```python
async def _run_tool_path(
    message: str,
    thread_id: str,
    profile_prompt: str | None = None,
    history: list | None = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
```

docstring `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt，回退路径 A 时透传。
```

- [ ] **Step 2: 回退 _run_chat_path 时透传 scene_prompt**

找到 `_run_tool_path` 内回退到 `_run_chat_path` 的调用（约 L270-275），改为：

```python
        async for sse in _run_chat_path(
            message, thread_id, system_prompt_extra=profile_prompt, history=history,
            scene_prompt=scene_prompt,
        ):
```

> 注：实际参数顺序以源码为准；只新增 `scene_prompt=scene_prompt` 这一个 kwarg。

- [ ] **Step 3: run_router 内 SINGLE_TOOL 分支透传**

找到 `run_router` 内 `elif classification == "SINGLE_TOOL":` 分支（约 L530-537），把 `_run_tool_path(...)` 调用改为：

```python
            async for sse in _run_tool_path(
                cleaned_message,
                thread_id,
                profile_prompt=profile_prompt,
                history=history,
                scene_prompt=scene_prompt,
            ):
                yield sse
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/python/unit/test_router_graph.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add backend/app/router/graph.py
git commit -m "feat(router): path B (tool) threads scene_prompt through to chat fallback"
```

---

## Task 5: 后端路径 C build_deep_agent / run_deep_path 接收 scene_prompt

**Files:**
- Modify: `backend/app/paths/deep_path.py:127-168`（build_deep_agent）
- Modify: `backend/app/paths/deep_path.py:313-370`（run_deep_path）
- Modify: `backend/app/router/graph.py:316-335`（_run_deep_path）
- Modify: `backend/app/router/graph.py:538-547`（run_router 内 DEEP_TASK 分支）

- [ ] **Step 1: build_deep_agent 加 scene_prompt 参数**

找到 `async def build_deep_agent(`（约 L127），改为：

```python
async def build_deep_agent(
    thread_id: str,
    tools: list | None = None,
    profile_prompt: str = "",
    scene_prompt: str | None = None,
    checkpointer: Any = None,
) -> CompiledStateGraph:
```

docstring `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt，非空时覆盖 ``_DEEP_SYSTEM_PROMPT``。
```

- [ ] **Step 2: build_deep_agent 内用 resolve_system_prompt**

在 `backend/app/paths/deep_path.py` 顶部 import 区追加（如尚未引入）：

```python
from app.router.graph import resolve_system_prompt
```

找到 `build_deep_agent` 内（约 L158-160）：

```python
    # T9：画像前缀拼到默认 system prompt 前（遵循与路径 A 一致的"画像优先"约定）
    system_prompt = _DEEP_SYSTEM_PROMPT
    if profile_prompt:
        system_prompt = f"{profile_prompt}\n{system_prompt}"
```

改为：

```python
    # T9：画像前缀拼到最前；scene_prompt 覆盖 _DEEP_SYSTEM_PROMPT（场景切换器注入）
    system_prompt = resolve_system_prompt(
        default=_DEEP_SYSTEM_PROMPT,
        scene_prompt=scene_prompt,
        skill_extra=profile_prompt or None,
    )
```

- [ ] **Step 3: run_deep_path 加 scene_prompt 参数**

找到 `async def run_deep_path(`（约 L313），改为：

```python
async def run_deep_path(
    state: RouterState,
    message: str,
    profile_prompt: str = "",
    history: list | None = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict]:
```

docstring `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt，透传给 build_deep_agent。
```

- [ ] **Step 4: run_deep_path 透传给 build_deep_agent**

找到 `run_deep_path` 内 `agent = await build_deep_agent(...)`（约 L363），改为：

```python
        agent = await build_deep_agent(
            thread_id,
            tools=agent_tools,
            profile_prompt=profile_prompt,
            scene_prompt=scene_prompt,
        )
```

- [ ] **Step 5: graph.py _run_deep_path 加 scene_prompt 参数**

找到 `async def _run_deep_path(`（约 L316），改为：

```python
async def _run_deep_path(
    message: str,
    thread_id: str,
    state: RouterState,
    profile_prompt: str = "",
    history: list | None = None,
    scene_prompt: str | None = None,
) -> AsyncIterator[dict[str, str]]:
```

docstring `Args:` 段追加：

```
        scene_prompt: 可选场景 prompt，透传给 run_deep_path。
```

- [ ] **Step 6: _run_deep_path 透传给 run_deep_path**

找到 `_run_deep_path` 内 `async for event in run_deep_path(...)`（约 L333-334），改为：

```python
        async for event in run_deep_path(
            state, message, profile_prompt=profile_prompt, history=history,
            scene_prompt=scene_prompt,
        ):
```

- [ ] **Step 7: run_router 内 DEEP_TASK 分支透传**

找到 `run_router` 内 `else:  # DEEP_TASK` 分支（约 L538-547），把 `_run_deep_path(...)` 调用改为：

```python
            async for sse in _run_deep_path(
                cleaned_message,
                thread_id,
                state,
                profile_prompt=profile_prompt,
                history=history,
                scene_prompt=scene_prompt,
            ):
                yield sse
```

- [ ] **Step 8: 跑测试**

Run: `uv run pytest tests/python/unit/test_router_graph.py tests/python/unit/test_subagents.py -v`
Expected: PASS

- [ ] **Step 9: 检查循环导入**

`deep_path.py` 顶部已 `TYPE_CHECKING` 延迟导入 `RouterState`。新增的 `from app.router.graph import resolve_system_prompt` 是运行时导入，可能触发循环（`graph.py` 顶层 `from app.paths.deep_path import run_deep_path`）。

Run: `uv run python -c "from app.paths.deep_path import run_deep_path; print('OK')"`
Expected: 输出 `OK`，无 ImportError。

> 若报循环导入：把 `resolve_system_prompt` 函数体复制到 `deep_path.py` 顶部作为本地私有副本（注释"与 graph.py 保持同步"），或在 `build_deep_agent` 函数体内做延迟 import。

- [ ] **Step 10: commit**

```bash
git add backend/app/paths/deep_path.py backend/app/router/graph.py
git commit -m "feat(router): path C (deep) consumes scene_prompt via resolve_system_prompt"
```

---

## Task 6: 后端集成测试验证 system_prompt 字段

**Files:**
- Create: `tests/python/unit/test_chat_endpoint.py`

- [ ] **Step 1: 写测试**

新建 `tests/python/unit/test_chat_endpoint.py`：

```python
"""POST /api/chat 的 system_prompt 字段集成测试。

不调真实 LLM / TEI / Milvus，全部 mock。验证：
1. ChatRequest 带 system_prompt 字段被路径 A 接收
2. system_prompt 非空时覆盖 default_system_prompt
3. system_prompt 为 null 时回退 default_system_prompt
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_chat_request_accepts_system_prompt_field(client: TestClient) -> None:
    """ChatRequest 接受 system_prompt 字段，且非空时覆盖 default。

    通过 mock run_router 捕获 scene_prompt 入参，断言其等于请求中的 system_prompt。
    """
    captured: dict = {}

    async def fake_run_router(message, thread_id, checkpointer=None, scene_prompt=None):
        captured["scene_prompt"] = scene_prompt
        captured["message"] = message
        captured["thread_id"] = thread_id
        yield {"event": "done", "data": "{}"}

    with patch("app.main.run_router", fake_run_router):
        resp = client.post(
            "/api/chat",
            json={
                "message": "hello",
                "thread_id": "test-thread-1",
                "system_prompt": "你是编程助手。",
            },
        )

    assert resp.status_code == 200
    assert captured["scene_prompt"] == "你是编程助手。"


def test_chat_request_system_prompt_null_falls_back(client: TestClient) -> None:
    """system_prompt 为 null 时 run_router 收到 scene_prompt=None。"""
    captured: dict = {}

    async def fake_run_router(message, thread_id, checkpointer=None, scene_prompt=None):
        captured["scene_prompt"] = scene_prompt
        yield {"event": "done", "data": "{}"}

    with patch("app.main.run_router", fake_run_router):
        client.post(
            "/api/chat",
            json={"message": "hi", "thread_id": "t2", "system_prompt": None},
        )

    assert captured["scene_prompt"] is None


def test_chat_request_without_system_prompt_field(client: TestClient) -> None:
    """老客户端不传 system_prompt 字段，run_router 收到 scene_prompt=None（向后兼容）。"""
    captured: dict = {}

    async def fake_run_router(message, thread_id, checkpointer=None, scene_prompt=None):
        captured["scene_prompt"] = scene_prompt
        yield {"event": "done", "data": "{}"}

    with patch("app.main.run_router", fake_run_router):
        client.post(
            "/api/chat",
            json={"message": "hi", "thread_id": "t3"},
        )

    assert captured["scene_prompt"] is None
```

- [ ] **Step 2: 跑测试看是否通过**

Run: `uv run pytest tests/python/unit/test_chat_endpoint.py -v`
Expected: PASS（3 个测试全过）

> 若失败：检查 `app.main` 中 `run_router` 是否已被 import 进 `chat()` 函数可见作用域；patch 路径可能需调整为 `app.main.run_router` 或实际 import 路径。

- [ ] **Step 3: commit**

```bash
git add tests/python/unit/test_chat_endpoint.py
git commit -m "test(chat): verify ChatRequest.system_prompt field threading"
```

---

## Task 7: 前端 scene store + 单测

**Files:**
- Create: `frontend/renderer/stores/scene.ts`
- Create: `tests/renderer/stores/scene.test.ts`

- [ ] **Step 1: 写 store**

新建 `frontend/renderer/stores/scene.ts`：

```ts
import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export type Scene = "work" | "coding";

/**
 * 两套场景内置 system prompt。前端硬编码，切换场景时通过 chat.send opts.systemPrompt
 * 透传给后端 ChatRequest.system_prompt，覆盖 default_system_prompt。
 */
export const SCENE_PROMPTS: Record<Scene, string> = {
  work: `你是 AgentX 工作助手。专注文档撰写、知识检索、日程任务、邮件沟通等办公场景。
- 回答简洁友好，优先调用 RAG/搜索/读写类工具
- 涉及代码时给出解释但默认不主动改文件
- 危险操作（写文件/shell）需用户确认`,
  coding: `你是 AgentX 编程助手。专注代码开发、调试、重构、shell 操作。
- 默认假设用户在某个 workspace 目录下工作
- 优先调用 filesystem/shell/rag_retrieve 工具
- 修改代码前先读文件，给出 diff 级别说明
- 危险操作仍走审批流`,
};

interface SceneState {
  scene: Scene;
  setScene: (s: Scene) => void;
}

export const useSceneStore = create<SceneState>()(
  devtools(
    persist(
      (set) => ({
        scene: "work",
        setScene: (scene) => set({ scene }),
      }),
      {
        name: "agentx-scene",
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({ scene: s.scene }),
      },
    ),
    { name: "scene-store" },
  ),
);
```

- [ ] **Step 2: 写测试**

新建 `tests/renderer/stores/scene.test.ts`：

```ts
import { beforeEach, describe, expect, it } from "vitest";
import { SCENE_PROMPTS, useSceneStore } from "@/stores/scene";

describe("scene store", () => {
  beforeEach(() => {
    localStorage.clear();
    useSceneStore.setState({ scene: "work" });
  });

  it("默认 scene 是 work", () => {
    expect(useSceneStore.getState().scene).toBe("work");
  });

  it("setScene(coding) 后 scene 切换为 coding", () => {
    useSceneStore.getState().setScene("coding");
    expect(useSceneStore.getState().scene).toBe("coding");
  });

  it("setScene 后持久化到 localStorage", () => {
    useSceneStore.getState().setScene("coding");
    const raw = localStorage.getItem("agentx-scene");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.scene).toBe("coding");
  });

  it("SCENE_PROMPTS 两个 key 都是非空字符串", () => {
    expect(SCENE_PROMPTS.work.trim().length).toBeGreaterThan(0);
    expect(SCENE_PROMPTS.coding.trim().length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 3: 跑测试**

Run: `npm test -- scene.test`
Expected: PASS（4 个测试全过）

- [ ] **Step 4: commit**

```bash
git add frontend/renderer/stores/scene.ts tests/renderer/stores/scene.test.ts
git commit -m "feat(renderer): add useSceneStore with work/coding scene prompts"
```

---

## Task 8: 前端 preload streamChat + api-types 同步

**Files:**
- Modify: `frontend/preload/index.ts:61-70`
- Modify: `frontend/shared/api-types.ts:207`

- [ ] **Step 1: streamChat 加 systemPrompt opt**

找到 `frontend/preload/index.ts` 的 `async function streamChat(`（约 L61），改为：

```ts
async function streamChat(
  msg: { role: string; content: string },
  opts?: { threadId?: string; systemPrompt?: string },
): Promise<void> {
  // 后端 ChatRequest: { message: str, thread_id: str, system_prompt: str | None }
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.content,
      thread_id: opts?.threadId ?? "",
      system_prompt: opts?.systemPrompt ?? null,
    }),
  });
```

> 其余函数体（reader / decoder / event 解析）保持不变。

- [ ] **Step 2: api-types 同步**

找到 `frontend/shared/api-types.ts` 的 `chat:` 块（约 L206-212），把 `send` 行改为：

```ts
  chat: {
    send: (
      msg: { role: string; content: string },
      opts?: { threadId?: string; systemPrompt?: string },
    ) => Promise<void>;
    abort: (threadId: string) => Promise<void>;
```

> 其余行保持不变。

- [ ] **Step 3: typecheck**

Run: `npm run typecheck`
Expected: PASS（无 TS 错误）

- [ ] **Step 4: commit**

```bash
git add frontend/preload/index.ts frontend/shared/api-types.ts
git commit -m "feat(preload): thread systemPrompt through streamChat to ChatRequest"
```

---

## Task 9: 前端 ChatView 调用 chat.send 时传 systemPrompt

**Files:**
- Modify: `frontend/renderer/components/chat/ChatView.tsx:282`（正常 send 调用）

- [ ] **Step 1: import SCENE_PROMPTS / useSceneStore**

在 `frontend/renderer/components/chat/ChatView.tsx` 顶部 import 区追加：

```ts
import { SCENE_PROMPTS, useSceneStore } from "@/stores/scene";
```

- [ ] **Step 2: 正常 send 调用传 systemPrompt**

找到 `await window.api.chat.send({ role: "user", content }, { threadId: tid });`（约 L282，在正常发送的 try 块内），改为：

```ts
      const scene = useSceneStore.getState().scene;
      await window.api.chat.send(
        { role: "user", content },
        { threadId: tid, systemPrompt: SCENE_PROMPTS[scene] },
      );
```

> **不改 L97 的 `/clear` 调用**：`/clear` 触发后端会话重置，prompt 不重要，保持原样。

- [ ] **Step 3: typecheck**

Run: `npm run typecheck`
Expected: PASS

- [ ] **Step 4: commit**

```bash
git add frontend/renderer/components/chat/ChatView.tsx
git commit -m "feat(chat): inject scene systemPrompt on normal send"
```

---

## Task 10: 前端 App.tsx 标题栏分段按钮 + 单测

**Files:**
- Modify: `frontend/renderer/App.tsx:83-95`（标题栏 Logo 区）
- Modify: `tests/renderer/App.test.tsx`（如有，否则新建）

- [ ] **Step 1: App.tsx 引入 useSceneStore**

在 `frontend/renderer/App.tsx` 顶部 import 区追加：

```ts
import { useSceneStore, type Scene } from "./stores/scene";
```

在 `App()` 函数顶部 `const toggleTheme = ...` 旁追加：

```ts
  const scene = useSceneStore((s) => s.scene);
  const setScene = useSceneStore((s) => s.setScene);
```

- [ ] **Step 2: 标题栏插入分段按钮**

找到 `<span className="ml-1 rounded-full bg-subtle px-2 py-0.5 text-[10px] font-medium text-secondary-c">v0.1</span>`（约 L92-94），在其后追加：

```tsx
          {/* 场景切换器：Work / Coding */}
          <div
            className="ml-2 inline-flex items-center rounded-md border border-default bg-surface"
            role="tablist"
            aria-label="场景切换"
          >
            {(["work", "coding"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={scene === s}
                onClick={() => setScene(s)}
                className={`h-6 px-2.5 text-[11px] font-medium transition-colors ${
                  scene === s
                    ? "bg-brand-600 text-white"
                    : "text-secondary-c hover:text-primary-c"
                }`}
                title={s === "work" ? "工作场景" : "编程场景"}
              >
                {s === "work" ? "Work" : "Coding"}
              </button>
            ))}
          </div>
```

> `bg-brand-600` 是项目已有的紫色 brand 色（与 Logo/发送按钮一致）。`Scene` 类型导入如未被使用可去掉（map 用了 `as const`），保留也无害。

- [ ] **Step 3: 写测试**

检查 `tests/renderer/App.test.tsx` 是否存在。若不存在则新建；若存在则补充用例。

新建/补充测试用例（最小集）：

```tsx
import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "@/App";
import { useSceneStore } from "@/stores/scene";

function renderApp() {
  return render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
}

describe("App 标题栏场景切换器", () => {
  beforeEach(() => {
    localStorage.clear();
    useSceneStore.setState({ scene: "work" });
  });

  it("渲染 Work / Coding 两个按钮", () => {
    renderApp();
    expect(screen.getByRole("tab", { name: "工作场景" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "编程场景" })).toBeInTheDocument();
  });

  it("点击 Coding 切换 store.scene", () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: "编程场景" }));
    expect(useSceneStore.getState().scene).toBe("coding");
  });
});
```

> 若 `App.test.tsx` 已有其他用例，仅追加这两个 `it` 块；若新建需确认 `tests/renderer/` 已配置 `@testing-library/react` 与 `jsdom`。

- [ ] **Step 4: 跑测试**

Run: `npm test -- App.test`
Expected: PASS

- [ ] **Step 5: typecheck**

Run: `npm run typecheck`
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add frontend/renderer/App.tsx tests/renderer/App.test.tsx
git commit -m "feat(renderer): add Work/Coding segmented switcher to title bar"
```

---

## Task 11: 手动验收

**Files:** 无（运行时验证）

- [ ] **Step 1: 启动应用**

Run: `npm run dev`

> 等待 `start electron app...` 后再等 8-10s（参见 AGENTS.md §14.7）。

- [ ] **Step 2: 验证标题栏 UI**

- 标题栏 Logo + v0.1 之后出现 `[ Work | Coding ]` 分段按钮
- Work 项高亮（紫色背景 `#4f46e5`），Coding 项为灰色文字
- 点击 Coding → Coding 项高亮，Work 项变灰
- 点击 Work → 切回 Work 高亮

- [ ] **Step 3: 验证场景 prompt 注入**

- 默认 Work 场景下，发送"你是谁"，观察后端日志（`logs/` 或 dev tools Network）中 `/api/chat` 请求 body 应包含 `system_prompt` 字段，内容是 work prompt
- 切换到 Coding，再发送"你是谁"，请求 body 的 `system_prompt` 应变为 coding prompt
- LLM 回答应体现场景风格（Work 偏办公介绍，Coding 偏编程助手介绍）

- [ ] **Step 4: 验证持久化**

- 切换到 Coding，关闭应用
- 重新 `npm run dev` 启动
- 标题栏应默认显示 Coding 高亮（localStorage `agentx-scene` 持久化生效）

- [ ] **Step 5: 验证向后兼容**

- 在 dev tools console 执行：
  ```js
  fetch("http://127.0.0.1:8123/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "hi", thread_id: "compat-test" }),
  });
  ```
- 后端应正常处理（不报 422），返回 SSE 流；scene_prompt 为 None，回退 default_system_prompt

- [ ] **Step 6: 全量测试回归**

Run（后端）: `uv run pytest tests/python/unit -m "not integration" -v`
Expected: PASS

Run（前端）: `npm test`
Expected: PASS

- [ ] **Step 7: 最终 commit（如有未提交的修正）**

```bash
git status
# 若有未提交修正：
git add -p
git commit -m "test: regression fixes from manual acceptance"
```

---

## Self-Review

**Spec coverage:**
- §2 需求边界（默认 Work / 全局生效 / 前端内置 prompt / 不清空会话历史）→ Task 7（store 默认 work + 持久化）+ Task 9（ChatView 注入 prompt，不清历史）+ Task 10（标题栏 UI）✅
- §4.1 前端 store → Task 7 ✅
- §4.2 标题栏 UI → Task 10 ✅
- §4.3 状态联动 → Task 9 ✅
- §4.4 ChatRequest 改造 → Task 2 ✅
- §4.5 三路径 resolve_system_prompt → Task 1（函数）+ Task 3/4/5（三路径接入）✅
- §4.6 run_router 透传 → Task 2 ✅
- §4.7 preload + api-types 同步 → Task 8 ✅
- §4.8 兼容性 → Task 6（测试 null / 不传字段）+ Task 11 Step 5（手动验证）✅
- §5.1 后端单测 → Task 1 + Task 6 ✅
- §5.2 渲染层测试 → Task 7 + Task 10 ✅
- §5.3 手动验收 → Task 11 ✅

**Placeholder scan:** 无 TBD/TODO；每步都有完整代码或命令。✅

**Type consistency:**
- `Scene = "work" | "coding"`（Task 7）→ Task 10 标题栏 `(["work", "coding"] as const)` 一致 ✅
- `SCENE_PROMPTS: Record<Scene, string>`（Task 7）→ Task 9 `SCENE_PROMPTS[scene]` 索引一致 ✅
- `system_prompt: str | None`（后端，Task 2）↔ `systemPrompt?: string`（前端，Task 8）↔ `scene_prompt: str | None`（run_router，Task 2）✅
- `resolve_system_prompt(default, scene_prompt, skill_extra)`（Task 1）→ 三路径调用一致（Task 3/5）✅
- `build_deep_agent(scene_prompt=...)`（Task 5 Step 1）↔ `run_deep_path(scene_prompt=...)`（Task 5 Step 3）↔ `_run_deep_path(scene_prompt=...)`（Task 5 Step 5）✅

**Ambiguity check:** Task 5 Step 9 已显式处理循环导入风险（给出延迟 import 备选方案）。✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-05-titlebar-scene-switcher.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个 Task 派发独立 subagent，task 间 review，迭代快
2. **Inline Execution** — 在当前会话按 executing-plans 批量执行，带 checkpoint review

请告知采用哪种执行方式。
