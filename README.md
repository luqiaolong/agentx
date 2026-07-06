# AgentX

本地优先的个人助理桌面应用，基于 Tauri 2.x + React + FastAPI + LangGraph 构建。

![主界面](docs/agentx.png)

## 特性

- **本地优先**：数据与模型配置均保存在本地，隐私可控
- **智能体编排**：基于 LangGraph 的多智能体路由与协作（CHAT / SINGLE_TOOL / DEEP_TASK / AgentTeam 四路径）
- **长期记忆**：会话检查点、技能、画像持久化（SQLite）
- **工具生态**：文件系统沙箱、RAG 检索、MCP 协议扩展
- **流式交互**：SSE 实时推送，支持人在回路审批
- **自定义子代理**：支持配置化添加自定义子智能体

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust 1.77+ |
| 前端 UI | React 18 + TypeScript + Tailwind CSS + Zustand |
| 后端 API | FastAPI + Uvicorn |
| AI 编排 | LangGraph + DeepAgents + LangChain |
| 向量存储 | Milvus（TEI BGE-M3 嵌入） |
| 检查点 | LangGraph SqliteSaver |
| 观测 | LangSmith / Langfuse |

## 项目结构

```
agentx/
├── backend/app/          # Python 后端
│   ├── router/           # LangGraph 路由与状态机
│   ├── chat/deep/team/   # 四路径实现（chat / tool / deep / team）
│   ├── subagents/        # 子智能体（code / rag / web / 自定义）
│   ├── memory/           # 检查点、画像、技能存储
│   ├── tools/            # 工具实现（文件系统、RAG 检索）
│   ├── vectorstore/      # Milvus 客户端
│   ├── embedding/        # TEI 嵌入客户端
│   ├── mcp/              # MCP 协议客户端
│   ├── observability/    # 日志与追踪
│   ├── main.py           # FastAPI 入口
│   └── config.py         # 应用配置
├── frontend/             # React 前端
│   ├── renderer/         # React 渲染层（含 lib/api 12 模块）
│   └── shared/           # 共享类型
├── src-tauri/            # Tauri 2.x Rust 主进程
│   ├── src/              # commands + backend + store + migration + git
│   ├── Cargo.toml
│   └── tauri.conf.json   # 窗口/打包/updater 骨架
├── tests/                # 测试集（Python + 前端）
├── docs/                 # 设计文档与截图
├── openspec/changes/     # 变更提案存档
└── scripts/              # 构建、图标生成、冒烟脚本
```

## 快速开始

### 环境要求

- Node.js >= 20
- Python >= 3.11
- uv（Python 包管理器）
- Rust stable（>= 1.77）+ Cargo
- WebView2 Runtime（Windows，Edge 自动安装）

### 安装依赖

```bash
npm install
uv sync
```

### 开发模式

```bash
npm run dev          # 等价于 tauri dev（vite + Rust + Python 后端）
```

### 测试

```bash
# 前端
npm run test

# Rust 单测
cd src-tauri && cargo test --lib

# Tauri 迁移冒烟脚本（10 项检查）
pwsh scripts/smoke-tauri.ps1

# Python 单元测试
uv run pytest tests/python/unit -v

# Python 集成测试（需要 myserver 连通性）
uv run pytest tests/python/integration -v
```

### 构建

```bash
npm run build        # 等价于 tauri build（生成 NSIS 安装包）
npm run dist:win     # 同上，显式 Windows 目标
```

## 配置

![设置界面](docs/agentx-config.png)

配置通过前端设置面板实时保存，由 Rust 主进程通过 tauri-plugin-store 持久化（凭证用 `enc:`/`plain:` 前缀格式）并注入后端进程，无需手动编辑 `.env`：

- **模型**：LLM 服务商、API Key、激活模型（支持多模型切换）
- **记忆**：系统提示词、上下文窗口（消息数 / token 上限）
- **技能**：Markdown 技能文件管理
- **MCP**：MCP Server 配置与工具发现
- **子代理**：内置子代理（code / rag / web）开关与参数，以及自定义子代理
- **工具**：各工具启用状态
- **知识库**：Milvus 连接配置
- **审批与安全**：自动审批倒计时、沙箱授权目录

### 从旧版 Electron 迁移

首次启动 Tauri 版本时，`migration::migrate_electron_store()` 会自动检测旧 Electron 配置文件（`%APPDATA%/agentx/config.json`）并迁移到 tauri-plugin-store。明文配置（`plain:` 前缀或裸字符串）自动迁移；`enc:` 加密凭证（safeStorage）无法跨进程解密，会在前端设置页提示用户重新输入。

## 协议

私有项目，未经授权不得转载或分发。
