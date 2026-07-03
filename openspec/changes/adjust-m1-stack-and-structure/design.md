## Context

设计草案 v2 假设"全本地"基础设施 —— sentence-transformers + BAAI/bge-m3 + Chroma。review 中暴露三个问题：

1. **首次启动需下载 ~2GB 模型**，且本地资源与 myserver 已部署的 TEI/Milvus 重复
2. **沙箱白名单与示例矛盾**：第 12 节限制 DeepAgent 仅可读写 `data/workspace/`、`data/uploads/`，但第 6 节示例 `分析 d:/docs 这周PDF` 的目标路径不在白名单
3. **目录命名歧义**：`src/` 在 Electron 生态中通常指渲染层，但本项目 `src/` 实际包含 main+preload+renderer 全部前端代码；与 `app/` 后端并列时易误读

myserver (192.168.1.4) 已部署：
- HuggingFace TEI 服务，端口 **8080**，提供 bge-m3 嵌入接口 `/embed`，无鉴权
- Milvus 2.x 服务，端口 **19530**，username/password 鉴权，配套 Attu 管理 9091

myserver 端口占用现状（来自 agentflow-ops 服务清单）：8085/8098/8100/8101/8102/8103/8104/8105/8106/8107/8108/8110/8111。**TEI 8080 与上述端口无冲突**。

约束：
- M1 阶段优先开发效率，避免本机装 2GB 模型
- LLM API Key 存储方式不变（Electron `safeStorage` + `electron-store`）
- 必须保留 LangSmith 全链路追踪能力
- 设计文档 v2 → v3 一次性收敛 review 中所有阻断性问题

## Goals / Non-Goals

**Goals:**
- 嵌入与向量检索全部走 myserver，应用启动零模型下载
- 目录结构 `frontend/ + backend/` 双根，语义清晰，与构建/打包工具链对齐
- DeepAgent 沙箱支持"会话级用户授权目录"，使 `分析 d:/docs 这周PDF` 类用例可直接执行
- 修复 review 中至少 4 条阻断/严重问题：沙箱矛盾、Zustand 版本不一致、interrupt_on 暂停时长、冒烟测试路径
- M2 PyInstaller 打包体积可控（目标 < 1GB）

**Non-Goals:**
- 不重写设计文档 v2 的整体架构（仍保持 Electron + React + FastAPI + LangGraph + DeepAgent）
- 不引入 myserver 上的 LLM 网关（LLM 仍走 OpenAI/Anthropic/DashScope 直连）
- 不实现 myserver 离线时的本地嵌入降级（M1 阶段 myserver 默认可用，离线策略仅文档化）
- 不引入多用户/多租户/云同步（沿用 v2 非目标）
- 不替换 Checkpoint 方案（仍用 langgraph-checkpoint-sqlite，本地 SQLite）

## Decisions

### D1. 嵌入服务：TEI HTTP 客户端

**选择**：封装 `backend/app/embedding/tei_client.py`，使用 `httpx.AsyncClient` 调用 `POST http://192.168.1.4:8080/embed`。

**TEI `/embed` 接口契约**（HuggingFace TEI 官方约定）：
- 请求体：`{"inputs": "<text>"}`（单文本）或 `{"inputs": ["<text1>", "<text2>", ...]}`（批量）
- **`model` 字段不放请求体**：TEI 单服务部署单模型，请求体只接受 `inputs`
- 响应体始终为 `[[float, ...], ...]`（list of list），即使单文本也是 `[[float, ...]]`
- 客户端封装约定：
  - `embed_text(text) -> list[float]`：内部 `response = await client.post(...); return response.json()[0]`（解包外层 list）
  - `embed_texts(texts) -> list[list[float]]`：返回 list of vector，不做额外解包
- 模型名 `bge-m3` 仅作为 LangSmith metadata 标记，不放入请求体

**bge-m3 token 上限**：模型最大支持 **8192 tokens**（约 6000-8000 中文字符）。文本超过 8192 tokens 时 TEI 会截断或报错。客户端在调用前 MUST 做粗略字符数预检（>24000 字符直接拒绝并返回 `TextTooLongError`），上层分块器（`backend/app/utils/chunks.py`）保证 chunk < 8192 tokens。

