import Store from "electron-store";
import { safeStorage } from "electron";
import type { ModelEntry } from "../shared/api-types";

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
    milvusDb: getString("knowledge.milvusDb", "agentx"),
    milvusCollection: getString("knowledge.milvusCollection", "agentx_knowledge"),
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
  triggerDescription: string;
}

export interface SubagentsConfig {
  code: SubagentConfig;
  rag: SubagentConfig;
  web: SubagentConfig;
}

// 软件开发专家团角色配置（与 backend/app/config.py _default_team_subagents() 一致）
export interface TeamSubagentsConfig {
  frontend_dev: SubagentConfig;
  backend_dev: SubagentConfig;
  tester: SubagentConfig;
  architect: SubagentConfig;
  devops: SubagentConfig;
  ui_designer: SubagentConfig;
  product_manager: SubagentConfig;
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
    systemPrompt:
      "你是代码与文件操作专家。你的职责是帮助用户处理代码相关的问题：\n" +
      "1. 读取、搜索、分析代码文件和目录结构\n" +
      "2. 回答与代码实现、技术选型、调试排错相关的问题\n" +
      "3. 支持 HTML/CSS/JS/Python/Java/TypeScript 等多种语言\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具获取文件信息\n" +
      "5. 保持回答简洁，优先给出代码示例和具体文件路径",
    tools: ["read_file", "list_dir", "glob", "grep"],
    triggerDescription:
      "用户问题涉及代码文件、项目目录、程序报错、函数/类定义、import依赖、技术实现细节、代码审查或重构建议时触发。",
  },
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是知识库检索专家。你的职责是帮助用户从向量知识库中检索信息：\n" +
      "1. 使用 rag_retrieve 工具检索与用户问题相关的文档片段\n" +
      "2. 基于检索结果给出准确、有依据的回答\n" +
      "3. 如果检索结果不足，明确告知用户知识库中未找到相关内容\n" +
      "4. 引用检索到的文档内容时保持原文含义，不随意扩展\n" +
      "5. 优先回答技术文档、API 文档、内部规范等知识库类型的问题",
    tools: ["rag_retrieve"],
    triggerDescription: "用户问题需要引用内部知识库、技术文档、API手册、产品规范或历史资料时触发。",
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是联网搜索专家。你的职责是帮助用户获取互联网上的实时信息：\n" +
      "1. 使用 web_search 工具搜索最新的外部信息\n" +
      "2. 回答新闻、资料、技术动态、产品信息等需要实时数据的问题\n" +
      "3. 搜索结果需注明信息来源和时间\n" +
      "4. 对于时效性强的信息（如版本号、价格、事件），优先使用搜索而非依赖训练数据\n" +
      "5. 如果搜索无结果，明确告知用户并建议调整查询词",
    tools: ["web_search"],
    triggerDescription: "用户问题需要获取互联网实时信息、最新新闻、当前版本号、市场价格、事件动态或外部资料时触发。",
  },
};

