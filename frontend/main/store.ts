import Store from "electron-store";
import { safeStorage } from "electron";

const store = new Store();

/**
 * electron-store + safeStorage 封装。
 * 加密值以 `enc:<base64>` 形式存储，明文回退以 `plain:<value>` 形式存储。
 * safeStorage 不可用时回退到明文并 console.warn。
 */
export function decryptString(encrypted: string | null | undefined): string | null {
  if (!encrypted || typeof encrypted !== "string") return null;
  if (encrypted.startsWith("enc:")) {
    if (!safeStorage.isEncryptionAvailable()) return null;
    try {
      const buf = Buffer.from(encrypted.slice(4), "base64");
      return safeStorage.decryptString(buf);
    } catch {
      return null;
    }
  }
  if (encrypted.startsWith("plain:")) {
    return encrypted.slice(6);
  }
  return null;
}

export function getDecrypted(key: string): string | null {
  const stored = store.get(key);
  if (typeof stored !== "string") return null;
  return decryptString(stored);
}

export function encryptString(value: string): string {
  if (safeStorage.isEncryptionAvailable()) {
    const encrypted = safeStorage.encryptString(value);
    return `enc:${encrypted.toString("base64")}`;
  }
  console.warn("safeStorage 不可用，凭证将以明文存储");
  return `plain:${value}`;
}

export function setEncrypted(key: string, value: string): void {
  store.set(key, encryptString(value));
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

// ---- 自定义子代理（CRUD，与内置 subagents 配置独立持久化）----
// 与 backend/app/config.py CustomSubagentEntry 字段一致。
// env 注入由 spawn.ts buildEnv 完成，后端 pydantic-settings 解析 AGENT_PY_CUSTOM_SUBAGENTS_CONFIG。

export interface CustomSubagentEntry {
  key: string;
  name: string;
  description: string;
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  keywords: string[];
}

export type CustomSubagentsMap = Record<string, CustomSubagentEntry>;

export interface CustomSubagentInput {
  key: string;
  name: string;
  description?: string;
  enabled?: boolean;
  temperature?: number;
  systemPrompt?: string;
  tools?: string[];
  keywords?: string[];
}

// 内置子代理 key（自定义 key 不允许冲突）
const BUILTIN_SUBAGENT_KEYS = new Set(["code", "rag", "web"]);

// 自定义子代理禁止绑定的危险工具（与后端 FORBIDDEN_SUBAGENT_TOOLS 一致）
const FORBIDDEN_SUBAGENT_TOOLS = new Set([
  "write_file",
  "edit_file",
  "shell_exec",
]);

// 允许的工具白名单（与后端 _ALL_TOOLS 一致）
const ALLOWED_TOOLS = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "write_file",
  "edit_file",
  "web_search",
  "rag_retrieve",
];

// key 正则：与 McpServerConfig.name 一致风格（避免特殊字符导致 env JSON 解析问题）
const CUSTOM_KEY_RE = /^[a-zA-Z0-9_-]{1,64}$/;

function sanitizeCustomTools(tools: unknown): string[] {
  if (!Array.isArray(tools)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const t of tools) {
    if (
      typeof t === "string" &&
      ALLOWED_TOOLS.includes(t) &&
      !FORBIDDEN_SUBAGENT_TOOLS.has(t) &&
      !seen.has(t)
    ) {
      seen.add(t);
      result.push(t);
    }
  }
  return result;
}

function sanitizeStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

