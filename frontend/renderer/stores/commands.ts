import { create } from "zustand";

/**
 * 内置斜杠命令注册表。
 *
 * 与"技能（skills）"的区别：
 * - 技能 = 后端加载的 .md 文件，最终通过 LLM 调用触发（无副作用或纯 LLM 行为）。
 * - 内置命令 = 前端本地操作，不经过 LLM，直接修改 UI/会话/状态。
 *
 * 注册表在 `BUILTIN_COMMANDS` 中静态声明；执行器由 ChatView 通过 `runCommand` 分发。
 * CommandPicker 同时展示内置命令和已加载技能，按用户当前已输入文本过滤。
 */

export type CommandScope = "chat" | "session" | "app" | "settings";

export interface BuiltinCommand {
  /** 命令主名（不含 /），如 "clear" 对应 "/clear" */
  name: string;
  /** 别名，触发命令时与主名等效 */
  aliases?: string[];
  /** 一句话描述，UI 上展示 */
  description: string;
  /** 详细说明，/help 列表使用 */
  detail?: string;
  /** 命令分类（UI 排序用） */
  scope: CommandScope;
  /** 是否需要参数（如 /model openai/gpt-4o） */
  takesArgument?: boolean;
  /** 参数占位符说明 */
  argumentHint?: string;
  /** UI 上显示的图标组件（lucide） */
  iconKey: BuiltinCommandIcon;
}

/** 用字符串键引用 lucide 图标，避免循环依赖 */
export type BuiltinCommandIcon =
  | "trash"
  | "refresh"
  | "plus"
  | "settings"
  | "sun"
  | "help"
  | "info"
  | "sparkles"
  | "wrench";

/**
 * 内置命令清单。
 *
 * 添加新命令时：
 *   1. 在此处新增一项
 *   2. 在 ChatView::runBuiltinCommand 中分发执行
 *   3. 如需参数校验，在分发器中完成
 */
export const BUILTIN_COMMANDS: BuiltinCommand[] = [
  {
    name: "help",
    aliases: ["?", "commands"],
    description: "查看可用命令与技能",
    detail: "列出所有内置命令和已加载的技能。",
    scope: "app",
    iconKey: "help",
  },
  {
    name: "clear",
    aliases: ["reset", "new"],
    description: "清空当前会话消息",
    detail: "调后端重置 checkpointer + 沙箱，再清空当前会话消息（保留会话条目）。",
    scope: "chat",
    iconKey: "trash",
  },
  {
    name: "compact",
    description: "压缩当前会话（保留最近 N 条）",
    detail: "前端本地操作，仅移除当前会话的早期消息以释放上下文窗口（占位实现）。",
    scope: "chat",
    iconKey: "sparkles",
  },
  {
    name: "settings",
    aliases: ["config", "preferences"],
    description: "打开设置面板",
    detail: "在前台弹出设置弹窗。",
    scope: "settings",
    iconKey: "settings",
  },
  {
    name: "theme",
    aliases: ["dark", "light"],
    description: "切换深色 / 浅色主题",
    detail: "无参数时切换主题；带 dark|light 参数时强制指定。",
    scope: "app",
    takesArgument: true,
    argumentHint: "dark | light",
    iconKey: "sun",
  },
  {
    name: "model",
    description: "切换当前 LLM 模型",
    detail: "无参数时打开设置中的模型条目面板；带参数时按 id 或 label 匹配并激活（重启后端后生效）。",
    scope: "settings",
    takesArgument: true,
    argumentHint: "<model-id | model-label>",
    iconKey: "wrench",
  },
  {
    name: "skills",
    description: "查看已加载的技能",
    detail: "调 /api/skills 重新拉取并列出技能名。",
    scope: "app",
    iconKey: "sparkles",
  },
  {
    name: "version",
    aliases: ["ver", "about"],
    description: "查看应用版本与后端状态",
    detail: "展示 renderer 版本与后端 /api/health 摘要。",
    scope: "app",
    iconKey: "info",
  },
];

/** 通过主名或别名匹配 */
export function findBuiltinCommand(raw: string): BuiltinCommand | undefined {
  const trimmed = raw.trim().replace(/^\//, "").toLowerCase();
  if (!trimmed) return undefined;
  // 拆分主命令名（首个空格前的部分）。
  // 已在 trim() 后判 !trimmed，所以 split 至少返回一项；用 ! 抑制 noUncheckedIndexedAccess 报错。
  const head = trimmed.split(/\s+/, 1)[0]!;
  return BUILTIN_COMMANDS.find(
    (c) => c.name === head || (c.aliases ?? []).includes(head),
  );
}

/**
 * 过滤内置命令 + 技能的统一列表。
 *
 * @param query 用户在 / 后面输入的文本（不含 / 本身）
 * @param skills 来自 useSkillsStore 的技能列表
 */
export interface CommandEntry {
  kind: "builtin" | "skill";
  /** 显示标题（不带 / 前缀） */
  title: string;
  /** 描述 */
  description: string;
  /** 完整插入文本：内置命令带 "/" 前缀，技能不带（与原约定一致） */
  insert: string;
  scope: CommandScope | "skill";
  iconKey: BuiltinCommandIcon;
  /** 内置命令引用，便于后续执行 */
  builtin?: BuiltinCommand;
}

export function buildCommandList(
  query: string,
  skills: { name: string; description: string }[],
): CommandEntry[] {
  const q = query.trim().toLowerCase();
  const builtinEntries: CommandEntry[] = BUILTIN_COMMANDS.filter(
    (c) =>
      !q ||
      c.name.toLowerCase().includes(q) ||
      (c.aliases ?? []).some((a) => a.toLowerCase().includes(q)) ||
      c.description.toLowerCase().includes(q),
  ).map((c) => ({
    kind: "builtin",
    title: c.name,
    description: c.description,
    insert: `/${c.name} `,
    scope: c.scope,
    iconKey: c.iconKey,
    builtin: c,
  }));

  const skillEntries: CommandEntry[] = skills
    .filter((s) => !q || s.name.toLowerCase().includes(q))
    .map((s) => ({
      kind: "skill",
      title: s.name,
      description: s.description || "已加载技能",
      insert: `${s.name} `,
      scope: "skill",
      iconKey: "sparkles" as const,
    }));

  // 内置命令排前，技能排后；同组内按名称排序
  builtinEntries.sort((a, b) => a.title.localeCompare(b.title));
  skillEntries.sort((a, b) => a.title.localeCompare(b.title));
  return [...builtinEntries, ...skillEntries];
}

// -------------------------------------------------------------
// 命令面板 UI 状态（用于 CommandPicker 在组件间共享打开/查询状态）
// -------------------------------------------------------------

interface CommandPickerState {
  open: boolean;
  query: string;
  /** `/` 在输入框中的字符索引（替换锚点） */
  anchor: number | null;
  /** 当前选中项索引（键盘上下移动） */
  activeIndex: number;
  setOpen: (v: boolean) => void;
  setQuery: (q: string) => void;
  setAnchor: (n: number | null) => void;
  setActiveIndex: (n: number) => void;
  reset: () => void;
}

export const useCommandPickerStore = create<CommandPickerState>()((set) => ({
  open: false,
  query: "",
  anchor: null,
  activeIndex: 0,
  setOpen: (v) => set({ open: v }),
  setQuery: (q) => set({ query: q, activeIndex: 0 }),
  setAnchor: (n) => set({ anchor: n }),
  setActiveIndex: (n) => set({ activeIndex: n }),
  reset: () => set({ open: false, query: "", anchor: null, activeIndex: 0 }),
}));