# Proposal: .agentx 项目级配置目录

## Why

AgentX 当前所有配置（子代理、工具、MCP servers、系统提示词、AGENTS.md）都存储在
**应用级** `tauri-plugin-store`（`config.json`），是全局的。这带来三个问题：

1. **配置不跟随项目**：用户切换工作区（workspace）后，AI 仍用全局配置，无法为不同
   项目定制不同的子代理、工具、MCP server 和 AI 规则。例如 A 项目需要 GitHub MCP、
   B 项目需要数据库 MCP，当前只能全局共用。
2. **AI 规则不可项目化**：`AGENTS.md` 仅存在于 agentx 应用自身根目录，用户工作区
   没有"项目级 AI 规则"概念。对比 Cursor（`.cursor/rules`）、Claude Code（`.claude/`）、
   Windsurf（`.windsurfrules`），均支持项目级 AI 规则。
3. **团队协作无配置载体**：项目配置无法提交到 git 与团队共享，每个成员都要在应用
   设置面板手动配置相同的子代理/MCP/工具。

本次提案引入 `.agentx/` 目录（参考 Cursor `.cursor/`），在用户选择工作区时自动生成，
提供项目级配置覆盖能力。

## What Changes

### 新建 `.agentx/` 目录结构（工作区根目录下）

```
<workspace>/
└── .agentx/
    ├── AGENTS.md           # 项目级 AI 规则（自动生成模板，用户编辑）
    ├── mcp.json            # 项目级 MCP servers（追加到全局）
    ├── subagents.json      # 项目级子代理配置（覆盖全局）
    ├── tools.json          # 项目级工具开关（覆盖全局）
    ├── system_prompt.md    # 项目级系统提示词（前置到默认提示词）
    └── rules/              # 附加规则文件目录（*.md 自动加载为上下文）
        └── README.md       # 说明如何添加规则
```

### 新建 `backend/app/project_config/` 包

- `templates.py`：`.agentx/` 各文件的模板内容（AGENTS.md / system_prompt.md / rules/README.md）
- `generator.py`：`generate_agentx_dir(workspace_path)` — 幂等生成 `.agentx/`（仅创建缺失文件，不覆盖用户编辑）
- `loader.py`：`load_project_config(workspace_path)` — 读取 `.agentx/` 配置为 `ProjectConfig` dataclass
- `merger.py`：`merge_configs(settings, project_config)` — 全局配置与项目配置深合并（项目覆盖全局）

### 新建 `backend/app/api/project_config.py` 路由

- `POST /api/project-config/init` — 在指定工作区生成 `.agentx/`（幂等）
- `GET /api/project-config?path=<workspace>` — 读取项目配置状态（哪些文件存在 + 摘要）

### 后端集成

- `router/graph.py::run_router`：消息处理前加载 `workspace_path/.agentx/`，合并到运行时配置
- `config/settings.py`：新增 `ProjectConfig` dataclass + `get_merged_settings(workspace_path)` 工具函数
- AGENTS.md 上下文注入：`workspace_path/.agentx/AGENTS.md` + `rules/*.md` 拼接到系统提示词

### 前端集成

- `lib/api/projectConfig.ts`：API client（init / get）
- `ChatComposer.tsx`：工作区授权成功后自动调 `POST /api/project-config/init`
- `components/workspace/ProjectConfigBadge.tsx`：工作区面板显示 `.agentx/` 配置状态徽章

### 配置合并策略

| 配置项 | 全局来源 | 项目来源 | 合并策略 |
|---|---|---|---|
| AGENTS.md / rules | 无（应用级 AGENTS.md 不参与） | `.agentx/AGENTS.md` + `rules/*.md` | 项目独占（无全局对应） |
| MCP servers | `AGENTX_MCP_SERVERS_CONFIG` | `.agentx/mcp.json` | 追加（按 name 去重，项目优先） |
| 子代理配置 | `AGENTX_SUBAGENTS_CONFIG` | `.agentx/subagents.json` | 深合并（项目字段覆盖全局） |
| 工具开关 | `AGENTX_TOOLS_CONFIG` | `.agentx/tools.json` | 覆盖（项目 key 覆盖全局） |
| 系统提示词 | `default_system_prompt` | `.agentx/system_prompt.md` | 前置（项目提示词 + "\n\n" + 默认） |
| 凭证（API key / Milvus） | tauri-plugin-store（加密） | **禁止** | 全局独占（安全红线） |

## Capabilities

### New Capabilities

- `agentx-project-config`：项目级配置目录能力，包含 `.agentx/` 生成、加载、合并全链路

### Modified Capabilities

- 无（新增能力，不修改现有 capability 的契约）

## Impact

- **后端**：
  - 新建 `project_config/`（4 文件：templates / generator / loader / merger）
  - 新建 `api/project_config.py`（2 端点）
  - 修改 `router/graph.py`（加载项目配置 + 注入上下文）
  - 修改 `config/settings.py`（新增 `ProjectConfig` dataclass）
  - 修改 `api/__init__.py`（注册新路由）
- **前端**：
  - 新建 `lib/api/projectConfig.ts`
  - 新建 `components/workspace/ProjectConfigBadge.tsx`
  - 修改 `ChatComposer.tsx`（工作区授权后触发生成）
  - 修改 `components/workspace/WorkspacePanel.tsx`（显示配置徽章）
- **API**：新增 2 个端点（`POST /api/project-config/init` + `GET /api/project-config`）
- **测试**：
  - 后端单元测试：generator / loader / merger / API 端点
  - 前端测试：ProjectConfigBadge 渲染 + ChatComposer 触发
- **文档**：更新 `AGENTS.md` §11 文件地图 + §16 配置入口（新增项目级配置章节）

## Future Extensibility

- `.agentx/skills/` 目录：项目级技能文件（当前技能在 `backend/app/config/prompts/`，未来可项目化）
- `.agentx/custom_subagents.json`：项目级自定义子代理
- `.agentx/.gitignore` 模板：自动生成，排除敏感配置
- GUI 编辑器：在设置面板直接编辑 `.agentx/` 文件
