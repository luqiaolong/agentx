# ContextUsage 视觉强化 + 模型 token 容量配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ContextUsage widget 加大到 28×12 + 4px 条纹以提升区分度；ModelEditor 加「上下文容量」「输出 token 上限」两个 k tokens 输入，并传 max_tokens 到后端 ChatOpenAI

**Architecture:** widget 仅样式改动（className），保留 justify-end 底部向上填充；ModelEntry 加 maxOutputTokens 字段；设置面板数字输入以 k tokens 为单位、内部 ×1000 落库；后端通过 AGENTX_MAX_OUTPUT_TOKENS env → pydantic-settings → llm.get_chat_model 透传 ChatOpenAI max_tokens

**Tech Stack:** React 18 + TypeScript + Tailwind v4 + zustand + lucide-react / Python 3.11 + pydantic-settings + langchain-openai / vitest + pytest

**Spec:** [`docs/superpowers/specs/2026-07-05-context-usage-and-model-config-design.md`](../specs/2026-07-05-context-usage-and-model-config-design.md)

---

## File Structure

| 文件 | 类型 | 职责 |
|---|---|---|
| `frontend/shared/api-types.ts` | 修改 | ModelEntry 加 `maxOutputTokens?: number \| null` |
| `frontend/renderer/components/chat/ContextUsage.tsx` | 修改 | 尺寸 18×10 → 28×12，条纹 2.5px → 4px |
| `frontend/renderer/stores/contextUsage.ts` | 微调 | 加 contextWindow=0 降级保护 |
| `frontend/renderer/components/settings/ModelProviderSettings.tsx` | 修改 | PROVIDER_PRESETS 加 defaultContextK/defaultOutputK；ModelEditor 加 2 个 input；handleProviderChange 同步默认值 |
| `frontend/main/python/spawn.ts` | 修改 | PythonCredentials 加 maxOutputTokens；buildEnv 写 AGENTX_MAX_OUTPUT_TOKENS |
| `backend/app/config.py` | 修改 | Settings 加 `max_output_tokens: int \| None = None` |
| `backend/app/llm.py` | 修改 | 三分支（DeepSeek/OpenAI/兜底）条件性注入 kwargs["max_tokens"] |
| `tests/renderer/context-usage.test.tsx` | 修改 | 加 widget 尺寸 + zero-contextWindow 降级断言 |
| `tests/python/unit/test_llm_max_tokens.py` | 新建 | llm max_tokens 透传测试（mock ChatOpenAI） |

---

## Task 1: ModelEntry 加 maxOutputTokens 字段

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\shared\api-types.ts:197-209`

- [ ] **Step 1: 修改 ModelEntry 接口**

找到（约 197-209 行，参考 [api-types.ts:197-209](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L197-L209)）：

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
  /** 模型最大上下文 token 上限（用户在「设置 → 模型」可选填入）；
   *  undefined / null → useContextUsage 降级使用默认 16000 */
  contextWindow?: number | null;
}
```

改为：

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
  /** 输入上下文 token 上限（用户在「设置 → 模型」可选填入）；
   *  undefined / null → useContextUsage 降级使用默认 16000 */
  contextWindow?: number | null;
  /** 模型单次响应输出 token 上限（透传到 ChatOpenAI.max_tokens）；
   *  undefined / null → 不设上限（langchain-openai 走模型默认） */
  maxOutputTokens?: number | null;
}
```

- [ ] **Step 2: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无新增错误。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/shared/api-types.ts && git commit -m "feat(types): ModelEntry 加 maxOutputTokens 可选字段"
```

---

## Task 2: ContextUsage widget 加大尺寸（18×10 → 28×12）

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\chat\ContextUsage.tsx`

- [ ] **Step 1: 改动 widget 容器类名**

找到（约 22 行）：

```tsx
    <div
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex h-[18px] w-[10px] flex-col items-stretch justify-end gap-[1px] cursor-help"
    >
```

改为：

```tsx
    <div
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex h-7 w-3 flex-col items-stretch justify-end gap-[1.5px] cursor-help"
    >
```

- [ ] **Step 2: 改动条纹类名**

找到（约 32 行）：

```tsx
            className={[
              "h-[2.5px] w-full rounded-[0.5px]",
              filled ? "bg-brand-500" : "bg-default",
            ].join(" ")}