**备选**：
- `langchain.embeddings.HuggingFaceInferenceEmbeddings` —— 抽象层薄但绑定 LangChain 命名空间，且其默认 endpoint 是 HF Hub 而非自部署 TEI，需 patch URL
- 直接用 sentence-transformers 连接 myserver 的 TEI 模型权重 —— 不适用，TEI 是独立服务不是模型文件

**为什么自封装**：
- TEI `/embed` 接口稳定且简单（单字段 inputs），自封装 50 行可控
- 避免 LangChain Embeddings 抽象层在 LangGraph 0.5 + LangChain 0.3 下的兼容性踩坑
- 显式暴露超时/重试/降级参数，便于 LangSmith 追踪

**配置**：
- `AGENT_PY_EMBEDDING_URL`（默认 `http://192.168.1.4:8080/embed`）
- `AGENT_PY_EMBEDDING_MODEL`（默认 `bge-m3`，仅用于 LangSmith metadata 标记，不放请求体）
- `AGENT_PY_EMBEDDING_TIMEOUT`（默认 10s）
- `AGENT_PY_EMBEDDING_MAX_BATCH`（默认 32，TEI 单次最大批次）
- `AGENT_PY_EMBEDDING_MAX_CHARS`（默认 24000，超过则拒绝并提示分块器切短）
- tenacity 重试 3 次（指数退避 0.5/1/2s），最终失败抛 `EmbeddingUnavailable` → DeepAgent 写入 state 错误回显

### D2. 向量库：Milvus + 经典 Collection API

**选择**：`backend/app/vectorstore/milvus_client.py`，使用 **pymilvus 经典 API**（`connections.connect` + `Collection` + `Partition` + `utility`），而非 `AsyncMilvusClient` Lite 模式。

**为什么不用 AsyncMilvusClient**：
- `AsyncMilvusClient`（pymilvus 2.4+）是 Lite 模式新 API，`create_collection` 用 schema-based 快速建表，但 **partition 操作能力受限**（无法显式创建 Partition + 按 partition 路由插入）
- 经典 `Collection` API 完整支持 schema 定义、Partition 创建、HNSW 索引构建、按 partition_name 插入/检索
- 与 FastAPI async 对齐通过 `asyncio.to_thread()` 包装同步调用即可，无需追求原生 async API
- pymilvus 官方文档与社区案例以经典 API 为主，遇到问题易查

**Schema**：
```
Collection: agent_py_knowledge
  - id (INT64, primary, auto_id)
  - text (VARCHAR, max 65535)
  - source (VARCHAR, max 512)         # 文件路径或 URL
  - source_type (VARCHAR, max 32)     # file | web | manual
  - chunk_idx (INT32)                 # 文件内分块序号
  - created_at (INT64)                # Unix timestamp
  - vector (FLOAT_VECTOR, dim=1024)   # bge-m3 输出维度
Index: HNSW (M=16, efConstruction=200), metric=COSINE
Partition: by source_type（file/web/manual 各一分区）
```

**partition 与 filter 的职责划分**（避免双重过滤冗余）：
- **partition 用于批量管理**：`delete_by_source_type("web")` 直接 `drop_partition` 一次性清空某类型全部数据，比 `delete(filter=...)` 高效
- **filter 用于查询时过滤**：`search(filter='source_type == "file"')` 在 HNSW 检索时按表达式过滤
- 插入时 `insert(data, partition_name="file")` 路由到对应 partition
- 查询时 **不**同时指定 partition_name + filter，只用 filter（避免实现复杂度，partition 仅作为删除效率优化）

**DB 预创建责任**：
- **DB `agent_py` 必须手动预创建**（Milvus 不支持应用层自动建 DB）
- collection `agent_py_knowledge` 与 partition 由应用启动时自动创建（spec 要求）
- 应用启动时若 DB 不存在 → `/api/health` 中 `milvus` 子项标记 `unhealthy: db not found`，并在日志中提示用户通过 Attu 或 `pymilvus` 手动 `create_database`

