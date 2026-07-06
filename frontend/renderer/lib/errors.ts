import { ZodError } from "zod";

/**
 * 统一错误类型，HTTP 请求失败时抛出。
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`HTTP ${status}: ${body}`);
    this.name = "ApiError";
  }
}

/**
 * 把任意错误转换为用户友好的提示信息。
 *
 * 区分 ApiError / ZodError / 普通 Error / 未知类型。
 */
export function humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    return `请求失败 (${e.status})`;
  }
  if (e instanceof ZodError) {
    const first = e.errors[0];
    return first ? first.message : "数据校验失败";
  }
  if (e instanceof Error) {
    return e.message || "操作失败";
  }
  if (typeof e === "string") return e;
  return "操作失败";
}
