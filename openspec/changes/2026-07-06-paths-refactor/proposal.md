# Proposal: paths/ 包重构为按能力域模块化结构

## 背景

当前 `backend/app/paths/` 包承载了路径 C（DeepAgent）和路径 D（AgentTeam）的实现，
而路径 A（chat）和路径 B（subagent dispatch）的实现却内联在 `router/graph.py` 中（约 350 行）。
这导致：

1. **graph.py 职责膨胀**：既做分类调度，又内嵌 `_run_chat_path`、`_run_tool_path`、
   `_convert_subagent_event`、`_select_subagent` 等路径实现，单文件 840+ 行
2. **paths/ 命名模糊**：只有路径 C 和 D 的实现，路径 A/B 在别处，新人无法从包名理解全貌
3. **SSE 事件构造重复 3 处**：`graph.py._sse()`、`deep_path.py` 局部版、`utils/sse_events.py`
4. **resolve_system_prompt 重复 2 处**：`graph.py` 和 `utils/prompts.py` 各有一份
5. **画像抽取耦合在 DeepAgent 中**：`_extract_profile_via_llm` 等函数与审批流无关，却放在 `deep_path.py`

## 目标

删除 `paths/` 包，重构为按能力域组织的模块化结构：

| 新包 | 职责 |
|---|---|
| `chat/` | 路径 A：LLM 直答 + ThinkFilter 流式 |
| `subagents/dispatch.py` | 路径 B：子代理选择 + delegation + 事件转换 |
| `deep/` | 路径 C：DeepAgent 构建/审批/状态修复 |
| `team/` | 路径 D：Orchestrator/并行调度/Blackboard/Aggregator |
| `memory/profile_extractor.py` | 画像抽取（从 deep 拆出） |
| `utils/` | 统一 SSE 事件构造、prompt 解析（消除重复） |

`router/` 精简为仅负责消息分类 + 路径调度，不再内嵌路径实现。

## 范围

- 纯后端 Python 代码重构，不改前端
- 不改任何运行时行为（功能 100% 等价）
- 不改 SSE 事件契约
- 不改 API 端点签名

## 预期收益

- graph.py 从 840 行精简到约 200 行，职责清晰
- 新人从包名即可理解四条路径的归属
- SSE 事件构造统一到 `utils/sse_events.py`，消除 3 处重复
- 画像抽取独立为 `memory/profile_extractor.py`，可被任意路径复用

## 风险

| 风险 | 缓解 |
|---|---|
| 循环导入（team→deep→router.state） | 维持 TYPE_CHECKING 延迟导入；统一 resolve_system_prompt 到 utils |
| import 路径全量变更导致测试断裂 | 先统一公共工具→拆 graph.py→搬迁→更新 import→跑测试 |
| 搬迁遗漏导致运行时 ImportError | 全量 grep `from app.paths` + `from app.router.graph` 确保无遗漏 |