function sanitizeCustomEntry(
  raw: unknown,
  fallbackKey?: string,
): CustomSubagentEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Partial<CustomSubagentEntry> & Record<string, unknown>;
  const key =
    typeof r.key === "string" ? r.key : typeof fallbackKey === "string" ? fallbackKey : "";
  if (!CUSTOM_KEY_RE.test(key) || BUILTIN_SUBAGENT_KEYS.has(key)) return null;
  const name = typeof r.name === "string" && r.name.trim() ? r.name.trim() : key;
  const description = typeof r.description === "string" ? r.description : "";
  const enabled = typeof r.enabled === "boolean" ? r.enabled : true;
  const temperature =
    typeof r.temperature === "number" && Number.isFinite(r.temperature)
      ? Math.min(Math.max(r.temperature, 0), 2)
      : 0.2;
  const systemPrompt = typeof r.systemPrompt === "string" ? r.systemPrompt : "";
  const tools = sanitizeCustomTools(r.tools);
  const keywords = sanitizeStringArray(r.keywords);
  return {
    key,
    name,
    description,
    enabled,
    temperature,
    systemPrompt,
    tools,
    keywords,
  };
}

export function getCustomSubagents(): CustomSubagentsMap {
  const raw = store.get("customSubagents") as Record<string, unknown> | undefined;
  if (!raw || typeof raw !== "object") return {};
  const result: CustomSubagentsMap = {};
  for (const [k, v] of Object.entries(raw)) {
    const entry = sanitizeCustomEntry(v, k);
    if (entry) result[entry.key] = entry;
  }
  return result;
}

export function setCustomSubagents(cfg: CustomSubagentsMap): void {
  // 写入前再次 sanitize，确保危险工具与非法 key 都被过滤
  const sanitized: CustomSubagentsMap = {};
  for (const [k, v] of Object.entries(cfg)) {
    const entry = sanitizeCustomEntry(v, k);
    if (entry) sanitized[entry.key] = entry;
  }
  store.set("customSubagents", sanitized);
}

/**
 * 新增自定义子代理。
 * - key 已存在或与内置 key 冲突 → 抛错
 * - 入参字段缺失时使用默认值
 */
export function addCustomSubagent(input: CustomSubagentInput): CustomSubagentEntry {
  if (!CUSTOM_KEY_RE.test(input.key) || BUILTIN_SUBAGENT_KEYS.has(input.key)) {
    throw new Error(
      `非法或冲突的子代理 key: ${input.key}（仅允许字母数字/下划线/连字符，且不与内置 key 冲突）`,
    );
  }
  const existing = getCustomSubagents();
  if (input.key in existing) {
    throw new Error(`子代理 key 已存在: ${input.key}`);
  }
  const entry = sanitizeCustomEntry({
    key: input.key,
    name: input.name,
    description: input.description ?? "",
    enabled: input.enabled ?? true,
    temperature: input.temperature ?? 0.2,
    systemPrompt: input.systemPrompt ?? "",
    tools: input.tools ?? [],
    keywords: input.keywords ?? [],
  });
  if (!entry) throw new Error("子代理配置无效");
  existing[input.key] = entry;
  store.set("customSubagents", existing);
  return entry;
}

export function removeCustomSubagent(key: string): { ok: boolean; key: string } {
  const existing = getCustomSubagents();
  if (!(key in existing)) {
    return { ok: false, key };
  }
  delete existing[key];
  store.set("customSubagents", existing);
  return { ok: true, key };
}

// ---- MCP server 配置 ----
// 与 backend/app/mcp/config.McpServerConfig 字段一致，存储为 JSON 数组。
// env 注入由 spawn.ts buildEnv 完成，后端 pydantic-settings 解析 AGENT_PY_MCP_SERVERS_CONFIG。

export interface McpServerConfig {
  name: string;
  transport: "stdio" | "sse" | "streamable_http";
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  enabled: boolean;
  trusted: boolean;
}

const TRANSPORTS: ReadonlyArray<McpServerConfig["transport"]> = [
  "stdio",
  "sse",
  "streamable_http",
];

