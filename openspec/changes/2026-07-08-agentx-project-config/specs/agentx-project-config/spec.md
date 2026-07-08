# Spec: agentx-project-config

## Capability

`agentx-project-config` — 项目级配置目录能力，在工作区根目录生成 `.agentx/` 配置目录，
提供项目级 AI 规则、MCP servers、子代理配置、工具开关、系统提示词的覆盖能力。

## Requirements

### REQ-1: .agentx/ 目录生成

- **REQ-1.1**: 用户选择工作区并授权成功后，前端自动调用 `POST /api/project-config/init`
  生成 `.agentx/` 目录
- **REQ-1.2**: 生成是幂等的——已存在的文件不被覆盖，仅创建缺失文件
- **REQ-1.3**: `.agentx/` 目录结构包含：`AGENTS.md` / `mcp.json` / `subagents.json` /
  `tools.json` / `system_prompt.md` / `rules/README.md`
- **REQ-1.4**: 未授权的路径调用 init 端点返回 400
- **REQ-1.5**: 生成结果返回 `created`（新建文件列表）和 `skipped`（已存在跳过列表）

### REQ-2: 项目配置加载

- **REQ-2.1**: `load_project_config(workspace_path)` 读取 `.agentx/` 目录
- **REQ-2.2**: `.agentx/` 不存在时返回 `exists=False` 的空 ProjectConfig
- **REQ-2.3**: JSON 文件解析失败时降级为空配置 + logger.warning，不抛异常
- **REQ-2.4**: `rules/*.md` 限制最多 10 个文件，每个最大 4KB，按文件名排序
- **REQ-2.5**: `ProjectConfig.context_prompt` 拼接 AGENTS.md + rules 为上下文提示词

### REQ-3: 配置合并

- **REQ-3.1**: `merge_configs(settings, project)` 将项目配置合并到全局 Settings 之上
- **REQ-3.2**: MCP servers 追加模式（全局 + 项目，按 name 去重，项目优先）
- **REQ-3.3**: 子代理配置深合并（项目字段覆盖全局同名字代理的字段）
- **REQ-3.4**: 工具开关覆盖模式（项目 key 覆盖全局 key）
- **REQ-3.5**: 系统提示词前置模式（项目提示词 + "\n\n" + 默认提示词）
- **REQ-3.6**: AGENTS.md / rules 为项目独占（无全局对应），通过 context_prompt 注入
- **REQ-3.7**: 凭证不参与合并（安全红线，凭证仅由 tauri-plugin-store 注入）
- **REQ-3.8**: `project is None` 或 `exists=False` 时，合并结果等价于原 Settings

### REQ-4: router 集成

- **REQ-4.1**: `run_router` 入口检查 `workspace_path`
- **REQ-4.2**: 有 workspace_path 时加载项目配置并合并
- **REQ-4.3**: 无 workspace_path 时直接用全局 `get_settings()`
- **REQ-4.4**: 合并后的 `MergedConfig` 传给下游所有 agent
- **REQ-4.5**: `context_prompt` 注入到系统提示词（追加到默认提示词）

### REQ-5: API 端点

- **REQ-5.1**: `POST /api/project-config/init` — 请求体 `{path: str}`，返回
  `{ok: bool, path: str, created: list[str], skipped: list[str]}`
- **REQ-5.2**: `GET /api/project-config?path=<workspace>` — 返回
  `{exists: bool, files: [{name, exists, size}], agents_md_preview: str | null}`
- **REQ-5.3**: 两个端点都校验路径在沙箱授权范围内

### REQ-6: 前端集成

- **REQ-6.1**: `ChatComposer.handleAttachWorkspace` 授权成功后调用 `initProjectConfig`
- **REQ-6.2**: 配置生成失败时不阻塞工作区绑定（best-effort），仅提示错误
- **REQ-6.3**: `ProjectConfigBadge` 显示 `.agentx/` 配置状态（绿色=已配置/灰色=未配置）
- **REQ-6.4**: `ProjectConfigBadge` 点击可重新生成（补缺失文件）
- **REQ-6.5**: `WorkspacePanel` 顶部显示 `ProjectConfigBadge`

### REQ-7: 模板内容

- **REQ-7.1**: `AGENTS.md` 模板为引导式，含项目简介/技术栈/编码规范/禁止事项/项目结构章节
- **REQ-7.2**: `mcp.json` 模板为空数组 `[]`，含注释说明格式
- **REQ-7.3**: `subagents.json` 模板为空对象 `{}`，含注释说明格式
- **REQ-7.4**: `tools.json` 模板为空对象 `{}`，含注释说明格式
- **REQ-7.5**: `system_prompt.md` 模板为引导式提示词
- **REQ-7.6**: `rules/README.md` 说明如何添加规则文件

## API Contract

### POST /api/project-config/init

**Request:**
```json
{
  "path": "/absolute/path/to/workspace"
}
```

**Response 200:**
```json
{
  "ok": true,
  "path": "/absolute/path/to/workspace/.agentx",
  "created": ["AGENTS.md", "mcp.json", "rules/README.md"],
  "skipped": []
}
```

**Response 400:** 路径未授权或非法

### GET /api/project-config?path=/absolute/path/to/workspace

**Response 200:**
```json
{
  "exists": true,
  "files": [
    {"name": "AGENTS.md", "exists": true, "size": 1024},
    {"name": "mcp.json", "exists": true, "size": 45},
    {"name": "rules", "exists": true, "size": 0}
  ],
  "agents_md_preview": "# Project AGENTS.md\n\n> 本文件是项目级..."
}
```

## Data Models

### ProjectConfig

```python
@dataclass
class RuleFile:
    name: str        # 文件名（不含扩展名）
    content: str     # 文件内容

@dataclass
class ProjectConfig:
    workspace_path: Path
    agents_md: str | None
    rules: list[RuleFile]
    mcp_servers: list[dict]
    subagents_config: dict
    tools_config: dict[str, bool]
    system_prompt: str | None
    exists: bool

    @property
    def context_prompt(self) -> str: ...
```

### MergedConfig

```python
@dataclass
class MergedConfig:
    """合并后的运行时配置，暴露与 Settings 同名的 property。"""
    base: Settings                    # 原始全局 Settings
    project: ProjectConfig | None     # 项目配置（None=无项目配置）

    @property
    def subagents(self) -> dict[str, SubagentSettings]: ...
    @property
    def tools_enabled(self) -> dict[str, bool]: ...
    @property
    def mcp_servers_config(self) -> list: ...
    @property
    def default_system_prompt(self) -> str: ...
    @property
    def context_prompt(self) -> str: ...  # AGENTS.md + rules
```

## Security Constraints

- **SEC-1**: `.agentx/` 模板不包含任何凭证字段（API key / password / token）
- **SEC-2**: `load_project_config` 不读取凭证字段
- **SEC-3**: `merge_configs` 不合并凭证
- **SEC-4**: API 端点校验路径在沙箱授权范围内
- **SEC-5**: MCP servers 从项目配置加载后仍走 `mcp/client.py` 安全校验
- **SEC-6**: `mcp.json` 模板的 env 值用 `<YOUR_KEY>` 占位符，不含真实凭证
