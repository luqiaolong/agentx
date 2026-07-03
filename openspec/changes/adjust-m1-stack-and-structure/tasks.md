# 任务追踪 — adjust-m1-stack-and-structure

> **注**：项目当前为 greenfield（仅 `docs/`、`openspec/`、`.gitignore` 存在，无 `src/`/`app/`/`package.json`/`pyproject.toml`）。
> 因此 OpenSpec `tasks.md` 中"rename/modify X"类任务统一转为"create X (按新结构)"。
> 本 change 聚焦 M1 基础设施调整：TEI/Milvus 远程化、frontend/backend 双根、Zustand v5、沙箱授权、审批机制。

## 预期修改/新增文件（greenfield → 新建）

### 后端 (backend/)
- [ ] `pyproject.toml`（根目录，单一 pyproject，pymilvus/httpx，移除 sentence-transformers/chromadb）
- [ ] `backend/app/__init__.py`
- [ ] `backend/app/config.py`（pydantic-settings，AGENT_PY_* 前缀，embedding/milvus/auto_approve 配置）
- [ ] `backend/app/embedding/__init__.py` + `tei_client.py`（httpx + tenacity 重试 + 批量拆分 + 字符数预检）
- [ ] `backend/app/vectorstore/__init__.py` + `milvus_client.py`（经典 API，collection/partition/HNSW，ingest/search/delete）
- [ ] `backend/app/utils/security.py`（SessionSandbox：会话级授权目录，路径规范化，系统目录黑名单）
- [ ] `backend/app/utils/chunks.py`（长消息切片，保证 chunk < 8192 tokens）
- [ ] `backend/app/observability/__init__.py` + `langsmith.py`（trace span + redaction 过滤器）+ `logger.py`
- [ ] `backend/app/router/__init__.py` + `state.py`（RouterState 含 authorized_dirs）+ `classifier.py` + `graph.py`（骨架）
- [ ] `backend/app/paths/__init__.py` + `chat_path.py` + `react_path.py` + `deep_path.py`（interrupt_on 无限期）
- [ ] `backend/app/tools/__init__.py` + `filesystem.py`（接入 SessionSandbox）+ `rag_retrieve.py`（切 TEI+Milvus）
- [ ] `backend/app/main.py`（FastAPI lifespan：TEI/Milvus 初始化；`/api/health`；`/api/sandbox/*`；`/api/chat/approve`；SSE）

### 前端 (frontend/)
- [ ] `package.json`（Zustand ^5.0.0，electron-vite，React 18，shadcn 依赖）
- [ ] `electron.vite.config.ts`（main/preload/renderer 入口指 frontend/）
- [ ] `tsconfig.json`（paths `@/*`→`frontend/renderer/*`，`@main/*`→`frontend/main/*`）
- [ ] `tailwind.config.ts` + `components.json`（shadcn/ui）
- [ ] `frontend/main/index.ts`（窗口管理 + Python 子进程 spawn + env 注入）
- [ ] `frontend/main/python/spawn.ts`（safeStorage 解密 → env 注入 Milvus 凭证）
- [ ] `frontend/main/store.ts`（electron-store，safeStorage 加密 milvusUser/milvusPassword）
- [ ] `frontend/preload/index.ts`（contextBridge：chat/sandbox/dialog/approve IPC）
- [ ] `frontend/renderer/index.html` + `main.tsx` + `App.tsx` + 路由
- [ ] `frontend/renderer/stores/chat.ts` / `tasks.ts` / `settings.ts`（Zustand v5 middleware 签名）
- [ ] `frontend/renderer/components/settings/MilvusCredentialsForm.tsx`
- [ ] `frontend/renderer/components/settings/SandboxSettings.tsx`
- [ ] `frontend/renderer/components/chat/ApprovalDialog.tsx`
- [ ] `frontend/renderer/components/StatusIndicator.tsx`