function sanitizeMcpServer(raw: unknown): McpServerConfig | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Partial<McpServerConfig> & Record<string, unknown>;
  const name = typeof r.name === "string" ? r.name.trim() : "";
  // 名称正则与后端 McpServerConfig.name pattern 一致
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(name)) return null;
  const transport =
    typeof r.transport === "string" && TRANSPORTS.includes(r.transport as McpServerConfig["transport"])
      ? r.transport
      : "stdio";
  const strArr = (v: unknown): string[] =>
    Array.isArray(v) && v.every((x) => typeof x === "string") ? v : [];
  const strRecord = (v: unknown): Record<string, string> => {
    if (!v || typeof v !== "object") return {};
    const out: Record<string, string> = {};
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val === "string") out[k] = val;
    }
    return out;
  };
  return {
    name,
    transport,
    command: typeof r.command === "string" ? r.command : null,
    args: strArr(r.args),
    env: strRecord(r.env),
    url: typeof r.url === "string" ? r.url : null,
    enabled: typeof r.enabled === "boolean" ? r.enabled : true,
    trusted: typeof r.trusted === "boolean" ? r.trusted : false,
  };
}

export function getMcpServersConfig(): McpServerConfig[] {
  const raw = store.get("mcp.servers");
  if (!Array.isArray(raw)) return [];
  const result: McpServerConfig[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const cfg = sanitizeMcpServer(item);
    if (!cfg) continue;
    if (seen.has(cfg.name)) continue;
    seen.add(cfg.name);
    result.push(cfg);
  }
  return result;
}

export function setMcpServersConfig(servers: McpServerConfig[]): void {
  // 防御性：renderer 传入的数据可能字段缺失或类型错误，逐项 sanitize
  const cleaned: McpServerConfig[] = [];
  const seen = new Set<string>();
  for (const item of servers) {
    const cfg = sanitizeMcpServer(item);
    if (!cfg) continue;
    if (seen.has(cfg.name)) continue;
    seen.add(cfg.name);
    cleaned.push(cfg);
  }
  store.set("mcp.servers", cleaned);
}

// ---- 模型条目（Model Entries）----
// 用户可保存多个 LLM 模型配置（provider + model + baseUrl + apiKey），
// "激活"某条目时将其写入 legacy 槽位（llm.defaultModel / llm.openaiBaseUrl / apikey.openai|deepseek），
// 后端 spawn 时从 legacy 槽位读取 env 注入，故 spawn.ts / config.py / llm.py 无需改动。
//
// 与 backend/app/llm.py 的路由逻辑对齐：
// - providerId="deepseek" → 写 apikey.deepseek（model 需以 deepseek 开头）
// - providerId="openai"   → 写 apikey.openai（model 需以 gpt/o1/o3 开头）
// - providerId="minimax" | "custom" → 写 apikey.openai + openaiBaseUrl（OpenAI 兼容兜底分支）

export type ModelProviderId = "openai" | "deepseek" | "minimax" | "custom";

export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
}

const MODEL_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;

function sanitizeModelEntry(raw: unknown): ModelEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Partial<ModelEntry> & Record<string, unknown>;
  const id = typeof r.id === "string" ? r.id : "";
  if (!MODEL_ID_RE.test(id)) return null;
  const providerId =
    typeof r.providerId === "string" &&
    ["openai", "deepseek", "minimax", "custom"].includes(r.providerId)
      ? (r.providerId as ModelProviderId)
      : "custom";
  const model = typeof r.model === "string" ? r.model.trim() : "";
  const baseUrl = typeof r.baseUrl === "string" ? r.baseUrl.trim() : "";
  const apiKey = typeof r.apiKey === "string" ? r.apiKey : "";
  const label = typeof r.label === "string" && r.label.trim() ? r.label.trim() : "";
  const createdAt = typeof r.createdAt === "number" ? r.createdAt : Date.now();
  return { id, label, providerId, model, baseUrl, apiKey, createdAt };
}

export function getModelEntries(): ModelEntry[] {
  const raw = store.get("models.entries");
  if (!Array.isArray(raw)) return [];
  const result: ModelEntry[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const entry = sanitizeModelEntry(item);
    if (!entry) continue;
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    result.push(entry);
  }
  // 按 createdAt 升序，保持添加顺序
  result.sort((a, b) => a.createdAt - b.createdAt);
  return result;
}

