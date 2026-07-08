import { create } from "zustand";
import {
  getMentionableAgents,
  type MentionableAgent,
} from "@/lib/api/agents";

/**
 * @mention 选择面板状态（参考 useCommandPickerStore）。
 *
 * 与命令面板的区别：
 * - 仅在 work 模式下触发（ChatComposer 检测 mode === "work"）
 * - 缓存 getMentionableAgents() 结果，供 MentionPicker 渲染和 ChatView 解析复用
 * - 选中后把 `@key ` 插入到 anchor 位置
 *
 * 状态流转：
 * 1. 用户输入 `@`（行首或紧跟空白）→ ChatComposer 打开 picker，记录 anchor
 * 2. 用户继续输入 → syncMentionQuery 同步 query；出现空白则关闭
 * 3. 键盘 ↑↓ 导航 / Enter 选中 / Esc 关闭（监听在 ChatComposer）
 * 4. 选中 → ChatComposer 把 `@key ` 替换到 anchor 位置，关闭 picker
 */
interface MentionPickerState {
  open: boolean;
  query: string;
  /** `@` 在输入框中的字符索引（替换锚点） */
  anchor: number | null;
  /** 当前选中项索引（键盘上下移动） */
  activeIndex: number;
  /** 缓存的 mentionable agent 列表（MentionPicker 挂载时拉取） */
  agents: MentionableAgent[];
  loading: boolean;
  error: string | null;
  setOpen: (v: boolean) => void;
  setQuery: (q: string) => void;
  setAnchor: (n: number | null) => void;
  setActiveIndex: (n: number) => void;
  fetchAgents: () => Promise<void>;
  reset: () => void;
}

export const useMentionPickerStore = create<MentionPickerState>()((set, get) => ({
  open: false,
  query: "",
  anchor: null,
  activeIndex: 0,
  agents: [],
  loading: false,
  error: null,
  setOpen: (v) => set({ open: v }),
  setQuery: (q) => set({ query: q, activeIndex: 0 }),
  setAnchor: (n) => set({ anchor: n }),
  setActiveIndex: (n) => set({ activeIndex: n }),
  fetchAgents: async () => {
    // 已有缓存且非 loading，跳过重复请求
    if (get().agents.length > 0 || get().loading) return;
    set({ loading: true, error: null });
    try {
      const agents = await getMentionableAgents();
      set({ agents, loading: false });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  reset: () => set({ open: false, query: "", anchor: null, activeIndex: 0 }),
}));
