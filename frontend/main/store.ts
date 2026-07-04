import Store from "electron-store";
import { safeStorage } from "electron";

const store = new Store();

/**
 * electron-store + safeStorage 封装。
 * 加密值以 `enc:<base64>` 形式存储，明文回退以 `plain:<value>` 形式存储。
 * safeStorage 不可用时回退到明文并 console.warn。
 */
export function getDecrypted(key: string): string | null {
  const stored = store.get(key);
  if (typeof stored !== "string") return null;
  if (stored.startsWith("enc:")) {
    if (!safeStorage.isEncryptionAvailable()) return null;
    try {
      const buf = Buffer.from(stored.slice(4), "base64");
      return safeStorage.decryptString(buf);
    } catch {
      return null;
    }
  }
  if (stored.startsWith("plain:")) {
    return stored.slice(6);
  }
  return null;
}

export function setEncrypted(key: string, value: string): void {
  if (safeStorage.isEncryptionAvailable()) {
    const encrypted = safeStorage.encryptString(value);
    store.set(key, `enc:${encrypted.toString("base64")}`);
  } else {
    console.warn(`safeStorage 不可用，${key} 将以明文存储`);
    store.set(key, `plain:${value}`);
  }
}

export function getMilvusCredentials(): { user: string | null; password: string | null } {
  return {
    user: getDecrypted("milvus.user"),
    password: getDecrypted("milvus.password"),
  };
}

export function setMilvusCredentials(user: string, password: string): void {
  setEncrypted("milvus.user", user);
  setEncrypted("milvus.password", password);
}

export function getApiKey(provider: string): string | null {
  return getDecrypted(`apikey.${provider}`);
}

export function setApiKey(provider: string, key: string): void {
  setEncrypted(`apikey.${provider}`, key);
}

// ---- T7 settings-completion ----
// 非凭证配置（明文存储），用 electron-store 的 get/set，key 带前缀。

function getString(key: string, def: string): string {
  const v = store.get(key);
  return typeof v === "string" ? v : def;
}

function getNumber(key: string, def: number): number {
  const v = store.get(key);
  return typeof v === "number" ? v : def;
}

function getBoolean(key: string, def: boolean): boolean {
  const v = store.get(key);
  return typeof v === "boolean" ? v : def;
}

export function getLLMConfig(): { defaultModel: string; openaiBaseUrl: string } {
  return {
    defaultModel: getString("llm.defaultModel", ""),
    openaiBaseUrl: getString("llm.openaiBaseUrl", ""),
  };
}

export function setLLMConfig(model: string, baseUrl: string): void {
  store.set("llm.defaultModel", model);
  store.set("llm.openaiBaseUrl", baseUrl);
}

export function getSystemPrompt(): string {
  return getString("systemPrompt", "");
}

export function setSystemPrompt(prompt: string): void {
  store.set("systemPrompt", prompt);
}

export function getApprovalConfig(): {
  autoApproveAfterSeconds: number;
  approvalMaxWait: number;
  maxUploadBytes: number;
} {
  return {
    autoApproveAfterSeconds: getNumber("approval.autoApproveAfterSeconds", 0),
    approvalMaxWait: getNumber("approval.approvalMaxWait", 300),
    maxUploadBytes: getNumber("approval.maxUploadBytes", 52428800),
  };
}

export function setApprovalConfig(
  cfg: Partial<{ autoApproveAfterSeconds: number; approvalMaxWait: number; maxUploadBytes: number }>,
): void {
  // 数值 clamp：防 renderer 传入负数/NaN/极大值导致后端行为异常
  const clamp = (v: number, min: number, max: number): number =>
    Number.isFinite(v) ? Math.min(Math.max(v, min), max) : min;
  if (cfg.autoApproveAfterSeconds !== undefined) {
    store.set("approval.autoApproveAfterSeconds", clamp(cfg.autoApproveAfterSeconds, 0, 3600));
  }
  if (cfg.approvalMaxWait !== undefined) {
    store.set("approval.approvalMaxWait", clamp(cfg.approvalMaxWait, 0, 3600));
  }
  if (cfg.maxUploadBytes !== undefined) {
    store.set("approval.maxUploadBytes", clamp(cfg.maxUploadBytes, 0, 1_073_741_824)); // 上限 1GB
  }
}

export function getKnowledgeConfig(): {
  embeddingUrl: string;
  milvusHost: string;
  milvusPort: number;
  milvusDb: string;
  milvusCollection: string;
  milvusAuthEnabled: boolean;
} {
  return {
    embeddingUrl: getString("knowledge.embeddingUrl", ""),
    // myserver Milvus 部署在 192.168.1.4:19530（authorizationEnabled=false）
    milvusHost: getString("knowledge.milvusHost", "192.168.1.4"),
    milvusPort: getNumber("knowledge.milvusPort", 19530),
    milvusDb: getString("knowledge.milvusDb", "agent_py"),
    milvusCollection: getString("knowledge.milvusCollection", "agent_py_knowledge"),
    // myserver auth disabled，默认 false 跳过凭证校验
    milvusAuthEnabled: getBoolean("knowledge.milvusAuthEnabled", false),
  };
}

