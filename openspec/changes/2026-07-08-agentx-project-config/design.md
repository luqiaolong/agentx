# Design: .agentx 项目级配置目录

## Context

AgentX 当前配置架构为**纯应用级**：所有配置通过 `tauri-plugin-store`（`config.json`）
存储，由 Rust `build_env` 注入为 `AGENTX_*` 环境变量，Python `Settings` 通过
`env_prefix="AGENTX_"` 读取，`@lru_cache get_settings()` 缓存。

工作区（workspace）选择流程：用户在 `ChatComposer` 点「选择目录」→ OS 文件夹选择器 →
`POST /api/sandbox/authorize` 授权 → `workspace_path` 存入 session。**工作区目录本身不
承载任何配置**，仅作为沙箱授权路径。

对比 Cursor（`.cursor/rules` + `.cursor/mcp.json`）、Claude Code（`.claude/`）、
Windsurf（`.windsurfrules`），均支持项目级配置随项目目录走、可提交 git 共享。

## Goals / Non-Goals

**Goals:**
- 用户选择工作区时，自动在工作区根目录生成 `.agentx/` 配置目录（幂等）
- `.agentx/AGENTS.md` 作为项目级 AI 规则，注入到所有 agent 的系统提示词
- `.agentx/mcp.json` / `subagents.json` / `tools.json` 覆盖全局配置
- `.agentx/system_prompt.md` 前置到默认系统提示词
- `.agentx/rules/*.md` 自动加载为附加上下文
- 配置合并：项目级覆盖全局级，凭证不进入项目配置（安全红线）
- 生成幂等：已存在的文件不覆盖（保护用户编辑）

**Non-Goals:**
- 不实现 GUI 编辑器（用户用任意编辑器修改 `.agentx/` 文件，后端读取即生效）
- 不实现项目级技能（`.agentx/skills/`，留作 Future）
- 不实现项目级自定义子代理（留作 Future）
- 不修改全局配置存储机制（`tauri-plugin-store` 不变）
- 不修改凭证注入链路（凭证仍由 Rust `build_env` 注入）
- 不修改 SSE 事件契约
- 不实现配置热重载（项目配置在 `run_router` 入口按需加载，天然即时生效）

## Decisions

### Decision 1: `.agentx/` 目录结构

**选择**：

```
.agentx/
├── AGENTS.md           # 项目级 AI 规则
├── mcp.json            # 项目级 MCP servers
├── subagents.json      # 项目级子代理配置
├── tools.json          # 项目级工具开关
├── system_prompt.md    # 项目级系统提示词
└── rules/              # 附加规则文件
    └── README.md
```

**理由**：
- `AGENTS.md` 是最核心文件——项目级 AI 规则，与 agentx 应用自身的 `AGENTS.md` 概念一致
- `mcp.json` / `subagents.json` / `tools.json` 与全局配置格式相同，降低认知成本
- `system_prompt.md` 用 Markdown 而非 JSON，便于写多行提示词
- `rules/` 目录参考 Cursor `.cursor/rules/`，支持按主题拆分规则文件

**替代方案**：
- 单一 `config.json` 包含所有配置 → 拒绝，AI 规则和提示词是 Markdown，混入 JSON 不自然
- 无 `rules/` 目录，仅 `AGENTS.md` → 拒绝，规则多了单文件臃肿，目录更灵活
- 用 YAML → 拒绝，项目已有 JSON 约定（`tauri-plugin-store`、`mcp_servers_config`）

### Decision 2: 生成触发时机 — 工作区授权成功后

**选择**：在 `POST /api/sandbox/authorize` 成功后，前端 `ChatComposer` 自动调用
`POST /api/project-config/init?path=<workspace>` 生成 `.agentx/`。

**理由**：
- 用户选择工作区 = 明确意图在该目录工作，此时生成配置最自然
- 前端触发而非后端自动：保持 `sandbox.authorize` 单一职责（仅授权），配置生成独立端点
- 幂等生成：已存在 `.agentx/` 时仅补缺失文件，不覆盖用户编辑

**替代方案**：
- 在 `sandbox.authorize` 内部自动生成 → 拒绝，职责混合，且非 workspace 授权场景不需要
- 首次发消息时懒生成 → 拒绝，用户可能在发消息前就想编辑配置
- 手动 `/init` 命令触发 → 作为补充手段保留，但主流程是自动生成

### Decision 3: 配置合并策略 — 项目覆盖全局

**选择**：分层合并，优先级从低到高：

```
内置默认值（代码）
  ↓ 全局配置覆盖
Settings（AGENTX_* env，来自 tauri-plugin-store）
  ↓ 项目配置覆盖
ProjectConfig（.agentx/*.json）
  ↓ 运行时
MergedConfig（实际使用的配置）
```

