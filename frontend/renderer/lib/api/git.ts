/**
 * Git 域 API（9 个 Tauri command 包装）。
 *
 * 对应原 preload `window.api.git.*`，底层改为 `invoke()` 调 Tauri command。
 * 替代原 Electron 的 dugite N-API 依赖。
 */
import { invoke } from "@tauri-apps/api/core";
import type { GitStatusEntry, GitCommit, GitBranch, GitRepoStatus } from "../../../shared/api-types";

export interface GitStatusResult {
  entries: GitStatusEntry[];
  repoStatus: GitRepoStatus;
}

export interface GitLogResult {
  commits: GitCommit[];
}

export interface GitBranchesResult {
  branches: GitBranch[];
}

export interface GitOpResult {
  ok: boolean;
  error?: string;
}

export interface GitDiffResult {
  diff: string;
}

export function getStatus(repoPath: string): Promise<GitStatusResult> {
  return invoke<GitStatusResult>("git_get_status", { repoPath });
}

export function getLog(repoPath: string, limit?: number): Promise<GitLogResult> {
  return invoke<GitLogResult>("git_get_log", { repoPath, limit });
}

export function getBranches(repoPath: string): Promise<GitBranchesResult> {
  return invoke<GitBranchesResult>("git_get_branches", { repoPath });
}

export function checkout(repoPath: string, branch: string): Promise<GitOpResult> {
  return invoke<GitOpResult>("git_checkout", { repoPath, branch });
}

export function stage(repoPath: string, files: string[]): Promise<GitOpResult> {
  return invoke<GitOpResult>("git_stage", { repoPath, files });
}

export function unstage(repoPath: string, files: string[]): Promise<GitOpResult> {
  return invoke<GitOpResult>("git_unstage", { repoPath, files });
}

export function commit(repoPath: string, message: string): Promise<GitOpResult> {
  return invoke<GitOpResult>("git_commit", { repoPath, message });
}

export function discardChanges(repoPath: string, files: string[]): Promise<GitOpResult> {
  return invoke<GitOpResult>("git_discard_changes", { repoPath, files });
}

export function getDiff(repoPath: string, file?: string): Promise<GitDiffResult> {
  return invoke<GitDiffResult>("git_get_diff", { repoPath, file });
}
