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

/**
 * 监听主窗口关闭流程启动事件（Tauri 拦截 `WindowEvent::CloseRequested` 后发出）。
 *
 * 收到后应用进入「关闭中」状态——前端一般用于显示 toast「正在关闭前后端进程…」，
 * 告知用户清理 Python + vite 子进程需要 0.5-2s。返回取消监听函数。
 */
export function onAppClosing(handler: () => void): Promise<UnlistenFn> {
  return listen("app:closing", () => handler());
}
