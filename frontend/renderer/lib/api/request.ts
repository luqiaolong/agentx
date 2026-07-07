/**
 * 统一 HTTP 边界：renderer → Python 后端的 fetch 封装。
 *
 * 职责：
 * - 拼接 API_BASE + path
 * - 序列化 JSON body（仅在 body !== undefined 时设置 Content-Type）
 * - HTTP 状态码非 2xx 时抛 ApiError（携带 status + 响应文本）
 * - 兼容 204 No Content / 空 body
 *
 * 不负责：
 * - SSE 流（chat.send 走原生 fetch + ReadableStream，不经过这里）
 * - Tauri invoke 调用（settings/app/git/logs 等域）
 * - camelCase ↔ snake_case 翻译（由调用方处理，如 models.testConnection）
 */
import { API_BASE } from "@/lib/api-constants";
import { ApiError } from "@/lib/errors";

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const r = await fetch(url, {
    method: opts.method ?? "GET",
    headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  if (!r.ok) {
    throw new ApiError(r.status, await r.text());
  }
  // 204 No Content 或空 body
  const text = await r.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const apiGet = <T>(path: string, signal?: AbortSignal): Promise<T> =>
  apiRequest<T>(path, { signal });

export const apiPost = <T>(path: string, body?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: "POST", body });

export const apiPut = <T>(path: string, body?: unknown): Promise<T> =>
  apiRequest<T>(path, { method: "PUT", body });

export const apiDelete = <T>(path: string): Promise<T> =>
  apiRequest<T>(path, { method: "DELETE" });