export function setKnowledgeConfig(
  cfg: Partial<{
    embeddingUrl: string;
    milvusHost: string;
    milvusPort: number;
    milvusDb: string;
    milvusCollection: string;
    milvusAuthEnabled: boolean;
  }>,
): void {
  if (cfg.embeddingUrl !== undefined) store.set("knowledge.embeddingUrl", cfg.embeddingUrl);
  if (cfg.milvusHost !== undefined) store.set("knowledge.milvusHost", cfg.milvusHost);
  if (cfg.milvusPort !== undefined) store.set("knowledge.milvusPort", cfg.milvusPort);
  if (cfg.milvusDb !== undefined) store.set("knowledge.milvusDb", cfg.milvusDb);
  if (cfg.milvusCollection !== undefined) store.set("knowledge.milvusCollection", cfg.milvusCollection);
  if (cfg.milvusAuthEnabled !== undefined) store.set("knowledge.milvusAuthEnabled", cfg.milvusAuthEnabled);
}

// ---- T11/T12/T13 子代理 + 工具 + 用户画像自动抽取 ----
// 默认值与 backend/app/config.py _default_subagents() / _default_tools_enabled() 保持一致，
// env 注入后后端 pydantic-settings 仍会做字段级覆盖合并。

export interface SubagentConfig {
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  keywords: string[];
}

export interface SubagentsConfig {
  code: SubagentConfig;
  rag: SubagentConfig;
  web: SubagentConfig;
}

export interface ToolsConfig {
  read_file: boolean;
  list_dir: boolean;
  glob: boolean;
  grep: boolean;
  write_file: boolean;
  edit_file: boolean;
  web_search: boolean;
  rag_retrieve: boolean;
}

const DEFAULT_SUBAGENTS: SubagentsConfig = {
  code: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: ["read_file", "list_dir", "glob", "grep"],
    keywords: [],
  },
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: ["rag_retrieve"],
    keywords: ["知识库", "文档库", "检索", "向量", "rag", "知识", "文档"],
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: ["web_search"],
    keywords: ["搜索", "网页", "联网", "查一下", "search", "web", "google", "百度"],
  },
};

const DEFAULT_TOOLS: ToolsConfig = {
  read_file: true,
  list_dir: true,
  glob: true,
  grep: true,
  write_file: true,
  edit_file: true,
  web_search: true,
  rag_retrieve: true,
};

function sanitizeSubagent(raw: unknown, def: SubagentConfig): SubagentConfig {
  // 防御性：electron-store 中的旧数据可能字段缺失或类型错误，逐字段做安全合并
  if (!raw || typeof raw !== "object") return { ...def };
  const r = raw as Partial<SubagentConfig> & Record<string, unknown>;
  const clampTemp = (v: unknown): number =>
    typeof v === "number" && Number.isFinite(v)
      ? Math.min(Math.max(v, 0), 2)
      : def.temperature;
  const strArr = (v: unknown, fallback: string[]): string[] =>
    Array.isArray(v) && v.every((x) => typeof x === "string")
      ? v
      : fallback;
  return {
    enabled: typeof r.enabled === "boolean" ? r.enabled : def.enabled,
    temperature: clampTemp(r.temperature),
    systemPrompt:
      typeof r.systemPrompt === "string" ? r.systemPrompt : def.systemPrompt,
    tools: strArr(r.tools, def.tools),
    keywords: strArr(r.keywords, def.keywords),
  };
}

export function getSubagentsConfig(): SubagentsConfig {
  const raw = store.get("subagents") as
    | Partial<Record<"code" | "rag" | "web", unknown>>
    | undefined;
  if (!raw) return DEFAULT_SUBAGENTS;
  return {
    code: sanitizeSubagent(raw.code, DEFAULT_SUBAGENTS.code),
    rag: sanitizeSubagent(raw.rag, DEFAULT_SUBAGENTS.rag),
    web: sanitizeSubagent(raw.web, DEFAULT_SUBAGENTS.web),
  };
}

export function setSubagentsConfig(cfg: SubagentsConfig): void {
  store.set("subagents", cfg);
}

export function getToolsConfig(): ToolsConfig {
  const raw = store.get("tools") as Partial<ToolsConfig> | undefined;
  if (!raw) return DEFAULT_TOOLS;
  const result: ToolsConfig = { ...DEFAULT_TOOLS };
  (Object.keys(DEFAULT_TOOLS) as (keyof ToolsConfig)[]).forEach((k) => {
    if (typeof raw[k] === "boolean") result[k] = raw[k] as boolean;
  });
  return result;
}

export function setToolsConfig(cfg: ToolsConfig): void {
  store.set("tools", cfg);
}

export function getProfileAutoExtract(): boolean {
  return getBoolean("profile.autoExtract", true);
}

export function setProfileAutoExtract(v: boolean): void {
  store.set("profile.autoExtract", v);
}
