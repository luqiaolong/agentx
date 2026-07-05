# ContextUsage 视觉强化 + 模型配置 token 容量设置

> 日期：2026-07-05
> 主题：widget 加大尺寸提升区分度 + ModelEditor 加输入/输出 token 字段
> 状态：已确认设计，待实现

## 1. 产品结论

**问题现状**

刚由 commit `5422710` 落地的 ContextUsage widget（18×10px + 5 条 2.5px 高的纵向条纹）在用户实际渲染中视觉区分度过低：5 条全填充 vs 仅 1 条填充看上去几乎都是「一条小横线」，导致：
- 用户看不出当前会话 token 占模型上限的多少
- 「从底部往上涨」的填充方向几乎无法察觉（条纹太细方向感丢失）

同时 [ModelProviderSettings.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/ModelProviderSettings.tsx) 编辑器字段不完整：
- 缺「输入上下文容量」(contextWindow) — 用户无法配置模型实际支持的上下文窗口
- 缺「输出 token 上限」(maxOutputTokens) — 用户无法控制单次响应最大 token 数

**改后效果**

```
改前 widget：
                  ░
                  ░  ← 1 条 dark 几乎看不见
                  ░
                  ░
                  ▓▓
─────────────────────────────────────
改后 widget（28×12，4px 条纹）：
                  ░
                  ░
                  ░
                  ░
                  ▓
                  ▓
                  ▓
                  ▓  ← 1 条 vs 5 条差异肉眼明显
                  ▓
                  ▓
                  ▓
                  ▓
─────────────────────────────────────
```

设置面板新增字段：
- 「上下文容量（k tokens，输入上限）」+ 「输出 token 上限（k tokens，单次响应）」
- 切换服务商时按预设自动填默认

**核心改动**

1. ContextUsage widget 尺寸 18×10 → 28×12，条纹 2.5px → 4px，肉眼可见区分度
2. ModelEntry 加 `maxOutputTokens?: number | null` 字段
3. ModelProviderSettings 编辑器加「上下文容量」+「输出 token 上限」两个数字输入（单位 k tokens）
4. 后端 `Settings.max_output_tokens` + `llm.get_chat_model()` 透传到 `ChatOpenAI(max_tokens=...)`（用户要求"传给后端"）
5. Spawn.ts 通过 `AGENTX_MAX_OUTPUT_TOKENS` 环境变量注入

## 2. 用户工作流

```
用户在 ChatComposer 看见 widget 区分度提升
  → 1 条 dark: 底部一格暗矩形
  → 5 条 dark: 整个 widget 都是黑灰
  → 切换会话/会话增长 → widget 立即变化

用户点击「模型 ▼」进入设置
  → 添加/编辑模型时看到新的 2 个字段
  → 切换服务商，空白字段自动填入该 provider 预设（如 MiniMax = 128k / 16k）
  → 保存 → contextWindow 立即影响 widget 分母
       → maxOutputTokens 经热更新（如已激活条目）传到后端 LLM 调用
```

## 3. 数据模型

### 3.1 ModelEntry 扩展（[api-types.ts](file:///d:/java/agentprojects/agentx/frontend/shared/api-types.ts#L197-L212)）

```ts
export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  apiKey: string;
  createdAt: number;
  /** 输入上下文 token 上限（用户设置面板输入；单位 token 个数）；
   *  undefined/null 或 ≤0 → useContextUsage 降级使用默认 16000 */
  contextWindow?: number | null;
  /** 模型单次响应输出 token 上限（透传到 ChatOpenAI.max_tokens）；
   *  undefined/null → 不设上限（langchain-openai 走模型默认） */
  maxOutputTokens?: number | null;
}
```

### 3.2 useContextUsage selector 加零值保护

`Math.max(1, ...)` 已能避免除零，但 `contextWindow=0` 仍是合法 number 不会被 nullish 拦下。增加显式校验：

```ts
const rawMax = activeEntry?.contextWindow;
const modelMax = rawMax && rawMax > 0 ? rawMax : 16000;
```

### 3.3 Settings 后端加字段（[backend/app/config.py](file:///d:/java/agentprojects/agentx/backend/app/config.py#L216-L306)）

```python
# 在 # ---- LLM ---- 区块加：
max_output_tokens: int | None = None  # 单次响应输出上限；None=不限制
```

> 与现有 `context_max_messages` / `context_max_tokens` 一致使用 `int | None` 风格。env 前缀 `AGENTX_MAX_OUTPUT_TOKENS`，未设置时为 `None`。

