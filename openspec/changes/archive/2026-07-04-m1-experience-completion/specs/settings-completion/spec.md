## ADDED Requirements

### Requirement: LLM 配置组

Renderer SHALL 在设置页提供 LLM 配置组，含 provider 下拉（openai / deepseek / minimax-compat）、model 输入、base_url 输入（minimax-compat 用）、对应 API Key（复用 `ApiKeySettings` 扩展 provider 列表）。配置通过 `electron-store` 持久化，spawn 时注入 `AGENT_PY_DEFAULT_MODEL` / `AGENT_PY_OPENAI_BASE_URL` / `AGENT_PY_OPENAI_API_KEY` / `AGENT_PY_DEEPSEEK_API_KEY` env。

#### Scenario: 切换 provider
- **WHEN** 用户选择 provider 为 `minimax-compat` 并输入 model `MiniMax-Text-01` + base_url `https://api.minimaxi.com/v1` + API Key
- **THEN** 持久化到 `electron-store`，下次 spawn 时注入对应 env

#### Scenario: provider 切换重置字段
- **WHEN** 用户从 `openai` 切换到 `deepseek`
- **THEN** model 输入清空，base_url 隐藏（deepseek 固定 base_url）

### Requirement: 系统提示词配置组

Renderer SHALL 在设置页提供系统提示词 textarea，编辑 `default_system_prompt`，持久化到 `electron-store`，spawn 时注入 `AGENT_PY_DEFAULT_SYSTEM_PROMPT` env。空值时使用代码默认值。

#### Scenario: 编辑系统提示词
- **WHEN** 用户在 textarea 输入 `你是一个专业的代码助手` 并保存
- **THEN** 持久化到 `electron-store`，下次 spawn 注入 `AGENT_PY_DEFAULT_SYSTEM_PROMPT=你是一个专业的代码助手`

#### Scenario: 空值回退默认
- **WHEN** textarea 为空并保存
- **THEN** `electron-store` 中 `systemPrompt` 为空字符串，Python 后端使用 `config.py` 默认值

### Requirement: 安全配置组

Renderer SHALL 在设置页提供安全配置组，含 `autoApproveAfterSeconds` 滑块（0-60，0=禁用）、`persistAuthorizedDirs` 开关、`maxUploadBytes` 输入、`approvalMaxWait` 输入（秒，0=无限等待）。配置持久化到 `electron-store` + `settings.ts` store，spawn 时注入对应 env。

#### Scenario: auto_approve 滑块
- **WHEN** 用户拖动 `autoApproveAfterSeconds` 滑块到 30
- **THEN** `settings.ts` store 更新 `autoApproveAfterSeconds=30`，`ApprovalDialog` 下次显示 30s 倒计时

#### Scenario: approval_max_wait
- **WHEN** 用户输入 `approvalMaxWait=600`
- **THEN** 持久化，spawn 时注入 `AGENT_PY_APPROVAL_MAX_WAIT=600`，`deep_path.py` 读取该值作为 `_APPROVAL_MAX_WAIT`

### Requirement: 知识库配置组

Renderer SHALL 在设置页提供知识库配置组，含 embedding URL、Milvus host/port/db/collection 输入 + Milvus 凭证（复用 `MilvusCredentialsForm` 扩展）。配置持久化到 `electron-store`，spawn 时注入对应 env。

#### Scenario: 修改 embedding URL
- **WHEN** 用户输入 embedding URL `http://192.168.1.5:8080/embed` 并保存
- **THEN** 持久化，下次 spawn 注入 `AGENT_PY_EMBEDDING_URL=http://192.168.1.5:8080/embed`

#### Scenario: Milvus 地址配置
- **WHEN** 用户输入 host `192.168.1.4` port `19530` db `agent_py`
- **THEN** 持久化，下次 spawn 注入 `AGENT_PY_MILVUS_HOST` / `AGENT_PY_MILVUS_PORT` / `AGENT_PY_MILVUS_DB`

### Requirement: 配置变更需重启 Python 后端

系统 SHALL 在设置页底部提示"配置变更需重启后端生效"，并提供"重启后端"按钮，点击后调 `window.api.app.restart()` 重启应用（Python 子进程随之重启）。

#### Scenario: 提示重启
- **WHEN** 用户保存任一配置组
- **THEN** 显示"配置已保存，重启后端以生效"提示 + "重启后端"按钮

#### Scenario: 点击重启
- **WHEN** 用户点击"重启后端"
- **THEN** 调 `window.api.app.restart()`，应用重启

### Requirement: 后端配置项扩展

`backend/app/config.py` SHALL 新增 `approval_max_wait: float = 300.0` `default_system_prompt: str = ""` `max_upload_bytes: int = 52428800` 配置项，从 `AGENT_PY_APPROVAL_MAX_WAIT` / `AGENT_PY_DEFAULT_SYSTEM_PROMPT` / `AGENT_PY_MAX_UPLOAD_BYTES` env 读取。

#### Scenario: 默认值
- **WHEN** 未设置 `AGENT_PY_APPROVAL_MAX_WAIT` env
- **THEN** `settings.approval_max_wait == 300.0`

#### Scenario: env 覆盖
- **WHEN** `AGENT_PY_APPROVAL_MAX_WAIT=600` env 设置
- **THEN** `settings.approval_max_wait == 600.0`
