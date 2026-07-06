import type {
  ModelCatalog,
  ModelCatalogEntry,
  ModelEntry,
  ModelPresetProviderId,
  ModelProviderId,
} from "../../shared/api-types";

/**
 * 服务商预置：默认模型列表 / Base URL / 文档链接 / 默认上下文与输出 token 数。
 *
 * 国内 TokenPlan / Coding Plan 重点：以下三家在国内均有独立编程套餐服务，
 * base_url 与通用 API 不同，凭证也独立：
 * - Kimi Coding Plan   → https://api.kimi.com/coding/v1 （独立密钥 KIMI_API_CODE，与通用 api.moonshot.cn/v1 完全分离）
 * - GLM Coding Plan    → https://open.bigmodel.cn/api/coding/paas/v4
 * - MiniMax Token Plan → https://api.minimaxi.com/v1 （订阅后生成的 Key 在同一 endpoint 自动计套餐量）
 *
 * 与 `backend/app/llm.py` 路由逻辑对齐：
 * - deepseek  → apikey.deepseek + https://api.deepseek.com
 * - kimi      → apikey.kimi + https://api.kimi.com/coding/v1 （Kimi Coding Plan 专用）
 * - glm       → apikey.glm + https://open.bigmodel.cn/api/coding/paas/v4
 * - minimax   → apikey.openai 兑底（https://api.minimaxi.com/v1，同 endpoint Token Plan 订阅 Key 自动计套餐量）
 * - openai    → apikey.openai + https://api.openai.com/v1
 *
 * 权威数据来源（更新于 2026-07-06）：
 * - DeepSeek API Docs       https://api-docs.deepseek.com/zh-cn/quick_start/pricing
 * - Moonshot Kimi Code      https://platform.kimi.com + https://api.kimi.com/coding/v1
 * - 智谱 GLM Coding Plan     https://bigmodel.cn/glm-coding + https://docs.bigmodel.cn/cn/coding-plan/overview
 * - MiniMax Token Plan       https://platform.minimaxi.com/subscribe/token-plan + https://platform.minimaxi.com/docs/token-plan/intro
 * - OpenAI API Docs          https://developers.openai.com/api/docs/models
 */
export const MODEL_CATALOG: ModelCatalog = {
  deepseek: {
    label: "DeepSeek",
    docs: "https://platform.deepseek.com/api_keys",
    baseUrl: "https://api.deepseek.com",
    models: [
      // V4-Flash 是 deepseek-chat / deepseek-reasoner 的下一代；老模型名 2026-07-24 弃用
      { value: "deepseek-v4-flash", desc: "V4-Flash 通用对话 + 思考模式（默认 1M 上下文）" },
      { value: "deepseek-v4-pro", desc: "V4-Pro 推理增强（1M 上下文，最大 384K 输出）" },
    ],
    defaultContextK: 1024,
    defaultOutputK: 64,
  },
  kimi: {
    label: "Kimi Coding Plan",
    docs: "https://platform.kimi.com",
    // Moonshot 编程套餐独立服务：base_url / 密钥 / 模型名均与通用 api.moonshot.cn/v1 不同
    // 凭证独立获取：在 https://platform.kimi.com 开通 Coding Plan 后生成 KIMI_API_CODE
    baseUrl: "https://api.kimi.com/coding/v1",
    models: [
      // K2.7-Code 是 Kimi Coding Plan 主力（2026-06 发布）
      { value: "kimi-k2-7-code", desc: "K2.7-Code 主力 Coding / Agent（256K 上下文）" },
      { value: "kimi-k2-6", desc: "K2.6 原生多模态（256K 上下文）" },
      { value: "kimi-k2-5", desc: "K2.5 通用对话（256K 上下文）" },
    ],
    defaultContextK: 256,
    defaultOutputK: 32,
  },
  minimax: {
    label: "MiniMax Token Plan",
    docs: "https://platform.minimaxi.com/subscribe/token-plan",
    // Token Plan 与 API 走同一 endpoint；订阅后生成的 Key 自动享有套餐内用量额度
    baseUrl: "https://api.minimaxi.com/v1",
    models: [
      // M3 是 MiniMax 2026 年最新旗舰，MSA 稀疏注意力 + 1M context
      { value: "MiniMax-M3", desc: "M3 旗舰 · Frontier Coding / Agent（1M 上下文）" },
      { value: "MiniMax-M2", desc: "M2 主力 Agent / 代码（兼容回退）" },
      { value: "MiniMax-Text-01", desc: "Text-01 长文本（兼容回退）" },
    ],
    defaultContextK: 1000,
    defaultOutputK: 32,
  },
  glm: {
    label: "智谱 GLM Coding Plan",
    docs: "https://bigmodel.cn/glm-coding",
    baseUrl: "https://open.bigmodel.cn/api/coding/paas/v4",
    models: [
      // GLM-5 是智谱 2026 旗舰（Coding Plan 端点默认模型），1M 上下文
      { value: "glm-5", desc: "GLM-5 旗舰 · 1M 上下文 · Coding Plan 主力" },
      { value: "glm-5-turbo", desc: "GLM-5-Turbo 高速版" },
      { value: "glm-4.7", desc: "GLM-4.7 通用对话（向下兼容）" },
    ],
    defaultContextK: 1024,
    defaultOutputK: 32,
  },
  openai: {
    label: "OpenAI",
    docs: "https://platform.openai.com/api-keys",
    baseUrl: "https://api.openai.com/v1",
    models: [
      // GPT-5 是 2026 OpenAI 最新 frontier；gpt-5-mini 是低延迟 / 低成本版
      { value: "gpt-5", desc: "GPT-5 旗舰 · 强 Coding / 推理" },
      { value: "gpt-5-mini", desc: "GPT-5 mini 低延迟 / 低成本" },
      { value: "gpt-4o", desc: "GPT-4o 多模态（向下兼容）" },
    ],
    defaultContextK: 400,
    defaultOutputK: 32,
  },
};

