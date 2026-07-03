## ADDED Requirements

### Requirement: 双根目录结构

项目 MUST 采用 `frontend/ + backend/` 双根目录布局：前端源码（Electron Main + Preload + Renderer）全部位于 `frontend/`，后端源码（Python 包）全部位于 `backend/`。`src/` 与 `app/` 命名 MUST NOT 再出现于项目根或文档中。

#### Scenario: 顶层目录布局
- **WHEN** 检查项目根目录
- **THEN** 存在 `frontend/` / `backend/` / `data/` / `tests/` / `docs/` / `openspec/` 目录与 `package.json` / `pyproject.toml` / `electron.vite.config.ts` / `.gitignore` 文件，不存在 `src/` 或 `app/` 顶层目录

#### Scenario: backend 内部保留 app 包根
- **WHEN** 检查 `backend/` 目录
- **THEN** 存在 `backend/app/` Python 包目录（保留 app 命名空间以避免深路径 import），启动命令 `cd backend && uv run python -m app.main` 可正常执行

### Requirement: 构建工具链路径适配

构建配置文件 MUST 显式指向新目录路径，MUST NOT 通过相对路径回溯到旧 `src/` 或 `app/`。

#### Scenario: electron-vite 配置指向 frontend
- **WHEN** 检查 `electron.vite.config.ts`
- **THEN** `main` / `preload` / `renderer` 三入口的 `build.rollupOptions.input` 或 `root` 字段路径前缀为 `frontend/main/` / `frontend/preload/` / `frontend/renderer/`

#### Scenario: tsconfig 路径别名更新
- **WHEN** 检查 `tsconfig.json`
- **THEN** `paths` 字段中 `@/*` 指向 `frontend/renderer/*`，`@main/*` 指向 `frontend/main/*`，无 `src/*` 残留

#### Scenario: Python 包路径配置
- **WHEN** 检查根 `pyproject.toml`
- **THEN** `[tool.setuptools.packages.find]` 的 `where = ["backend"]`，或 `[tool.uv]` 的包发现路径包含 `backend/app`

### Requirement: .gitignore 与构建产物路径

`.gitignore` MUST 同步更新为新目录结构，MUST NOT 仍 ignore 旧路径。

#### Scenario: gitignore 同步
- **WHEN** 检查 `.gitignore`
- **THEN** 包含 `/frontend/dist/` / `/frontend/node_modules/` / `/backend/__pycache__/` / `/backend/.venv/` / `/backend/dist/`（PyInstaller 产物），且不包含 `/src/` 或 `/app/` 条目

#### Scenario: PyInstaller 产物落 backend/dist
- **WHEN** 执行 PyInstaller 打包命令
- **THEN** 产物路径为 `backend/dist/agent-py-backend/`，spec 文件位于 `backend/agent-py.spec`，`pathex` 包含 `backend/`

### Requirement: 启动脚本路径更新

启动脚本与文档示例 MUST 反映新目录结构，M1 双终端启动命令保持简单。

#### Scenario: M1 启动命令
- **WHEN** 用户在项目根执行启动命令
- **THEN** 终端 A `cd backend && uv run python -m app.main` 启动 FastAPI；终端 B `npm run dev`（在项目根）启动 Electron；两条命令均无需 `cd src` 或 `cd app`

#### Scenario: 测试命令路径
- **WHEN** 运行测试
- **THEN** Python 测试命令为 `cd backend && uv run pytest ../tests/python/`，前端测试命令为 `npm test`（在项目根），测试发现路径均指向新目录

### Requirement: 文档目录引用一致性

设计文档与 README MUST 引用新目录路径，MUST NOT 残留 `src/` 或 `app/` 引用。

#### Scenario: 设计文档 v3 目录结构
- **WHEN** 检查 `docs/superpowers/specs/2026-07-03-agent-py-design.md` 第 5 节目录结构
- **THEN** 顶层显示 `frontend/` 与 `backend/` 双根，子目录树正确反映新结构

### Requirement: 全项目无旧路径残留

重构后全项目 MUST NOT 残留旧 `src/` 或 `app/` 路径引用（除 `backend/app/` 这种 backend 前缀形式）。

#### Scenario: 全项目 grep 无残留
- **WHEN** 在项目根执行 `grep -rn -E "(^|[/\s\"])src/(main|preload|renderer|components|hooks|stores|lib|styles)" --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=data --exclude-dir=.git`
- **THEN** 无任何匹配

#### Scenario: Python import 无 app. 残留
- **WHEN** 在项目根执行 `grep -rn -E "from app\.|import app\." --include="*.py" --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=data`
- **THEN** 仅匹配 `backend/app/` 目录内的文件（即 `from app.xxx` 出现在 `backend/app/` 内是合法的，因 `cd backend && python -m app.main` 启动），其他位置无残留

### Requirement: 前端依赖版本一致性

前端依赖版本 MUST 在 `package.json` 与设计文档中保持一致，MUST NOT 出现文档与代码版本号矛盾。本次 change 统一 Zustand 到 v5。

#### Scenario: Zustand 版本统一 v5
- **WHEN** 检查 `package.json` 的 `dependencies.zustand` 与设计文档 §4.1 状态管理行
- **THEN** 两者均为 `^5.0.0`（或文档写"5+"），MUST NOT 出现 `package.json` 写 v5 而文档写 v4 的情况

#### Scenario: Zustand v5 middleware 迁移
- **WHEN** 检查 `frontend/renderer/stores/chat.ts` / `tasks.ts` / `settings.ts` 三个 store
- **THEN** 使用 v5 `middleware` 签名（`persist` / `devtools` API），MUST NOT 残留 v4 旧签名