### 测试 (tests/)
- [ ] `tests/python/conftest.py`（pytest markers: integration / requires_myserver）
- [ ] `tests/python/unit/test_tei_client.py`（mock httpx）
- [ ] `tests/python/unit/test_milvus_client.py`（mock pymilvus）
- [ ] `tests/python/unit/test_session_sandbox.py`
- [ ] `tests/python/integration/test_tei_contract.py`（@pytest.mark.integration，CI skip）
- [ ] `tests/python/integration/test_milvus_contract.py`
- [ ] `tests/python/integration/test_smoke.py`（路径 A/B/C 骨架）
- [ ] `tests/renderer/vitest.config.ts` + smoke 测试骨架

### 配置/文档
- [ ] `.gitignore`（frontend/dist, frontend/node_modules, backend/__pycache__, backend/.venv, backend/dist, data/）
- [ ] `.env.example`（仅配置项文档，运行时不读取，凭证标注 `<from electron-store>`）
- [ ] `docs/superpowers/specs/2026-07-03-agent-py-design.md` → v3 同步（§3.2/§4.1/§4.3/§5/§11/§12/§16）
- [ ] `docs/superpowers/specs/2026-07-03-myserver-dependency.md`（myserver TEI/Milvus 依赖 + 降级 + 端口 + DB 预创建）

## OpenSpec Tasks 映射

| ID | 任务描述 | 涉及文件 | 验收标准 | 状态 |
|----|---------|---------|---------|------|
| T1 | 项目结构脚手架（frontend/backend 双根 + 配置文件） | pyproject.toml, package.json, electron.vite.config.ts, tsconfig.json, .gitignore | 双根目录存在，无 src//app/ 顶层残留 | ⬜ |
| T2 | 设计文档 v2 → v3 同步 | docs/.../2026-07-03-agent-py-design.md | §3.2/4.1/4.3/5/11/12/16 全部更新，头部 v3 | ⬜ |
| T3 | TEI 嵌入客户端 | backend/app/embedding/*, config.py, langsmith.py | embed_text/embed_texts + 重试 + 预检 + 单测 | ⬜ |
| T4 | Milvus 向量库客户端 | backend/app/vectorstore/*, config.py, main.py lifespan | 经典 API + collection/partition + ingest/search/delete + 单测 | ⬜ |
| T5 | 沙箱授权机制 | backend/app/utils/security.py, main.py, tools/filesystem.py, router/state.py, preload, SandboxSettings | SessionSandbox + /api/sandbox/* + 授权/撤销/路径规范化 + 单测 | ⬜ |
| T6 | 危险操作审批 | backend/app/paths/deep_path.py, main.py, preload, ApprovalDialog | interrupt_on 无限期 + /api/chat/approve + SSE approval_request + auto_approve 配置 | ⬜ |
| T7 | 健康检查与降级 | backend/app/main.py, tools/rag_retrieve.py, StatusIndicator.tsx | /api/health 含 embedding/milvus 子项 + error_code 区分 | ⬜ |
| T8 | Zustand v5 迁移 | frontend/renderer/stores/*.ts | v5 middleware 签名，与 package.json 一致 | ⬜ |
| T9 | 凭证注入流程 | frontend/main/python/spawn.ts, store.ts, MilvusCredentialsForm.tsx | safeStorage 加解密 + env 注入 + 首次强制输入 | ⬜ |
| T10 | 测试与文档收尾 | tests/*, .env.example, myserver-dependency.md | 单测可跑（mock），integration 标记 skip，pytest markers 配置 | ⬜ |

## 规模判定
- 涉及文件数: 40+ → 规模: **L**
- 涉及模块数: backend(embedding/vectorstore/security/router/tools/observability) + frontend(main/preload/renderer) + tests + docs → 跨模块

## 部署验证说明（Phase 6）
- 本项目为本地桌面应用（Electron + Python 子进程），非 myserver 上 AgentFlow 服务（8085-8111）。
- Phase 6 部署验证（agentflow-ops）不适用；以"本地双端启动 + /api/health + 单测通过"作为验证替代。
