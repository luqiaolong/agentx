## Why

设计草案 v2（`docs/superpowers/specs/2026-07-03-agent-py-design.md`）review 暴露两类问题：
1. **基础设施本地化过重**：bge-m3 (~2GB) 与 Chroma 均落本地，首次启动需联网下载模型，且桌面应用与 myserver 已有 TEI/Milvus 资源重复造轮子，违背"避免造轮子"原则。
2. **目录结构与命名歧义**：`src/` 是 Electron/Vite 惯例的渲染层目录，但本项目的 `src/` 包含 main/preload/renderer 全部前端代码，与 `app/` 后端并列时语义不清；同时 review 发现的沙箱白名单与示例路径矛盾、Zustand 版本不一致等阻断性问题需要收敛。

本次 change 把 M1 基础设施从"全本地"调整为"复用 myserver"，并把目录结构对齐到 `frontend/ + backend/` 双根，顺带修复 review 中的阻断性问题。

## What Changes

- **BREAKING**：移除 `sentence-transformers` + 本地 `BAAI/bge-m3` 依赖，改用 myserver 上 HuggingFace TEI 服务（`http://192.168.1.4:8080/embed`），模型名 `bge-m3`，无鉴权
- **BREAKING**：移除 `chromadb` 依赖，向量库改用 myserver Milvus（`192.168.1.4:19530`，username/password 鉴权），通过 `pymilvus` SDK 接入
- **BREAKING**：项目目录重构 —— 前端源码目录 `src/` 重命名为 `frontend/`（含 main/preload/renderer 子目录），后端源码目录 `app/` 重命名为 `backend/`
- **BREAKING**：DeepAgent 沙箱白名单从"仅 `data/workspace/` + `data/uploads/`"扩展为"data/workspace + data/uploads + 用户本次会话通过 dialog 显式授权的目录"，使第 6 节时序图示例 `分析 d:/docs 这周PDF` 可执行
- 修复 review 阻断性问题 #2：统一 Zustand 版本到 v5（设计文档 §4.1 与 §16.1 package.json 对齐）
- 新增配置项 `AGENT_PY_EMBEDDING_URL` / `AGENT_PY_MILVUS_HOST` / `AGENT_PY_MILVUS_USER` / `AGENT_PY_MILVUS_PASSWORD` / `AGENT_PY_MILVUS_DB`
- LLM API Key 存储沿用原设计（Electron `safeStorage` 加密 → `electron-store`），不变
- 端口分配核查：TEI 8080 与 AgentFlow 服务端口（8085/8098/8100-8111）无冲突，已在 design.md D1/D2 中文档化为固定端口

## Capabilities

### New Capabilities
- `embedding-service`: 远程嵌入服务接入 —— 通过 TEI HTTP 端点调用 bge-m3，替代本地 sentence-transformers；包含连接配置、超时重试、降级策略、LangSmith redaction
- `vector-store`: 远程向量库接入 —— 通过 pymilvus 连接 Milvus 19530，含 collection schema、鉴权、连接池、离线降级
- `project-structure`: 项目目录结构约定 —— `frontend/` + `backend/` 双根目录布局，含构建产物路径、PyInstaller 打包路径适配、前端依赖版本约束（Zustand v5）
- `filesystem-sandbox`: 用户授权目录机制 —— 沙箱白名单支持会话级动态授权，含授权生命周期、路径校验、撤销机制
- `dangerous-operation-approval`: 危险操作审批机制 —— interrupt_on 改无限期暂停，含审批 UI 推送、auto_approve 配置、拒绝时 state 转换

### Modified Capabilities
<!-- 本项目当前 openspec/specs/ 为空，无已有 capability 需修改 -->

## Impact

- **代码影响**：
  - 新增 `backend/app/embedding/` 模块（TEI 客户端 + 重试）
  - 新增 `backend/app/vectorstore/` 模块（Milvus collection 管理 + 检索）
  - 删除 `backend/app/tools/rag_retrieve.py` 中 Chroma 调用，替换为 Milvus 客户端
  - 删除 `backend/app/config.py` 中 `sentence_transformers` 配置项
  - `pyproject.toml` 依赖变更：移除 `sentence-transformers` / `chromadb`，新增 `pymilvus>=2.4.0` / `httpx`（已有）
  - 全项目路径引用从 `src/` 改 `frontend/`、`app/` 改 `backend/`（含 `electron.vite.config.ts`、`pyproject.toml` `[tool.setuptools.packages]`、`.gitignore`、启动脚本、文档）
- **API 影响**：FastAPI `/api/health` 新增 embedding/milvus 健康检查子项
- **依赖影响**：Python 依赖体积减少 ~2.3GB（bge-m3 模型 + sentence-transformers + chromadb 依赖树），首次启动无需下载模型
- **运维影响**：应用启动依赖 myserver TEI + Milvus 可用，需在 README 增加"myserver 离线时降级行为"说明
- **文档影响**：设计文档 v2 → v3，§4.3 后端栈表、§5 目录结构、§12 安全、§16.1 依赖基线同步更新；review 中 11 条意见中至少 4 条阻断/严重项随本次落地
- **打包影响**：M2 PyInstaller 打包不再需要捆绑模型文件，包体积从 ~3GB 降至 ~700MB
