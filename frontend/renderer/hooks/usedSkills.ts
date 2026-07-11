import type { ChatMessage } from "@/stores/chat";
import type { SkillSummary } from "../../shared/api-types";

const SKILL_TAG_RE = /(?:\/|@)skill:([A-Za-z0-9_-]{1,64})/g;
const SKILL_FIELD_NAMES = new Set(["skill", "skillname", "skill_name", "skillid", "skill_id"]);

export function filterUsedSkills(
  skills: SkillSummary[],
  messages: ChatMessage[],
): SkillSummary[] {
  const usedNames = collectUsedSkillNames(messages);
  if (usedNames.size === 0) return [];
  return skills.filter((skill) => usedNames.has(normalizeSkillName(skill.name)));
}

function collectUsedSkillNames(messages: ChatMessage[]): Set<string> {
  const names = new Set<string>();
  for (const message of messages) {
    for (const part of message.parts) {
      collectFromUnknown(part, names);
    }
  }
  return names;
}

function collectFromUnknown(value: unknown, names: Set<string>, key?: string): void {
  if (typeof value === "string") {
    collectTags(value, names);
    if (key && SKILL_FIELD_NAMES.has(key.toLowerCase())) {
      addSkillName(value, names);
    }
    return;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      collectFromUnknown(item, names, key);
    }
    return;
  }

  if (!value || typeof value !== "object") return;

  for (const [childKey, childValue] of Object.entries(value as Record<string, unknown>)) {
    collectFromUnknown(childValue, names, childKey);
  }
}

function collectTags(text: string, names: Set<string>): void {
  SKILL_TAG_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SKILL_TAG_RE.exec(text)) !== null) {
    addSkillName(match[1] ?? "", names);
  }
}

function addSkillName(name: string, names: Set<string>): void {
  const normalized = normalizeSkillName(name);
  if (normalized) names.add(normalized);
}

function normalizeSkillName(name: string): string {
  return name.trim().toLowerCase();
}