**备选**：
- 保留 Chroma —— 用户已明确要求改用 Milvus
- Qdrant —— myserver 未部署
- Postgres + pgvector —— 增加 Postgres 依赖，违背"零外部 DB"原则（除 Milvus 外）

**配置**：
- `AGENT_PY_MILVUS_HOST`（默认 `192.168.1.4`）
- `AGENT_PY_MILVUS_PORT`（默认 `19530`）
- `AGENT_PY_MILVUS_USER` / `AGENT_PY_MILVUS_PASSWORD`（必填，从 `electron-store` 经 safeStorage 解密后通过环境变量注入 Python 子进程）
- `AGENT_PY_MILVUS_DB`（默认 `agent_py`，需在 Milvus 中预创建）
- `AGENT_PY_MILVUS_COLLECTION`（默认 `agent_py_knowledge`）
- 连接：经典 API 单例连接，FastAPI lifespan 管理生命周期（startup connect / shutdown disconnect）

### D3. 目录结构：`frontend/ + backend/` 双根

**选择**：
```
agent-py/
├── frontend/                # 原 src/
│   ├── main/
│   ├── preload/
│   ├── renderer/
│   └── ...
├── backend/                 # 原 app/
│   └── app/                 # Python 包（保持 app 命名空间，避免 import 冲突）
│       ├── embedding/
│       ├── vectorstore/
│       ├── tools/
│       └── ...
├── data/
├── tests/
├── docs/
├── package.json
├── pyproject.toml           # 单一 pyproject，位于根目录（Q4 默认假设）
├── electron.vite.config.ts
└── ...
```

**关键决策**：
- **`backend/` 内仍保留 `app/` 子目录作为 Python 包根**，避免 `import backend.app.xxx` 这种深路径；启动命令 `cd backend && uv run python -m app.main` 保持简洁
- **单一 `pyproject.toml` 位于项目根目录**（不新建 `backend/pyproject.toml`，与 Q4 默认假设一致；M3+ 评估 monorepo 拆分）
- `frontend/` 直接对应 `electron-vite` 的 root 配置，`electron.vite.config.ts` 中 `main` / `preload` / `renderer` 三入口路径前缀从 `src/` 改为 `frontend/`
- `.gitignore` 同步：`/data/` 不变，`/src/` 改为 `/frontend/dist/`，`/app/` 改为 `/backend/__pycache__/` 等
- PyInstaller spec 文件 `backend/agent-py.spec`，`pathex` 指向 `backend/`，打包产物落 `backend/dist/`

**备选**：
- 单根 `src/` 下分 `frontend/` + `backend/` —— 增加路径深度，且 Electron/Vite 默认 root 与 Python 包根期望不同层级
- monorepo（pnpm workspace + uv workspace）—— M1 过度工程化

### D4. 沙箱授权目录机制

**选择**：在 `backend/app/utils/security.py` 中维护 `SessionSandbox` 类，白名单 = `data/workspace` + `data/uploads` + `session_authorized_dirs: set[Path]`。

**授权生命周期**：
1. 用户在 Renderer 通过 `dialog.openFolder()` 选择目录
2. 路径通过 IPC → Main → POST `/api/sandbox/authorize` 传给 Python
3. Python 把路径加入当前 thread_id 的 `SessionSandbox.authorized_dirs`
4. DeepAgent 调用 `filesystem.list_dir` / `read_file` 时，`SessionSandbox.check_read(path)` 校验：路径在白名单 → 通过；否则抛 `PathNotAuthorized`
5. 写操作仍限 `data/workspace/` + `data/uploads/`（授权目录默认只读，写操作需用户在 dialog 中勾选"允许写入"）
6. 会话结束（用户关闭会话或 `/reset`）→ `SessionSandbox.clear(thread_id)`，授权目录自动撤销

**与 LangGraph checkpoint 关系**：授权目录列表写入 `RouterState.authorized_dirs: list[str]`，checkpoint 后可恢复；用户重开会话时授权仍生效，可通过 `/revoke <path>` 主动撤销。