// 软件开发专家团角色默认配置
const DEFAULT_TEAM_SUBAGENTS: TeamSubagentsConfig = {
  frontend_dev: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是前端开发专家。你的职责是帮助用户解决前端相关的问题：\n" +
      "1. 分析 React、Vue、Angular 等框架的代码问题，包括 Hooks 使用、生命周期、状态管理\n" +
      "2. 处理 HTML、CSS、JavaScript/TypeScript 的 bug 和优化，包括类型安全、泛型、类型推断\n" +
      "3. 关注前端性能（Lighthouse/Core Web Vitals）、响应式设计、组件化开发与前端工程化（Vite/Webpack）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看前端代码和配置文件\n" +
      "5. 使用 web_search 获取最新前端技术动态和最佳实践\n" +
      "6. 使用 rag_retrieve 检索项目内部前端规范和组件文档\n" +
      "7. 保持回答简洁，给出具体代码示例、文件路径和重构建议",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "前端开发相关问题：React/Vue/Angular、HTML/CSS/JS/TypeScript、组件、状态管理、前端工程化、性能优化、Lighthouse。",
  },
  backend_dev: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是后端开发专家。你的职责是帮助用户解决后端相关的问题：\n" +
      "1. 分析 Python（Django/FastAPI/Flask）、Java（Spring Boot）、Go（Gin/Echo）、Node.js（NestJS/Express）等后端代码\n" +
      "2. 处理 RESTful/GraphQL API 设计、数据库设计与优化（SQL/NoSQL）、业务逻辑实现\n" +
      "3. 关注性能优化（缓存/异步/连接池）、并发处理（协程/线程/锁）、安全实践（OWASP/注入/XSS）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看后端代码和配置文件\n" +
      "5. 使用 web_search 获取最新后端技术动态和框架版本信息\n" +
      "6. 使用 rag_retrieve 检索项目内部后端规范、API 文档和数据库设计\n" +
      "7. 保持回答简洁，给出具体代码示例、文件路径和架构改进建议",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "后端开发相关问题：Python/Java/Go/Node、API设计、数据库、消息队列、缓存、微服务、RESTful/GraphQL。",
  },
  tester: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是测试专家。你的职责是帮助用户保障代码质量：\n" +
      "1. 设计单元测试（pytest/Jest/Mocha）、集成测试（API/DB/MQ）、E2E 测试（Cypress/Playwright）用例\n" +
      "2. 分析测试覆盖率（行/分支/函数覆盖率），找出测试盲区和边界条件遗漏\n" +
      "3. 推荐测试框架和最佳实践（TDD/BDD、Mock/Stub、Fixture、参数化测试）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看代码和测试文件\n" +
      "5. 使用 web_search 获取最新测试框架版本和测试策略最佳实践\n" +
      "6. 使用 rag_retrieve 检索项目内部测试规范和质量门禁标准\n" +
      "7. 保持回答简洁，给出可执行的测试代码示例、覆盖率提升方案和缺陷预防建议",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "测试相关问题：单元测试、集成测试、E2E测试、pytest/jest、覆盖率、TDD/BDD、Mock、性能测试、质量门禁。",
  },
  architect: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是架构专家。你的职责是帮助用户进行系统设计和技术决策：\n" +
      "1. 分析系统架构的合理性（耦合度、内聚性、扩展性），给出改进建议和重构方案\n" +
      "2. 进行技术选型，比较不同方案的优劣（性能/成本/生态/团队能力匹配度）\n" +
      "3. 关注性能（高并发/低延迟/高可用）、可扩展性（水平/垂直扩展）、可维护性、安全性（纵深防御）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看项目结构和关键代码\n" +
      "5. 使用 web_search 获取最新架构模式、技术趋势和业界最佳实践\n" +
      "6. 使用 rag_retrieve 检索项目内部架构规范、技术债务记录和演进文档\n" +
      "7. 保持回答简洁，给出架构图描述（Mermaid/PlantUML）、关键决策依据和风险评估",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "架构相关问题：系统设计、技术选型、DDD、设计模式、高并发/高可用、微服务、云原生、性能优化、扩展性。",
  },
  devops: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是运维专家。你的职责是帮助用户解决部署和运维问题：\n" +
      "1. 设计 CI/CD 流水线（GitHub Actions/GitLab CI/Jenkins），优化构建、测试、部署流程\n" +
      "2. 配置 Docker、Kubernetes（Deployment/Service/Ingress/ConfigMap/Secret）、Nginx 等基础设施\n" +
      "3. 设计监控告警方案（Prometheus/Grafana/ELK/Loki/Alertmanager），保障系统稳定性（SLO/SLI）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看配置文件（Dockerfile/yaml/nginx.conf）\n" +
      "5. 使用 web_search 获取最新 DevOps 工具版本、云原生最佳实践和安全配置建议\n" +
      "6. 使用 rag_retrieve 检索项目内部运维规范、部署手册和应急预案\n" +
      "7. 保持回答简洁，给出可执行的配置示例、脚本代码和故障排查流程",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "运维相关问题：CI/CD、Docker/Kubernetes、监控告警、Prometheus/Grafana、Nginx、Terraform、云平台、SRE。",
  },
  ui_designer: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是 UI 设计师。你的职责是帮助用户优化界面和交互体验：\n" +
      "1. 评审界面设计，给出视觉（色彩/排版/图标/间距）和交互（动效/反馈/流程）改进建议\n" +
      "2. 维护设计系统（Design Tokens/组件库/规范文档），确保跨平台组件风格一致性\n" +
      "3. 关注用户体验（易用性/效率/满意度）、可访问性（WCAG 2.1 AA/键盘导航/屏幕阅读器）、响应式设计\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看样式代码（CSS/SCSS/Tailwind/Styled Components）\n" +
      "5. 使用 web_search 获取最新设计趋势、组件库更新和 UX 研究方法论\n" +
      "6. 使用 rag_retrieve 检索项目内部设计规范、品牌指南和组件使用文档\n" +
      "7. 保持回答简洁，给出具体的设计建议、规范代码（CSS/Tailwind）和验收标准",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "UI设计相关问题：界面设计、交互设计、Figma、设计系统、视觉设计、用户体验、WCAG、响应式设计、A/B测试。",
  },
  product_manager: {
    enabled: true,
    temperature: 0.2,
    systemPrompt:
      "你是产品专家。你的职责是帮助用户梳理需求和规划功能：\n" +
      "1. 分析用户需求（痛点/场景/目标用户），转化为清晰的产品功能描述和验收标准\n" +
      "2. 撰写 PRD（产品需求文档）、用户故事（User Story/Acceptance Criteria）、原型标注\n" +
      "3. 进行优先级排序（RICE/Kano/WSJF），制定迭代计划（Sprint Planning/Release Planning）\n" +
      "4. 使用 read_file、list_dir、glob、grep 等工具查看项目文档（PRD/需求文档/会议纪要）\n" +
      "5. 使用 web_search 获取竞品分析、行业趋势和用户研究方法\n" +
      "6. 使用 rag_retrieve 检索项目内部产品文档、历史需求和用户反馈\n" +
      "7. 保持回答简洁，给出可执行的产品方案、功能清单、验收标准和数据度量指标",
    tools: ["read_file", "list_dir", "glob", "grep", "web_search", "rag_retrieve"],
    triggerDescription: "产品相关问题：需求分析、PRD、用户故事、优先级排序、Scrum/Kanban、竞品分析、数据驱动、A/B测试。",
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
  // 策略：空字符串回退到默认值，确保首次加载时显示完整配置而非空值
  if (!raw || typeof raw !== "object") return { ...def };
  const r = raw as Partial<SubagentConfig> & Record<string, unknown>;
  const clampTemp = (v: unknown): number =>
    typeof v === "number" && Number.isFinite(v)
      ? Math.min(Math.max(v, 0), 2)
      : def.temperature;
  const strArr = (v: unknown, fallback: string[]): string[] =>
    Array.isArray(v) && v.every((x) => typeof x === "string")
      ? v.length > 0 ? v : fallback
      : fallback;
  const strOrDef = (v: unknown, fallback: string): string =>
    typeof v === "string" && v.trim().length > 0 ? v : fallback;
  return {
    enabled: typeof r.enabled === "boolean" ? r.enabled : def.enabled,
    temperature: clampTemp(r.temperature),
    systemPrompt: strOrDef(r.systemPrompt, def.systemPrompt),
    tools: strArr(r.tools, def.tools),
    triggerDescription: strOrDef(r.triggerDescription, def.triggerDescription),
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

// 软件开发专家团角色配置持久化
export function getTeamSubagentsConfig(): TeamSubagentsConfig {
  const raw = store.get("teamSubagents") as
    | Partial<Record<keyof TeamSubagentsConfig, unknown>>
    | undefined;
  if (!raw) return DEFAULT_TEAM_SUBAGENTS;
  return {
    frontend_dev: sanitizeSubagent(raw.frontend_dev, DEFAULT_TEAM_SUBAGENTS.frontend_dev),
    backend_dev: sanitizeSubagent(raw.backend_dev, DEFAULT_TEAM_SUBAGENTS.backend_dev),
    tester: sanitizeSubagent(raw.tester, DEFAULT_TEAM_SUBAGENTS.tester),
    architect: sanitizeSubagent(raw.architect, DEFAULT_TEAM_SUBAGENTS.architect),
    devops: sanitizeSubagent(raw.devops, DEFAULT_TEAM_SUBAGENTS.devops),
    ui_designer: sanitizeSubagent(raw.ui_designer, DEFAULT_TEAM_SUBAGENTS.ui_designer),
    product_manager: sanitizeSubagent(raw.product_manager, DEFAULT_TEAM_SUBAGENTS.product_manager),
  };
}

export function setTeamSubagentsConfig(cfg: TeamSubagentsConfig): void {
  store.set("teamSubagents", cfg);
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
// env 注入由 spawn.ts buildEnv 完成，后端 pydantic-settings 解析 AGENTX_CUSTOM_SUBAGENTS_CONFIG。

export interface CustomSubagentEntry {
  key: string;
  name: string;
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  triggerDescription: string;
}

export type CustomSubagentsMap = Record<string, CustomSubagentEntry>;

export interface CustomSubagentInput {
  key: string;
  name: string;
  enabled?: boolean;
  temperature?: number;
  systemPrompt?: string;
  tools?: string[];
  triggerDescription?: string;
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
  const enabled = typeof r.enabled === "boolean" ? r.enabled : true;
  const temperature =
    typeof r.temperature === "number" && Number.isFinite(r.temperature)
      ? Math.min(Math.max(r.temperature, 0), 2)
      : 0.2;
  const systemPrompt = typeof r.systemPrompt === "string" ? r.systemPrompt : "";
  const tools = sanitizeCustomTools(r.tools);
  const triggerDescription = typeof r.triggerDescription === "string" ? r.triggerDescription : "";
  return {
    key,
    name,
    enabled,
    temperature,
    systemPrompt,
    tools,
    triggerDescription,
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
    enabled: input.enabled ?? true,
    temperature: input.temperature ?? 0.2,
    systemPrompt: input.systemPrompt ?? "",
    tools: input.tools ?? [],
    triggerDescription: input.triggerDescription ?? "",
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
// env 注入由 spawn.ts buildEnv 完成，后端 pydantic-settings 解析 AGENTX_MCP_SERVERS_CONFIG。

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
// - providerId="deepseek"        → 写 apikey.deepseek（model 需以 deepseek 开头）
// - providerId="kimi"            → 写 apikey.kimi（model 需以 kimi/moonshot 开头）
// - providerId="glm"             → 写 apikey.glm（model 需以 glm 开头）
// - providerId="openai"          → 写 apikey.openai（model 需以 gpt/o1/o3 开头）
// - providerId="minimax"|"custom" → 写 apikey.openai + openaiBaseUrl（OpenAI 兼容兜底分支）

export type ModelProviderId =
  | "openai"
  | "deepseek"
  | "kimi"
  | "minimax"
  | "glm"
  | "custom";

// ModelEntry 现复用 frontend/shared/api-types.ts 中的定义（含 contextWindow + maxOutputTokens），
// 单一事实源避免 main / renderer / preload 三处声明漂移。

const MODEL_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;

const ALLOWED_PROVIDERS = new Set<ModelProviderId>([
  "openai",
  "deepseek",
  "kimi",
  "minimax",
  "glm",
  "custom",
]);

function sanitizeModelEntry(raw: unknown): ModelEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Partial<ModelEntry> & Record<string, unknown>;
  const id = typeof r.id === "string" ? r.id : "";
  if (!MODEL_ID_RE.test(id)) return null;
  const providerId =
    typeof r.providerId === "string" && ALLOWED_PROVIDERS.has(r.providerId as ModelProviderId)
      ? (r.providerId as ModelProviderId)
      : "custom";
  const model = typeof r.model === "string" ? r.model.trim() : "";
  const baseUrl = typeof r.baseUrl === "string" ? r.baseUrl.trim() : "";
  const apiKey = typeof r.apiKey === "string" ? r.apiKey : "";
  const label = typeof r.label === "string" && r.label.trim() ? r.label.trim() : "";
  const createdAt = typeof r.createdAt === "number" ? r.createdAt : Date.now();
  // contextWindow 接受正整数；非正数/null/undefined 统一规整为 undefined
  const contextWindow =
    typeof r.contextWindow === "number" && r.contextWindow > 0
      ? r.contextWindow
      : undefined;
  // maxOutputTokens 接受正整数；非正数/null/undefined 统一规整为 undefined，避免把 0/负数/字符串 传到后端
  const maxOutputTokens =
    typeof r.maxOutputTokens === "number" && r.maxOutputTokens > 0
      ? r.maxOutputTokens
      : undefined;
  // label 改为可选字段（向后兼容旧数据），未设置时返回 undefined
  return {
    id,
    ...(label ? { label } : {}),
    providerId,
    model,
    baseUrl,
    apiKey,
    createdAt,
    contextWindow,
    maxOutputTokens,
  };
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
 * - kimi provider     → apikey.kimi
 * - glm provider      → apikey.glm
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
  // 2. 按 provider 路由到对应 api key 槽位
  const apiKey = decryptString(entry.apiKey) ?? "";
  const slotByProvider: Record<ModelProviderId, string> = {
    deepseek: "deepseek",
    kimi: "kimi",
    glm: "glm",
    openai: "openai",
    // minimax / custom 走 OpenAI 兼容兜底分支
    minimax: "openai",
    custom: "openai",
  };
  setApiKey(slotByProvider[entry.providerId], apiKey);
  // 3. 标记激活
  setActiveModelId(id);
}

/**
 * 解密指定 ModelEntry 的 API Key（safeStorage），返回明文或 null。
 * 仅供 renderer 「点击眼睛图标 → 真实回显」使用，调用方不应持久化该返回值。
 * - 未找到条目 → null
 * - safeStorage 不可用或解密失败 → null
 * - 条目 apiKey 为空 → null
 */
export function revealModelApiKey(id: string): string | null {
  const entries = getModelEntries();
  const entry = entries.find((e) => e.id === id);
  if (!entry) return null;
  return decryptString(entry.apiKey);
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
  // 推断 provider（覆盖 5 家预设 + custom）
  let providerId: ModelProviderId = "custom";
  if (llm.defaultModel.startsWith("deepseek")) providerId = "deepseek";
  else if (
    llm.defaultModel.startsWith("kimi") ||
    llm.defaultModel.startsWith("moonshot")
  )
    providerId = "kimi";
  else if (llm.defaultModel.startsWith("glm")) providerId = "glm";
  else if (/^(gpt|o1|o3)/.test(llm.defaultModel)) providerId = "openai";
  else if (llm.openaiBaseUrl && llm.openaiBaseUrl.includes("minimaxi"))
    providerId = "minimax";
  // 读取已有 key（按 provider 对应槽位）
  const keyByProvider: Record<ModelProviderId, string | undefined> = {
    deepseek: store.get("apikey.deepseek") as string | undefined,
    kimi: store.get("apikey.kimi") as string | undefined,
    glm: store.get("apikey.glm") as string | undefined,
    openai: store.get("apikey.openai") as string | undefined,
    // minimax / custom 历史上都走 apikey.openai 槽位
    minimax: store.get("apikey.openai") as string | undefined,
    custom: store.get("apikey.openai") as string | undefined,
  };
  const apiKeyEnc = keyByProvider[providerId] ?? "";
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
