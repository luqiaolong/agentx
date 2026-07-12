# 三层架构与文件地图

> 原 `AGENTS.md` §11 拆分。阅读时机：第一次接触项目结构、查找具体文件位置、新人 Onboarding。

---

```
agentx/
├── backend/app/                ← Python 后端
│   ├── main.py                 ← FastAPI 入口（lifespan + app + 中间件 + register_routes）
│   ├── llm.py                  ← ChatModel 单例
│   ├── api/                    ← REST + SSE 端点（按职责拆分，register_*_routes 注册）
│   │   ├── schemas.py          ← 15 个 Pydantic 请求/响应模型
│   │   ├── health.py           ← / + /api/health
│   │   ├── chat.py             ← /api/chat + approve + abort + compact + _event_generator
│   │   ├── memory.py           ← skills/profile/checkpointer CRUD
│   │   ├── mcp.py              ← MCP servers/tools/test/refresh
│   │   ├── skills.py           ← skills list/reload
│   │   ├── config_reload.py    ← 配置热重载
│   │   ├── models_test.py      ← 模型连通性测试
│   │   └── __init__.py         ← register_routes(app) 聚合
│   ├── sandbox/                ← 沙箱路径授权（与 security/ 平行，独立包）
│   │   ├── path_guard.py       ← 路径归一化 + 关键目录保护（Linux Path('/') bug 已修复）
│   │   ├── store.py            ← SQLite WAL + busy_timeout 持久化
│   │   ├── session_sandbox.py  ← SessionSandbox (async + asyncio.Lock + DB-first + parent_thread_id)
│   │   ├── schemas.py          ← AuthorizeRequest / RevokeRequest
│   │   ├── api.py              ← /api/sandbox/* 路由 + 审计日志
│   │   └── __init__.py         ← 聚合导出
│   ├── security/               ← 安全策略与审批（与 sandbox/ 平行，独立包）
│   │   ├── approval/           ← 审批决策与状态
│   │   │   ├── decision.py     ← ApprovalDecision(str, Enum) + ApprovalResult.approved property
│   │   │   ├── state.py        ← TTL reaper + wait_for_resume/wait_for_abort 原子原语
│   │   │   └── __init__.py     ← 聚合导出
│   │   ├── dangerous_tools.py  ← DANGEROUS_TOOLS + FORBIDDEN_SUBAGENT_TOOLS (frozenset)
│   │   ├── command_filter.py   ← DEFAULT_BLOCKLIST + redact_args (cli_execute 脱敏)
│   │   ├── approval_flow.py    ← run_approval_loop 公共审批循环 (work/coding 统一)
│   │   └── __init__.py         ← 聚合导出
│   ├── config/                 ← pydantic-settings 包（替代单文件 config.py）
│   │   ├── settings.py         ← Settings + get_settings + 路径常量
│   │   ├── subagents.py        ← SubagentSettings + _default_subagents + _parse_custom_subagents
│   │   └── prompts/            ← 内置 system prompt + trigger 描述 + tools 常量
│   │       ├── builtin.py      ← code/rag/web 子代理默认值
│   │       └── team.py         ← 7 个团队专家默认值
│   │                           ⚠️ 与 ``app.workspace.config``（workspace 内的 .agentx/ 目录配置）含义不同，
│   │                              ``app.config`` 管 pydantic Settings + 子代理 + 专家 prompt。两者职责独立。
│   ├── router/                 ← 消息分类 + StateGraph（仅编排，不嵌路径实现）
│   │   ├── classifier.py       ← 规则前置 + LLM 分类
│   │   ├── graph.py            ← Router 图 + run_router（主入口）+ _parse_skill_tag
│   │   └── state.py            ← RouterState TypedDict
│   ├── scenarios/              ← 场景化智能体（work / coding / coding_team）
│   │   ├── __init__.py
│   │   ├── work/               ← work 场景：LLM 直答 + delegate_to_expert/subagent + @mention
│   │   │   ├── __init__.py
│   │   │   ├── agent.py        ← run_work_supervisor（主入口）
│   │   │   └── mention.py      ← @mention 语法解析（仅 work 场景生效）
│   │   ├── coding/             ← coding 场景：DeepAgent + interrupt_before 审批
│   │   │   ├── __init__.py
│   │   │   └── agent.py        ← run_coding_expert（主入口）
│   │   └── coding_team/        ← coding_team 场景：AgentTeam 多代理协作
│   │       ├── __init__.py
│   │       └── agent.py        ← run_coding_team → run_team_path
│   ├── deepagent/              ← DeepAgent 框架（供 scenarios/coding 复用）
│   │   ├── __init__.py
│   │   ├── agent.py            ← create_deep_agent / build_deep_agent
│   │   ├── factory.py          ← create_agent 工厂（统一构造 ReAct agent）
│   │   ├── approval_runner.py  ← run_agent_with_approval 公共审批循环（所有 ReAct 路径复用）
│   │   ├── streaming.py        ← _stream_agent_events（custom/values/messages 三模）
│   │   ├── tool_assembly.py    ← 工具组装
│   │   ├── authorized_backend.py ← AuthorizedLocalShellBackend（继承父线程授权）
│   │   ├── safe_shell_backend.py ← 安全 shell 后端
│   │   └── context.py          ← current_parent_thread_id ContextVar
│   ├── team/                   ← AgentTeam 多代理协作（由 scenarios/coding_team 调用）
│   │   ├── __init__.py
│   │   ├── orchestrator.py     ← run_team_path（主入口）+ StateGraph DAG（plan → fan-out → aggregate → END）
│   │   ├── planner.py          ← _build_orchestrator_prompt + _parse_todos_from_text + _build_project_context
│   │   ├── scheduler.py        ← _run_team_role_subtask + astream_events 驱动
│   │   ├── blackboard.py       ← TeamState + Blackboard + TeamPlanTask + TeamSubtaskResult
│   │   └── aggregator.py       ← _run_aggregator + _quality_gate + _build_summary
│   ├── cli/                    ← CLI 终端交互（REPL + One-shot + config 子命令）
│   │   ├── __init__.py        ← 包导出 main
│   │   ├── app.py             ← main() + argparse + 模式分发 + config 子命令
│   │   ├── repl.py            ← run_repl + consume_events
│   │   ├── one_shot.py        ← run_one_shot
│   │   ├── approval.py        ← handle_approval 终端审批交互
│   │   ├── commands.py        ← CommandResult + handle_command + _cmd_*（全部 await）
│   │   ├── renderer.py        ← EventRenderer SSE 事件终端渲染
│   │   └── store.py           ← Tauri store 配置读取 + 凭证解密（DPAPI/AES-GCM）
│   ├── subagents/              ← rag / web / 自定义子代理（code 已由 coding Expert 取代）
│   │   ├── base.py             ← make_fs_tools / make_rag_tools / make_web_tools + extract_text
│   │   ├── rag_agent.py        ← rag 子代理（ReAct）
│   │   ├── web_agent.py        ← web 子代理（ReAct）
│   │   └── custom_agent.py     ← 自定义子代理工厂（build_custom_agent）
│   ├── tools/                  ← filesystem + rag_retrieve
│   ├── memory/                 ← skills / profile / checkpointer
│   │   ├── profile_extractor.py ← LLM 画像抽取（extract_profile_via_llm）
│   │   ├── profile_store.py    ← 画像存储（upsert_from_llm / build_profile_prompt）
│   │   ├── skills_loader.py    ← 技能加载
│   │   ├── skills_store.py     ← 技能存储
│   │   ├── checkpointer.py     ← LangGraph checkpointer
│   │   └── context.py          ← 消息截断（trim_messages_with_budget）
│   ├── workspace/              ← workspace 业务包（与 deep/team/subagents 平行；config 子包 + api 路由）
│   │   ├── __init__.py         ← register_routes 聚合
│   │   ├── api.py              ← /api/workspace/* + /api/project-config/* 路由（合并自原 api/workspace.py + api/project_config.py）
│   │   └── config/             ← .agentx/ 项目级配置（generator/loader/merger/templates）
│   │       ├── __init__.py     ← 包导出
│   │       ├── templates.py    ← 6 个文件模板（AGENTS.md/mcp.json/subagents.json/tools.json/system_prompt.md/rules/README.md）
│   │       ├── generator.py    ← generate_agentx_dir 幂等生成
│   │       ├── loader.py       ← load_project_config 容错加载
│   │       └── merger.py       ← merge_configs 合并到 Settings 之上
│   ├── vectorstore/            ← Milvus 客户端
│   ├── embedding/              ← TEI 客户端
│   ├── mcp/                    ← MCP 客户端 + 配置
│   ├── observability/          ← LangSmith SDK + ObservationStore + logger
│   │   ├── observation.py      ← SqliteObservationSink（4 表 + WAL）+ ObservationCallback（FR-1/2）
│   │   ├── langsmith.py        ← LangSmith SDK trace_span + redact + dual_trace contextmanager（本地+remote 双写+降级，FR-3/3.3）
│   │   ├── trace.py            ← bind_trace ContextVar（trace_id 透传 0-intrusion）
│   │   └── feedback.py         ← 隐式信号 record_implicit_ok/bad（FR-9)
│   ├── sse/                    ← SSE 事件协议层（make_sse_event 等 7 个工厂函数）
│   └── utils/                  ← text(ThinkFilter) + chunks + prompts
├── frontend/
│   ├── renderer/               ← React UI（chat/settings/workspace 组件）
│   │   ├── lib/
│   │   │   ├── api/            ← Tauri invoke + fetch 模块 + request.ts (apiGet/apiPost/apiPut/apiDelete)
│   │   │   ├── schemas/        ← zod schemas (approval/mcp-server/model-entry/subagent/system-prompt/tools/sandbox/milvus)
│   │   │   ├── format.ts       ← formatTime/formatDate/formatSize
│   │   │   ├── validators.ts   ← KEY_RE/NAME_RE/validateKey/validateName
│   │   │   ├── logger.ts       ← logger.warn/error
│   │   │   ├── errors.ts       ← ApiError/humanizeError
│   │   │   └── subagentConstants.ts ← ALL_TOOLS/emptyToolsConfig
│   │   ├── hooks/
│   │   │   ├── useChatStream.ts  ← SSE 流解析（直连 fetch 8123）
│   │   │   ├── useCrudList.ts    ← 通用 CRUD 列表 hook
│   │   │   ├── useConfigSave.ts  ← 配置保存 hook
│   │   │   ├── usePopover.ts     ← Popover 状态 + 外部点击
│   │   │   └── useModalDialog.ts ← Modal a11y (ESC/焦点/Tab 陷阱)
│   │   ├── components/
│   │   │   ├── ui/             ← 通用 UI（无业务语义）
│   │   │   ├── settings/       ← 设置面板（按子域拆分子目录 model-provider/subagents/mcp/memory）
│   │   │   └── ...             ← 其他顶层目录命名见 §9.5 视觉区域术语表（titlebar/sidebar/main/workspace/overlay/log）
│   │   └── stores/
│   │       ├── chat/           ← chat store 拆分 (index/migrations/quotaStorage/messageOps)
│   │       └── ...             ← zustand 状态 (agentMode/scene/skills/settings)
│   └── shared/api-types.ts     ← renderer/shared 共享类型
├── src-tauri/                  ← Rust 主进程（替代 Electron main + preload）
│   ├── src/
│   │   ├── lib.rs              ← run() 入口 + setup() 启动 Python + 注册命令
│   │   ├── backend/            ← PythonHandle（spawn + 健康握手 + 崩溃退避）+ env 注入
│   │   ├── commands/           ← settings / dialog / shell / window / clipboard / notify / logs / app / git
│   │   ├── store/              ← tauri-plugin-store 封装（enc:/plain: 凭证）+ 自定义子代理存储
│   │   ├── migration/          ← electron-store → tauri-plugin-store 数据迁移
│   │   ├── git/                ← git2 crate 封装
│   │   └── logger/             ← log 落盘
│   ├── Cargo.toml
│   └── tauri.conf.json         ← 窗口/打包/updater 骨架
├── tests/python/{unit,integration}/  ← pytest（asyncio_mode=auto）
├── tests/renderer/             ← vitest
├── scripts/smoke-tauri.ps1     ← Tauri 迁移冒烟脚本
├── openspec/changes/           ← OpenSpec 提案（archive/ 历史）
├── docs/superpowers/specs/     ← 项目设计文档
├── docs/agents/                ← AGENTS.md 拆分出的离线查阅文档（架构 / 契约 / SOP）
├── .env.example                ← 配置项文档（后端 MUST NOT 读取，仅文档）
└── AGENTS.md                   ← 全 AI 代理通用规范
```

> **关键重构**：`backend/app/paths/` 包已删除（见
> [openspec/2026-07-06-paths-refactor](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-06-paths-refactor/proposal.md)），
> 各路径按能力域拆分为 `scenarios/` / `deepagent/` / `team/` / `subagents/`。**禁止**重新创建 `backend/app/paths/` 目录。
> 场景化迁移（见 [openspec/2026-07-09-backend-package-naming-refactor](file:///d:/java/agentprojects/agentx/openspec/changes/archive/2026-07-09-backend-package-naming-refactor/proposal.md)）：
> `agents/supervisor/` → `scenarios/work/`、`agents/expert/` → `scenarios/coding/`、`agents/team/` → `scenarios/coding_team/`、`deep/` → `deepagent/`。