具体合并规则：
- **MCP servers**：追加模式（全局 + 项目，按 `name` 去重，项目优先）
- **子代理配置**：深合并（项目字段覆盖全局同名字代理的字段）
- **工具开关**：覆盖模式（项目的 key 覆盖全局同 key）
- **系统提示词**：前置模式（项目提示词 + "\n\n" + 默认提示词）
- **AGENTS.md / rules**：项目独占（无全局对应，直接注入上下文）
- **凭证**：全局独占（安全红线，项目配置不得包含凭证）

**理由**：
- 追加（MCP）：项目可能需要额外的 MCP server（如项目专属数据库），全局的也保留
- 深合并（子代理）：用户可能只想调项目的 `temperature`，不想重写整个子代理配置
- 覆盖（工具）：开关是布尔值，直接覆盖语义最清晰
- 前置（提示词）：项目提示词作为"额外指令"，默认提示词作为"基础格式规范"

### Decision 4: `ProjectConfig` 数据结构

**选择**：

```python
@dataclass
class ProjectConfig:
    """从 .agentx/ 加载的项目级配置。"""
    workspace_path: Path
    agents_md: str | None              # .agentx/AGENTS.md 内容，None=文件不存在
    rules: list[RuleFile]              # .agentx/rules/*.md 列表
    mcp_servers: list[dict]            # .agentx/mcp.json，空列表=无项目级 MCP
    subagents_config: dict             # .agentx/subagents.json，空 dict=无覆盖
    tools_config: dict[str, bool]      # .agentx/tools.json，空 dict=无覆盖
    system_prompt: str | None          # .agentx/system_prompt.md，None=无覆盖
    exists: bool                       # .agentx/ 目录是否存在

    @property
    def context_prompt(self) -> str:
        """拼接 AGENTS.md + rules 为上下文提示词。"""
        parts = []
        if self.agents_md:
            parts.append(self.agents_md)
        for rule in self.rules:
            parts.append(f"## {rule.name}\n\n{rule.content}")
        return "\n\n".join(parts)
```

**理由**：
- `exists` 字段让调用方区分"无 .agentx/" vs "有 .agentx/ 但文件为空"
- `context_prompt` property 统一拼接 AGENTS.md + rules，调用方无需关心拼接逻辑
- 所有字段 nullable / 空集合默认值，缺失文件不报错（降级为全局配置）

### Decision 5: 项目配置加载位置 — `run_router` 入口

**选择**：在 `router/graph.py::run_router` 入口加载项目配置：

```python
async def run_router(state: RouterState) -> AsyncIterator[dict]:
    settings = get_settings()
    workspace_path = state.get("workspace_path")
    project_config = load_project_config(Path(workspace_path)) if workspace_path else None
    merged = merge_configs(settings, project_config) if project_config else settings
    # 后续用 merged 替代 settings
```

**理由**：
- `run_router` 是所有聊天消息的唯一入口（work / coding / coding_team 三场景都经过）
- 在入口加载确保所有下游 agent（supervisor / expert / team）用合并后的配置
- 每次消息加载（非缓存）：项目配置可能随时被用户编辑，不能缓存
- 无 workspace_path（Home 模式）时跳过加载，直接用全局 `Settings`

**替代方案**：
- 在 `Settings` 层缓存项目配置 → 拒绝，`Settings` 是全局单例，项目配置是 per-request
- 在各 agent 内部分别加载 → 拒绝，重复加载 + 合并逻辑分散

### Decision 6: 幂等生成策略 — 仅创建缺失文件

**选择**：`generate_agentx_dir` 对每个文件先检查是否存在，存在则跳过：

```python
def generate_agentx_dir(workspace_path: Path) -> GenerationResult:
    agentx_dir = workspace_path / ".agentx"
    agentx_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []

    files = {
        "AGENTS.md": TEMPLATES["agents_md"],
        "mcp.json": TEMPLATES["mcp_json"],
        "subagents.json": TEMPLATES["subagents_json"],
        "tools.json": TEMPLATES["tools_json"],
        "system_prompt.md": TEMPLATES["system_prompt"],
    }
    for name, content in files.items():
        path = agentx_dir / name
        if path.exists():
            skipped.append(name)
        else:
            path.write_text(content, encoding="utf-8")
            created.append(name)

    # rules/ 目录 + README
    rules_dir = agentx_dir / "rules"
    rules_dir.mkdir(exist_ok=True)
    readme = rules_dir / "README.md"
    if not readme.exists():
        readme.write_text(TEMPLATES["rules_readme"], encoding="utf-8")
        created.append("rules/README.md")
    else:
        skipped.append("rules/README.md")

    return GenerationResult(created=created, skipped=skipped, path=str(agentx_dir))
```

**理由**：
- 保护用户编辑：已编辑的文件绝不覆盖
- 返回 `created` / `skipped` 列表，前端可提示用户哪些是新生成的
- `mkdir(exist_ok=True)` + 文件级检查 = 完全幂等，多次调用安全

