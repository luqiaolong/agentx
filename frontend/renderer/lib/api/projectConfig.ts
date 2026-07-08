/**
 * 项目级配置目录 .agentx/ 的 HTTP API client。
 *
 * 对应后端端点：
 * - POST /api/project-config/init  —— 初始化（补缺失文件，幂等）
 * - GET  /api/project-config      —— 查询当前状态
 *
 * 响应字段沿用后端 snake_case（agents_md_preview），与 shared/api-types.ts
 * 中的接口定义保持一致；调用方按字段名直接使用即可。
 *
 * 走统一 HTTP 边界 apiPost/apiGet（@/lib/api/request），它们会在
 * `!r.ok` 时抛 ApiError，避免后端 4xx/5xx 被静默吞没。
 */
import type {
  ProjectConfigInitResult,
  ProjectConfigStatus,
} from "../../../shared/api-types";
import { apiGet, apiPost } from "@/lib/api/request";

/**
 * 初始化指定项目的 .agentx/ 配置目录（幂等：已存在的文件会被跳过）。
 *
 * @param path     项目根目录的绝对路径
 * @param threadId 当前会话 thread_id，用于后端沙箱授权校验
 * @returns `{ok, path, created, skipped}` —— created/skipped 为文件名列表
 */
export async function initProjectConfig(
  path: string,
  threadId: string,
): Promise<ProjectConfigInitResult> {
  return apiPost<ProjectConfigInitResult>("/api/project-config/init", {
    path,
    thread_id: threadId,
  });
}

/**
 * 查询指定项目的 .agentx/ 配置目录状态。
 *
 * @param path     项目根目录的绝对路径
 * @param threadId 当前会话 thread_id，用于后端沙箱授权校验
 * @returns `{exists, files, agents_md_preview}`
 */
export async function getProjectConfig(
  path: string,
  threadId: string,
): Promise<ProjectConfigStatus> {
  return apiGet<ProjectConfigStatus>(
    `/api/project-config?path=${encodeURIComponent(path)}&thread_id=${encodeURIComponent(threadId)}`,
  );
}