export function setModelEntries(entries: ModelEntry[]): void {
  const cleaned: ModelEntry[] = [];
  const seen = new Set<string>();
  for (const item of entries) {
    let entry = sanitizeModelEntry(item);
    if (!entry) continue;
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    // 若 apiKey 是明文（非 enc:/plain: 前缀且非空），加密后存储。
    // 这样 renderer 传新 key（明文）时自动加密，传已有加密 key（从 getModelEntries 读回）时保持原样。
    if (
      entry.apiKey &&
      !entry.apiKey.startsWith("enc:") &&
      !entry.apiKey.startsWith("plain:")
    ) {
      entry = { ...entry, apiKey: encryptString(entry.apiKey) };
    }
    cleaned.push(entry);
  }
  store.set("models.entries", cleaned);
}

export function getActiveModelId(): string | null {
  const id = store.get("models.activeId");
  return typeof id === "string" ? id : null;
}

export function setActiveModelId(id: string | null): void {
  if (id === null) {
    store.delete("models.activeId");
    return;
  }
  store.set("models.activeId", id);
}

/**
 * 激活指定模型条目：将其 {model, baseUrl, apiKey} 写入 legacy 槽位。
 * - deepseek provider → apikey.deepseek
 * - 其他 provider（openai/minimax/custom）→ apikey.openai + openaiBaseUrl
 * 写入后需重启后端才能让新 env 生效。
 */
export function activateModelEntry(id: string): void {
  const entries = getModelEntries();
  const entry = entries.find((e) => e.id === id);
  if (!entry) throw new Error(`模型条目不存在: ${id}`);
  if (!entry.model) throw new Error("模型名称为空，无法激活");
  // 1. 写入 llm.defaultModel + llm.openaiBaseUrl
  setLLMConfig(entry.model, entry.baseUrl);
  // 2. 写入对应 provider 的 API Key 槽位
  const apiKey = decryptString(entry.apiKey) ?? "";
  if (entry.providerId === "deepseek") {
    setApiKey("deepseek", apiKey);
  } else {
    // openai / minimax / custom 均走 OpenAI 兼容兜底分支，使用 openai 槽位
    setApiKey("openai", apiKey);
  }
  // 3. 标记激活
  setActiveModelId(id);
}

/**
 * 迁移：若 models.entries 为空但 legacy 配置（llm.defaultModel + apikey.*）已存在，
 * 则种子一条默认条目，避免老用户升级后丢失已配置的模型。
 * 在 app.whenReady() 启动后端前调用一次。
 */
export function migrateLegacyLLMConfig(): void {
  const existing = getModelEntries();
  if (existing.length > 0) return;
  const llm = getLLMConfig();
  if (!llm.defaultModel) return;
  // 推断 provider
  let providerId: ModelProviderId = "custom";
  if (llm.defaultModel.startsWith("deepseek")) providerId = "deepseek";
  else if (/^(gpt|o1|o3)/.test(llm.defaultModel)) providerId = "openai";
  else if (llm.openaiBaseUrl && llm.openaiBaseUrl.includes("minimaxi")) providerId = "minimax";
  // 读取已有 key
  const apiKeyEnc =
    providerId === "deepseek"
      ? (store.get("apikey.deepseek") as string | undefined) ?? ""
      : (store.get("apikey.openai") as string | undefined) ?? "";
  const entry: ModelEntry = {
    id: "migrated",
    label: `${providerId} · ${llm.defaultModel}`,
    providerId,
    model: llm.defaultModel,
    baseUrl: llm.openaiBaseUrl,
    apiKey: typeof apiKeyEnc === "string" ? apiKeyEnc : "",
    createdAt: Date.now(),
  };
  setModelEntries([entry]);
  setActiveModelId(entry.id);
}
