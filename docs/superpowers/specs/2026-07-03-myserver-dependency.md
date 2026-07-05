# myserver 基础设施依赖说明

> **状态**：草案 v1
> **创建日期**：2026-07-03
> **关联**：`docs/superpowers/specs/2026-07-03-agentx-design.md`（v3 §4.3 / §16.4）、`openspec/changes/adjust-m1-stack-and-structure/`

---

## myserver 服务清单

AgentX M1 阶段嵌入与向量检索能力依赖 myserver (192.168.1.4) 上已部署的三个服务：

| 服务 | 端口 | 用途 | 鉴权 | 备注 |
|---|---|---|---|---|
| HuggingFace TEI | 8080 | bge-m3 文本嵌入，`POST /embed` | 无鉴权 | 单服务部署单模型，请求体仅 `inputs` |
| Milvus 2.x | 19530 | 向量库（collection `AGENTX_knowledge`） | username/password | 经典 API，pymilvus 接入 |
| Attu | 9091 | Milvus 管理 UI | 同 Milvus | 用于手动建 DB / 查看 collection |

**端口冲突核查**：TEI 8080 与 myserver 上 AgentFlow 服务端口（8085 / 8098 / 8100 / 8101 / 8102 / 8103 / 8104 / 8105 / 8106 / 8107 / 8108 / 8110 / 8111）**无冲突**。Milvus 19530 与 Attu 9091 同样独立，不与 AgentFlow 端口段重叠。

**TEI `/embed` 接口契约**：
- 请求体：`{"inputs": "<text>"}`（单文本）或 `{"inputs": ["<text1>", "<text2>", ...]}`（批量）
- `model` 字段不放请求体（TEI 单服务单模型）
- 响应体始终为 `[[float, ...], ...]`（list of list），单文本也是 `[[float, ...]]`
- bge-m3 输出维度 1024，token 上限 8192（约 6000-8000 中文字符）

---

## 防火墙端口要求

AgentX 主机需能访问 myserver 的 8080/tcp（TEI）与 19530/tcp（Milvus）。Attu 9091 仅运维管理用，可选放行。

```bash
# 在 myserver (192.168.1.4) 上执行（firewall-cmd）
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --permanent --add-port=19530/tcp
sudo firewall-cmd --reload

# 验证
sudo firewall-cmd --list-ports
```

若 myserver 使用 ufw / iptables，对应命令等价替换。放行后从 AgentX 主机验证：

```bash
curl http://192.168.1.4:8080/health      # TEI 健康检查
nc -zv 192.168.1.4 19530                  # Milvus 端口连通
```

---

## Milvus DB 预创建

**DB `agentx` 必须手动预创建**，Milvus 不支持应用层自动建 DB。

创建方式（二选一）：

1. **Attu UI**：浏览器打开 `http://192.168.1.4:9091` → 登录 → DB 管理 → 新建 DB `agentx`
2. **pymilvus 脚本**：
   ```python
   from pymilvus import connections, db
   connections.connect(host="192.168.1.4", port="19530", user="<user>", password="<pwd>")
   db.create_database("agentx")
   ```

**collection 与 partition 由应用启动自动创建**：
- collection `AGENTX_knowledge`（schema 见设计文档 §4.3 / openspec design.md D2）
- partition 按 `source_type` 分区（file / web / manual）
- HNSW 索引（M=16, efConstruction=200, metric=COSINE）

**DB 不存在时的行为**：
- 应用启动时 `pymilvus` 连接成功但切到 `agentx` DB 失败
- `/api/health` 中 `milvus` 子项返回 `unhealthy` + `error_code: db_not_found`
- 日志提示用户通过 Attu 或 `pymilvus.db.create_database` 手动创建
- 路径 A 闲聊不受影响；路径 B/C 的 RAG 工具返回显式错误

---

## 离线降级行为

myserver 不可用时（TEI 8080 或 Milvus 19530 任一失联）：

| 路径 | 行为 |
|---|---|
| **A · 闲聊/简单问答** | 正常工作，不依赖 myserver |
| **B · 中等任务** | 非 RAG 工具（filesystem / web_search / web_fetch）正常；RAG 检索工具返回显式错误 |
| **C · 复杂任务** | 同 B；rag-subagent 不可用，code-subagent / web-subagent 正常 |

**RAG 工具错误回显**：
- TEI 不可用 → `"嵌入服务不可用（myserver TEI 8080 无响应），请检查网络或运维"`
- Milvus 不可用 → `"向量库不可用（myserver Milvus 19530 无响应），请检查网络或运维"`

**`/api/health` 响应**：整体仍返回 HTTP 200，但 `status: "unhealthy"`，`components.embedding` / `components.milvus` 标记 `unhealthy` + 对应 `error_code`。前端 `StatusIndicator` 显示降级状态并提示用户。

非 RAG 能力（LLM 对话、文件操作、联网搜索）完全不受 myserver 可用性影响。

---

## 凭证注入

**Milvus 凭证存储**：username / password 存于 Electron `electron-store`，使用 `safeStorage` 加密。明文不落盘、不入 git、不写 `.env`。

**注入流程**：
1. Electron Main 进程启动 → 读 `electron-store` → `safeStorage.decryptString()`
2. Main 进程 spawn Python 子进程时，通过 `env` 注入：
   - `AGENTX_MILVUS_USER`
   - `AGENTX_MILVUS_PASSWORD`
   - `AGENTX_EMBEDDING_URL`（非密，但统一走 env 注入）
   - LLM API Key、`LANGSMITH_API_KEY` 等同流程
3. Python `backend/app/config.py` 用 `pydantic-settings`（`env_prefix="AGENTX_"`，`env_file=None`）加载
4. **不写 .env 文件**，避免凭证落盘被备份软件或误提交读取

**首次启动强制输入**：首次启动检测到 `electron-store` 中无 Milvus 凭证 → Settings 页强制弹出 `MilvusCredentialsForm`，用户必须输入 username/password 后才能使用 RAG 功能；不输入时聊天可用、RAG 禁用并在 Chat 输入框上方提示。

**LangSmith redaction**：`backend/app/observability/langsmith.py` 在 trace 上送前对 `extra.metadata` 做过滤，剔除 `*_PASSWORD` / `*_KEY` 字段，防止凭证泄漏到 LangSmith。

---

## 首次启动检查清单

部署 AgentX 前在 myserver 侧与本机依次确认：

1. **TEI 8080 健康**：`curl http://192.168.1.4:8080/health` 返回 200
2. **Attu 9091 可访问**：浏览器 `http://192.168.1.4:9091` 能登录（用于后续 DB 管理）
3. **Milvus DB `agentx` 已创建**：通过 Attu UI 或 `pymilvus.db.create_database` 确认存在
4. **Milvus 凭证已输入**：AgentX 首次启动 Settings 页 `MilvusCredentialsForm` 已填并通过连通性测试
5. **防火墙 8080 + 19530 放行**：`firewall-cmd --list-ports` 包含 8080/tcp 与 19530/tcp，本机 `nc -zv 192.168.1.4 8080` / `nc -zv 192.168.1.4 19530` 均通

任一项失败 → AgentX `/api/health` 标记对应组件 `unhealthy`，前端 `StatusIndicator` 显示降级指引，用户按提示修复后重启应用。
