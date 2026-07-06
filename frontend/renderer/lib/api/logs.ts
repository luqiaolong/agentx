/** Logs 域 API（1 个 Tauri command 包装）。 */
import { invoke } from "@tauri-apps/api/core";

/**
 * 读取日志文件。
 * @param date YYYYMMDD 格式，不传则读当天
 * @param maxLines 返回最后 N 行，默认 200
 */
export function read(date?: string, maxLines?: number): Promise<string[]> {
  return invoke<string[]>("logs_read", { date, maxLines });
}
