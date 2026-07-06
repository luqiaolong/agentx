/**
 * Shell 域 API（3 个 Tauri command 包装）。
 *
 * 对应原 preload `window.api.shell.*`，底层改为 `invoke()` 调 Tauri command。
 * `revealInFolder` 在 Rust 端用 `std::process::Command` 跨平台实现
 * （tauri-plugin-shell 未提供 `reveal_in_folder` 方法）。
 */
import { invoke } from "@tauri-apps/api/core";

/** 在文件管理器中显示文件（Windows: explorer /select, macOS: open -R, Linux: xdg-open）。 */
export function revealInFolder(path: string): Promise<void> {
  return invoke("shell_reveal_in_folder", { path });
}

/** 在默认编辑器中打开文件（deprecated shell().open()，后续可迁移到 tauri-plugin-opener）。 */
export function openInEditor(path: string): Promise<void> {
  return invoke("shell_open_in_editor", { path });
}

/** 用系统默认程序打开 URL（外部浏览器等）。 */
export function openExternal(url: string): Promise<void> {
  return invoke("shell_open_external", { url });
}
