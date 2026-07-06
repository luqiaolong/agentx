/** Notify 域 API（1 个 Tauri command 包装）。 */
import { invoke } from "@tauri-apps/api/core";

export interface NotifyOptions {
  title: string;
  body: string;
}

export function show(options: NotifyOptions): Promise<void> {
  return invoke("notify_show", { options });
}