```

改为：

```tsx
            className={[
              "h-1 w-full rounded-[0.5px]",
              filled ? "bg-brand-500" : "bg-default",
            ].join(" ")}
```

> `h-1` = 4px（Tailwind 默认间距尺度）

- [ ] **Step 3: 跑现有测试确认未破**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：5/5 PASS（widget 内部行为不变，仅尺寸变化）。

- [ ] **Step 4: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 5: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/chat/ContextUsage.tsx && git commit -m "style(context-usage): widget 加大到 28×12 + 4px 条纹提升视觉区分度"
```

---

## Task 3: useContextUsage 加 contextWindow=0 降级保护

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\stores\contextUsage.ts`

- [ ] **Step 1: 改 selector 中的 modelMax 推导**

找到（约 30 行）：

```ts
  const activeEntry = entries.find((e) => e.id === activeId) ?? null;
  const modelMax = activeEntry?.contextWindow ?? 16000;
  const activeLabel = activeEntry?.label ?? "";
```

改为：

```ts
  const activeEntry = entries.find((e) => e.id === activeId) ?? null;
  // 防御性保护：contextWindow 必须是正整数；0 / null / undefined 均降级到默认 16000
  const rawMax = activeEntry?.contextWindow;
  const modelMax = rawMax && rawMax > 0 ? rawMax : 16000;
  const activeLabel = activeEntry?.label ?? "";
```

- [ ] **Step 2: 在测试加 zero-contextWindow 降级断言**

在 `tests/renderer/context-usage.test.tsx` 末尾追加：

```tsx
describe("useContextUsage selector 边界", () => {
  it("active model contextWindow=0 时降级使用默认 16000", () => {
    useModelStore.setState({
      entries: [
        {
          id: "m1",
          label: "ZeroCtx",
          providerId: "custom",
          model: "x",
          baseUrl: "",
          apiKey: "",
          createdAt: 0,
          contextWindow: 0,
        },
      ],
      activeId: "m1",
    });
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.max)).toBe(16000);
  });
});
```

- [ ] **Step 3: 跑测试**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx
```

期望：6/6 PASS（含新加 1 条）。

- [ ] **Step 4: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/stores/contextUsage.ts tests/renderer/context-usage.test.tsx && git commit -m "fix(context-usage): contextWindow=0 降级到默认 16000，避免除零"
```

---

## Task 4: PROVIDER_PRESETS 加 defaultContextK/defaultOutputK

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\settings\ModelProviderSettings.tsx:28-53`

- [ ] **Step 1: 扩 PROVIDER_PRESETS 类型**

找到（约 28-31 行）：

```ts
const PROVIDER_PRESETS: Record<
  Exclude<ModelProviderId, "custom">,
  { label: string; desc: string; docs: string; defaultModel: string; defaultBaseUrl: string }
> = {
```

改为：

```ts
const PROVIDER_PRESETS: Record<
  Exclude<ModelProviderId, "custom">,
  {
    label: string;
    desc: string;
    docs: string;
    defaultModel: string;
    defaultBaseUrl: string;
    defaultContextK: number;
    defaultOutputK: number;
  }
> = {
```

- [ ] **Step 2: 给三个预设加默认值**

找到（约 32-53 行），把 openai / deepseek / minimax 三个 block 中各加两个字段：

```ts
  openai: {
    label: "OpenAI",
    desc: "GPT-4o / o1 / o3 系列",
    docs: "https://platform.openai.com/api-keys",
    defaultModel: "gpt-4o-mini",
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultContextK: 128,
    defaultOutputK: 4,
  },
  deepseek: {
    label: "DeepSeek",
    desc: "DeepSeek-V3 / R1",
    docs: "https://platform.deepseek.com/api_keys",
    defaultModel: "deepseek-chat",
    defaultBaseUrl: "https://api.deepseek.com",
    defaultContextK: 64,
    defaultOutputK: 8,
  },
  minimax: {
    label: "MiniMax",
    desc: "MiniMax-M3 / abab 系列",
    docs: "https://platform.minimaxi.com/",
    defaultModel: "MiniMax-M3",
    defaultBaseUrl: "https://api.minimaxi.com/v1",
    defaultContextK: 128,
    defaultOutputK: 16,
  },
```

