# 标题栏 Work/Coding 场景切换器设计

> 日期：2026-07-05
> 状态：已通过 brainstorming，待 writing-plans 落地
> 关联文件：[App.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/App.tsx)、
> [settings.ts](file:///d:/java/agentprojects/agentx/frontend/renderer/stores/settings.ts)、
> [graph.py](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py)、
> [main.py](file:///d:/java/agentprojects/agentx/backend/app/main.py)、
> [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts)

---

## 1. 背景与目标

在桌面应用标题栏增加 **Work / Coding** 两种场景切换器。本次只做"场景切换器 + 不同 system prompt"，其他改造（布局/工具/路径差异）后续再说。

**为什么需要场景切换**：当前 `default_system_prompt` 是全局唯一的（`config.py:195` 默认 "你是个人助理。简洁友好地回答用户问题。"）。办公场景与编程场景对 LLM 行为的期望差异显著（工具偏好、回答风格、危险操作默认值），需要前端按场景注入不同 prompt。

## 2. 需求边界（已与用户对齐）

- 标题栏新增 Work / Coding 分段切换器，**默认 Work**
- **全局生效**（非按会话绑定），持久化到 localStorage
- 两套 system prompt **前端内置**（硬编码在 renderer）
- 切换场景不清空当前会话历史；下一条消息起携带新场景 prompt
- 不做：场景图标、场景级主题色、场景级工具/路径差异、切换确认弹窗

## 3. 方案选择

| 方案 | 改动 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 复用 `/api/config/reload` 热重载 `default_system_prompt` | 仅前端 | 复用现成端点 | 场景 prompt 覆盖用户自定义 default，多次切换语义漂移 | ❌ |
| **B. 扩展 `ChatRequest` 加 `system_prompt` 可选字段** | 后端契约 + 三路径 + preload + 前端 store/UI | 场景 prompt 与 default 解耦，单一职责 | 改 SSE 契约（§13 三处同步），但只加可选字段，向后兼容 | ✅ |
| C. 前端拼 `<system>` 标签到 message | 仅前端 | 无契约改动 | 靠 LLM 理解标签，与 `<workspace>`/`<file>` 混杂，语义噪声 | ❌ |

**采用方案 B。** 理由：①符合 §1.2 P2 单一职责（场景 prompt ≠ 全局 default）；②只加可选字段，老客户端不传 → 行为不变；③为后续"按场景暴露不同工具/路径"留下干净的数据通道。

## 4. 详细设计

### 4.1 前端 store

新建 `frontend/renderer/stores/scene.ts`：

```ts
import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export type Scene = "work" | "coding";

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

### 4.2 标题栏 UI

[App.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/App.tsx#L83-L95) Logo + v0.1 之后插入分段按钮：

```
[Bot] AgentX  v0.1   [ Work | Coding ]      [状态] [☀] [— ☐ ✕]
```

实现要点：
- 容器：`inline-flex items-center rounded-md border border-default bg-surface`
- 每项：`h-6 px-2.5 text-[11px] font-medium transition-colors`
- 激活项：`bg-brand-600 text-white`（紫色 #4f46e5，与现有 Logo/发送按钮色调一致）
- 非激活：`text-secondary-c hover:text-primary-c`
- 点击 → `useSceneStore.getState().setScene(scene)`，store 自动持久化
- 双向绑定：`const scene = useSceneStore((s) => s.scene)`

### 4.3 状态联动

`useChatStore.send` 调用 `window.api.chat.send` 时，从 `useSceneStore.getState().scene` 读 prompt，通过新增的 `opts.systemPrompt` 传给 preload。**不**在切换场景时清空会话历史。

### 4.4 后端 ChatRequest 改造

[main.py:194-196](file:///d:/java/agentprojects/agentx/backend/app/main.py#L194-L196)：

```python
class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息（/reset 触发会话重置）")
    thread_id: str = Field(..., description="会话 ID")
    system_prompt: str | None = Field(
        None, description="可选场景 prompt；非空时覆盖 default_system_prompt"
    )
```

### 4.5 三路径取 prompt 改造

在 [graph.py](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py) 顶部新增工具函数（与 `run_router` 同文件，三路径统一从此导入）：

```python
def resolve_system_prompt(
    default: str, scene_prompt: str | None, skill_extra: str | None
) -> str:
    """scene_prompt 非空时覆盖 default；skill_extra 始终拼在最前。

    优先级：skill_extra > scene_prompt > default
    （spec R9：画像/skill 优先于默认 prompt，与路径 C 现有行为一致）
    """
    base = scene_prompt if scene_prompt else default
    return f"{skill_extra}\n{base}" if skill_extra else base
```

三路径调用处（[chat_path.py](file:///d:/java/agentprojects/agentx/backend/app/paths/chat_path.py)、`tool_path.py`、[deep_path.py](file:///d:/java/agentprojects/agentx/backend/app/paths/deep_path.py)）统一改为：

```python
system_prompt = resolve_system_prompt(
    default=get_settings().default_system_prompt,
    scene_prompt=req.system_prompt,
    skill_extra=system_prompt_extra,
)
```

### 4.6 run_router 主入口

[graph.py::run_router](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py) 签名加 `system_prompt: str | None = None`；[main.py::chat](file:///d:/java/agentprojects/agentx/backend/app/main.py#L534) 端点把 `req.system_prompt` 透传。

### 4.7 preload + api-types 契约同步（§13 三处对齐）

[preload/index.ts:61-70](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts#L61-L70) `streamChat` 加 `systemPrompt` 选项：

```ts
async function streamChat(
  msg: { role: string; content: string },
  opts?: { threadId?: string; systemPrompt?: string },
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.content,
      thread_id: opts?.threadId ?? "",
      system_prompt: opts?.systemPrompt ?? null,
    }),
  });
  // ...（其余不变）
}
```

[shared/api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts) 的 `chat.send` 重载类型同步加 `systemPrompt?: string`。

### 4.8 兼容性

- `system_prompt` 是可选字段，老客户端不传 → 后端回退 `default_system_prompt`，行为不变
- 不改 SSE 事件类型，不改 `/api/config/reload` 的 `default_system_prompt` 语义
- 不破坏 [preload/index.ts](file:///d:/java/agentprojects/agentx/frontend/preload/index.ts) 现有调用方

## 5. 测试

### 5.1 后端单元测试（不依赖 myserver）

`tests/python/unit/test_router_graph.py`：

```python
def test_resolve_system_prompt_default_only():
    assert resolve_system_prompt("default", None, None) == "default"

