import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  Database,
  Globe,
  Save,
  Check,
  RotateCw,
  AlertTriangle,
  Plus,
  Pencil,
  Trash2,
  Lock,
  User,
  Monitor,
  Server,
  Bug,
  Layers,
  Cloud,
  Palette,
  ClipboardList,
  Users,
} from "lucide-react";
import type {
  SubagentConfig,
  SubagentsConfig,
  TeamSubagentsConfig,
  ToolsConfig,
  CustomSubagentsMap,
  CustomSubagentEntry,
} from "@/lib/utils";
import { useSceneStore } from "@/stores/scene";
import {
  SubagentEditModal,
  type SubagentEditModalData,
} from "./SubagentEditModal";

// 子代理键名与后端 backend/app/config.py _default_subagents() 一致
type BuiltinSubagentKey = "code" | "rag" | "web";

// 软件开发专家团角色键名
type TeamSubagentKey =
  | "frontend_dev"
  | "backend_dev"
  | "tester"
  | "architect"
  | "devops"
  | "ui_designer"
  | "product_manager";

// 内置子代理可选工具清单（与 backend _ALL_TOOLS 一致，但不含危险工具）
const ALL_TOOLS: string[] = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "web_search",
  "rag_retrieve",
];

interface BuiltinMeta {
  key: BuiltinSubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

const BUILTIN_SUBAGENTS: BuiltinMeta[] = [
  {
    key: "code",
    label: "Code 子代理",
    desc: "代码与文件操作专家：擅长读取、搜索、分析代码文件和目录结构，回答与代码、文件内容、项目结构、HTML/CSS/JS/Python/Java 等技术实现相关的问题。",
    Icon: Code2,
  },
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

interface TeamMeta {
  key: TeamSubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

const TEAM_SUBAGENTS: TeamMeta[] = [
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

const EMPTY_CONFIG: SubagentsConfig = {
  code: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    triggerDescription: "",
  },
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    triggerDescription: "",
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    triggerDescription: "",
  },
};

const EMPTY_TEAM_CONFIG: TeamSubagentsConfig = {
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

interface BuiltinCardProps {
  meta: BuiltinMeta | TeamMeta;
  cfg: SubagentConfig;
  onEnabledChange: (enabled: boolean) => void;
  onEdit: () => void;
}

/** 内置子代理卡片：默认折叠，仅显示开关 + 编辑按钮。 */
function BuiltinCard({ meta, cfg, onEnabledChange, onEdit }: BuiltinCardProps) {
  const [open, setOpen] = useState(false);
  const [toolsConfig, setToolsConfig] = useState<ToolsConfig>(
    {} as ToolsConfig,
  );

  useEffect(() => {
    void (async () => {
      try {
        const tc = await window.api.settings.getToolsConfig();
        setToolsConfig(tc);
      } catch {
        // 后端未就绪时保留空对象
      }
    })();
  }, []);

  // 绑定的工具在全局均被禁用时触发警告
  const allToolsOff =
    cfg.tools.length > 0 &&
    cfg.tools.every((t) => toolsConfig[t as keyof ToolsConfig] === false);

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        cfg.enabled ? "border-default" : "border-default opacity-60"
      }`}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-c shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-c shrink-0" />
          )}
          <meta.Icon className="h-4 w-4 text-brand-500 shrink-0" />
          <span className="font-semibold text-primary-c truncate" style={{ fontSize: 'var(--fs-card-title)' }}>
            {meta.label}
          </span>
        </button>

        {/* 开关（始终显示） */}
        <button
          type="button"
          role="switch"
          aria-checked={cfg.enabled}
          data-checked={cfg.enabled}
          onClick={() => onEnabledChange(!cfg.enabled)}
          className="switch-track shrink-0"
        >
          <span className="switch-thumb" data-checked={cfg.enabled} />
        </button>

        {/* 编辑按钮 */}
        <button
          type="button"
          onClick={onEdit}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-brand-500 shrink-0"
          aria-label="编辑"
          title="编辑"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>

      {open && (
        <div className="space-y-2 border-t border-default px-3 py-2.5">
          <div className="flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
            <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5">
              <Lock className="h-2.5 w-2.5" />
              内置
            </span>
            <span>温度 {cfg.temperature.toFixed(1)}</span>
            <span>·</span>
            <span>工具 {cfg.tools.length}</span>
            <span>·</span>
            <span>条件</span>
          </div>
          {allToolsOff && (
            <div className="flex items-center gap-1 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              <AlertTriangle className="h-3 w-3" />
              绑定的工具全部被禁用，子代理将不可用
            </div>
          )}
          {cfg.tools.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {cfg.tools.map((t) => (
                <span
                  key={t}
                  className="rounded bg-subtle px-1.5 py-0.5 font-mono text-secondary-c"
                  style={{ fontSize: 'var(--fs-card-meta)' }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {cfg.triggerDescription && (
            <div className="rounded bg-brand-500/10 px-2 py-1 text-brand-600 dark:text-brand-400 line-clamp-2" style={{ fontSize: 'var(--fs-card-meta)' }}>
              {cfg.triggerDescription}
            </div>
          )}
          {cfg.systemPrompt && (
            <div className="space-y-1">
              <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>系统提示词</div>
              <div className="rounded bg-subtle/40 px-2 py-1 text-muted-c line-clamp-3" style={{ fontSize: 'var(--fs-card-desc)' }}>
                {cfg.systemPrompt}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface CustomCardProps {
  entry: CustomSubagentEntry;
  onEnabledChange: (enabled: boolean) => void;
  onEdit: () => void;
  onRemove: () => void;
}

/** 自定义子代理卡片：默认折叠，显示开关 + 编辑 + 删除按钮。 */
function CustomCard({
  entry,
  onEnabledChange,
  onEdit,
  onRemove,
}: CustomCardProps) {
  const [open, setOpen] = useState(false);
  const [toolsConfig, setToolsConfig] = useState<ToolsConfig>(
    {} as ToolsConfig,
  );

  useEffect(() => {
    void (async () => {
      try {
        const tc = await window.api.settings.getToolsConfig();
        setToolsConfig(tc);
      } catch {
        // 后端未就绪时保留空对象
      }
    })();
  }, []);

  const allToolsOff =
    entry.tools.length > 0 &&
    entry.tools.every((t) => toolsConfig[t as keyof ToolsConfig] === false);

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        entry.enabled ? "border-default" : "border-default opacity-60"
      }`}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-c shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-c shrink-0" />
          )}
          <User className="h-4 w-4 text-purple-500 shrink-0" />
          <span className="font-semibold text-primary-c truncate" style={{ fontSize: 'var(--fs-card-title)' }}>
            {entry.name}
          </span>
          <span className="font-mono text-muted-c shrink-0" style={{ fontSize: 'var(--fs-card-meta)' }}>
            @{entry.key}
          </span>
        </button>

        {/* 开关 */}
        <button
          type="button"
          role="switch"
          aria-checked={entry.enabled}
          data-checked={entry.enabled}
          onClick={() => onEnabledChange(!entry.enabled)}
          className="switch-track shrink-0"
        >
          <span className="switch-thumb" data-checked={entry.enabled} />
        </button>

        {/* 编辑 */}
        <button
          type="button"
          onClick={onEdit}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-brand-500 shrink-0"
          aria-label="编辑"
          title="编辑"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>

        {/* 删除 */}
        <button
          type="button"
          onClick={onRemove}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-rose-500 shrink-0"
          aria-label="删除"
          title="删除"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {open && (
        <div className="space-y-2 border-t border-default px-3 py-2.5">
          <div className="flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
            <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-1.5 py-0.5 text-purple-600 dark:text-purple-400">
              <User className="h-2.5 w-2.5" />
              自定义
            </span>
            <span>温度 {entry.temperature.toFixed(1)}</span>
            <span>·</span>
            <span>工具 {entry.tools.length}</span>
            <span>·</span>
            <span>条件</span>
          </div>
          {allToolsOff && (
            <div className="flex items-center gap-1 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              <AlertTriangle className="h-3 w-3" />
              绑定的工具全部被禁用，子代理将不可用
            </div>
          )}
          {entry.tools.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {entry.tools.map((t) => (
                <span
                  key={t}
                  className="rounded bg-subtle px-1.5 py-0.5 font-mono text-secondary-c"
                  style={{ fontSize: 'var(--fs-card-meta)' }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {entry.triggerDescription && (
            <div className="rounded bg-brand-500/10 px-2 py-1 text-brand-600 dark:text-brand-400 line-clamp-2" style={{ fontSize: 'var(--fs-card-meta)' }}>
              {entry.triggerDescription}
            </div>
          )}
          {entry.systemPrompt && (
            <div className="space-y-1">
              <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>系统提示词</div>
              <div className="rounded bg-subtle/40 px-2 py-1 text-muted-c line-clamp-3" style={{ fontSize: 'var(--fs-card-desc)' }}>
                {entry.systemPrompt}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 分组容器：标题可折叠整个分组。 */
function SubagentGroup({
  title,
  icon: Icon,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  icon: typeof Bot;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-1 py-1 text-left"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-c" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-c" />
        )}
        <Icon className="h-4 w-4 text-brand-500" />
        <span className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-card-title)' }}>{title}</span>
        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
          {count}
        </span>
      </button>
      {open && <div className="space-y-2">{children}</div>}
    </div>
  );
}

export function SubagentsSettings() {
  const scene = useSceneStore((s) => s.scene);
  const isCoding = scene === "coding";

  const [config, setConfig] = useState<SubagentsConfig>(EMPTY_CONFIG);
  const [teamConfig, setTeamConfig] = useState<TeamSubagentsConfig>(EMPTY_TEAM_CONFIG);
  const [customMap, setCustomMap] = useState<CustomSubagentsMap>({});
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // 编辑弹窗状态
  const [modalOpen, setModalOpen] = useState(false);
  const [modalData, setModalData] = useState<SubagentEditModalData | null>(
    null,
  );
  const [modalIsNew, setModalIsNew] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [cfg, team, custom] = await Promise.all([
          window.api.settings.getSubagentsConfig(),
          window.api.settings.getTeamSubagentsConfig(),
          window.api.settings.getCustomSubagents(),
        ]);
        setConfig(cfg);
        setTeamConfig(team);
        setCustomMap(custom);
      } catch {
        // 后端未就绪时保留默认值
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const updateBuiltin = (
    key: BuiltinSubagentKey,
    next: SubagentConfig,
  ): void => {
    setConfig((s) => ({ ...s, [key]: next }));
  };

  const updateTeam = (
    key: TeamSubagentKey,
    next: SubagentConfig,
  ): void => {
    setTeamConfig((s) => ({ ...s, [key]: next }));
  };

  const updateCustom = (key: string, next: CustomSubagentEntry): void => {
    setCustomMap((s) => ({ ...s, [key]: next }));
  };

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await Promise.all([
        window.api.settings.setSubagentsConfig(config),
        window.api.settings.setTeamSubagentsConfig(teamConfig),
        window.api.settings.setCustomSubagents(customMap),
      ]);
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const restart = async (): Promise<void> => {
    setErrMsg(null);
    try {
      setRestarting(true);
      await save();
      const result = await window.api.app.restartBackend();
      if (!result.ok) {
        setErrMsg(result.message ?? "重启后端超时");
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRestarting(false);
    }
  };

  // 打开编辑弹窗（内置）
  const openEditBuiltin = (meta: BuiltinMeta): void => {
    const cfg = config[meta.key];
    setModalData({
      builtinKey: meta.key,
      name: meta.label,
      enabled: cfg.enabled,
      temperature: cfg.temperature,
      systemPrompt: cfg.systemPrompt,
      tools: cfg.tools,
      triggerDescription: cfg.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开编辑弹窗（团队角色）
  const openEditTeam = (meta: TeamMeta): void => {
    const cfg = teamConfig[meta.key];
    setModalData({
      teamKey: meta.key,
      name: meta.label,
      enabled: cfg.enabled,
      temperature: cfg.temperature,
      systemPrompt: cfg.systemPrompt,
      tools: cfg.tools,
      triggerDescription: cfg.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开编辑弹窗（自定义已存在）
  const openEditCustom = (entry: CustomSubagentEntry): void => {
    setModalData({
      customKey: entry.key,
      name: entry.name,
      enabled: entry.enabled,
      temperature: entry.temperature,
      systemPrompt: entry.systemPrompt,
      tools: entry.tools,
      triggerDescription: entry.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开新建弹窗
  const openNewCustom = (): void => {
    setModalData({
      customKey: "",
      name: "",
      enabled: true,
      temperature: 0.2,
      systemPrompt: "",
      tools: [],
      triggerDescription: "",
    });
    setModalIsNew(true);
    setModalOpen(true);
  };

  // 弹窗保存
  const handleModalSave = (data: SubagentEditModalData): void => {
    if (data.builtinKey) {
      // 内置：仅更新可编辑字段
      updateBuiltin(data.builtinKey, {
        enabled: data.enabled,
        temperature: data.temperature,
        systemPrompt: data.systemPrompt,
        tools: data.tools,
        triggerDescription: data.triggerDescription,
      });
      setModalOpen(false);
    } else if (data.teamKey) {
      // 团队角色
      updateTeam(data.teamKey, {
        enabled: data.enabled,
        temperature: data.temperature,
        systemPrompt: data.systemPrompt,
        tools: data.tools,
        triggerDescription: data.triggerDescription,
      });
      setModalOpen(false);
    } else if (data.customKey) {
      // 提取到局部 const 以便 TS 在 async 闭包内正确收窄类型
      const customKey = data.customKey;
      if (modalIsNew) {
        // 新建：调用 IPC addCustomSubagent（会校验 key 唯一性）
        void (async () => {
          try {
            const entry = await window.api.settings.addCustomSubagent({
              key: customKey,
              name: data.name,
              enabled: data.enabled,
              temperature: data.temperature,
              systemPrompt: data.systemPrompt,
              tools: data.tools,
              triggerDescription: data.triggerDescription,
            });
            setCustomMap((s) => ({ ...s, [entry.key]: entry }));
            setModalOpen(false);
          } catch (e) {
            setErrMsg(e instanceof Error ? e.message : String(e));
          }
        })();
      } else {
        // 编辑现有自定义
        updateCustom(customKey, {
          key: customKey,
          name: data.name,
          enabled: data.enabled,
          temperature: data.temperature,
          systemPrompt: data.systemPrompt,
          tools: data.tools,
          triggerDescription: data.triggerDescription,
        });
        setModalOpen(false);
      }
    } else {
      setErrMsg("无效的弹窗数据");
    }
  };

  // 删除自定义
  const handleRemoveCustom = (key: string): void => {
    void (async () => {
      try {
        await window.api.settings.removeCustomSubagent(key);
        setCustomMap((s) => {
          const next = { ...s };
          delete next[key];
          return next;
        });
      } catch (e) {
        setErrMsg(e instanceof Error ? e.message : String(e));
      }
    })();
  };

  const builtinDisabledCount = useMemo(
    () =>
      [config.code, config.rag, config.web].filter((c) => !c.enabled).length,
    [config],
  );

  const teamDisabledCount = useMemo(
    () =>
      isCoding
        ? Object.values(teamConfig).filter((c) => !c.enabled).length
        : 0,
    [teamConfig, isCoding],
  );

  const customDisabledCount = useMemo(
    () => Object.values(customMap).filter((c) => !c.enabled).length,
    [customMap],
  );

  const existingCustomKeys = useMemo(
    () => Object.keys(customMap),
    [customMap],
  );

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置内置（Code/RAG/Web）与自定义子代理。所有卡片默认折叠，点击展开查看详情或编辑。
          保存后需重启后端生效。
        </span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {(builtinDisabledCount > 0 || teamDisabledCount > 0 || customDisabledCount > 0) && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>
            有 {builtinDisabledCount + teamDisabledCount + customDisabledCount} 个子代理被禁用，相关路由将回退到主代理。
          </span>
        </div>
      )}

      {/* 内置子代理分组 */}
      <SubagentGroup
        title="内置子代理"
        icon={Bot}
        count={BUILTIN_SUBAGENTS.length}
        defaultOpen={false}
      >
        {BUILTIN_SUBAGENTS.map((meta) => (
          <BuiltinCard
            key={meta.key}
            meta={meta}
            cfg={config[meta.key]}
            onEnabledChange={(enabled) =>
              updateBuiltin(meta.key, { ...config[meta.key], enabled })
            }
            onEdit={() => openEditBuiltin(meta)}
          />
        ))}
      </SubagentGroup>

      {/* 软件开发专家团分组 — 仅在 Coding 场景下展示 */}
      {isCoding && (
        <SubagentGroup
          title="软件开发专家团"
          icon={Users}
          count={TEAM_SUBAGENTS.length}
          defaultOpen={false}
        >
          {TEAM_SUBAGENTS.map((meta) => (
            <BuiltinCard
              key={meta.key}
              meta={meta}
              cfg={teamConfig[meta.key]}
              onEnabledChange={(enabled) =>
                updateTeam(meta.key, { ...teamConfig[meta.key], enabled })
              }
              onEdit={() => openEditTeam(meta)}
            />
          ))}
        </SubagentGroup>
      )}

      {/* 自定义子代理分组 */}
      <SubagentGroup
        title="自定义子代理"
        icon={User}
        count={Object.keys(customMap).length}
      >
        {Object.values(customMap).length === 0 ? (
          <div className="rounded-lg border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
            暂无自定义子代理，点击下方按钮新建
          </div>
        ) : (
          Object.values(customMap).map((entry) => (
            <CustomCard
              key={entry.key}
              entry={entry}
              onEnabledChange={(enabled) =>
                updateCustom(entry.key, { ...entry, enabled })
              }
              onEdit={() => openEditCustom(entry)}
              onRemove={() => handleRemoveCustom(entry.key)}
            />
          ))
        )}
        <button
          type="button"
          onClick={openNewCustom}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-brand-500/50 px-3 py-2 text-brand-600 hover:bg-brand-500/5 dark:text-brand-400"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
        >
          <Plus className="h-3.5 w-3.5" />
          新建自定义子代理
        </button>
      </SubagentGroup>

      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        <button
          type="button"
          onClick={restart}
          className="btn-secondary"
          disabled={restarting}
        >
          <RotateCw className="h-3.5 w-3.5" />
          {restarting ? "重启中…" : "保存并重启后端"}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            <Check className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>

      <SubagentEditModal
        open={modalOpen}
        initial={modalData}
        existingCustomKeys={existingCustomKeys}
        isNew={modalIsNew}
        onClose={() => setModalOpen(false)}
        onSave={handleModalSave}
      />
    </div>
  );
}