- [ ] **Step 3: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 4: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/settings/ModelProviderSettings.tsx && git commit -m "feat(settings): PROVIDER_PRESETS 加 defaultContextK / defaultOutputK 默认值"
```

---

## Task 5: ModelEditor 加上下文容量 + 输出 token 上限 2 个 input

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\settings\ModelProviderSettings.tsx:14-20, 422-465`

- [ ] **Step 1: import lucide 新图标**

找到（约 1-20 行）：

```tsx
import {
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  Pencil,
  Trash2,
  Save,
  X,
  Check,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  RotateCw,
  RefreshCw,
  ExternalLink,
  Server,
} from "lucide-react";
```

改为：

```tsx
import {
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  Pencil,
  Trash2,
  Save,
  X,
  Check,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  RotateCw,
  RefreshCw,
  ExternalLink,
  Server,
  Sliders,
  ArrowDownToLine,
} from "lucide-react";
```

- [ ] **Step 2: 在 API Key 字段之后插入两个新 input**

找到（约 422-452 行，API Key 字段的 `</div>` 之后、「获取密钥链接」`{preset && ...}` 之前），插入：

```tsx
      {/* 上下文容量（k tokens = 1×1000 存储）*/}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <Sliders className="h-3 w-3 text-muted-c" />
          上下文容量
          <span className="text-muted-c">（k tokens，输入上限）</span>
        </label>
        <input
          type="number"
          min="1"
          step="1"
          value={
            draft.contextWindow && draft.contextWindow > 0
              ? String(Math.round(draft.contextWindow / 1000))
              : ""
          }
          onChange={(e) => {
            const k = e.target.value ? Number(e.target.value) : null;
            setDraft((s) => ({
              ...s,
              contextWindow: k && k > 0 ? k * 1000 : null,
            }));
          }}
          placeholder={preset ? String(preset.defaultContextK) : "例：128"}
          className="input-field font-mono text-[11px]"
        />
        {draft.contextWindow && draft.contextWindow > 0 && (
          <p className="mt-1 text-[10px] text-muted-c">
            ≈ {draft.contextWindow.toLocaleString()} tokens · 决定右下角
            ContextUsage widget 的分母
          </p>
        )}
      </div>

      {/* 输出 token 上限 */}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <ArrowDownToLine className="h-3 w-3 text-muted-c" />
          输出 token 上限
          <span className="text-muted-c">（k tokens，单次响应）</span>
        </label>
        <input
          type="number"
          min="1"
          step="1"
          value={
            draft.maxOutputTokens && draft.maxOutputTokens > 0
              ? String(Math.round(draft.maxOutputTokens / 1000))
              : ""
          }
          onChange={(e) => {
            const k = e.target.value ? Number(e.target.value) : null;
            setDraft((s) => ({
              ...s,
              maxOutputTokens: k && k > 0 ? k * 1000 : null,
            }));
          }}
          placeholder={preset ? String(preset.defaultOutputK) : "例：4 / 8 / 16"}
          className="input-field font-mono text-[11px]"
        />
        <p className="mt-1 text-[10px] text-muted-c">
          激活后经 AGENTX_MAX_OUTPUT_TOKENS 传给后端 ChatOpenAI；留空不限制
        </p>
      </div>
```

- [ ] **Step 3: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 4: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/settings/ModelProviderSettings.tsx && git commit -m "feat(settings): ModelEditor 加上下文容量 + 输出 token 上限 2 个 input"
```

---

## Task 6: handleProviderChange 同步 context/maxOutput 默认值

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\renderer\components\settings\ModelProviderSettings.tsx:267-289`

- [ ] **Step 1: 改 handleProviderChange**

找到（约 267-289 行）：

