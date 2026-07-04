## ADDED Requirements

### Requirement: 分类器规则覆盖扩展

`classifier.py` SHALL 扩展规则关键词：
- `_SINGLE_TOOL_KEYWORDS` 增加："打开" / "查看" / "显示" / "查找" / "列出"
- `_CHAT_KEYWORDS`（新增集合）："翻译" / "解释" / "计算" / "对比" / "什么" / "为什么" / "如何"（命中且消息长度 < 50 → CHAT）
- `_DEEP_TASK_KEYWORDS` 保持不变

规则过滤顺序：命令 → 短消息 → CHAT 关键词（短消息）→ SINGLE_TOOL 关键词 → DEEP_TASK 关键词 → LLM 分类。

#### Scenario: 翻译走 CHAT
- **WHEN** 用户消息为 `翻译这段话：Hello World`（长度 < 50 且含"翻译"）
- **THEN** 规则命中 CHAT 关键词，返回 `CHAT`，不调 LLM

#### Scenario: 查找文件走 SINGLE_TOOL
- **WHEN** 用户消息为 `查找文件 README.md`
- **THEN** 规则命中 SINGLE_TOOL 关键词"查找"，返回 `SINGLE_TOOL`

#### Scenario: 打开文件走 SINGLE_TOOL
- **WHEN** 用户消息为 `打开 data/workspace/out.txt`
- **THEN** 规则命中 SINGLE_TOOL 关键词"打开"，返回 `SINGLE_TOOL`

#### Scenario: 规则未命中走 LLM
- **WHEN** 用户消息为 `帮我把这个数据分析一下`（长度 > 20，含"分析" → 实际命中 DEEP_TASK）
- **THEN** 规则命中 DEEP_TASK 关键词"分析"，返回 `DEEP_TASK`

### Requirement: thinking 状态占位

Renderer SHALL 在 `setStreaming(true)` 后、首个 `token` 事件到达前，在 pending 消息气泡内显示"思考中..."动画占位。首个 token 到达后替换为实际内容。

#### Scenario: 显示 thinking
- **WHEN** 用户发送消息，`setStreaming(true)` 被调用
- **THEN** pending assistant 消息气泡显示"思考中..."（带省略号动画）

#### Scenario: 首个 token 替换
- **WHEN** SSE 推送首个 `token` 事件
- **THEN** "思考中..."占位消失，pending 气泡开始追加实际 token 内容

#### Scenario: 错误时清除
- **WHEN** SSE 推送 `error` 事件
- **THEN** "思考中..."占位消失，显示错误消息

### Requirement: approval 超时可配置

`deep_path.py` `_APPROVAL_MAX_WAIT` SHALL 从 `settings.approval_max_wait` 读取（默认 300.0），0 表示无限等待。`settings.approval_max_wait` 通过 `AGENT_PY_APPROVAL_MAX_WAIT` env 注入。

#### Scenario: 默认 300s
- **WHEN** 未配置 `AGENT_PY_APPROVAL_MAX_WAIT`
- **THEN** `_APPROVAL_MAX_WAIT = 300.0`，审批等待 5 分钟后返回 None

#### Scenario: 自定义超时
- **WHEN** `AGENT_PY_APPROVAL_MAX_WAIT=600`
- **THEN** `_APPROVAL_MAX_WAIT = 600.0`，审批等待 10 分钟

#### Scenario: 无限等待
- **WHEN** `AGENT_PY_APPROVAL_MAX_WAIT=0`
- **THEN** `_await_approval` 无限轮询直至收到审批决定或 abort 标志，不因超时返回 None

### Requirement: ThinkFilter 缓冲可配置

`ThinkFilter` SHALL 接受 `max_hold: int` 构造参数（默认 6），控制短 chunk 合并的上限。`_run_chat_path` 与 `_run_tool_path` 从 `settings` 读取 `think_filter_max_hold`（新增配置项，默认 6）。

#### Scenario: 默认缓冲
- **WHEN** 未配置 `AGENT_PY_THINK_FILTER_MAX_HOLD`
- **THEN** `ThinkFilter(max_hold=6)`，行为与当前一致

#### Scenario: 自定义缓冲
- **WHEN** `AGENT_PY_THINK_FILTER_MAX_HOLD=10`
- **THEN** `ThinkFilter(max_hold=10)`，短 chunk 合并上限提升

### Requirement: 配置项扩展

`backend/app/config.py` SHALL 新增 `think_filter_max_hold: int = 6` 配置项，从 `AGENT_PY_THINK_FILTER_MAX_HOLD` env 读取。

#### Scenario: 默认值
- **WHEN** 未设置 env
- **THEN** `settings.think_filter_max_hold == 6`

#### Scenario: env 覆盖
- **WHEN** `AGENT_PY_THINK_FILTER_MAX_HOLD=10`
- **THEN** `settings.think_filter_max_hold == 10`
