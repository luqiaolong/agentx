## ADDED Requirements

### Requirement: Milvus 向量库客户端

系统 SHALL 通过 pymilvus 经典 API（`connections.connect` + `Collection` + `Partition` + `utility`）连接 myserver Milvus 服务（默认 `192.168.1.4:19530`），使用 username/password 鉴权。系统 MUST NOT 在本地运行 Chroma 或嵌入 Chroma 客户端。系统 MUST NOT 使用 `AsyncMilvusClient` Lite 模式 API（其 partition 操作能力受限）。

#### Scenario: 首次启动自动创建 collection
- **WHEN** FastAPI 启动且 Milvus 中不存在 `agent_py_knowledge` collection
- **THEN** 系统通过 `Collection(schema=...)` 自动创建 collection，schema 包含 `id` (INT64 auto_id) / `text` (VARCHAR 65535) / `source` (VARCHAR 512) / `source_type` (VARCHAR 32) / `chunk_idx` (INT32) / `created_at` (INT64) / `vector` (FLOAT_VECTOR dim=1024)，并在 `vector` 字段上创建 HNSW 索引（M=16, efConstruction=200, metric=COSINE）

#### Scenario: 按 source_type 创建分区
- **WHEN** collection 创建完成
- **THEN** 系统通过 `Partition(collection, "file")` / `Partition(collection, "web")` / `Partition(collection, "manual")` 创建 3 个 partition，后续插入按 `source_type` 路由到对应 partition

#### Scenario: partition 用于批量删除而非查询路由
- **WHEN** 调用 `delete_by_source_type("web")`
- **THEN** 系统通过 `drop_partition("web")` 一次性清空 web 类型全部数据（比 `delete(filter=...)` 高效），随后重建空 partition 保持 schema 完整
- **AND** 查询时 MUST NOT 同时指定 `partition_name` + `filter`，只用 `filter='source_type == "X"'` 表达式过滤（partition 仅作为删除效率优化，查询统一走 filter）

### Requirement: Milvus DB 预创建责任

DB `agent_py` MUST 由用户手动在 myserver Milvus 中预创建（通过 Attu UI 或 `pymilvus.db.create_database`），应用层 MUST NOT 自动创建 DB。collection 与 partition 由应用启动时自动创建。应用启动时若 DB 不存在 SHALL 在 `/api/health` 中显式提示。

#### Scenario: DB 不存在时显式提示
- **WHEN** FastAPI 启动且 Milvus 中不存在 `agent_py` DB
- **THEN** `/api/health` 中 `milvus` 子项返回 `{"status": "unhealthy", "error": "db 'agent_py' not found, please create via Attu or pymilvus create_database"}`，FastAPI 仍启动但 RAG 工具调用时返回显式错误

#### Scenario: DB 存在但 collection 不存在时自动创建
- **WHEN** FastAPI 启动、DB `agent_py` 存在、collection `agent_py_knowledge` 不存在
- **THEN** 系统自动创建 collection + 3 partition + HNSW 索引，`/api/health` 中 `milvus` 子项标记 `healthy`

### Requirement: 凭证错误统一处理

系统 SHALL 通过环境变量 `AGENT_PY_MILVUS_HOST` / `AGENT_PY_MILVUS_PORT` / `AGENT_PY_MILVUS_USER` / `AGENT_PY_MILVUS_PASSWORD` / `AGENT_PY_MILVUS_DB` 加载连接配置，MUST NOT 从 `.env` 文件读取凭证。凭证问题（缺失或错误）SHALL 通过统一 schema 返回，区分 `no_credentials` 与 `auth_failed` 两种子状态。

#### Scenario: Electron Main 进程注入凭证
- **WHEN** Electron Main 启动 Python 子进程
- **THEN** Main 进程从 `electron-store`（safeStorage 解密）读取 Milvus 凭证，通过 `subprocess.Popen(env=...)` 注入到 Python 子进程环境变量

#### Scenario: 凭证缺失时降级
- **WHEN** `AGENT_PY_MILVUS_USER` 或 `AGENT_PY_MILVUS_PASSWORD` 未设置
- **THEN** 启动日志记录 `Milvus credentials missing, RAG features disabled`，FastAPI 仍启动，`/api/health` 中 `milvus` 子项返回 `{"status": "unhealthy", "error_code": "no_credentials", "error": "AGENT_PY_MILVUS_USER/PASSWORD not set"}`

#### Scenario: 凭证错误时显式报错
- **WHEN** `AGENT_PY_MILVUS_USER` / `AGENT_PY_MILVUS_PASSWORD` 设置但鉴权失败
- **THEN** `/api/health` 中 `milvus` 子项返回 `{"status": "unhealthy", "error_code": "auth_failed", "error": "<pymilvus 原始错误信息>"}`，FastAPI 仍启动但 RAG 工具调用时返回显式错误

### Requirement: 知识入库

