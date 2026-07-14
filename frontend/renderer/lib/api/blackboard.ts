/**
 * 后端黑板快照类型 — re-export 自 shared/api-types.ts（REQ-SSE-5）。
 *
 * 类型定义已提升到 shared 层供前后端共享：
 * - BlackboardSnapshot / BlackboardFinding 在 shared/api-types.ts 中声明
 * - 此处 re-export 以保持现有 import 路径不变（@/lib/api/blackboard）
 *
 * 与 team_done SSE 事件的 blackboard payload 对齐：
 * 当后端 AgentTeam 执行结束时，team_done 事件可选携带 blackboard 字段，
 * 包含每个子任务的 finding 摘要 + 错误列表。前端 BlackboardPanel 优先消费
 * 此快照（含 task_id / wave_index / retries / error 等富信息），
 * 缺失时回退到档位 A 的纯前端 agents 聚合（buildBlackboardRows）。
 */
export type {
  BlackboardSnapshot,
  BlackboardFinding,
} from "../../../shared/api-types";

import type { BlackboardFinding } from "../../../shared/api-types";

/**
 * 兼容旧 import：保留 Finding 别名等价于 BlackboardFinding。
 * 新代码请直接使用 BlackboardFinding。
 */
export type Finding = BlackboardFinding;
