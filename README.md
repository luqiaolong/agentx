# AgentX

本地优先的个人助理桌面应用，基于 Electron + React + FastAPI + LangGraph 构建。

## 特性

- **本地优先**：数据与模型配置均保存在本地，隐私可控
- **智能体编排**：基于 LangGraph 的多智能体路由与协作
- **长期记忆**：会话检查点与技能/画像持久化
- **工具生态**：文件系统沙箱、RAG 检索、MCP 协议扩展
- **流式交互**：SSE 实时推送，支持人在回路审批

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron + electron-vite |
| 前端 UI | React 18 + TypeScript + Tailwind CSS + Zustand |
| 后端 API | FastAPI + Uvicorn |
| AI 编排 | LangGraph + DeepAgents + LangChain |
| 向量存储 | Milvus（通过 TEI 做嵌入） |
| 检查点 | LangGraph SqliteSaver |
| 观测 | LangSmith / Langfuse |

## 项目结构

```
agentx/
├── backend/app/          # Python 后端
│   ├── router/           # LangGraph 路由与状态机
│   ├── subagents/        # 子智能体实现（代码/RAG/网页/自定义）
│   ├── memory/           # 检查点、画像、技能存储
│   ├── tools/            # 工具实现（文件系统、RAG 检索）
│   ├── vectorstore/      # Milvus 客户端
│   ├── embedding/        # TEI 嵌入客户端
│   ├── mcp/              # MCP 协议客户端
│   ├── observability/    # 日志与追踪
│   ├── main.py           # FastAPI 入口
│   └── config.py         # 应用配置
├── frontend/             # Electron + React 前端
│   ├── main/             # Electron 主进程
│   ├── preload/          # 预加载脚本（IPC 安全边界）
│   ├── renderer/         # React 渲染进程
│   └── shared/           # 前后端共享类型
├── tests/                # 测试集（Python + 前端）
├── docs/openspec/        # 设计规格与变更归档
└── scripts/              # 构建与图标生成脚本
```

## 快速开始

### 环境要求

- Node.js >= 20
- Python >= 3.11
- uv（Python 包管理器）

### 安装依赖

```bash
# 前端依赖
npm install

# Python 依赖
uv sync
```

### 开发模式

```bash
# 同时启动 Electron 主进程、渲染进程与 Python 后端
npm run dev
```

### 测试

```bash
# 前端测试
npm run test

# Python 单元测试
uv run pytest tests/python/unit -v

# Python 集成测试（需要 myserver 连通性）
uv run pytest tests/python/integration -v
```

### 构建

```bash
# 打包当前平台
npm run dist

# 指定平台
npm run dist:win
npm run dist:mac
npm run dist:linux
```

## 配置

复制 `.env.example` 为 `.env`，按需填写以下关键项：

- `OPENAI_API_KEY` / `OPENAI_BASE_URL`：大模型接入
- `MILVUS_URI` / `MILVUS_TOKEN`：向量数据库
- `TEI_ENDPOINT`：嵌入服务地址
- `LANGSMITH_API_KEY`：观测追踪（可选）

## 协议

私有项目，未经授权不得转载或分发。