**备选**：
- 全局白名单（不区分会话）—— 安全性差，授权后无法回收
- 每次 tool call 弹窗确认 —— UX 灾难
- OS 级 ACL —— 过度工程化，跨平台兼容差

### D5. review 阻断性问题修复

| 问题 | 修复 |
|---|---|
| Zustand 版本 v4 vs v5 不一致 | 统一 v5（`^5.0.0`），设计文档 §4.1 改为"5+"；tasks 增加一项"迁移到 v5 breaking change"（middleware 签名） |
| interrupt_on 暂停 5s 过短 | 改为"无限期暂停直至用户操作"，提供可选 `auto_approve_after_seconds` 配置（默认 0=禁用），设计文档 §12 同步 |
| 路径 A < 1s 不现实 | 改为 TTFT < 1s / 总响应 < 2s，设计文档 §3.2 同步 |
| 冒烟测试路径 `tests/python/integration/test_smoke.py` 在目录结构中无对应 | 在 `tests/python/` 下创建 `integration/` 子目录，目录结构图同步补齐 |

### D6. 配置注入流程

LLM API Key 与 Milvus 凭证均存于 `electron-store`（safeStorage 加密）。启动流程：

1. Electron Main 进程启动 → 读 `electron-store` → safeStorage 解密
2. Main 进程 spawn Python 子进程时，通过 `env` 注入：
   - `AGENT_PY_OPENAI_API_KEY` / `AGENT_PY_ANTHROPIC_API_KEY` / ...
   - `AGENT_PY_MILVUS_USER` / `AGENT_PY_MILVUS_PASSWORD`
   - `AGENT_PY_EMBEDDING_URL`
   - `LANGSMITH_API_KEY`
3. Python `backend/app/config.py` 用 `pydantic-settings` 的 `SettingsConfigDef(env_prefix="AGENT_PY_")` 加载
4. **不写 .env 文件**，避免凭证落盘

**为什么不用 .env**：safeStorage 加密 + 进程 env 注入是 Electron 生态最佳实践；.env 文件易被误提交 git 或被备份软件读取。

**`.env.example` 的定位**：项目根目录可保留 `.env.example` 文件作为**配置项文档**（列出所有 `AGENT_PY_*` 环境变量及默认值，凭证字段以 `<from electron-store>` 标注），但应用运行时 MUST NOT 读取此文件（pydantic-settings 不配置 `.env` 加载）。该文件仅作开发参考，可入仓。

**LangSmith 配置**：`LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_TRACING` 通过 env 注入 Python 子进程，与 LLM API Key 同流程。本次 change 不变更 LangSmith 配置方式，沿用 v2 设计。

## Risks / Trade-offs

- **[myserver 不可用 → 应用无法启动]** → M1 阶段文档化为"myserver 是硬依赖"；`/api/health` 启动时 ping TEI + Milvus，失败时前端显示"基础设施不可用"指引；M3+ 评估本地降级
- **[TEI `/embed` 接口字段名变更]** → tenacity 重试 + 接口契约测试 `tests/python/integration/test_tei_contract.py`，CI 跑契约测试
- **[Milvus 凭证泄漏到 LangSmith]** → `LangSmithTracer` 在 trace 前对 `extra.metadata` 做 redaction，过滤 `*_PASSWORD` / `*_KEY` 字段
- **[bge-m3 维度 1024 与未来模型不兼容]** → `vector_store` 模块暴露 `dim` 参数，未来切模型时新建 collection，旧 collection 保留 30 天后归档
- **[会话级授权目录误删用户文件]** → 写操作仍限 `data/workspace` + `data/uploads`，授权目录默认只读；写操作前 `interrupt_on` 暂停等批准
- **[目录重构引发大量 import 路径变更]** → 全量 grep + 一次 commit，CI 跑全量测试兜底；提供 `git mv src frontend && git mv app backend` 单步重构脚本
- **[Zustand v5 breaking change]** → `middleware` / `devtools` 签名变更，需迁移 3 处 store（chat / tasks / settings），tasks 中明确列出
- **[PyInstaller 不识别 `backend/app/` 包根]** → spec 文件 `pathex=['backend']` + `hiddenimports=['app.embedding.tei_client', 'app.vectorstore.milvus_client']`，M2 阶段验证

