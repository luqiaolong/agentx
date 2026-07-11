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
import { filterUsedSkills } from "@/hooks/usedSkills";
import type { ProfileEntry, SkillSummary } from "../../shared/api-types";

export function useContextFiles() {
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = currentSession?.workspacePath ?? homeWorkspacePath ?? null;
  const messages = currentSession?.messages ?? [];

  const tasks = useTasksStore((s) => s.tasks);
  const skills = useSkillsStore((s) => s.skills);
  const skillsWorkspacePath = useSkillsStore((s) => s.workspacePath);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  const [profileEntries, setProfileEntries] = useState<ProfileEntry[]>([]);

  useEffect(() => {
    if (skills.length === 0 || skillsWorkspacePath !== workspacePath) {
      void fetchSkills(workspacePath).catch((e) => {
        logger.warn("useContextFiles: fetchSkills failed", e);
      });
    }
  }, [skills.length, skillsWorkspacePath, workspacePath, fetchSkills]);

  useEffect(() => {
    let cancelled = false;
    memory
      .getProfile(undefined, workspacePath, "workspace")
      .then((res) => {
        if (!cancelled) setProfileEntries(res.entries ?? []);
      })
      .catch((e) => {
        logger.warn("useContextFiles: getProfile (memory) failed", e);
        if (!cancelled) setProfileEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [workspacePath]);

  const preferenceEntries = useMemo(
    () => profileEntries.filter((e) => e.category === "preference"),
    [profileEntries],
  );

  const sessionTasks = useMemo(
    () => (currentSession ? tasks.filter((t) => t.sessionId === currentSession.id) : []),
    [tasks, currentSession],
  );

  const toolFiles = useMemo(
    () => extractCategorizedFiles(messages, workspacePath),
    [messages, workspacePath],
  );

  const usedSkills = useMemo(
    () => filterUsedSkills(skills, messages),
    [skills, messages],
  );

  const skillFiles = useMemo<CategorizedFile[]>(
    () => mapSkillsToFiles(usedSkills),
    [usedSkills],
  );

  const sessionSummary = useMemo<CategorizedFile[]>(
    () => mapTasksToSummary(sessionTasks),
    [sessionTasks],
  );

  const memoryFiles = useMemo<CategorizedFile[]>(
    () => mapProfileToFiles(profileEntries, "memory_files"),
    [profileEntries],
  );

  const preferenceFiles = useMemo<CategorizedFile[]>(
    () => mapProfileToFiles(preferenceEntries, "preference_files"),
    [preferenceEntries],
  );

  return {
    tool_files: toolFiles,
    skill_files: skillFiles,
    session_summary: sessionSummary,
    memory_files: memoryFiles,
    preference_files: preferenceFiles,
    _raw: {
      skills: usedSkills,
      sessionTasks,
      profileEntries,
      preferenceEntries,
    },
  };
}

function mapSkillsToFiles(skills: SkillSummary[]): CategorizedFile[] {
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

function mapProfileToFiles(
  entries: ProfileEntry[],
  category: "memory_files" | "preference_files" = "memory_files",
): CategorizedFile[] {
  return entries.map((e) => ({
    id: `profile-${e.key}`,
    name: e.title?.trim() || e.key,
    path: "",
    category,
    ts: Date.parse(e.updated_at) || Date.now(),
    meta: e.category,
  }));
}
