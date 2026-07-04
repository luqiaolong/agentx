import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type {
  ChatEvent,
  ApprovalRequest,
  HealthSubitem,
  HealthStatus,
  AuthorizedDir,
  WorkspaceEntry,
  SkillSummary,
  SubagentConfig,
  SubagentsConfig,
  ToolsConfig,
  SkillFileInfo,
  ThreadInfo,
  ProfileCategory,
  ProfileEntry,
  ProfileEntryRequest,
  ElectronAPI,
  WindowAPI,
} from "../../shared/api-types";

export type {
  ChatEvent,
  ApprovalRequest,
  HealthSubitem,
  HealthStatus,
  AuthorizedDir,
  WorkspaceEntry,
  SkillSummary,
  SubagentConfig,
  SubagentsConfig,
  ToolsConfig,
  SkillFileInfo,
  ThreadInfo,
  ProfileCategory,
  ProfileEntry,
  ProfileEntryRequest,
  ElectronAPI,
  WindowAPI,
};

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

declare global {
  interface Window {
    api: WindowAPI;
  }
}
