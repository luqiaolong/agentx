# LangSmith 部署到 myserver（社区 dev 模式）

## 目标
把 LangSmith 自托管 dev 模式部署到 `192.168.1.4`，让 agentx 桌面应用（Tauri + Python）的 `dual_trace` 把 trace 写入该实例，UI 在 `http://192.168.1.4:21980` 可查。

**说明**：LangSmith 自托管在生产环境**仅支持 Kubernetes/Helm**（官方文档：https://docs.smith.langchain.com/self_hosting/kubernetes）。本目录的 docker-compose 是社区 dev 模式，**仅用于本地/内网开发测试**，不保证生产可用。生产环境请走官方 Helm chart。

## 端口
| 端口 | 用途 | 映射到容器 |
|---|---|---|
| 21980 | Web UI | langchain-frontend:1980 |
| 21984 | REST API + OTLP ingestion | langchain-backend:1984 |
| 21985 | Playground（可选） | langchain-playground:3001 |
| 21986 | Platform backend（auth/run ingestion） | langchain-platform-backend:1986 |
| 21987 | ACE backend（任意代码执行） | langsmith-ace-backend:1987 |
| 25432 | postgres（命名卷） | langchain-db:5432 |
| 26379 | redis（命名卷） | langchain-redis:6379 |
| 28123 | clickhouse HTTP | langchain-clickhouse:8123 |
| 29000 | clickhouse native | langchain-clickhouse:9000 |

## 文件清单
```
deploy/langsmith/
├── docker-compose.yml     # 10 服务 compose（含 2 init jobs：clickhouse-setup / postgres-setup）
├── .env.template          # 必填 LANGSMITH_LICENSE_KEY / API_KEY_SALT / INITIAL_ORG_ADMIN_*
├── .gitignore             # 忽略 .env
└── README.md              # 本文件
```

## 前置条件

### 1. 拿到 LANGSMITH_LICENSE_KEY
联系 LangChain 商务（https://www.langchain.com/contact-sales）申请 Enterprise add-on 的 trial license。会拿到 `lsv2_sk_xxx...` 格式的纯字符串。

**注意**：license 校验需要容器能访问 `https://beacon.langchain.com`（egress）。myserver 需放行。

### 2. 生成 API_KEY_SALT 和 JWT_SECRET
```bash
openssl rand -base64 32   # 复制输出到 API_KEY_SALT
openssl rand -base64 32   # 复制输出到 JWT_SECRET
```
**警告**：API_KEY_SALT 一旦设置**永不修改**——这是用于 hash 所有 API key 的 salt，修改会导致所有现存 API key 失效，用户必须重新生成。

### 3. 确认 myserver 镜像拉取能力
myserver 之前测试无法从 docker.io 拉取镜像。两条备选方案：

**方案 A（推荐）**：本地拉取 + save/load
```bash
# 本地机器（任意能联 docker.io 的）
docker pull langchain/langsmith-frontend:0.11.32
docker pull langchain/langsmith-backend:0.11.32
docker pull langchain/langsmith-go-backend:0.11.32
docker pull langchain/langsmith-queue:0.11.32  # 同 langsmith-backend
docker pull langchain/langsmith-playground:0.11.32
docker pull langchain/langsmith-ace-backend:0.11.32
docker pull postgres:14.7
docker pull redis:7
docker pull clickhouse/clickhouse-server:24.8

docker save -o langsmith-images.tar \
  langchain/langsmith-frontend:0.11.32 \
  langchain/langsmith-backend:0.11.32 \
  langchain/langsmith-go-backend:0.11.32 \
  langchain/langsmith-playground:0.11.32 \
  langchain/langsmith-ace-backend:0.11.32 \
  postgres:14.7 \
  redis:7 \
  clickhouse/clickhouse-server:24.8
# tar 大小约 2-3GB

scp langsmith-images.tar admin@192.168.1.4:/tmp/
ssh admin@192.168.1.4 'docker load -i /tmp/langsmith-images.tar && rm /tmp/langsmith-images.tar'
```

**方案 B**：myserver 直接拉取（需先确认 docker.io 可达）
```bash
ssh admin@192.168.1.4 'docker pull langchain/langsmith-frontend:0.11.32 && ...'
```

## 部署步骤

### 1. 上传部署文件到 myserver
```bash
scp D:\java\agentprojects\agentx\deploy\langsmith\docker-compose.yml admin@192.168.1.4:/tmp/
scp D:\java\agentprojects\agentx\deploy\langsmith\.env.template admin@192.168.1.4:/tmp/
```

### 2. myserver 上组织文件
```bash
ssh admin@192.168.1.4
sudo mkdir -p /home/admin/apps/langsmith
sudo mv /tmp/docker-compose.yml /tmp/.env.template /home/admin/apps/langsmith/
sudo chown -R admin:admin /home/admin/apps/langsmith
cd /home/admin/apps/langsmith
cp .env.template .env
nano .env   # 填入 LANGSMITH_LICENSE_KEY / API_KEY_SALT / INITIAL_ORG_ADMIN_*
```

### 3. 启动
```bash
cd /home/admin/apps/langsmith
docker compose up -d
# 等 60-120s 让 postgres/redis/clickhouse 初始化 + setup jobs 跑 alembic migration
docker compose ps
# 预期看到 12 个容器（10 服务 + 2 setup），状态全 UP/healthy
```

