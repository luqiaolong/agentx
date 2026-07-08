import type { AgentMode } from "../../shared/api-types";

/**
 * 场景（UI 维度）的取值集合。
 *
 * 设计要点：
 * - 场景由 `agent_mode` 派生（mode==="work" → scene==="work"；其余 → scene==="coding"），
 *   不作为独立可变状态，避免与 mode 出现不一致。
 * - 后端契约 `agent_mode` 单字段不变（`work` / `coding` / `coding_team`），
 *   场景仅为前端 UI 概念与用户心智模型。
 * - 工作场景绑定唯一 Supervisor（work agent），编程场景绑定唯一 Expert（coding agent），
 *   可选 Team 模式。Supervisor + Expert 架构不允许「work + Team」组合，
 *   因此 scene 与 mode 的派生关系是唯一确定的。
 */
export type Scene = "work" | "coding";

/**
 * 从 agent_mode 派生场景。
 *
 * @param mode 后端 agent_mode 字段值
 * @returns UI 层 scene 取值
 */
export function getSceneFromMode(mode: AgentMode): Scene {
  return mode === "work" ? "work" : "coding";
}

/**
 * 应用用户从顶部 tab 触发的场景切换，返回应写入 agentMode store 的新 mode。
 *
 * 联动规则：
 * - scene = "work" → 强制 mode = "work"（work 场景不允许 Team 模式）
 * - scene = "coding" → 保留原 mode，除非原 mode === "work"（此时升级为 "coding"）
 *
 * @param targetScene 用户点击的顶部 tab 目标场景
 * @param currentMode 当前 agent_mode
 * @returns 应写入 store 的新 mode（若与 currentMode 相同，调用方可跳过 setState）
 */
export function applySceneChange(
  targetScene: Scene,
  currentMode: AgentMode,
): AgentMode {
  if (targetScene === "work") {
    // work 场景无 Team 模式，强制切回 work
    return "work";
  }
  // scene === "coding"
  if (currentMode === "work") {
    // 从 work 升级到 coding 场景，默认选单 Expert（Coding Agent），不自动升级为 Team
    return "coding";
  }
  // 已是 coding 或 coding_team，保持
  return currentMode;
}