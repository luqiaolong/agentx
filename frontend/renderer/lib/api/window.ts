/**
 * Window 域 API（4 个 Tauri command + 1 个事件）。
 *
 * 对应原 preload `window.api.window.*`，命令改为 `invoke()`，
 * `onMaximizedChange` 改为 `listen<boolean>("window:maximized-change")`。
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export function minimize(): Promise<void> {
  return invoke("window_minimize");
}

/** 最大化/还原切换（toggle 语义，与 Electron 实现一致）。 */
export function maximize(): Promise<void> {
  return invoke("window_maximize");
}

export function close(): Promise<void> {
  return invoke("window_close");
}

export function isMaximized(): Promise<boolean> {
  return invoke<boolean>("window_is_maximized");
}

/** 监听窗口最大化状态变化，返回取消监听函数。 */
export function onMaximizedChange(handler: (maximized: boolean) => void): Promise<UnlistenFn> {
  return listen<boolean>("window:maximized-change", (e) => handler(e.payload));
}
