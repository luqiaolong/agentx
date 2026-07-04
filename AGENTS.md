# AGENTS.md — AI 代理协作规范

> 适用对象：在本项目（`d:\java\agentprojects\agent-py`）上工作的所有 AI 代理
> （Qoder、Claude、Codex、Cursor 等）
> 维护者：项目所有者
> 适用范围：本仓库全项目，跨后端 Python、前端 Electron+React、AI 编排三层

---

## 1. 核心原则

### 1.1 优先使用成熟框架，避免自己造轮子（最重要）

当一个能力**已经存在成熟、社区维护活跃的框架/库**时，**必须优先使用现成方案**。
只有在确认现有方案确实无法满足需求时，才考虑自研或扩展。

**判断"成熟"的标准**（任一不满足都视为"不成熟"）：
- GitHub Stars ≥ 1k，或被大厂生产环境使用
- 12 个月内仍有发版（非僵尸项目）
- 官方文档完整、有可运行的快速开始

### 1.2 衍生原则

| 编号 | 原则 | 一句话解释 |
|---|---|---|
| P1 | 组合优于重写 | 在现成框架上加 middleware/callback/子类，而不是另起炉灶 |
| P2 | 单一职责 | 一个文件/一个函数只做一件事 |
| P3 | 可观测 | 关键路径必须可被 LangSmith / Langfuse 追踪 |
| P4 | 类型安全 | Python 用 type hints；TypeScript 严格模式 + 路径别名 |
| P5 | 配置外置 | 业务参数走 `.env` / `config.py`，不写死在代码里 |
| P6 | 可测试 | 业务逻辑与 IO 解耦，核心函数可纯函数化测试 |

---

## 2. 已确定的技术栈（禁止随意替换）

### 后端（Python）
- 框架：FastAPI
- AI 编排：LangGraph
- 智能体框架：DeepAgents
- 嵌入服务：TEI（Hugging Face Text Embeddings Inference）
- 检查点：LangGraph SqliteSaver
- 观测：LangSmith + Langfuse
- 依赖管理：uv + pyproject.toml

### 前端
- 桌面壳：Electron
- UI：React 18 + TypeScript
- 构建：electron-vite
- 状态管理：zustand

---

## 3. 反面清单（绝对不要做的事）

| # | ❌ 不要做的 | ✅ 应该用的 | 理由 |
|---|---|---|---|
| R1 | 手写 LLM 路由 / 智能体编排循环 | LangGraph `StateGraph` / DeepAgents `create_deep_agent` | 框架已提供检查点、人在回路、断点恢复 |
| R2 | 手写工具调用（ReAct/CoT）循环 | LangGraph `ToolNode` + `@tool` 装饰器 | 错误重试、token 计数、tool_choice 控制很容易错 |
| R3 | 自己写 RAG 检索（chunk + embed + retrieve） | LangChain Retriever + TEI 嵌入服务 | 分块策略、rerank、元数据过滤是工程化重灾区 |
| R4 | 自己写检查点 / 会话持久化 | LangGraph `SqliteSaver` | 序列化、thread_id 隔离、断点恢复由框架处理 |
| R5 | 自己实现 SSE/流式分块协议 | LangChain `astream_events` + FastAPI `StreamingResponse` | 协议细节（heartbeat、reconnect）容易出错 |
| R6 | 自己写桌面应用框架 | Electron + electron-vite + preload IPC | 跨平台、签名、自动更新都已就绪 |
| R7 | 自己造状态管理（store）| zustand（已用）| 引入 Redux/MobX 会与现有架构冲突 |
| R8 | 自己写 CSV/JSON 解析 | 标准库 `csv` / `json` / `pathlib` | 处理边界情况（编码、转义）成本高 |
| R9 | 自己写 HTTP 客户端 | `httpx`（已用）| 重试、超时、连接池都现成 |
| R10 | 自己实现 OpenAPI 文档 | FastAPI 的 `pydantic` 模型 + 自动生成 `/docs` | 手动维护文档必然过时 |

---

## 4. 正面例子（项目里"对"的做法）

```text
需求                        │ 用现成框架
────────────────────────────┼──────────────────────────────
智能体对话编排               │ DeepAgents + LangGraph
工具调用                     │ @tool 装饰器 + ToolNode
长期记忆 / 会话恢复          │ LangGraph checkpointer + SQLite
嵌入向量                     │ TEI 客户端（已有 HTTP 模块）
向量检索                     │ LangChain VectorStore
Web API                      │ FastAPI + pydantic
桌面壳                       │ Electron + preload + IPC
前端状态                     │ zustand
文件 IO                      │ pathlib + with 块
HTTP 客户端                  │ httpx
CSV/JSON                     │ 标准库 csv / json
配置                         │ pydantic-settings + .env
观测                         │ LangSmith + Langfuse
测试                         │ pytest + httpx.AsyncClient
```

---

## 5. 决策流程（写新代码前必走 5 步）

```
┌─ Step 1: 问题归类 ───────────────────────────┐
│  这个需求属于"已有框架能解决"还是"框架外"？   │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 2: 查官方文档 ─────────────────────────┐
│  LangGraph / DeepAgents / Electron 官方文档   │
│  找现成 API / 官方示例 / cookbook              │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 3: 搜项目内现成代码 ────────────────────┐
│  Grep 看看同事/历史 PR 怎么实现的              │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 4: 确认缺口 ───────────────────────────┐
│  仍不满足 → 评估"扩展现成" vs "自研"           │
│  优先扩展（middleware/callback/子类）          │
└────────────────────┬────────────────────────┘
                     ▼
┌─ Step 5: 记录原因 ───────────────────────────┐
│  在 commit message / PR 描述里写明             │
│  "为什么不用现成 / 为什么必须自研"             │
└──────────────────────────────────────────────┘
```

---

## 6. 例外情况（什么时候可以"自己造"）

### ✅ 允许自研的场景

1. **`backend/pythonlearning/` 下的示例文件**（教学目的，演示原理）
2. **性能瓶颈已被 profiling 明确证实**的微型工具（须有数据支撑）
3. **框架 bug 无 workaround**，且已提交 issue
4. **业务规则极特殊**（如自定义 DSL、特定行业协议），且无法用配置表达

### ❌ 禁止自研的借口

- "我觉得现有方案不够优雅" → 用 1.2 P1，**组合优于重写**
- "框架学习成本高" → 这是必须投入的成本
- "网上有类似代码可以参考" → 网络代码质量不可控
- "想练手 / 想加深理解" → 去做 `pythonlearning/`，不要污染主项目

---

## 7. 违反本规范的处置

- AI 代理在生成代码前**应主动说明** "为什么没用现成框架"
- 评审人**应优先质疑**任何自研部分
- 突破例外清单时，须在 `docs/decisions/ADR-xxxx.md` 写 ADR
  （Architecture Decision Record），并在 PR 链接 ADR

---

## 8. 维护

- 本文件随项目技术栈变化更新，修改需在 commit message 中说明
- 任何 AI 代理如有"应加入反面清单"的新发现，可在 PR 中提出
- 与 OpenSpec 的关系：本规范是 OpenSpec 之外的"工程文化层"约束，
  与 OpenSpec 的 proposal/design/tasks 互不替代