系统 SHALL 提供 `ingest(texts: list[str], metadatas: list[dict], source_type: str)` 接口，将文本与其嵌入向量批量插入 Milvus，按 `source_type` 路由到对应 partition。

#### Scenario: 批量入库文件分块
- **WHEN** 用户拖入 `report.pdf`（10MB，分块为 50 个 chunk），调用 `ingest(texts=chunks, metadatas=[{source, chunk_idx}...], source_type="file")`
- **THEN** 系统先调用 TEI 批量嵌入（遵循 `max_batch=32` 与 `max_chars=24000` 预检），再调用 `Collection.insert(data, partition_name="file")` 一次性写入 50 条记录到 `file` 分区，返回插入后的 id 列表

#### Scenario: 入库前文本长度校验
- **WHEN** 单个 chunk 文本长度超过 `AGENT_PY_EMBEDDING_MAX_CHARS`（默认 24000）
- **THEN** 系统抛 `TextTooLongError`，跳过该 chunk 并在日志中记录 source + chunk_idx，其余 chunk 正常入库

#### Scenario: 按 source 删除
- **WHEN** 调用 `delete_by_source(source="/data/uploads/old.pdf")`
- **THEN** 系统通过 `Collection.delete(filter='source == "/data/uploads/old.pdf"')` 删除所有匹配记录，返回删除条数

### Requirement: 向量检索

系统 SHALL 提供 `search(query: str, top_k: int = 5, filter: str | None = None)` 接口，先嵌入 query 再在 Milvus 中检索 top_k 相似记录。查询时 MUST 通过 `filter` 表达式过滤，MUST NOT 同时指定 `partition_name` + `filter`。

#### Scenario: 基础检索
- **WHEN** 调用 `search("RAG 是什么", top_k=5)`
- **THEN** 系统调用 TEI 嵌入 query，再在 Milvus 中执行 `Collection.search(data=[vec], anns_field="vector", param={"params": {"ef": 64}}, limit=5)` HNSW 检索，返回 5 条 `(text, source, score)` 元组，按 score 降序

#### Scenario: 按 source_type 过滤
- **WHEN** 调用 `search("X", top_k=5, filter='source_type == "file"')`
- **THEN** 系统在 `search()` 调用中传 `expr='source_type == "file"'`，仅返回 file 类型数据（不指定 partition_name，partition 仅用于删除）

#### Scenario: 检索结果 LangSmith 追踪与 redaction
- **WHEN** 任一检索调用完成
- **THEN** LangSmith 中可见 `vectorstore.milvus.search` span，metadata 含 `top_k` / `filter` / `latency_ms` / `result_count`，但不包含检索到的原始文本（避免泄漏）；metadata 中匹配 `*_PASSWORD` / `*_KEY` / `*_SECRET` 的字段在发送前被 redaction 替换为 `<redacted>`

### Requirement: Milvus 健康检查

系统 SHALL 在 `/api/health` 中暴露 `milvus` 子项，执行轻量级连通性检查。

#### Scenario: Milvus 可用
- **WHEN** `/api/health` 调用且 Milvus 鉴权通过且 DB 存在
- **THEN** `milvus` 子项返回 `{"status": "healthy", "latency_ms": <int>, "collection": "agent_py_knowledge"}`

#### Scenario: Milvus 不可用
- **WHEN** Milvus 不可达或鉴权失败或 DB 不存在
- **THEN** `milvus` 子项返回 `{"status": "unhealthy", "error_code": "<no_credentials|auth_failed|db_not_found|unreachable>", "error": "<message>"}`，但 `/api/health` 整体仍返回 200（其他子项可能 healthy）

### Requirement: Milvus 离线时降级行为

系统 MUST NOT 在 Milvus 不可用时静默失败。RAG 入库与检索工具 SHALL 在 Milvus 不可用时返回显式错误信息，非 RAG 能力（聊天 / 文件操作 / 联网检索）MUST 保持可用。

#### Scenario: Milvus 离线时 RAG 检索显式失败
- **WHEN** 用户输入"基于知识库回答 X"且 Milvus 不可达
- **THEN** `rag_retrieve` 工具返回 `"向量库不可用，无法检索知识库"`，DeepAgent 在 state 中记录错误并回显到 Chat

#### Scenario: Milvus 离线时入库显式失败
- **WHEN** 用户拖入文件触发 RAG 入库且 Milvus 不可达
- **THEN** 入库工具返回 `"向量库不可用，无法入库"`，前端显示错误提示，已嵌入的向量不缓存（避免数据丢失，用户可在 Milvus 恢复后重新入库）

#### Scenario: Milvus 离线时非 RAG 能力正常
- **WHEN** 用户输入"读一下 README.md"（路径 B）或"早安"（路径 A）且 Milvus 不可达
- **THEN** 路径 A/B 正常执行，不受 Milvus 不可达影响
