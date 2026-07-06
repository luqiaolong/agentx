/** Clipboard 域 API（2 个 Tauri command 包装）。 */
import { invoke } from "@tauri-apps/api/core";

export function read(): Promise<string> {
  return invoke<string>("clipboard_read");
}

export function write(text: string): Promise<void> {
  return invoke("clipboard_write", { text });
}
