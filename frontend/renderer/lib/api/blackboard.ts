/**
 * 后端黑板快照类型（来自 team_done SSE 事件的 blackboard payload）。
 *
 * 当后端 AgentTeam 执行结束时，team_done 事件可选携带 blackboard 字段，
 * 包含每个子任务的 finding 摘要 + 错误列表。前端 BlackboardPanel 优先消费
 * 此快照（含 task_id / wave_index / retries / error 等富信息），
 * 缺失时回退到档位 A 的纯前端 agents 聚合（buildBlackboardRows）。
 */
export interface Finding {
  /** 子代理角色名（如 frontend_dev / backend_dev / code 等） */
  agent: string;
  /** TeamTask.id，对应前端 TeamAgentState.taskId */
  task_id: string;
  /** wave 索引（重规划 / 多轮编排时递增） */
  wave_index: number;
  /** finding 内容摘要 */
  content: string;
  /** 是否成功（false 时通常伴随 error 字段） */
  success: boolean;
  /** 失败原因（仅 success=false 时通常存在） */
  error?: string;
  /** 重试次数 */
  retries: number;
}

export interface BlackboardSnapshot {
  findings: Finding[];
  /** 错误列表（团队级聚合） */
  errors: string[];
}