### Decision 7: AGENTS.md 模板内容 — 引导式

**选择**：模板包含结构化引导，而非空白：

```markdown
# Project AGENTS.md

> 本文件是项目级 AI 规则，会自动注入到所有 agent 的上下文。
> 编辑此文件为你的项目添加专属指令。

## 项目简介
<!-- 一句话描述这个项目是做什么的 -->

## 技术栈
<!-- 列出项目使用的语言、框架、工具 -->

## 编码规范
<!-- 列出项目特有的编码规范，例如：
- 使用 4 空格缩进
- 函数名用 snake_case
- 必须写类型注解
-->

## 禁止事项
<!-- 列出 AI 不应该做的事，例如：
- 不要修改 config/ 目录下的文件
- 不要引入新的第三方依赖
-->

## 项目结构
<!-- 描述关键目录结构，帮助 AI 理解项目布局 -->
```

**理由**：
- 引导式模板降低用户上手成本（vs 空文件）
- HTML 注释 `<!-- -->` 不影响 Markdown 渲染，用户填充后自然变成正文
- 与 agentx 自身 `AGENTS.md` 风格一致（结构化 + 章节）

### Decision 8: 安全约束 — 凭证不进项目配置

**选择**：
- `.agentx/` 模板不包含任何凭证字段
- `mcp.json` 模板仅含 server 连接信息（command/args/env），env 值用占位符
- `load_project_config` 不读取任何凭证字段
- `merge_configs` 不合并凭证

**理由**：
- `.agentx/` 设计为可提交 git，凭证绝不能进 git
- 凭证仍由 `tauri-plugin-store`（加密）→ `build_env` → `AGENTX_*` 注入，链路不变
- MCP server 的 `env` 字段（如 API key）由用户手动填写，模板用 `<YOUR_KEY>` 占位

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| [Risk] 项目配置文件格式错误（JSON 语法错误） | [Mitigation] `load_project_config` 用 try/except 包裹每个文件解析，解析失败降级为空配置 + logger.warning |
| [Risk] 用户删除 `.agentx/` 后配置丢失 | [Mitigation] `load_project_config` 返回 `exists=False`，下游降级为全局配置；前端可重新触发生成 |
| [Risk] `rules/*.md` 文件过多导致上下文膨胀 | [Mitigation] 限制最多加载 10 个规则文件，每个最大 4KB，总计不超过 40KB |
| [Risk] 项目配置 MCP server 指向恶意端点 | [Mitigation] MCP server 仍走 `mcp/client.py` 的安全校验（transport 校验 + 超时），项目配置不绕过安全检查 |
| [Risk] `run_router` 每次加载文件影响性能 | [Mitigation] `.agentx/` 文件总量小（< 50KB），文件 IO < 5ms，可接受；不做缓存以保证即时生效 |
| [Risk] 并发请求同时触发 `generate_agentx_dir` | [Mitigation] `mkdir(exist_ok=True)` + 文件级 `exists()` 检查天然幂等，无竞态 |

## Migration Plan

### Phase 1: 后端 `project_config/` 包
- 新建 `templates.py`（模板内容）
- 新建 `generator.py`（`generate_agentx_dir`）
- 新建 `loader.py`（`load_project_config` + `ProjectConfig` dataclass）
- 新建 `merger.py`（`merge_configs`）
- 单元测试：generator 幂等性、loader 容错、merger 合并规则

### Phase 2: 后端 API + 集成
- 新建 `api/project_config.py`（2 端点）
- 修改 `api/__init__.py` 注册路由
- 修改 `router/graph.py`（加载项目配置 + 注入上下文）
- 单元测试：API 端点 + graph 集成

### Phase 3: 前端集成
- 新建 `lib/api/projectConfig.ts`
- 新建 `components/workspace/ProjectConfigBadge.tsx`
- 修改 `ChatComposer.tsx`（授权后触发生成）
- 修改 `WorkspacePanel.tsx`（显示徽章）
- 前端测试

### Phase 4: 文档 + 收尾
- 更新 `AGENTS.md` §11 文件地图 + §16 配置入口
- 端到端验证
- OpenSpec 归档

## Open Questions

1. **项目配置是否支持 `.agentx/local.json`（不入 git 的本地覆盖）？** → Future 扩展，
   本次不实现。用户可用 `.gitignore` 排除整个 `.agentx/` 或特定文件。
2. **是否需要 `/api/project-config/reload` 热重载端点？** → 不需要。项目配置在
   `run_router` 每次调用时加载，天然即时生效，无需热重载。
3. **CLI 模式是否加载项目配置？** → 是。CLI 模式直连 `run_router`，复用同一加载链路，
   cwd 作为 workspace_path。
