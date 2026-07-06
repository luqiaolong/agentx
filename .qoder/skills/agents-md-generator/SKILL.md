# AGENTS.md 自动生成与维护 Skill

## 1. 触发条件

当用户在 coding 模式下执行以下任一操作时，自动触发本 skill：
- 输入 `/agents` 或 `/agents-md` 命令
- 首次进入 coding 模式且工作区存在 AGENTS.md 或 claude.md
- 用户明确要求"生成 agents.md"、"完善 agents.md"、"更新 agents.md"
- 检测到项目结构发生重大变化（新增核心目录、技术栈变更等）

## 2. 执行流程

### 2.1 读取阶段（必须执行）

1. **读取 AGENTS.md**：如果项目根目录存在 `AGENTS.md`，完整读取其内容
2. **读取 claude.md**：如果项目根目录存在 `claude.md`，完整读取其内容
3. **读取 README.md**：如果存在，提取关键信息（技术栈、命令、架构概述）
4. **扫描项目结构**：
   - `package.json` / `pyproject.toml` / `Cargo.toml` / `pom.xml` 等构建文件
   - `tsconfig.json` / `vite.config.*` / `webpack.config.*` 等配置文件
   - `.cursor/rules/` 或 `.cursorrules` 文件
   - `.github/copilot-instructions.md` 文件
   - 测试配置文件（`vitest.config.*`、`pytest.ini`、`jest.config.*` 等）

### 2.2 分析阶段

基于读取的所有文件，提取以下信息：

| 类别 | 提取内容 |
|------|----------|
| 技术栈 | 前端框架、后端框架、构建工具、包管理器、测试框架 |
| 常用命令 | dev/build/test/lint/typecheck 等脚本 |
| 架构结构 | 目录层级、核心模块、入口文件、关键约定 |
| 安全红线 | 凭证管理、危险操作、禁止事项 |
| 易踩坑点 | 循环导入、特殊配置、环境依赖、重启流程 |
| 开发规范 | 代码风格、类型安全、配置外置、可测试性 |

### 2.3 生成/完善阶段

#### 场景 A：AGENTS.md 不存在

使用以下模板生成全新的 AGENTS.md：

```markdown
# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## 1. 项目定位

[一句话描述项目是什么，解决什么问题]

## 2. 技术栈

| 层 | 选型 |
|---|---|
| [前端/后端/桌面壳等] | [具体技术] |

## 3. 常用命令

### 开发
```bash
[命令]    # 说明
```

### 测试
```bash
[命令]    # 说明
```

### 构建/部署
```bash
[命令]    # 说明
```

## 4. 架构与文件地图

```
[目录树，标注关键文件职责]
```

## 5. 关键约定 / 易踩坑

- [约定1]
- [约定2]

## 6. 安全红线

- [红线1]
- [红线2]

## 7. 修改前必读清单

| 任务 | 先读 |
|---|---|
| [任务类型] | [相关文件/章节] |
```

#### 场景 B：AGENTS.md 已存在

对比现有内容与项目实际状态，识别以下差异并建议更新：

1. **新增命令**：package.json/pyproject.toml 中新增的 scripts
2. **技术栈变更**：依赖版本升级、框架替换
3. **架构变化**：新增/删除/重组的目录或模块
4. **新增易踩坑**：从代码审查或 issue 中沉淀的经验
5. **安全红线更新**：新增的危险操作或凭证管理方式变化
6. **过时内容**：已删除的功能、废弃的命令、不再适用的约定

### 2.4 输出阶段

1. **向用户展示变更摘要**：列出新增、修改、删除的内容
2. **询问用户确认**："是否将以上变更写入 AGENTS.md？"
3. **用户确认后**：使用 SearchReplace 工具精确修改，或 Write 工具生成新文件
4. **同时更新 claude.md**（如果存在）：确保其指向 AGENTS.md 的引用是最新的

## 3. 质量检查清单

生成/修改 AGENTS.md 后，必须验证：

- [ ] 文件以 `# AGENTS.md` 开头，包含 `This file provides guidance to Qoder` 声明
- [ ] 包含常用命令（dev/build/test/lint），且命令可直接复制执行
- [ ] 包含架构概述，能帮助新实例快速理解项目结构
- [ ] 包含安全红线（凭证、危险操作）
- [ ] 不包含通用开发建议（如"写单元测试"、"不要提交密钥"）
- [ ] 不包含可轻松发现的文件列表（如每个组件的目录）
- [ ] 如果存在 claude.md，确保它被引用或同步更新
- [ ] 语言与项目主要语言一致（中文项目用中文，英文项目用英文）

## 4. 记忆留存

完成 AGENTS.md 的生成或更新后，创建以下记忆：

- **项目技术栈**：记录核心框架和版本
- **项目架构**：记录目录结构和关键模块职责
- **常用命令**：记录开发、测试、构建命令
- **易踩坑点**：记录从代码中发现的特殊约定或陷阱

## 5. 特殊规则

### 5.1 与 claude.md 的关系
- 如果 `claude.md` 存在且内容是指向 `AGENTS.md` 的指针，保持其不变
- 如果 `claude.md` 包含独立内容，将其合并到 `AGENTS.md`，然后让 `claude.md` 成为指针
- 如果 `claude.md` 不存在，不主动创建

### 5.2 与 Cursor/Copilot 规则的集成
- 如果存在 `.cursorrules` 或 `.cursor/rules/`，提取其关键约束写入 AGENTS.md
- 如果存在 `.github/copilot-instructions.md`，提取其关键约束写入 AGENTS.md
- 不重复规则内容，以引用或摘要形式纳入

### 5.3 多语言项目
- 如果项目同时包含前端（TypeScript）和后端（Python），AGENTS.md 需覆盖两层
- 分别列出各层的命令和约定，避免混淆
