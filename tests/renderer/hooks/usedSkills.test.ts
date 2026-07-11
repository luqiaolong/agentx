import { describe, expect, it } from "vitest";
import { filterUsedSkills } from "@/hooks/usedSkills";
import type { ChatMessage } from "@/stores/chat";
import type { SkillSummary } from "../../../frontend/shared/api-types";

const skills: SkillSummary[] = [
  skill("brainstorming"),
  skill("writing-plans"),
  skill("unused-skill"),
];

describe("filterUsedSkills", () => {
  it("keeps only skills referenced by /skill tags in this session", () => {
    const messages = [
      message("user", "/skill:writing-plans 生成计划"),
      message("assistant", "已处理"),
      message("user", "/skill:brainstorming 梳理方案"),
    ];

    expect(filterUsedSkills(skills, messages).map((s) => s.name)).toEqual([
      "brainstorming",
      "writing-plans",
    ]);
  });

  it("returns an empty list when the session has not used any loaded skill", () => {
    const messages = [message("user", "/skill:missing-skill 继续"), message("assistant", "ok")];

    expect(filterUsedSkills(skills, messages)).toEqual([]);
  });
});

function skill(name: string): SkillSummary {
  return {
    name,
    description: `${name} description`,
    trigger: "",
    tools: [],
    content_preview: "",
    path: `d:/skills/${name}/SKILL.md`,
  };
}

function message(role: ChatMessage["role"], text: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    parts: [{ type: "text", id: crypto.randomUUID(), text }],
    ts: Date.now(),
  };
}
