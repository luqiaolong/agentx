import { useEffect, useMemo, useState } from "react";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { useSkillsStore } from "@/stores/skills";
import { memory } from "@/lib/api/http";
import { logger } from "@/lib/logger";
import {
  extractCategorizedFiles,
  type CategorizedFile,
} from "@/components/workspace/extractFiles";
import type { ProfileEntry, SkillSummary } from "../../shared/api-types";

/**
 * 上下文面板 4 类文件的统一获取层。
 *
 * 数据源：
 * - tool_files: extractCategorizedFiles（本地解析 tool-call args）
 * - skill_files: useSkillsStore（HTTP /api/skills）
 * - session_summary: useTasksStore（todo_update/plan 事件聚合）
 * - memory_files: memory.getProfile()（HTTP /api/memory/profile）
 *
 * 设计：所有数据源统一映射到 CategorizedFile[]，ContextTabPanel 只负责渲染。
 */
export function useContextFiles() {
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  // 会话显式 workspacePath 优先；Home 会话 workspacePath=null 时回退到 homeWorkspacePath
  const workspacePath = currentSession?.workspacePath ?? homeWorkspacePath ?? null;
  const messages = currentSession?.messages ?? [];

  const tasks = useTasksStore((s) => s.tasks);
  const skills = useSkillsStore((s) => s.skills);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  const [profileEntries, setProfileEntries] = useState<ProfileEntry[]>([]);

  // 拉取技能列表（仅在尚未加载时触发）
  useEffect(() => {
    if (skills.length === 0) {
      void fetchSkills().catch((e) => {
        logger.warn("useContextFiles: fetchSkills failed", e);
      });
    }
  }, [skills.length, fetchSkills]);

  // 拉取画像条目
  useEffect(() => {
    let cancelled = false;
    memory
      .getProfile()
      .then((res) => {
        if (!cancelled) setProfileEntries(res.entries ?? []);
      })
      .catch((e) => {
        logger.warn("useContextFiles: getProfile failed", e);
        if (!cancelled) setProfileEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 只取当前会话的任务用于摘要展示
  const sessionTasks = useMemo(
    () => (currentSession ? tasks.filter((t) => t.sessionId === currentSession.id) : []),
    [tasks, currentSession],
  );

  const toolFiles = useMemo(
    () => extractCategorizedFiles(messages, workspacePath),
    [messages, workspacePath],
  );

  const skillFiles = useMemo<CategorizedFile[]>(
    () => mapSkillsToFiles(skills, workspacePath),
    [skills, workspacePath],
  );

  const sessionSummary = useMemo<CategorizedFile[]>(
    () => mapTasksToSummary(sessionTasks),
    [sessionTasks],
  );

  const memoryFiles = useMemo<CategorizedFile[]>(
    () => mapProfileToFiles(profileEntries),
    [profileEntries],
  );

  return {
    tool_files: toolFiles,
    skill_files: skillFiles,
    session_summary: sessionSummary,
    memory_files: memoryFiles,
    /** 原始数据：用于详情弹框查找完整内容 */
    _raw: {
      skills,
      sessionTasks,
      profileEntries,
    },
  };
}

/* ------------------------------------------------------------------ */
/*  类型适配层                                                          */
/* ------------------------------------------------------------------ */

function mapSkillsToFiles(
  skills: SkillSummary[],
  _workspacePath: string | null,
): CategorizedFile[] {
  // 直接使用后端 /api/skills 返回的真实路径（DATA_DIR/skills/<name>/SKILL.md）。
  // 旧实现误用 workspacePath/.qoder/skills/<name>.md 前缀拼路径，导致打开报错。
  return skills.map((s) => ({
    id: `skill-${s.name}`,
    name: s.name,
    path: s.path,
    category: "skill_files" as const,
    ts: Date.now(),
    meta: s.trigger || undefined,
  }));
}

function mapTasksToSummary(
  tasks: ReturnType<typeof useTasksStore.getState>["tasks"],
): CategorizedFile[] {
  return tasks.map((t) => {
    const total = t.todos?.length ?? 0;
    const done = t.todos?.filter((x) => x.status === "completed").length ?? 0;
    return {
      id: `task-${t.id}`,
      name: t.title,
      path: "",
      category: "session_summary" as const,
      ts: t.createdAt,
      meta: total > 0 ? `${done}/${total}` : undefined,
    };
  });
}

function mapProfileToFiles(entries: ProfileEntry[]): CategorizedFile[] {
  return entries.map((e) => ({
    id: `profile-${e.key}`,
    name: e.key,
    path: "",
    category: "memory_files" as const,
    ts: Date.parse(e.updated_at) || Date.now(),
    meta: e.category,
  }));
}