/** 自定义服务商不放在 catalog 中，单独定义显示元信息 */
export const CUSTOM_PROVIDER = {
  label: "自定义",
  docs: "",
  baseUrl: "",
  models: [],
  defaultContextK: 0,
  defaultOutputK: 0,
} as const;

/** 服务商显示顺序（去掉 label 字段后的下拉顺序） */
export const PROVIDER_ORDER: ModelProviderId[] = [
  "deepseek",
  "kimi",
  "minimax",
  "glm",
  "openai",
  "custom",
];

/**
 * 上下文容量下拉默认选项（k tokens）。
 * 覆盖从 64k（小型兼容）到 1M（最新 V4 / GLM-5 / MiniMax-M3 / GPT-5）所有常见量级。
 */
export const CONTEXT_K_OPTIONS = [64, 128, 250, 512, 1024];
/** 输出 token 下拉默认选项（k tokens） */
export const OUTPUT_K_OPTIONS = [4, 8, 16, 32, 64, 128];

/** 取预设服务商元信息；custom 返回占位 */
export function getProviderPreset(id: ModelProviderId): ModelCatalogEntry | null {
  if (id === "custom") return null;
  return MODEL_CATALOG[id as ModelPresetProviderId];
}

/** 服务商中文显示名 */
export function providerLabel(id: ModelProviderId): string {
  if (id === "custom") return CUSTOM_PROVIDER.label;
  return MODEL_CATALOG[id as ModelPresetProviderId].label;
}

/**
 * ModelEntry 的展示名：优先使用用户自定义 label（最友好），未设置时回退到 model id。
 *
 * **不**拼接 provider 标记。"DeepSeek · deepseek-v4-flash" 这种格式会让弹框/trigger
 * 拉长、挤右复上下文 hint，看起来像"额外信息"。项目 UI 只展示模型名本身；
 * provider 信息在 ModelProviderSettings 表格里、ChatView 的错误提示里有独立位置。
 *
 * 弹框中其他必要信息（provider、上下文大小）走 `title={}` hover tooltip，不堆到主体。
 */
export function modelDisplayName(entry: ModelEntry): string {
  return entry.label?.trim() || entry.model || '未命名模型';
}

/** 彩色徽章背景色（按 provider 区分） */
export function providerBadgeColor(id: ModelProviderId): string {
  switch (id) {
    case "deepseek":
      return "bg-violet-500/10 text-violet-600 dark:text-violet-400";
    case "kimi":
      return "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400";
    case "minimax":
      return "bg-amber-500/10 text-amber-600 dark:text-amber-400";
    case "glm":
      return "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400";
    case "openai":
      return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    case "custom":
      return "bg-sky-500/10 text-sky-600 dark:text-sky-400";
  }
}