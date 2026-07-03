## ADDED Requirements

### Requirement: TEI 嵌入服务客户端

系统 SHALL 通过 HTTP 调用 myserver 上的 HuggingFace TEI 服务（默认 `http://192.168.1.4:8080/embed`）获取文本嵌入向量，模型为 `bge-m3`，输出维度 1024。系统 MUST NOT 在本地加载 sentence-transformers 或下载 `BAAI/bge-m3` 模型权重。

**TEI `/embed` 接口契约**：
- 请求体 `{"inputs": "<text>"}`（单文本）或 `{"inputs": ["<text1>", ...]}`（批量），**不**包含 `model` 字段
- 响应体始终为 `list[list[float]]`（即使单文本也是 `[[float, ...]]`）
- 客户端 `embed_text(text)` MUST 解包外层 list 返回 `list[float]`
- 客户端 `embed_texts(texts)` MUST 返回 `list[list[float]]` 不做额外解包

#### Scenario: 单文本嵌入成功
- **WHEN** 调用 `embed_text("hello")` 且 TEI 服务可用
- **THEN** 客户端发送 `POST /embed` body `{"inputs": "hello"}`，收到响应 `[[float, ...]]` 后解包外层 list，返回长度为 1024 的 `list[float]`，HTTP 200，延迟 < 500ms

#### Scenario: 批量嵌入遵循最大批次限制
- **WHEN** 调用 `embed_texts(list_of_64_strings)` 且 `AGENT_PY_EMBEDDING_MAX_BATCH=32`
- **THEN** 系统拆分为 2 次请求（body 各含 32 个字符串），合并响应的 `list[list[float]]` 返回 64 个向量

#### Scenario: 请求体不含 model 字段
- **WHEN** 客户端发送任一嵌入请求
- **THEN** 请求 body 仅含 `inputs` 字段，**不**包含 `model` 字段（TEI 单服务部署单模型）

#### Scenario: TEI 超时触发重试
- **WHEN** TEI 响应超过 `AGENT_PY_EMBEDDING_TIMEOUT`（默认 10s）
- **THEN** 系统按 tenacity 指数退避重试 3 次（0.5s / 1s / 2s），3 次均失败后抛 `EmbeddingUnavailable`

#### Scenario: 启动健康检查
- **WHEN** FastAPI 启动时执行 `/api/health` 检查
- **THEN** 子项 `embedding` 调用 TEI `/embed` 嵌入字符串 `"healthcheck"`，200 返回标记 `healthy`，否则 `unhealthy` 并附带错误信息

### Requirement: 文本长度预检

系统 MUST 在调用 TEI 前对输入文本做字符数预检，超过 `AGENT_PY_EMBEDDING_MAX_CHARS`（默认 24000）时拒绝并返回 `TextTooLongError`，避免触发 bge-m3 8192 tokens 上限导致的截断或报错。

#### Scenario: 文本超长被拒绝
- **WHEN** 调用 `embed_text(<30000 字符的文本>)` 且 `AGENT_PY_EMBEDDING_MAX_CHARS=24000`
- **THEN** 客户端不发送 HTTP 请求，直接抛 `TextTooLongError`，错误信息含 `"文本长度 30000 超过上限 24000，请通过分块器切短后重试"`

#### Scenario: 批量嵌入中超长文本跳过
- **WHEN** 调用 `embed_texts([短文本, <30000 字符的长文本>, 短文本])`
- **THEN** 客户端对长文本抛 `TextTooLongError` 并跳过，其余 2 个短文本正常嵌入返回，最终返回 `[vec, None, vec]`（或调用方提供 on_skip 回调）

### Requirement: 嵌入服务配置

系统 SHALL 通过环境变量加载嵌入服务配置，配置项前缀为 `AGENT_PY_EMBEDDING_`。

#### Scenario: 默认配置生效
- **WHEN** 未设置任何 `AGENT_PY_EMBEDDING_*` 环境变量
- **THEN** `url=http://192.168.1.4:8080/embed`，`model=bge-m3`，`timeout=10`，`max_batch=32`，`max_chars=24000`

#### Scenario: 自定义 TEI 端点
- **WHEN** 设置 `AGENT_PY_EMBEDDING_URL=http://other-host:8080/embed`
- **THEN** 客户端调用新 URL，且 LangSmith metadata 中 `embedding.url` 字段记录该值

### Requirement: LangSmith 追踪嵌入调用与 redaction

系统 SHALL 在每次 TEI 调用上添加 LangSmith trace span，包含 `model` / `batch_size` / `latency_ms` 字段，但 MUST NOT 包含原始文本内容（避免敏感数据泄漏）。系统 SHALL 在 trace 发送前对 metadata 做字段名 redaction，过滤匹配 `*_PASSWORD` / `*_KEY` / `*_SECRET` 的字段。

#### Scenario: trace span 包含必要字段
- **WHEN** 任一嵌入调用完成
- **THEN** LangSmith 中可见名为 `embedding.tei.embed` 的 span，metadata 含 `model=bge-m3` / `batch_size=N` / `latency_ms=<int>`，inputs/outputs 字段为 `<redacted>`

#### Scenario: 凭证字段被 redaction 过滤
- **WHEN** tracer 准备发送含 `OPENAI_API_KEY` / `MILVUS_PASSWORD` 字段的 metadata
- **THEN** 这两个字段在发送前被替换为 `<redacted>`，不出现在 LangSmith trace 中

### Requirement: myserver 离线时降级行为

系统 MUST NOT 在 TEI 不可用时静默失败。RAG 相关工具（`rag_retrieve`）SHALL 在嵌入服务不可用时返回显式错误信息，聊天与文件操作等其他能力 MUST 保持可用。

#### Scenario: TEI 离线时 RAG 工具显式失败
- **WHEN** 用户输入"基于知识库回答 X"且 TEI 不可达
- **THEN** `rag_retrieve` 工具返回 `"嵌入服务不可用，无法检索知识库"`，DeepAgent 在 state 中记录错误并回显到 Chat

#### Scenario: TEI 离线时闲聊路径不受影响
- **WHEN** 用户输入"早安"且 TEI 不可达
- **THEN** 路径 A（直接 chat）正常响应，不受 TEI 不可达影响（路径 A 不调用 TEI，TEI 状态与路径 A 延迟无关）
