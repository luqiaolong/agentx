/**
 * Dialog 域 API（4 个 Tauri command 包装）。
 *
 * 对应原 preload `window.api.dialog.*`，底层改为 `invoke()` 调 Tauri command。
 */
import { invoke } from "@tauri-apps/api/core";

export interface OpenDialogOptions {
  title?: string;
  defaultPath?: string;
  filters?: { name: string; extensions: string[] }[];
  /** Electron 兼容字段，Tauri 端忽略（Tauri 用 add_filter + 多选 API） */
  properties?: string[];
}

export interface SaveDialogOptions {
  title?: string;
  defaultPath?: string;
  filters?: { name: string; extensions: string[] }[];
}

export interface OpenDialogResult {
  canceled: boolean;
  filePaths: string[];
}

export interface SaveDialogResult {
  canceled: boolean;
  filePath?: string;
}

export function openFile(opts?: OpenDialogOptions): Promise<OpenDialogResult> {
  return invoke<OpenDialogResult>("dialog_open_file", { opts });
}

export function openFolder(): Promise<OpenDialogResult> {
  return invoke<OpenDialogResult>("dialog_open_folder");
}

export function saveFile(opts?: SaveDialogOptions): Promise<SaveDialogResult> {
  return invoke<SaveDialogResult>("dialog_save_file", { opts });
}

/**
 * T2 文件拖拽上传：复制源文件到 `data/uploads/{uuid}_{fileName}`，返回相对路径。
 * Rust 端做系统目录黑名单 + 大小校验 + 路径穿越防护。
 */
export function saveDroppedFile(filePath: string, fileName: string): Promise<string> {
  return invoke<string>("dialog_save_dropped_file", { filePath, fileName });
}
