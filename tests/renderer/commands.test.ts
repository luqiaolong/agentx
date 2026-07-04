import { beforeEach, describe, expect, it } from "vitest";
import {
  BUILTIN_COMMANDS,
  buildCommandList,
  findBuiltinCommand,
  useCommandPickerStore,
} from "@/stores/commands";

describe("findBuiltinCommand", () => {
  it("主名匹配", () => {
    expect(findBuiltinCommand("/help")?.name).toBe("help");
    expect(findBuiltinCommand("/clear")?.name).toBe("clear");
    expect(findBuiltinCommand("/theme")?.name).toBe("theme");
  });

  it("支持别名", () => {
    expect(findBuiltinCommand("/reset")?.name).toBe("clear");
    expect(findBuiltinCommand("/new")?.name).toBe("clear");
    expect(findBuiltinCommand("/dark")?.name).toBe("theme");
    expect(findBuiltinCommand("/light")?.name).toBe("theme");
    expect(findBuiltinCommand("/config")?.name).toBe("settings");
    expect(findBuiltinCommand("/about")?.name).toBe("version");
  });

  it("接受带参数的形式", () => {
    expect(findBuiltinCommand("/theme dark")?.name).toBe("theme");
    expect(findBuiltinCommand("/model gpt-4o")?.name).toBe("model");
  });

  it("大小写不敏感", () => {
    expect(findBuiltinCommand("/HELP")?.name).toBe("help");
    expect(findBuiltinCommand("/Clear")?.name).toBe("clear");
  });

  it("未知命令返回 undefined", () => {
    expect(findBuiltinCommand("/foo")).toBeUndefined();
    expect(findBuiltinCommand("/unknowncmd")).toBeUndefined();
  });

  it("findBuiltinCommand 是无前缀的识别器 —— 不强制要求 / 前缀", () => {
    // ChatView::handleSend 先用 startsWith("/") 判定"是否走命令路径"，
    // 再调 findBuiltinCommand 从纯命令名段识别；所以这里不带 / 也能匹配。
    expect(findBuiltinCommand("help")?.name).toBe("help");
    expect(findBuiltinCommand("clear")?.name).toBe("clear");
  });

  it("空字符串返回 undefined", () => {
    expect(findBuiltinCommand("")).toBeUndefined();
    expect(findBuiltinCommand("/")).toBeUndefined();
  });
});

describe("buildCommandList", () => {
  it("空 query 返回全部内置命令 + 全部技能", () => {
    const list = buildCommandList("", [
      { name: "translate", description: "翻译文本" },
      { name: "summarize", description: "摘要" },
    ]);
    // 8 个内置命令 + 2 个技能
    expect(list.length).toBe(BUILTIN_COMMANDS.length + 2);
    // 内置命令排在前面
    expect(list[0].kind).toBe("builtin");
    expect(list[BUILTIN_COMMANDS.length].kind).toBe("skill");
  });

  it("按 query 过滤内置命令（按主名）", () => {
    const list = buildCommandList("hel", []);
    expect(list.some((e) => e.title === "help" && e.kind === "builtin")).toBe(true);
    expect(list.some((e) => e.title === "settings")).toBe(false);
  });

  it("按 query 过滤内置命令（按别名）", () => {
    const list = buildCommandList("about", []);
    expect(list.some((e) => e.title === "version" && e.kind === "builtin")).toBe(true);
  });

  it("按 query 过滤技能", () => {
    const list = buildCommandList("trans", [
      { name: "translate", description: "翻译文本" },
      { name: "summarize", description: "摘要" },
    ]);
    const skillEntries = list.filter((e) => e.kind === "skill");
    expect(skillEntries).toHaveLength(1);
    expect(skillEntries[0].title).toBe("translate");
  });

  it("内置命令 insert 含 / 前缀，技能 insert 不含", () => {
    const list = buildCommandList("", [{ name: "translate", description: "" }]);
    const builtin = list.find((e) => e.kind === "builtin");
    const skill = list.find((e) => e.kind === "skill");
    expect(builtin?.insert.startsWith("/")).toBe(true);
    expect(skill?.insert.startsWith("/")).toBe(false);
    expect(skill?.insert).toBe("translate ");
  });

  it("查询无匹配时返回空数组", () => {
    const list = buildCommandList("zzz_no_match_zzz", [
      { name: "translate", description: "" },
    ]);
    expect(list).toEqual([]);
  });

  it("内置命令按名称升序排序", () => {
    const list = buildCommandList("", []);
    const builtinTitles = list
      .filter((e) => e.kind === "builtin")
      .map((e) => e.title);
    const sorted = [...builtinTitles].sort((a, b) => a.localeCompare(b));
    expect(builtinTitles).toEqual(sorted);
  });

  it("区分大小写查询以小写 query 为准", () => {
    const list = buildCommandList("CL", []);
    expect(list.some((e) => e.title === "clear")).toBe(true);
  });
});

describe("useCommandPickerStore", () => {
  beforeEach(() => {
    useCommandPickerStore.setState({
      open: false,
      query: "",
      anchor: null,
      activeIndex: 0,
    });
  });

  it("初始状态", () => {
    const s = useCommandPickerStore.getState();
    expect(s.open).toBe(false);
    expect(s.query).toBe("");
    expect(s.anchor).toBeNull();
    expect(s.activeIndex).toBe(0);
  });

  it("setQuery 同时重置 activeIndex", () => {
    useCommandPickerStore.setState({ activeIndex: 5 });
    useCommandPickerStore.getState().setQuery("foo");
    expect(useCommandPickerStore.getState().activeIndex).toBe(0);
    expect(useCommandPickerStore.getState().query).toBe("foo");
  });

  it("reset 清空所有状态", () => {
    useCommandPickerStore.setState({
      open: true,
      query: "x",
      anchor: 3,
      activeIndex: 2,
    });
    useCommandPickerStore.getState().reset();
    const s = useCommandPickerStore.getState();
    expect(s.open).toBe(false);
    expect(s.query).toBe("");
    expect(s.anchor).toBeNull();
    expect(s.activeIndex).toBe(0);
  });

  it("setOpen / setAnchor / setActiveIndex 单独工作", () => {
    useCommandPickerStore.getState().setOpen(true);
    useCommandPickerStore.getState().setAnchor(5);
    useCommandPickerStore.getState().setActiveIndex(3);
    const s = useCommandPickerStore.getState();
    expect(s.open).toBe(true);
    expect(s.anchor).toBe(5);
    expect(s.activeIndex).toBe(3);
  });
});