## 4. UI 规范

### 4.1 ContextUsage 组件（[ContextUsage.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/chat/ContextUsage.tsx)）

**class 改动**

```tsx
// 前：
<div className="inline-flex h-[18px] w-[10px] flex-col items-stretch justify-end gap-[1px] cursor-help">
  <span className="h-[2.5px] w-full rounded-[0.5px]" ... />

// 后：
<div className="inline-flex h-7 w-3 flex-col items-stretch justify-end gap-[1.5px] cursor-help">
  <span className="h-1 w-full rounded-[0.5px]" ... />   // h-1 = 4px
```

**尺寸校验**
- 5 条 × 4px + 4 gap × 1.5px = 26px ≤ 28px ✓
- 容器顶部留 2px（justify-end 不破坏底部对齐）

**视觉对比**

| 用量 | 改前（18×10） | 改后（28×12） |
|---|---|---|
| 1 条 dark | 2.5×10 = 25 平方像素（几乎不可见） | 4×12 = 48 平方像素 |
| 5 条 dark | 16.5×10 = 165 | 26×12 = 312 |
| 比例 | 1:6.6 | 1:6.5 |

像素绝对量翻倍，肉眼区分度显著提升。颜色、tooltip 内容、data-filled 字段全部不变。

### 4.2 ModelEditor 加字段（[ModelProviderSettings.tsx](file:///d:/java/agentprojects/agentx/frontend/renderer/components/settings/ModelProviderSettings.tsx#L322-L497)）

**位置**：API Key 字段之后，「获取密钥链接」之前。

```tsx
{/* 上下文容量 */}
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
    value={contextWindow ? String(Math.round(contextWindow / 1000)) : ""}
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
  {contextWindow && (
    <p className="mt-1 text-[10px] text-muted-c">
      ≈ {contextWindow.toLocaleString()} tokens
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
    value={maxOutputTokens ? String(Math.round(maxOutputTokens / 1000)) : ""}
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
</div>
```

### 4.3 PROVIDER_PRESETS 加默认 k 值（约 28-53 行）

```ts
const PROVIDER_PRESETS: Record<...> = {
  openai: {
    label: "OpenAI",
    desc: "GPT-4o / o1 / o3 系列",
    docs: "https://platform.openai.com/api-keys",
    defaultModel: "gpt-4o-mini",
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultContextK: 128,   // 128k tokens
    defaultOutputK: 4,     // 4k tokens (OpenAI mini 模型常见)
  },
  deepseek: {
    ...
    defaultContextK: 64,
    defaultOutputK: 8,
  },
  minimax: {
    ...
    defaultContextK: 128,
    defaultOutputK: 16,
  },
};
```

**handleProviderChange 同步默认值**

```ts
const handleProviderChange = (id: ModelProviderId): void => {
  setDraft((s) => {
    if (id !== "custom") {
      const p = PROVIDER_PRESETS[id];
      // 已有 model / baseUrl / contextWindow / maxOutputTokens 同步替换逻辑
      const modelIsDefault = !s.model || somePresetDefault === s.model;
      const urlIsDefault = !s.baseUrl || somePresetDefault === s.baseUrl;
      const contextIsDefault = !s.contextWindow || somePresetDefault * 1000 === s.contextWindow;
      const outputIsDefault = !s.maxOutputTokens || somePresetDefault * 1000 === s.maxOutputTokens;
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

## 5. 行为与兼容

### 5.1 数据流

```
Renderer (ModelProviderSettings)
  → 填入 contextWindow / maxOutputTokens
  → setModelEntries() 写 electron-store
  → activateModel(id)（如激活条目）
    → 已激活条目的字段编辑后：调 activateModel + reloadBackendConfig
        → 主要 process spawn 时一次注入；
           本次走 reload_settings() 应也能更新 max_output_tokens
  → IPC: rebuildCredentials 调用前端 main store 读取 entry
  → Spawn.ts::buildEnv 写 AGENTX_MAX_OUTPUT_TOKENS=...
  → Backend Settings.max_output_tokens 自动加载（pydantic-settings）
  → llm.get_chat_model() 透传 max_tokens=...
```

### 5.2 后端改动（用户选定「传给后端」）

```python
# backend/app/llm.py::get_chat_model 中：
# DeepSeek 分支：
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

