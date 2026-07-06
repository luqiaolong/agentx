/**
 * 事件域 API：Tauri 后端事件监听。
 *
 * 对应原 preload `window.api.python.onStatus`，改为 `listen()` 订阅 Tauri 事件。
 */
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/** 监听 Python 后端状态变化（starting / ready / giving_up 等），返回取消监听函数。 */
export function onPythonStatus(handler: (status: string) => void): Promise<UnlistenFn> {
  return listen<string>("python:status", (e) => handler(e.payload));
}