```ts
  const handleProviderChange = (id: ModelProviderId): void => {
    setDraft((s) => {
      if (id !== "custom") {
        const p = PROVIDER_PRESETS[id];
        // 若当前 model/baseUrl 为空或等于其他预设的默认值，则替换为新预设的默认值
        const modelIsDefault =
          !s.model ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultModel === s.model);
        const urlIsDefault =
          !s.baseUrl ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultBaseUrl === s.baseUrl);
        return {
          ...s,
          providerId: id,
          model: modelIsDefault ? p.defaultModel : s.model,
          baseUrl: urlIsDefault ? p.defaultBaseUrl : s.baseUrl,
        };
      }
      return { ...s, providerId: id };
    });
    setErrs({});
  };
```

改为：

```ts
  const handleProviderChange = (id: ModelProviderId): void => {
    setDraft((s) => {
      if (id !== "custom") {
        const p = PROVIDER_PRESETS[id];
        // 若当前 model/baseUrl 为空或等于其他预设的默认值，则替换为新预设的默认值
        const modelIsDefault =
          !s.model ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultModel === s.model);
        const urlIsDefault =
          !s.baseUrl ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultBaseUrl === s.baseUrl);
        // contextWindow / maxOutputTokens 同样按 k 值匹配：当前值是某个预设 k*1000 即视为默认
        const contextKOptions = Object.values(PROVIDER_PRESETS).map((pp) => pp.defaultContextK * 1000);
        const outputKOptions = Object.values(PROVIDER_PRESETS).map((pp) => pp.defaultOutputK * 1000);
        const contextIsDefault =
          !s.contextWindow || contextKOptions.includes(s.contextWindow);
        const outputIsDefault =
          !s.maxOutputTokens || outputKOptions.includes(s.maxOutputTokens);
        return {
          ...s,
          providerId: id,
          model: modelIsDefault ? p.defaultModel : s.model,
          baseUrl: urlIsDefault ? p.defaultBaseUrl : s.baseUrl,
          contextWindow: contextIsDefault ? p.defaultContextK * 1000 : s.contextWindow,
          maxOutputTokens: outputIsDefault ? p.defaultOutputK * 1000 : s.maxOutputTokens,
        };
      }
      return { ...s, providerId: id };
    });
    setErrs({});
  };
```

- [ ] **Step 2: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/renderer/components/settings/ModelProviderSettings.tsx && git commit -m "feat(settings): handleProviderChange 同步 contextWindow / maxOutputTokens 默认值"
```

---

## Task 7: 后端 Settings 加 max_output_tokens

**Files:**
- Modify: `d:\java\agentprojects\agentx\backend\app\config.py:229-235`

- [ ] **Step 1: 加 Settings 字段**

找到（约 229-235 行 `# ---- LLM ----` 区块，`default_model` 字段之后）：

```python
    # ---- LLM ----
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI 兼容端点（中转服务、MiniMax Token Plan 等）
    anthropic_api_key: str | None = None
    dashscope_api_key: str | None = None
    deepseek_api_key: str | None = None
    default_model: str = "minimax-m3"
```

改为：

```python
    # ---- LLM ----
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI 兼容端点（中转服务、MiniMax Token Plan 等）
    anthropic_api_key: str | None = None
    dashscope_api_key: str | None = None
    deepseek_api_key: str | None = None
    default_model: str = "minimax-m3"
    # 单次响应最大 token 数；None = 不限制（依赖模型默认）
    # 从 AGENTX_MAX_OUTPUT_TOKENS env 读取；用户在 ModelProviderSettings 设置面板填写
    max_output_tokens: int | None = None
```

- [ ] **Step 2: 跑后端 Python 单元测试**

```bash
cd d:\java\agentprojects\agentx && uv run pytest tests/python/unit -m "not integration" -q
```

期望：现有测试无破坏。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add backend/app/config.py && git commit -m "feat(config): Settings 加 max_output_tokens 可选字段（环境变量 AGENTX_MAX_OUTPUT_TOKENS）"
```

---

## Task 8: llm.get_chat_model 三分支条件性透传 max_tokens

**Files:**
- Modify: `d:\java\agentprojects\agentx\backend\app\llm.py`

- [ ] **Step 1: DeepSeek 分支加 max_tokens**

找到（约 41-46 行）：

```python
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=temperature,
            streaming=streaming,
        )
```

改为：

```python
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.deepseek_api_key,
            "base_url": "https://api.deepseek.com",
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)
```

- [ ] **Step 2: OpenAI 分支加 max_tokens**

找到（约 53-59 行）：

```python
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            streaming=streaming,
        )