# OpenAI 与兜底分支同样模式
```

`Settings.max_output_tokens: int | None` → `Settings.max_output_tokens: int | None = None`（保留 None = 不限制）

### 5.3 Spawn.ts 改动（[frontend/main/python/spawn.ts](file:///d:/java/agentprojects/agentx/frontend/main/python/spawn.ts#L32-L60)）

```ts
export interface PythonCredentials {
  // ... 现有字段
  maxOutputTokens?: number |  // 来自 activeModelEntry.maxOutputTokens
}

// buildEnv 中：
if (creds.maxOutputTokens) {
  env.AGENTX_MAX_OUTPUT_TOKENS = String(creds.maxOutputTokens);
}
```

**Hot reload**：`reload_settings()` 走 `@lru_cache`，已能 hot-reload。`max_output_tokens` 是新字段会自动注册。

### 5.4 保留不变

- 所有现有 ChatComposer / ContextUsage / ModelProviderSettings 测试预期 PASS
- ChatComposer 整体结构不动
- Toolbar 中 widget 位置不动
- `useContextUsage` selector 行为仅加零值保护

### 5.5 测试

| 文件 | 新增内容 |
|---|---|
| `tests/renderer/context-usage.test.tsx` | 加 1 条「widget 高度 28px 宽度 12px」样式断言 |
| `tests/renderer/context-usage.test.tsx` | 加 1 条「contextWindow=0 降级用默认 16000」 |
| 后端 `tests/python/unit/test_llm_max_tokens.py`（或追加到现有 llm 测试） | llm 把 max_output_tokens 传 ChatOpenAI |

### 5.6 风险

- **风险 1**：编辑未激活条目时不应该传后端（仅持久化） — `saveEdit` 已有 `wasActive` 判断，仅 wasActive 时调 `activateModel + reloadBackendConfig`；新字段一并走相同路径
- **风险 2**：maxOutputTokens 误传过大触发后端 rate limit — ChatOpenAI 自身做截断，不会有运行时 panic；可在 unit test 加 `kwargs["max_tokens"] is not None` 校验
- **风险 3**：toolbar widget 尺寸变大可能顶到 ModelToggle 文字 — 已确认 toolbar gap-1.5 (6px) + widget 12px 宽 = 与按钮 24px+padding 28px 共存无虞
- **风险 4**：用户输入小数（type=number 默认接受）— step="1" 强制整数

## 6. 文件改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/shared/api-types.ts` | 修改 | ModelEntry 加 `maxOutputTokens?: number \| null` |
| `frontend/renderer/components/chat/ContextUsage.tsx` | 修改 | widget 尺寸 18×10 → 28×12，条纹 2.5 → 4px |
| `frontend/renderer/stores/contextUsage.ts` | 微调 | 加 contextWindow=0 降级保护 |
| `frontend/renderer/components/settings/ModelProviderSettings.tsx` | 修改 | import Sliders / ArrowDownToLine；PROVIDER_PRESETS 加 defaultContextK / defaultOutputK；ModelEditor 加 2 个 input；handleProviderChange 同步默认值 |
| `frontend/main/store.ts` | **零改动** | 已对新字段宽容（sanitizeModelEntry 通过 Record<string,unknown> 透传） |
| `frontend/main/python/spawn.ts` | 修改 | PythonCredentials 加 maxOutputTokens；buildEnv 写 AGENTX_MAX_OUTPUT_TOKENS |
| `backend/app/config.py` | 修改 | Settings 加 max_output_tokens: int \| None = None |
| `backend/app/llm.py` | 修改 | 三分支（DeepSeek / OpenAI / 兜底）条件性注入 kwargs["max_tokens"] |
| `tests/renderer/context-usage.test.tsx` | 修改 | 加 widget 尺寸断言 + zero-contextWindow 降级断言 |
| `tests/python/unit/test_llm_max_tokens.py`（或追加） | 新增/扩展 | llm max_tokens 透传测试（mock ChatOpenAI） |

## 7. 与现有规范契合度

- 遵循 AGENTS.md §1.1 优先用现成框架：复用现有 ChatOpenAI 的 max_tokens 参数，无需引入新机制
- 遵循 chat-context-management 提案 P1：M2 推迟的「前端暴露」按本次落地
- 延续已有 Settings 风格（int | None）配置新字段
- 数据流路径最小侵入：renderer → existing electron-store IPC → existing spawn.ts env injection → existing pydantic-settings → existing ChatOpenAI kwargs

## 8. 回退路径

仅 7 个文件改动（含后端 2 个），任一处出错可单独 `git revert`：
- 前端 widget 改动可独立 revert 不影响后端
- 后端 llm.max_tokens 改动仅影响输出长度，可独立 revert