def test_resolve_system_prompt_scene_overrides_default():
    assert resolve_system_prompt("default", "coding-prompt", None) == "coding-prompt"

def test_resolve_system_prompt_skill_prepended():
    result = resolve_system_prompt("default", "coding-prompt", "skill-content")
    assert result == "skill-content\ncoding-prompt"
```

`tests/python/unit/test_chat_endpoint.py`：验证 `ChatRequest` 带 `system_prompt` 字段时路径 A 接收并覆盖 default（mock `get_chat_model`，捕获传入的 messages，断言 `SystemMessage.content == req.system_prompt`）。

### 5.2 渲染层测试（vitest）

`tests/renderer/stores/scene.test.ts`：
- 默认 `scene === "work"`
- `setScene("coding")` 后 localStorage 持久化生效
- `SCENE_PROMPTS` 两个 key 都是非空字符串

`tests/renderer/App.test.tsx`（补充）：
- 渲染 App，断言标题栏存在 Work / Coding 两个按钮
- 点击 Coding，断言 `store.scene === "coding"`，激活 class 切换

### 5.3 手动验收

1. `npm run dev` 启动
2. 标题栏看到 `[ Work | Coding ]`，Work 高亮（紫色 #4f46e5）
3. 发送"你是谁"，后端 SystemMessage 应包含 work prompt
4. 切换到 Coding，发送"你是谁"，应得到 coding prompt 风格回答
5. 重启应用，localStorage 持久化 → 上次场景被记住
6. 不传 `system_prompt`（模拟旧客户端）→ 后端回退 default_system_prompt，无 regression

## 6. 不做的事（YAGNI）

- 不加 e2e 测试（项目无 e2e 框架）
- 不测试场景 prompt 文案内容（文案易变，只测"非空"和"key 完整"）
- 不做场景图标、场景主题色、场景工具/路径差异（用户明确说后续再说）
- 不改 `/api/config/reload` 的 `default_system_prompt` 语义
- 不改 SSE 事件类型

## 7. 影响面

| 文件 | 改动类型 |
|---|---|
| `frontend/renderer/stores/scene.ts` | 新建 |
| `frontend/renderer/App.tsx` | 改：标题栏加分段按钮 |
| `frontend/renderer/stores/chat.ts` | 改：`send` 时读 sceneStore 拼 systemPrompt |
| `frontend/preload/index.ts` | 改：`streamChat` 加 `systemPrompt` opt + body 字段 |
| `frontend/shared/api-types.ts` | 改：`chat.send` 类型同步 |
| `backend/app/main.py` | 改：`ChatRequest` 加字段 + `chat()` 透传 |
| `backend/app/router/graph.py` | 改：`run_router` 加参数 + 新增 `resolve_system_prompt` |
| `backend/app/paths/chat_path.py` | 改：取 prompt 处用 `resolve_system_prompt` |
| `backend/app/paths/tool_path.py` | 改：同上 |
| `backend/app/paths/deep_path.py` | 改：同上 |
| `tests/python/unit/test_router_graph.py` | 新建/补充 |
| `tests/python/unit/test_chat_endpoint.py` | 补充 |
| `tests/renderer/stores/scene.test.ts` | 新建 |
| `tests/renderer/App.test.tsx` | 补充 |

## 8. 风险与回滚

- **风险**：preload + api-types + 后端三处契约不同步 → SSE 流异常。
  - 缓解：§13 要求三处同步，本设计已显式列出，PR 自检清单包含此项。
- **风险**：场景 prompt 文案过长导致 token 浪费。
  - 缓解：当前两份 prompt 都 < 200 字，可忽略。
- **回滚**：若场景切换器引入问题，前端把分段按钮隐藏 + `useChatStore.send` 不传 `systemPrompt` 即可；后端字段为可选，无需回滚。