### 4. 看启动日志（首次必看）
```bash
# Setup jobs 进度
docker compose logs clickhouse-setup postgres-setup
# 期望：postgres-setup 末尾 "Running upgrade ... -> head"；clickhouse-setup 末尾 "Migration completed"

# Backend 启动进度
docker compose logs -f langchain-backend | head -100
# 期望：最后出现 "Uvicorn running on http://0.0.0.0:1984"；无 license error
```

### 5. 健康检查
```bash
# UI 健康（前端容器）
curl -fsS http://127.0.0.1:21980 || echo "frontend not ready"
# API 健康（后端容器）
curl -fsS http://127.0.0.1:21984/health
# API 信息
curl -fsS http://127.0.0.1:21984/api/v1/info
```

### 6. 注册 admin + 拿 API key
浏览器开 `http://192.168.1.4:21980`：
1. 用 `.env` 里 `INITIAL_ORG_ADMIN_EMAIL` / `INITIAL_ORG_ADMIN_PASSWORD` 登录（**首次启动自动创建**）
2. Settings → API Keys → Create API Key
3. 复制格式为 `ls__...` 的 key

### 7. 端口登记
```bash
# 追加到 /home/admin/port-registry.json
ssh admin@192.168.1.4 'cat /home/admin/port-registry.json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); d.update({\
    \"langsmith-ui-http\": 21980, \
    \"langsmith-api-http\": 21984, \
    \"langsmith-playground\": 21985, \
    \"langsmith-platform-backend\": 21986, \
    \"langsmith-ace-backend\": 21987, \
    \"langsmith-postgres\": 25432, \
    \"langsmith-redis\": 26379, \
    \"langsmith-clickhouse-http\": 28123 \
  }); json.dump(d, sys.stdout, indent=2)" > /tmp/pr.json && \
  mv /tmp/pr.json /home/admin/port-registry.json'
```

### 8. agentx 端填 LangSmith API key
Tauri DevTools console：
```js
await window.__TAURI__.invoke('settings_set_api_key', {
  provider: 'langsmith',
  key: 'ls__...'   // 步骤 6 拿到的 key
})
```
然后重启 agentx 桌面应用（关闭重开）→ `env.rs::build_env()` 会把 key + endpoint 注入到 Python 子进程。

## 端到端验证
```bash
# 1. 发一条 chat（在 agentx UI）
# 2. 看 LangSmith UI: http://192.168.1.4:21980 → 项目 agentx → 1 个 run
# 3. 点开 run，metadata 应含 thread_id / agent_mode / user_message
# 4. 验证降级：把 API key 改错（步骤 8），重启 Tauri，再发 chat
#    期望：LangSmith UI 无新 run + 本地 log warning + SQLite observation_run +1
```

## 关键修改（已落代码）
- `backend/app/config/settings.py:148-152` — 新增 `langsmith_endpoint` 字段
- `backend/app/observability/langsmith.py:21,39-48` — 模块级桥接 LANGSMITH_ENDPOINT/LANGSMITH_API_KEY
- `src-tauri/src/backend/env.rs:46-64` — env.rs 双前缀注入（AGENTX_ + LANGSMITH_）
- `backend/app/observability/langsmith.py::dual_trace` — 不变，复用现有双写逻辑

## 故障排查

| 现象 | 排查 |
|---|---|
| backend 容器反复重启 | `docker compose logs langchain-backend` 看 license error；检查 .env 是否填了 LANGSMITH_LICENSE_KEY；或检查 beacon.langchain.com 可达性 `docker exec langsmith-backend wget -qO- https://beacon.langchain.com` |
| frontend 502 / UI 进不去 | backend 还没 ready；等 60s 再试，或 `docker compose restart langsmith-frontend` |
| clickhouse-setup 报 "Dirty database version N" | 版本降级导致；删卷重启：`docker compose down -v && docker compose up -d`（**会清空所有 trace**，慎用） |
| postgres-setup 报 alembic 错误 | 检查 LANGSMITH_LICENSE_KEY 是否填；log_min_messages=WARNING 配置可能屏蔽关键错，调整 `langchain-db` command 临时改 NOTICE 看完整日志 |
| agentx chat 后 LangSmith UI 无 run | 用户本机 `curl -fsS http://192.168.1.4:21984/api/v1/info` 测可达；检查 Tauri Python 子进程的 env：DevTools console 看 build_env 日志，确认 LANGSMITH_API_KEY/ENDPOINT 已注入 |
| run 出现但 metadata 空 | `LANGSMITH_TRACING` 开关没生效；检查 Tauri 启动日志里是否注入 `LANGSMITH_TRACING=true` |
| Playground 页面空白 | `.env` 缺 `OPENAI_API_KEY`；本环境不依赖 Playground，可忽略 |

## 升级路径

dev 模式验证通过后，生产环境请迁移到官方 Helm chart：
```bash
# 在 myserver 装 k3s（如未装）
curl -sfL https://get.k3s.io | sh -

# 加 helm repo
helm repo add langchain https://langchain-ai.github.io/helm
helm repo update

# 部署（需要正式 license + 外部 postgres/redis/clickhouse）
helm upgrade -i langsmith langchain/langsmith \
  --values langsmith_config.yaml \
  --version 0.13.0 \
  -n langsmith --create-namespace \
  --wait --debug
```

参考：https://docs.smith.langchain.com/self_hosting/kubernetes