```

改为：

```python
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.openai_api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)
```

- [ ] **Step 3: 兜底分支加 max_tokens**

找到（约 64-73 行）：

```python
    if settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.openai_api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        return ChatOpenAI(**kwargs)
```

改为（追加一行 if）：

```python
    if settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": settings.openai_api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if settings.max_output_tokens:
            kwargs["max_tokens"] = settings.max_output_tokens
        return ChatOpenAI(**kwargs)
```

- [ ] **Step 4: TypeScript/语法快速 sanity**

```bash
cd d:\java\agentprojects\agentx && uv run python -c "from app.llm import get_chat_model; print('import OK')"
```

期望：`import OK`

- [ ] **Step 5: 提交**

```bash
cd d:\java\agentprojects\agentx && git add backend/app/llm.py && git commit -m "feat(llm): get_chat_model 三分支条件性透传 max_tokens"
```

---

## Task 9: 后端 llm 单元测试（mock ChatOpenAI 验证 max_tokens 注入）

**Files:**
- Create: `d:\java\agentprojects\agentx\tests\python\unit\test_llm_max_tokens.py`

- [ ] **Step 1: 写测试**

新建 `d:\java\agentprojects\agentx\tests\python\unit\test_llm_max_tokens.py`：

```python
"""``app.llm.get_chat_model`` 透传 ``settings.max_output_tokens`` 的单元测试。

通过 monkeypatch ChatOpenAI 捕获构造参数，避免真实网络与外部依赖。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.llm import get_chat_model


@pytest.fixture
def base_settings_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """最小可工作的 env：openai api key + 一个 gpt 模型 + 不设 max_output_tokens。"""
    monkeypatch.setenv("AGENTX_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENTX_DEFAULT_MODEL", "gpt-4o-mini")
    # 显式清除可能从父进程串入的 AGENTX_MAX_OUTPUT_TOKENS
    monkeypatch.delenv("AGENTX_MAX_OUTPUT_TOKENS", raising=False)
    return {}


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_max_tokens_not_injected_when_unset(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    base_settings_env: dict[str, str],
) -> None:
    settings = Settings()
    settings.openai_api_key = "test-key"
    settings.default_model = "gpt-4o-mini"
    settings.max_output_tokens = None
    mock_get_settings.return_value = settings
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert "max_tokens" not in kwargs, (
        f"未设置 max_output_tokens 时不应注入 max_tokens, kwargs={kwargs!r}"
    )


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_max_tokens_injected_when_set(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    base_settings_env: dict[str, str],
) -> None:
    # 设置环境变量让 pydantic-settings 读取
    monkeypatch.setenv("AGENTX_MAX_OUTPUT_TOKENS", "8192")
    settings = Settings()
    settings.openai_api_key = "test-key"
    settings.default_model = "gpt-4o-mini"
    settings.max_output_tokens = 8192
    mock_get_settings.return_value = settings
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("max_tokens") == 8192, (
        f"应注入 max_tokens=8192, kwargs={kwargs!r}"
    )


@patch("app.llm.get_settings")
@patch("langchain_openai.ChatOpenAI")
def test_max_tokens_injected_for_fallback_branch(
    mock_chat: MagicMock,
    mock_get_settings: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底分支（openai_base_url + 不在 deepseek/gpt 前缀）也应注入 max_tokens。"""
    monkeypatch.setenv("AGENTX_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENTX_DEFAULT_MODEL", "MiniMax-M3")
    monkeypatch.setenv("AGENTX_OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    monkeypatch.setenv("AGENTX_MAX_OUTPUT_TOKENS", "16384")

    settings = Settings()
    settings.openai_api_key = "test-key"
    settings.default_model = "MiniMax-M3"
    settings.openai_base_url = "https://api.minimaxi.com/v1"
    settings.max_output_tokens = 16384
    mock_get_settings.return_value = settings
    mock_chat.return_value = MagicMock(name="chat_instance")

    get_chat_model(temperature=0.7)

    kwargs = mock_chat.call_args.kwargs
    assert kwargs.get("max_tokens") == 16384
    assert kwargs.get("base_url") == "https://api.minimaxi.com/v1"
```

- [ ] **Step 2: 跑测试**

```bash
cd d:\java\agentprojects\agentx && uv run pytest tests/python/unit/test_llm_max_tokens.py -v
```

期望：3/3 PASS。

- [ ] **Step 3: 提交**

```bash
cd d:\java\agentprojects\agentx && git add tests/python/unit/test_llm_max_tokens.py && git commit -m "test(llm): 验证 get_chat_model 三分支按 settings.max_output_tokens 注入 max_tokens"
```

---

## Task 10: spawn.ts 加 maxOutputTokens 透传（前端 main → python env）

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\main\python\spawn.ts`

- [ ] **Step 1: 加 PythonCredentials 字段**

找到（约 32-60 行）：

```ts
export interface PythonCredentials {
  openaiApiKey?: string;
  anthropicApiKey?: string;
  milvusUser?: string;
  milvusPassword?: string;
  langsmithApiKey?: string;
  embeddingUrl?: string;
  deepseekApiKey?: string;
  tavilyApiKey?: string;
  // T7 新增
  defaultModel?: string;
  openaiBaseUrl?: string;
  systemPrompt?: string;
  approvalMaxWait?: number;
  maxUploadBytes?: number;
  thinkFilterMaxHold?: number;
```

改为（在 `thinkFilterMaxHold?: number;` 行后追加）：

```ts
export interface PythonCredentials {
  openaiApiKey?: string;
  anthropicApiKey?: string;
  milvusUser?: string;
  milvusPassword?: string;
  langsmithApiKey?: string;
  embeddingUrl?: string;
  deepseekApiKey?: string;
  tavilyApiKey?: string;
  // T7 新增
  defaultModel?: string;
  openaiBaseUrl?: string;
  systemPrompt?: string;
  approvalMaxWait?: number;
  maxUploadBytes?: number;
  thinkFilterMaxHold?: number;
  /** ModelProviderSettings 设置面板填入的 maxOutputTokens
   * （千 token × 1000 → 实际 token 数） */
  maxOutputTokens?: number;
```

- [ ] **Step 2: 在 buildEnv 写 AGENTX_MAX_OUTPUT_TOKENS**

找到 `buildEnv` 函数（应在文件较后部分），注入 env：

```ts
    if (creds.maxOutputTokens && creds.maxOutputTokens > 0) {
      env.AGENTX_MAX_OUTPUT_TOKENS = String(creds.maxOutputTokens);
    }
```

> 放在已有的同类 env 写入处附近（defaultModel/openaiBaseUrl 写入后）。

- [ ] **Step 3: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 4: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/main/python/spawn.ts && git commit -m "feat(spawn): PythonCredentials 加 maxOutputTokens，buildEnv 写 AGENTX_MAX_OUTPUT_TOKENS"
```

---

## Task 11: renderer reloadConfig 链路补 maxOutputTokens

**Files:**
- Modify: `d:\java\agentprojects\agentx\frontend\main\index.ts` （可能也涉及 [index.ts](file:///d:/java/agentprojects/agentx/frontend/main/index.ts) 调用链）

- [ ] **Step 1: 检查现有 reloadBackendConfig 调用链**

读 `frontend/main/index.ts`，找到 `reloadBackendConfig` IPC handler：

```bash
grep -n "reloadBackendConfig\|rebuildCredentials\|app:reloadBackendConfig" frontend/main/index.ts
```

找到把 creds 传给 `spawn.ts::buildEnv` 的地方。

- [ ] **Step 2: 注入 maxOutputTokens**

在调用 `buildEnv(creds)` 之前，从 `getActiveModelEntry()` 读取 `maxOutputTokens`：

```ts
const activeEntry = getActiveModelEntry();
if (activeEntry?.maxOutputTokens && activeEntry.maxOutputTokens > 0) {
  creds.maxOutputTokens = activeEntry.maxOutputTokens;
}
```

（具体位置依赖现有代码结构；若现有 creds 构造已经有 activeEntry 引用，则可直接 `creds.maxOutputTokens = activeEntry.maxOutputTokens`）

> **本任务可能比其他任务更临时性**——若代码结构差异大，按现状等效修改即可。

- [ ] **Step 3: TypeScript 校验**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 4: 提交**

```bash
cd d:\java\agentprojects\agentx && git add frontend/main/index.ts && git commit -m "feat(reload): reloadBackendConfig 把 activeEntry.maxOutputTokens 注入 env"
```

> 若 Step 2 改动实际无新行（已在 reload path 中），可仅提交一个微注释或缩小改动。

---

## Task 12: 全量验证

**Files:** —
- 验收：跑命令验证

- [ ] **Step 1: 全量 TypeScript**

```bash
cd d:\java\agentprojects\agentx && npm run typecheck
```

期望：无错误。

- [ ] **Step 2: 全量 renderer 测试**

```bash
cd d:\java\agentprojects\agentx && npx vitest run tests/renderer/context-usage.test.tsx tests/renderer/chat-composer.test.tsx tests/renderer/permission-toggle.test.tsx
```

期望：全部 PASS（合计 33 测试 = 6 + 6 + 16 + 5 ... 视累计）。

- [ ] **Step 3: 全量 Python 单元测试**

```bash
cd d:\java\agentprojects\agentx && uv run pytest tests/python/unit -m "not integration" -q
```

期望：现有测试 + 新增 3 条全部 PASS。

- [ ] **Step 4: 视觉走查清单**（运行 `npm run dev` 后）

- [ ] ChatComposer Toolbar 右组最左 widget 明显比之前大，1 vs 5 条纹区分肉眼可见
- [ ] 「设置 → 模型」编辑任一模型，新增 2 个 input（上下文容量 / 输出 token 上限）
- [ ] 输入 k token 数（数字）后下方 token 数提示更新
- [ ] 切换服务商（如从 OpenAI 到 DeepSeek）2 个 input 自动填入对应预设值
- [ ] 保存后 active 模型的字段变更 → reloadBackendConfig 触发 → ChatOpenAI 接收 max_tokens
- [ ] 留空（不填）时后端走模型默认（不传 max_tokens）

- [ ] **Step 5: 列出本任务全部 commit 概览**

```bash
cd d:\java\agentprojects\agentx && git log --oneline -12
```

期望：本任务至少 11 个新 commit（Task 1-11）。

- [ ] **Step 6: 提交（如有 Step 4 视觉微调）**

若有 padding / 字号微调，独立 `style:` 提交。无则跳过。

---

## Self-Review

**Spec coverage check:**

| Spec 章节 | 对应任务 |
|---|---|
| §1 产品结论 | Task 2 (widget 尺寸) + Task 5 (UI 字段) |
| §2 用户工作流 | Task 5/10/11 (设置 → reload → 后端) |
| §3.1 ModelEntry maxOutputTokens | Task 1 |
| §3.2 useContextUsage 零值保护 | Task 3 |
| §3.3 Settings.max_output_tokens | Task 7 |
| §4.1 ContextUsage 28×12 + 4px | Task 2 |
| §4.2 ModelEditor 加字段 | Task 5 |
| §4.3 PROVIDER_PRESETS 加默认值 | Task 4 |
| handleProviderChange 同步 | Task 6 |
| §5.1 数据流 | Task 1+7+10+11 |
| §5.2 llm 三分支 max_tokens 透传 | Task 8 |
| §5.5 测试 | Task 3 (前端) + Task 9 (后端) |
| §6 文件改动清单 | Task 1-11 完整覆盖 9 个文件 |

**Type consistency:**
- `ModelEntry.maxOutputTokens` 在 Task 1 定义 → 在 Task 5/6/10/11 引用一致
- `Settings.max_output_tokens` 在 Task 7 定义 → 在 Task 8 引用一致
- `PythonCredentials.maxOutputTokens` 在 Task 10 定义 → Task 11 引用一致

**Placeholders:** 无 TBD / TODO / 待补。

**Known risks to monitor:**
- Task 11 改动依赖 reloadBackendConfig 实现现状；如 reload path 较复杂可缩小本任务范围或拆分为多个 PR
- Task 9 测试 monkeypatch 的 `app.llm.get_settings` 与 `langchain_openai.ChatOpenAI`；如 import path 不同需要适配
