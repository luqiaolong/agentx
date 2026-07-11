import { describe, expect, it } from "vitest";
import { stripSkillTag } from "@/lib/skillTag";

/**
 * stripSkillTag 工具纯函数单测：用于会话名 / 任务名生成时剥离 /skill:<name> 激活标记。
 *
 * 关键规则（见 frontend/renderer/lib/skillTag.ts）：
 * - 同时识别 /skill: 和 @skill: 两种语法
 * - 技能名字符集 [A-Za-z0-9_-]，长度 1~64
 * - 剥离所有 /skill:<name> 标记，不合并内部空白
 * - 剥离后为空时 fallbackSkillName 兜底为首个被剥离的技能名
 */
describe("stripSkillTag", () => {
  it("剥离 /skill:<name> 激活标记，保留用户输入", () => {
    expect(stripSkillTag("/skill:coding 帮我写代码")).toEqual({
      text: "帮我写代码",
      fallbackSkillName: "coding",
    });
  });

  it("兼容 @skill:<name> 语法", () => {
    expect(stripSkillTag("@skill:brainstorming 梳理方案")).toEqual({
      text: "梳理方案",
      fallbackSkillName: "brainstorming",
    });
  });

  it("无标记文本原文不变，fallbackSkillName 为 null", () => {
    expect(stripSkillTag("分析下本项目技术栈")).toEqual({
      text: "分析下本项目技术栈",
      fallbackSkillName: null,
    });
  });

  it("仅含 /skill:<name> 时文本为空，回退到首个技能名", () => {
    expect(stripSkillTag("/skill:using-superpowers")).toEqual({
      text: "",
      fallbackSkillName: "using-superpowers",
    });
  });

  it("多个 /skill 标签全部剥离，仅首个作为 fallbackSkillName", () => {
    expect(stripSkillTag("/skill:a /skill:b 多标签")).toEqual({
      text: "多标签",
      fallbackSkillName: "a",
    });
  });

  it("混合 /skill: 和 @skill: 全部剥离", () => {
    expect(stripSkillTag("/skill:coding @skill:debug 排查问题")).toEqual({
      text: "排查问题",
      fallbackSkillName: "coding",
    });
  });

  it("不合并内部空白：标记前后各留一格（与后端 _parse_skill_tag 行为一致）", () => {
    // 与后端 [backend/app/router/graph.py::_parse_skill_tag] 同源：不合并内部空白，
    // 避免破坏代码块缩进和换行。标题生成时由 slice(0, 40) 截断，连续空格不影响展示。
    expect(stripSkillTag("hello /skill:coding world")).toEqual({
      text: "hello  world",
      fallbackSkillName: "coding",
    });
  });

  it("trim 首尾空白", () => {
    expect(stripSkillTag("  /skill:coding 帮我  ")).toEqual({
      text: "帮我",
      fallbackSkillName: "coding",
    });
  });

  it("空字符串返回空文本 + null", () => {
    expect(stripSkillTag("")).toEqual({ text: "", fallbackSkillName: null });
  });

  it("纯空白字符串返回空文本 + null", () => {
    expect(stripSkillTag("   ")).toEqual({ text: "", fallbackSkillName: null });
  });

  it("支持下划线和连字符的技能名", () => {
    expect(stripSkillTag("/skill:test_skill-name 内容")).toEqual({
      text: "内容",
      fallbackSkillName: "test_skill-name",
    });
  });

  it("含连字符的技能名匹配后端格式（如 using-superpowers）", () => {
    expect(stripSkillTag("/skill:using-superpowers 分析下项目")).toEqual({
      text: "分析下项目",
      fallbackSkillName: "using-superpowers",
    });
  });

  it("多行文本保留换行不被合并", () => {
    const input = "/skill:coding\n帮我\n写代码";
    const result = stripSkillTag(input);
    expect(result.text).toBe("帮我\n写代码");
    expect(result.fallbackSkillName).toBe("coding");
  });

  it("代码块风格的 /skill 不影响后续内容（标记前后保留空白）", () => {
    expect(stripSkillTag("先 /skill:brainstorming 然后写计划")).toEqual({
      text: "先  然后写计划",
      fallbackSkillName: "brainstorming",
    });
  });
});