## Migration Plan

**阶段 1：目录重构（独立 commit）**
1. `git mv src frontend && git mv app backend`
2. 改 `electron.vite.config.ts` / `tsconfig.json` / `package.json` scripts / `.gitignore` / 启动脚本
3. 全量 grep 残留 `src/` `app/` 路径引用，逐一替换
4. `npm run dev` + `cd backend && uv run python -m app.main` 双端启动验证

**阶段 2：嵌入服务迁移**
1. 新建 `backend/app/embedding/tei_client.py` + 单元测试
2. `backend/app/config.py` 增加 `embedding_*` 配置项
3. 删除 `sentence-transformers` 依赖，`pyproject.toml` 同步
4. RAG subagent / 检索工具切到 TEI 客户端
5. 集成测试 `test_tei_contract.py` 验证 myserver 8080 连通
6. 删除本地 bge-m3 模型缓存（`~/.cache/huggingface/`）确认零下载

**阶段 3：向量库迁移**
1. 在 myserver Milvus 中预创建 DB `agent_py`（手动，通过 Attu 或 pymilvus `create_database`）
2. 新建 `backend/app/vectorstore/milvus_client.py` + collection 初始化脚本
3. `backend/app/tools/rag_retrieve.py` 切到 Milvus 客户端
4. 删除 `chromadb` 依赖
5. 删除本地 `data/chroma/` 目录（先确认无重要数据，必要时导出 JSON 备份）
6. 集成测试 `test_milvus_contract.py` 验证连接 + 插入 + 检索

**阶段 4：沙箱授权机制**
1. 新建 `backend/app/utils/security.py` 中 `SessionSandbox` 类
2. 新增 `/api/sandbox/authorize` / `/api/sandbox/revoke` 端点
3. filesystem 工具的 read/write 路径校验接入 `SessionSandbox`
4. `RouterState` 增加 `authorized_dirs` 字段
5. Renderer 增加"授权目录管理"UI（Settings 页 + Chat 输入框 `@授权目录` 快捷操作）

**阶段 5：review 阻断性问题修复（文档与依赖一致性）**
1. 设计文档 v3 §4.1 / §12 / §3.2 / §5 同步更新（与 tasks 第 2 节对齐）
2. `tests/python/integration/` 目录创建 + 冒烟测试补齐

**阶段 6：Zustand v5 迁移与 interrupt_on 调整**
1. Zustand v5 迁移（chat / tasks / settings 三个 store）
2. interrupt_on 暂停时长改无限期 + auto_approve 配置
3. 审批 UI（ApprovalDialog）与 IPC 桥接入

**回滚策略**：
- 每阶段独立 commit，单阶段失败可 `git revert`
- 目录重构阶段若发现遗漏路径，补丁 commit 而非回滚（重构本身无功能影响）
- Milvus 数据迁移不可逆（Chroma 数据无法自动迁回），但 M1 阶段 Chroma 中无生产数据，回滚 = 重新入库

## Open Questions

| # | 问题 | 默认假设 |
|---|---|---|
| Q1 | Milvus 凭证如何首次注入到 electron-store？ | 首次启动 Settings 页强制用户输入，存 safeStorage；不输入则 RAG 功能禁用但聊天可用 |
| Q2 | Milvus collection 的 `partition` by `source_type` 是否会因单分区数据量过大性能下降？ | M1 数据量 < 10万 chunk，单分区足够；M3+ 评估按 source 哈希分片 |
| Q3 | TEI 8080 是否在 myserver 防火墙放行？ | 默认假设放行；若未放行，需在 myserver `firewall-cmd` 增加 8080/tcp |
| Q4 | `backend/` 是否需要独立 `pyproject.toml`（与根 `pyproject.toml` 并存）？ | M1 单 pyproject 在根目录，`backend/` 仅作为代码目录；M3+ 评估 monorepo 拆分 |
| Q5 | 用户授权目录是否跨会话保留？ | 默认保留（写入 checkpoint），用户可手动 `/revoke <path>` 撤销；提供"退出时清空所有授权"开关 |
