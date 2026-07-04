## Context

当前项目状态（截至 2026-07-04）：
- 后端 M1 闭环已通：Router 三路径 + 子代理 + 沙箱 + 审批 + SSE + 183 单测通过
- 前端骨架已就位：三栏布局、ChatView、ApprovalDialog、SettingsModal（6 tab：模型/系统提示词/知识库/审批/沙箱/日志）
- 子代理硬编码：[subagents/code_agent.py#L58](file:///d:/java/agentprojects/agent-py/backend/app/subagents/code_agent.py) `temperature=0.2`、`tools = _make_fs_tools(thread_id)`、`name="code_agent"` 全部写死；rag/web 同样
- 工具集硬编码：[paths/deep_path.py#L49-L77](file:///d:/java/agentprojects/agent-py/backend/app/paths/deep_path.py) `_make_deep_tools` 暴露 `[*fs_tools, write_file, edit_file, *rag_tools, *web_tools]`，无法按需关闭
- 路径 B 关键词硬编码：[router/graph.py#L124-L144](file:///d:/java/agentprojects/agent-py/backend/app/router/graph.py) `_WEB_KEYWORDS` / `_RAG_KEYWORDS` 写死，用户无法调整触发词
- 技能加载器已存在：[memory/skills_loader.py](file:///d:/java/agentprojects/agent-py/backend/app/memory/skills_loader.py) `load_skills()` 扫描 `data/skills/*.md`，但前端无管理界面
- checkpointer 不可见：[memory/checkpointer.py](file:///d:/java/agentprojects/agent-py/backend/app/memory/checkpointer.py) `data/agent_py.db` 大小、已持久化 thread_id 列表均无 API 暴露
- 长期用户画像：完全没有存储机制，每次会话都从零开始

设计约束（来自 [project_memory.md](file:///c:/Users/luqia/.trae-cn/memory/projects/-d-java-agentprojects-agent-py/project_memory.md)）：
- 凭证（LLM API Key / Milvus）走 `electron-store` + `safeStorage`，子代理/工具配置同通道（非凭证可明文）
- 路径 B 命中禁用子代理时静默退回路径 A（CHAT），不报错
- LLM classifier 规则顺序：cmd → DANGEROUS → short msg → CHAT_KEYWORDS → SINGLE_TOOL → DEEP_TASK
- 子代理禁止暴露 write_file/edit_file/shell_exec（安全关键，仅 DeepAgent 路径走 interrupt_before 审批）

## Goals / Non-Goals

**Goals:**
- 3 个新 tab 全部落地，设置页扩到 9 tab
- 子代理 5 维度可配置：启用、温度、system prompt、绑定工具集、触发关键词
- 工具集 8 个工具可单独启用/禁用，影响 DeepAgent 与所有子代理
- 技能文件完整 CRUD + 热重载
- checkpointer 状态可见 + 单会话/全量清理
- 长期用户画像：LLM 自动抽取 + 手动编辑 + system prompt 注入
- 所有新增端点有单测覆盖
- 前端 typecheck + smoke 测试通过

**Non-Goals:**
- 不做子代理的「自定义新子代理」（仅配置现有 3 个）
- 不做工具的「参数默认值配置」（仅启用/禁用）
- 不做画像的「按会话隔离」（画像全局共享）
- 不做技能的「在线市场」（仅本地文件管理）
- 不重构 Router 三路径核心逻辑（仅扩展配置读取）
- 不做 M2 打包/自动更新

## Decisions

### D1. 配置持久化双通道：env-injected vs HTTP-managed

**选择**：混合模式
- **子代理/工具配置** → `electron-store` → spawn 注入 `AGENT_PY_*` env → 后端 `get_settings()` `lru_cache` 读取 → **重启后端生效**
- **画像/技能/checkpointer** → 后端 HTTP 端点直接读写文件 → **即时生效**

**理由**：
- 子代理/工具配置与现有 6 tab 同通道，保持架构一致性；`get_settings()` 是 `lru_cache`，热加载需清除缓存 + 改造所有调用点，工作量翻倍
- 画像需 LLM 动态抽取、技能需热 reload、checkpointer 需运行时清理，必须即时生效；走 env 缓存反而别扭

**备选**：
- 全部走 env + 重启 → 画像/技能每次变更都要重启，UX 灾难
- 全部走 HTTP → 需新增 `agents_config.json` `tools_config.json`，与现有 6 tab 不一致；且 `get_settings()` `lru_cache` 模式已固化

**UX 补偿**：子代理/工具 tab 保存时弹 toast「已保存，重启后端生效」+ 「立即重启后端」按钮（调现有 `app:restart`）。

### D2. 子代理配置数据结构

**选择**：`electron-store` 中 `subagents` 键存 JSON：

```typescript
interface SubagentConfig {
  enabled: boolean;          // 启用/禁用
  temperature: number;       // 0.0-2.0，默认 0.2
  systemPrompt: string;      // 空则用代码默认
  tools: string[];           // 工具名列表，如 ["read_file","list_dir"]
  keywords: string[];        // 触发关键词，仅 code/rag/web 有效
}
interface SubagentsConfig {
  code: SubagentConfig;
  rag: SubagentConfig;
  web: SubagentConfig;
}
```

**默认值**（与当前硬编码一致，保证零配置行为不变）：
- code: `{enabled: true, temperature: 0.2, systemPrompt: "", tools: ["read_file","list_dir","glob","grep"], keywords: []}`
- rag: `{enabled: true, temperature: 0.2, systemPrompt: "", tools: ["rag_retrieve"], keywords: ["知识库","文档库","检索","向量","rag","知识","文档"]}`
- web: `{enabled: true, temperature: 0.2, systemPrompt: "", tools: ["web_search"], keywords: ["搜索","网页","联网","查一下","search","web","google","百度"]}`

**后端读取**：`config.py` 新增 `SubagentSettings` 嵌套配置，从 `AGENT_PY_SUBAGENTS_CONFIG` env 读取 JSON 字符串（spawn 时序列化注入）。

**备选**：
- 每个字段独立 env（`AGENT_PY_CODE_TEMPERATURE` 等）→ 21 个 env 变量过多，难维护
- 后端独立 JSON 文件 → 与现有 6 tab 不一致

### D3. 工具配置数据结构

**选择**：`electron-store` 中 `tools` 键存启用状态：

```typescript
interface ToolsConfig {
  read_file: boolean;
  list_dir: boolean;
  glob: boolean;
  grep: boolean;
  write_file: boolean;
  edit_file: boolean;
  web_search: boolean;
  rag_retrieve: boolean;
}
```

**默认值**：全部 `true`（零配置行为不变）。

**后端读取**：`config.py` 新增 `tools_enabled: dict[str, bool]`，从 `AGENT_PY_TOOLS_CONFIG` env 读取 JSON。

**生效点**：
- DeepAgent `_make_deep_tools`：根据启用状态过滤工具
- 子代理 `_make_*_tools`：根据启用状态过滤工具；若子代理绑定的工具全部被禁用，该子代理自动禁用（前端展示警告「绑定的工具全部被禁用，子代理将不可用」）

### D4. 路径 B 子代理禁用处理：静默退回路径 A

**选择**：[router/graph.py](file:///d:/java/agentprojects/agent-py/backend/app/router/graph.py) `_select_subagent` 改造：

```python
def _select_subagent(message: str) -> str | None:
    """返回子代理名；若命中的子代理被禁用或无可用工具，返回 None 退回路径 A。"""
    settings = get_settings()
    for agent_name in ("web", "rag", "code"):
        cfg = settings.subagents[agent_name]
        if not cfg.enabled:
            continue
        # 检查关键词命中
        if any(kw in message for kw in cfg.keywords):
            # 检查绑定的工具是否全部被禁用
            if any(settings.tools_enabled.get(t, True) for t in cfg.tools):
                return agent_name
            logger.warning(f"subagent {agent_name} matched but all tools disabled, fallback to CHAT")
            return None
    return None  # 无命中，退回路径 A
```

`run_router` 中：`_select_subagent` 返回 `None` 时走 `_run_chat_path`。

**备选**：
- 报错提示 → 用户体验差，需手动启用
- 升级为路径 C → DeepAgent 已有这些工具，但路径 C 成本高（interrupt_before 审批），不适合简单查询

### D5. 技能文件 CRUD：直接操作 `data/skills/*.md`

**选择**：后端新增 `memory/skills_store.py`，提供：

```python
def list_skills_files() -> list[SkillFileInfo]:
    """返回 [{name, size, mtime, content_preview}]，不含完整 content。"""

def get_skill_file(name: str) -> str:
    """返回完整文件内容（含 frontmatter）。"""
    # 路径校验：name 不含 .. / \ / ，防止目录逃逸

def save_skill_file(name: str, content: str) -> None:
    """写入 data/skills/{name}.md，自动 reload_skills()。"""
    # name 校验：^[a-zA-Z0-9_-]+$，长度 1-64

def delete_skill_file(name: str) -> bool:
    """删除 data/skills/{name}.md，自动 reload_skills()。"""
```

**安全**：
- `name` 严格校验（`^[a-zA-Z0-9_-]+$`），拒绝任何路径分隔符
- 路径必须在 `DATA_DIR / "skills"` 内（resolve 后校验父目录）

**API**：
- `GET /api/memory/skills` → 列表
- `GET /api/memory/skills/{name}` → 单个完整内容
- `POST /api/memory/skills` body `{name, content}` → 新建/覆盖（name 已存在时覆盖并 reload）
- `DELETE /api/memory/skills/{name}` → 删除

每次写操作后调 `reload_skills()` 刷新缓存。

### D6. Checkpointer 只读视图 + 单会话清理

**选择**：后端新增 `memory/checkpointer_view.py`：

```python
async def list_threads() -> list[dict]:
    """返回 [{thread_id, checkpoint_count, last_updated, size_bytes}]。"""
    # 直接查 sqlite checkpoint 表，按 thread_id 分组

async def get_db_size() -> int:
    """返回 data/agent_py.db 文件大小（字节）。"""

async def delete_thread(thread_id: str) -> int:
    """删除指定 thread 的所有 checkpoint，返回删除条数。"""
    # 复用 AsyncSqliteSaver.adelete_thread，已有方法
```

**API**：
- `GET /api/memory/checkpointer` → `{db_size, threads: [...]}`
- `DELETE /api/memory/checkpointer/{thread_id}` → `{deleted: count}`

**安全**：
- `thread_id` 校验（`^[a-zA-Z0-9_-]+$`），防 SQL 注入
- 只读 + 删除，不暴露 checkpoint 内容（含消息历史，可能敏感）

### D7. 长期用户画像：LLM 自动抽取 + 手动编辑

**选择**：后端新增 `memory/profile_store.py`，画像存 `data/config/profile.json`：

```python
interface ProfileEntry {
  key: string;          # 唯一键，如 "prefers_concise_reply"
  category: "preference" | "project" | "fact" | "custom";
  content: string;      # 画像内容
  source: "manual" | "llm_extracted";
  created_at: number;   # ISO timestamp
  updated_at: number;
}
interface ProfileStore {
  entries: ProfileEntry[];
}
```

**API**：
- `GET /api/memory/profile` → `{entries: [...]}`
- `POST /api/memory/profile` body `{key, category, content}` → 新建
- `PUT /api/memory/profile/{key}` body `{content, category?}` → 更新
- `DELETE /api/memory/profile/{key}` → 删除
- `POST /api/memory/profile/extract` body `{thread_id, message, assistant_reply}` → LLM 抽取并写入

**LLM 抽取 prompt**（路径 C 结束时调用）：
```
你是一个用户画像抽取器。分析以下对话，抽取"值得跨会话记住的事实"：
- 用户偏好（如"喜欢简洁回复"、"用 TypeScript"）
- 项目约定（如"项目用 FastAPI"、"测试用 pytest"）
- 重要事实（如"用户是前端工程师"、"工作日 9-18 点在线"）

对话：
用户: {message}
助手: {assistant_reply}

输出 JSON: {"entries": [{"key": "...", "category": "...", "content": "..."}]}
若无可抽取内容，返回 {"entries": []}。不要编造，只抽取明确的事实。
```

**注入 system prompt**：`run_router` 入口读取画像，拼到 system prompt 前：

```python
def _build_profile_prompt() -> str:
    """读取画像，构造 system prompt 前缀。"""
    entries = profile_store.get_all()
    if not entries:
        return ""
    lines = ["用户画像（请遵循以下偏好与约定）:"]
    for e in entries:
        lines.append(f"- [{e.category}] {e.content}")
    return "\n".join(lines) + "\n"
```

**自动抽取控制**：`config.py` 新增 `profile_auto_extract: bool = True`，可在设置页关闭（降级为纯手动）。

**去重策略**：LLM 抽取的条目 `key` 与现有重复时，更新 `content` 与 `updated_at`，不新建。

**备选**：
- 纯手动填写 → 简单但无法积累，每次会话都要手动记
- 向量化存 Milvus → 过度设计，画像条目通常 < 100 条，全量注入 system prompt 足够

### D8. SettingsModal tab 顺序与布局

**选择**：9 tab 顺序（按使用频率与逻辑分组）：
1. 模型与密钥（已有）
2. 系统提示词（已有）
3. **子代理**（新）
4. **工具**（新）
5. 知识库（已有）
6. 审批与安全（已有）
7. 沙箱目录（已有）
8. **记忆**（新）
9. 日志（已有）

**布局**：每个新 tab 复用现有 `px-5 py-4` 内容区，子模块用 `<section>` 分隔 + `<h4>` 标题。「记忆」tab 内部用二级 tab（技能/Checkpointer/画像）避免单页过长。

### D9. preload IPC 扩展

**选择**：`preload/index.ts` `settings` 命名空间下新增：

```typescript
settings: {
  // ...现有
  getSubagentsConfig: () => Promise<SubagentsConfig>;
  setSubagentsConfig: (cfg: SubagentsConfig) => Promise<unknown>;
  getToolsConfig: () => Promise<ToolsConfig>;
  setToolsConfig: (cfg: ToolsConfig) => Promise<unknown>;
}
// 新增 memory 命名空间（走 HTTP，不走 IPC）
memory: {
  listSkills: () => Promise<{ skills: SkillFileInfo[] }>;
  getSkill: (name: string) => Promise<{ content: string }>;
  saveSkill: (name: string, content: string) => Promise<unknown>;
  deleteSkill: (name: string) => Promise<unknown>;
  getCheckpointer: () => Promise<{ db_size: number; threads: ThreadInfo[] }>;
  deleteThread: (thread_id: string) => Promise<{ deleted: number }>;
  getProfile: () => Promise<{ entries: ProfileEntry[] }>;
  saveProfile: (entry: ProfileEntry) => Promise<unknown>;
  deleteProfile: (key: string) => Promise<unknown>;
  extractProfile: (thread_id: string, message: string, reply: string) => Promise<{ extracted: number }>;
}
```

**为什么 memory 走 HTTP 不走 IPC**：memory 操作直接读写后端文件，与已加载的 Python 后端共享文件系统；走 HTTP 与现有 `sandbox` `skills` `workspace` 一致，避免双进程写入竞争。

## Risks / Trade-offs

- **[子代理配置 JSON 序列化失败]** → `electron-store` 存对象，spawn 注入 env 时 `JSON.stringify`；后端 `Settings` 用 pydantic `validator` 解析，失败时回退默认值并记 warning
- **[工具禁用后子代理不可用]** → 前端展示警告「绑定的工具全部被禁用」；后端 `_select_subagent` 检查后静默退回路径 A
- **[LLM 抽取画像质量差]** → prompt 约束「不要编造，只抽取明确的事实」；用户可在设置页手动编辑/删除；提供「关闭自动抽取」开关
- **[画像累积过多撑爆 system prompt]** → 单条 content 限 500 字符；总数超 50 条时设置页提示「建议清理」；system prompt 注入时截断到前 30 条
- **[技能文件名注入攻击]** → 严格正则 `^[a-zA-Z0-9_-]+$` + resolve 后父目录校验
- **[checkpointer 删除误操作]** → 前端删除前弹确认框；删除仅清 checkpoint，不影响 localStorage 会话列表
- **[配置未保存就关闭]** → 子代理/工具 tab 表单 dirty 状态 + 离开提示
- **[DeepAgent 路径 LLM 抽取增加延迟]** → 抽取在 `done` 事件前异步触发，不阻塞 token 流式；失败时仅记日志不报错
- **[画像与 system prompt 冲突]** → 画像注入在 default system prompt 前，用户可在系统提示词 tab 用「{{profile}}」占位符控制注入位置（M1 简化为固定前缀，占位符留 M2）

## Migration Plan

**阶段 1：后端配置与 API**
1. `config.py` 新增 `SubagentSettings` `tools_enabled` `profile_auto_extract` 配置项（独立 commit）
2. `subagents/*.py` `deep_path.py` `graph.py` 改为读 settings（独立 commit）
3. `memory/skills_store.py` `profile_store.py` `checkpointer_view.py` 新建 + 9 个端点（独立 commit）
4. 路径 C 结束时调画像抽取（独立 commit）

**阶段 2：前端 UI**
5. `SubagentsSettings.tsx` `ToolsSettings.tsx` 新建 + SettingsModal 扩 9 tab（独立 commit）
6. `MemorySettings.tsx` 新建（含技能/Checkpointer/画像三子模块）（独立 commit）
7. `preload/index.ts` `main/store.ts` `spawn.ts` 注入新 env（独立 commit）

**阶段 3：测试与验证**
8. 后端单测 5 个文件 + smoke 测试断言（独立 commit）

**回滚策略**：
- 每阶段独立 commit，单阶段失败可 `git revert`
- 配置项默认值与当前硬编码一致，零配置行为不变
- 画像/技能/checkpointer 操作不影响已有功能，回滚后仅丢失管理 UI

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | 子代理 system prompt 是否支持模板变量（如 `{{user_name}}`）？ | M1 纯文本，模板变量留 M2 |
| Q2 | 画像 LLM 抽取是否区分路径（仅路径 C 抽取）？ | 仅路径 C，路径 A/B 不抽取（避免简单对话也调 LLM） |
| Q3 | 工具禁用是否影响 `DANGEROUS_TOOLS` 集合？ | 不影响，`DANGEROUS_TOOLS` 是代码常量，仅控制工具是否暴露 |
| Q4 | 技能文件删除是否影响已注入的 system prompt？ | 删除后 `reload_skills()` 即时生效，下次 `@skill` 调用不再命中 |
| Q5 | checkpointer 列表是否分页？ | M1 全量返回（thread_id 通常 < 100），分页留 M2 |
| Q6 | 画像条目是否支持导出/导入？ | M1 不支持，留 M2 |
