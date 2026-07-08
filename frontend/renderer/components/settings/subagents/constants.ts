import type {
  SubagentConfig,
  SubagentsConfig,
  TeamSubagentsConfig,
} from "@/lib/utils";
import {
  Code2,
  Database,
  Globe,
  Monitor,
  Server,
  Bug,
  Layers,
  Cloud,
  Palette,
  ClipboardList,
} from "lucide-react";

// 子代理键名与后端 backend/app/config/subagents.py BUILTIN_SUBAGENT_KEYS 一致
// 场景化架构下 code 子代理已被 coding Expert 取代，仅保留 rag/web
export type BuiltinSubagentKey = "rag" | "web";

// 软件开发专家团角色键名
export type TeamSubagentKey =
  | "frontend_dev"
  | "backend_dev"
  | "tester"
  | "architect"
  | "devops"
  | "ui_designer"
  | "product_manager";

// 内置子代理可选工具清单（与 backend _ALL_TOOLS 一致，但不含危险工具） — 已迁移到 @/lib/subagentConstants
// emptyToolsConfig() 也已迁移到 @/lib/subagentConstants

export interface BuiltinMeta {
  key: BuiltinSubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

export const BUILTIN_SUBAGENTS: BuiltinMeta[] = [
  {
    key: "rag",
    label: "RAG 子代理",
    desc: "知识库检索专家：擅长从向量知识库中检索文档、知识点、技术文档，回答需要引用内部知识库资料的问题。",
    Icon: Database,
  },
  {
    key: "web",
    label: "Web 子代理",
    desc: "联网搜索专家：擅长搜索互联网上的实时信息、新闻、资料，回答需要最新外部信息的问题。",
    Icon: Globe,
  },
];

export interface TeamMeta {
  key: TeamSubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

export const TEAM_SUBAGENTS: TeamMeta[] = [
  {
    key: "frontend_dev",
    label: "前端开发",
    desc: "前端开发专家：精通 React/Vue/Angular、HTML5/CSS3、TypeScript、前端工程化、状态管理、组件库、性能优化（Lighthouse/Core Web Vitals）、可访问性（a11y）。",
    Icon: Monitor,
  },
  {
    key: "backend_dev",
    label: "后端开发",
    desc: "后端开发专家：精通 Python（Django/FastAPI）、Java（Spring Boot）、Go（Gin/Echo）、Node.js（NestJS）、数据库设计（PostgreSQL/MySQL/Redis）、RESTful/GraphQL API、消息队列（Kafka/RabbitMQ）、微服务通信。",
    Icon: Server,
  },
  {
    key: "tester",
    label: "测试",
    desc: "测试专家：精通单元测试（pytest/Jest）、集成测试（Postman/Newman）、E2E 测试（Cypress/Playwright）、性能测试（k6/JMeter）、TDD/BDD 实践、自动化测试流水线、质量门禁。",
    Icon: Bug,
  },
  {
    key: "architect",
    label: "架构",
    desc: "架构专家：精通系统架构设计（单体/微服务/Serverless）、DDD、设计模式、高并发/高可用/低延迟优化、数据架构、安全架构（OAuth2/JWT）、云原生（K8s/Service Mesh）。",
    Icon: Layers,
  },
  {
    key: "devops",
    label: "运维",
    desc: "运维专家：精通 CI/CD（GitHub Actions/GitLab CI/Jenkins）、Docker/Kubernetes、基础设施即代码（Terraform/Ansible）、云平台（AWS/Azure/阿里云）、监控告警（Prometheus/Grafana/ELK）、SRE 实践、蓝绿/金丝雀发布。",
    Icon: Cloud,
  },
  {
    key: "ui_designer",
    label: "UI 设计",
    desc: "UI 设计师：精通界面设计（Figma/Sketch）、交互设计（原型/动效）、设计系统构建（Tokens/组件库）、视觉设计（色彩/排版/图标）、用户体验研究（可用性测试/A/B 测试）、无障碍设计（WCAG）。",
    Icon: Palette,
  },
  {
    key: "product_manager",
    label: "产品",
    desc: "产品专家：精通需求分析（用户调研/竞品分析）、PRD 撰写、用户故事地图、敏捷产品管理（Scrum/Kanban）、优先级排序（RICE/Kano/WSJF）、产品路线图、数据驱动决策（AARRR/漏斗分析）。",
    Icon: ClipboardList,
  },
];

export const EMPTY_CONFIG: SubagentsConfig = {
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: ["rag_retrieve"],
    triggerDescription: "",
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: ["web_search"],
    triggerDescription: "",
  },
};

function normalizeSubagentConfig(
  raw: Partial<SubagentConfig> | unknown,
  fallback: SubagentConfig = EMPTY_CONFIG.rag,
): SubagentConfig {
  if (!raw || typeof raw !== "object") {
    return { ...fallback };
  }
  const r = raw as Partial<SubagentConfig>;
  return {
    enabled: typeof r.enabled === "boolean" ? r.enabled : fallback.enabled,
    temperature:
      typeof r.temperature === "number" && !Number.isNaN(r.temperature)
        ? r.temperature
        : fallback.temperature,
    systemPrompt: typeof r.systemPrompt === "string" ? r.systemPrompt : fallback.systemPrompt,
    tools: Array.isArray(r.tools) ? r.tools.filter((t): t is string => typeof t === "string") : fallback.tools,
    triggerDescription:
      typeof r.triggerDescription === "string" ? r.triggerDescription : fallback.triggerDescription,
  };
}

/** 将后端/旧版可能不完整的 subagents 配置合并为完整 SubagentsConfig。 */
export function normalizeSubagentsConfig(
  raw: Partial<SubagentsConfig> | unknown,
): SubagentsConfig {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<SubagentsConfig>;
  return {
    rag: normalizeSubagentConfig(r.rag),
    web: normalizeSubagentConfig(r.web),
  };
}

/** 将后端/旧版可能不完整的 teamSubagents 配置合并为完整 TeamSubagentsConfig。 */
export function normalizeTeamSubagentsConfig(
  raw: Partial<TeamSubagentsConfig> | unknown,
): TeamSubagentsConfig {
  const r = (raw && typeof raw === "object" ? raw : {}) as Partial<TeamSubagentsConfig>;
  return {
    frontend_dev: normalizeSubagentConfig(r.frontend_dev, EMPTY_TEAM_CONFIG.frontend_dev),
    backend_dev: normalizeSubagentConfig(r.backend_dev, EMPTY_TEAM_CONFIG.backend_dev),
    tester: normalizeSubagentConfig(r.tester, EMPTY_TEAM_CONFIG.tester),
    architect: normalizeSubagentConfig(r.architect, EMPTY_TEAM_CONFIG.architect),
    devops: normalizeSubagentConfig(r.devops, EMPTY_TEAM_CONFIG.devops),
    ui_designer: normalizeSubagentConfig(r.ui_designer, EMPTY_TEAM_CONFIG.ui_designer),
    product_manager: normalizeSubagentConfig(r.product_manager, EMPTY_TEAM_CONFIG.product_manager),
  };
}

export const EMPTY_TEAM_CONFIG: TeamSubagentsConfig = {
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
