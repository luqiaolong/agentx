## ADDED Requirements

### Requirement: Markdown 渲染

Renderer SHALL 用 `react-markdown` 渲染 assistant 消息内容，支持标题、列表、表格、链接、代码块等 Markdown 语法。用户消息保持纯文本（`whitespace-pre-wrap`）。

#### Scenario: 渲染 Markdown
- **WHEN** assistant 消息内容为 `# 标题\n\n- 列表项\n\n\`\`\`python\nprint("hi")\n\`\`\``
- **THEN** MessageBubble 渲染为 H1 标题、无序列表、带高亮的代码块

#### Scenario: 用户消息保持纯文本
- **WHEN** 用户消息内容包含 Markdown 语法
- **THEN** MessageBubble 用 `whitespace-pre-wrap` 纯文本显示，不解析 Markdown

### Requirement: 代码块高亮与复制

Renderer SHALL 用 `shiki` 对代码块做语法高亮，每个代码块右上角显示"复制"按钮，点击后复制代码内容到剪贴板。

#### Scenario: 代码高亮
- **WHEN** assistant 消息含 ` ```python ` 代码块
- **THEN** 代码块按 Python 语法高亮渲染

#### Scenario: 复制代码
- **WHEN** 用户点击代码块"复制"按钮
- **THEN** 代码内容写入剪贴板（`window.api.clipboard.write`），按钮短暂显示"已复制"

### Requirement: 文件拖拽上传

Renderer SHALL 在聊天输入区支持文件拖拽，拖入文件后通过 `dialog:saveDroppedFile` IPC 复制到 `data/uploads/{uuid}_{filename}`，并在消息文本中插入 `<file>data/uploads/xxx</file>` 标记供 agent 引用。单文件大小超过 `maxUploadBytes`（默认 50MB）时拒绝并提示。

#### Scenario: 拖拽单个文件
- **WHEN** 用户拖拽一个 10MB 的 PDF 到输入区
- **THEN** 文件复制到 `data/uploads/{uuid}_report.pdf`，输入框插入 `<file>data/uploads/{uuid}_report.pdf</file>`

#### Scenario: 文件超限
- **WHEN** 用户拖拽一个 100MB 文件且 `maxUploadBytes=52428800`（50MB）
- **THEN** 拖拽被拒绝，显示提示"文件超过 50MB 限制"，文件不复制

#### Scenario: 多文件拖拽
- **WHEN** 用户同时拖拽多个文件
- **THEN** 每个文件依次复制，输入框插入多个 `<file>` 标记

### Requirement: 拖拽 IPC 与 Main 进程文件处理

系统 SHALL 在 Main 进程注册 `dialog:saveDroppedFile` IPC handler，接收 `{filePath, originalName}`，用 `fs.copyFile` 复制到 `data/uploads/{uuid}_{originalName}`，返回 `{path: "data/uploads/..."}`。

#### Scenario: IPC 处理
- **WHEN** Renderer 调 `window.api.dialog.saveDroppedFile({filePath, originalName})`
- **THEN** Main 进程生成 UUID，`fs.copyFile(filePath, data/uploads/uuid_originalName)`，返回 `{"path": "data/uploads/uuid_originalName"}`

#### Scenario: 源文件不存在
- **WHEN** `filePath` 指向不存在的文件
- **THEN** 返回 `{"error": "源文件不存在"}`

### Requirement: 拖拽视觉反馈

Renderer SHALL 在文件拖拽悬停输入区时显示视觉反馈（边框高亮 + "释放以上传"提示）。

#### Scenario: 拖拽悬停
- **WHEN** 文件拖拽悬停在输入区
- **THEN** 输入区边框变为蓝色高亮，显示"释放以上传"文字

#### Scenario: 拖拽离开
- **WHEN** 文件拖拽离开输入区
- **THEN** 视觉反馈消失，边框恢复
