import { useEffect, useMemo, useRef, useState } from "react";
import {
  Pause,
  Play,
  Paperclip,
  Folder,
  FolderPlus,
  X,
  Send,
  Sparkles,
} from "lucide-react";
import { CommandPicker } from "./CommandPicker";
import { MentionPicker } from "./MentionPicker";
import { ContextUsage } from "./ContextUsage";
import { useFixedTextarea } from "@/hooks/useFixedTextarea";
import {
  buildCommandList,
  useCommandPickerStore,
  type CommandEntry,
} from "@/stores/commands";
import { useMentionPickerStore } from "@/stores/mention";
import { useAgentModeStore } from "@/stores/agentMode";
import { useSkillsStore } from "@/stores/skills";
import { useChatStore } from "@/stores/chat";
import type { MentionableAgent } from "@/lib/api/agents";
import { PermissionToggle } from "./PermissionToggle";
import { ModelToggle } from "./ModelToggle";
import { ModeToggle } from "./ModeToggle";
import { openFile, openFolder, saveDroppedFile } from "@/lib/api/dialog";
import { initProjectConfig } from "@/lib/api/projectConfig";
import { logger } from "@/lib/logger";

/**
 * 输入区 + 拖拽 + 命令面板（内置命令 + 技能）+ @mention 委派面板。
 *
 * 命令面板状态由 stores/commands.ts::useCommandPickerStore 持有：
 * - open / anchor / query / activeIndex
 * @mention 面板状态由 stores/mention.ts::useMentionPickerStore 持有（同构）：
 * - 仅 work 模式下触发；coding/coding_team 模式下 @ 无意义
 * ChatComposer 只负责：检测 / 或 @ 触发、键盘导航、关闭、把选中项回填输入框。
 * 内置命令的"执行"由 ChatView 接管（onSend 收到完整文本后再分发）。
 */
export function ChatComposer({
  isStreaming,
  isPaused,
  setDropError,
  onSend,
  onPause,
  onResume,
}: {
  isStreaming: boolean;
  isPaused: boolean;
  setDropError: (msg: string | null) => void;
  onSend: (content: string) => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const [input, setInput] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const currentId = useChatStore((s) => s.currentId);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const moveSessionToWorkspace = useChatStore((s) => s.moveSessionToWorkspace);
  const setSessionPermissionMode = useChatStore((s) => s.setSessionPermissionMode);
  // workspace 路径跟随当前会话绑定，而非本地 state，
  // 这样切换会话能正确切换 workspace；store 会持久化到 localStorage
  // Home 会话（workspacePath=null）回退到 homeWorkspacePath，与 useContextFiles 同源，
  // 保证技能拉取 / 上下文附件看到的工作区一致。
  const workspacePath = currentSession?.workspacePath ?? homeWorkspacePath ?? null;
  const permissionMode = currentSession?.permissionMode ?? "standard";
  const showWorkspaceChip = Boolean(workspacePath);
  const { textareaRef, textareaHeight } = useFixedTextarea();

  // 切会话时重置本地输入与全局 picker 状态，并把焦点拉回 textarea。
  //
  // 根因（用户报告"有时候点击会话，输入框会失灵"）：
  // 1. ChatComposer 不随会话切换重挂载（同级组件，无 key）；
  // 2. `input` 是 useState 局部态，跨会话残留半截草稿；
  // 3. useCommandPickerStore 是全局单例，picker open/anchor/query 跨会话残留；
  // 4. 残留 picker 会让 handleKeyDown 把 Enter 当作"选中第一项"，
  //    同时 handleChange 进入 syncQueryFromInput 错误分支，
  //    整体表现为"按了不响应 / 文本被吃掉"。
  // 5. 用户点 SessionList 后焦点离开 textarea，必须主动 .focus() 拉回。
  useEffect(() => {
    setInput("");
    resetPicker();
    resetMentionPicker();
    // 延迟 focus，避免与 SessionList 的 confirm/blur 或 SettingsModal 的焦点恢复竞争
    const t = window.setTimeout(() => {
      if (!document.querySelector('[aria-modal="true"]')) {
        textareaRef.current?.focus();
      }
    }, 0);
    // 依赖 currentId：会话变化时上述全部副作用触发一次。
    // 故意不复位 isStreaming/dragOver：流式状态由父组件控制，
    // 拖拽状态由用户当前手势决定，不应被切会话擦掉。
    // 权限模式已下沉为会话级字段（session.permissionMode），切会话自动跟随。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => window.clearTimeout(t);
  }, [currentId]);

  // SSE 流结束后自动恢复焦点，让用户可以继续输入（无需手动点击）
  useEffect(() => {
    if (!isStreaming) {
      // 延迟 focus，避免与 modal/overlay 的焦点恢复竞争
      const t = window.setTimeout(() => {
        if (!document.querySelector('[aria-modal="true"]')) {
          textareaRef.current?.focus();
        }
      }, 0);
      return () => window.clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming]);

  const skills = useSkillsStore((s) => s.skills);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  // workspacePath 变化时重新拉取技能列表，让 “/” 选择器 / 上下文面板
  // 始终能看到当前工作区合并后的技能（全局 + .agentx/skills/）。
  // store 内部会用 workspacePath 做 dedup，相同路径不重复拉。
  useEffect(() => {
    void fetchSkills(workspacePath).catch((err) => {
      logger.warn("ChatComposer: fetchSkills failed", err);
    });
  }, [workspacePath, fetchSkills]);

  const pickerOpen = useCommandPickerStore((s) => s.open);
  const setPickerOpen = useCommandPickerStore((s) => s.setOpen);
  const setAnchor = useCommandPickerStore((s) => s.setAnchor);
  const setQuery = useCommandPickerStore((s) => s.setQuery);
  const activeIndex = useCommandPickerStore((s) => s.activeIndex);
  const setActiveIndex = useCommandPickerStore((s) => s.setActiveIndex);
  const resetPicker = useCommandPickerStore((s) => s.reset);

  // @mention 面板状态（与命令面板同构，仅 work 模式下触发）
  const agentMode = useAgentModeStore((s) => s.mode);
  const mentionOpen = useMentionPickerStore((s) => s.open);
  const setMentionOpen = useMentionPickerStore((s) => s.setOpen);
  const setMentionAnchor = useMentionPickerStore((s) => s.setAnchor);
  const setMentionQuery = useMentionPickerStore((s) => s.setQuery);
  const mentionActiveIndex = useMentionPickerStore((s) => s.activeIndex);
  const setMentionActiveIndex = useMentionPickerStore((s) => s.setActiveIndex);
  const resetMentionPicker = useMentionPickerStore((s) => s.reset);
  const mentionQuery = useMentionPickerStore((s) => s.query);
  const mentionAgents = useMentionPickerStore((s) => s.agents);

  // 同步打开状态：当面板关闭时清空 query/anchor，避免残留影响下一次触发
  const handleClosePicker = () => {
    resetPicker();
  };

  const handleCloseMentionPicker = () => {
    resetMentionPicker();
  };

  // 把 `/` 到当前光标的子串作为 query 写入 store。
  // 例如当前输入 "/set" 时，query = "set"。
  const syncQueryFromInput = (val: string, anchor: number) => {
    const slice = val.slice(anchor + 1);
    // 若 slice 中出现空白或换行，说明已经退出命令模式，关闭面板
    if (/\s/.test(slice) || slice.includes("\n")) {
      setPickerOpen(false);
      setQuery("");
      setAnchor(null);
    } else {
      setQuery(slice);
    }
  };

  // 把 `@` 到当前光标的子串作为 query 写入 mention store（与命令面板同构）。
  const syncMentionQueryFromInput = (val: string, anchor: number) => {
    const slice = val.slice(anchor + 1);
    if (/\s/.test(slice) || slice.includes("\n")) {
      setMentionOpen(false);
      setMentionQuery("");
      setMentionAnchor(null);
    } else {
      setMentionQuery(slice);
    }
  };

  // 检测输入末尾的 `@`（仅 work 模式）或 `/`，处于行首或紧跟空白 → 打开对应面板。
  // 两个面板互斥：打开一个时关闭另一个，避免 @ 和 / 互相干扰。
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);

    const lastChar = val.length > 0 ? val[val.length - 1] ?? "" : "";
    const prevChar = val.length >= 2 ? val[val.length - 2] ?? "" : "";
    const atBoundary = prevChar === "" || /\s/.test(prevChar);

    // 检测 `@` — 仅 work 模式，行首或紧跟空白后
    if (lastChar === "@" && atBoundary && agentMode === "work") {
      resetPicker();
      const anchor = val.length - 1;
      setMentionAnchor(anchor);
      setMentionQuery("");
      setMentionOpen(true);
      return;
    }

    // 检测 `/` — 行首或紧跟空白后（原有逻辑）
    if (lastChar === "/" && atBoundary) {
      resetMentionPicker();
      const anchor = val.length - 1;
      setAnchor(anchor);
      setQuery("");
      setPickerOpen(true);
      return;
    }

    // 同步 query（同一时刻最多一个面板打开）
    if (pickerOpen) {
      const anchor = useCommandPickerStore.getState().anchor;
      if (anchor !== null && anchor < val.length) {
        syncQueryFromInput(val, anchor);
      }
    } else if (mentionOpen) {
      const anchor = useMentionPickerStore.getState().anchor;
      if (anchor !== null && anchor < val.length) {
        syncMentionQueryFromInput(val, anchor);
      }
    }
  };

  // 选中条目后，把 entry.insert 替换到 anchor 位置；用户继续输入参数或回车
  const handleEntrySelect = (entry: CommandEntry) => {
    const anchor = useCommandPickerStore.getState().anchor;
    setInput((s) => {
      if (anchor === null || anchor >= s.length) {
        return `${s}${entry.insert}`;
      }
      // 替换从 anchor（含 /）开始到当前光标的整段子串
      const tail = s.slice(anchor + 1);
      const tailEnd = /\s/.test(tail) ? anchor + 1 + tail.search(/\s/) : s.length;
      return `${s.slice(0, anchor)}${entry.insert}${s.slice(tailEnd)}`;
    });
    resetPicker();
    textareaRef.current?.focus();
  };

  // 选中 mention agent 后，把 `@key ` 替换到 anchor 位置（与 handleEntrySelect 同构）
  const handleMentionSelect = (agent: MentionableAgent) => {
    const anchor = useMentionPickerStore.getState().anchor;
    setInput((s) => {
      if (anchor === null || anchor >= s.length) {
        return `${s}@${agent.key} `;
      }
      const tail = s.slice(anchor + 1);
      const tailEnd = /\s/.test(tail) ? anchor + 1 + tail.search(/\s/) : s.length;
      return `${s.slice(0, anchor)}@${agent.key} ${s.slice(tailEnd)}`;
    });
    resetMentionPicker();
    textareaRef.current?.focus();
  };

  const entries = buildCommandList(
    useCommandPickerStore.getState().query,
    skills,
  );

  // mention 面板过滤列表（与 MentionPicker 内部过滤逻辑一致，供键盘导航使用）
  const mentionEntries = useMemo(() => {
    const q = mentionQuery.trim().toLowerCase();
    if (!q) return mentionAgents;
    return mentionAgents.filter(
      (a) =>
        a.key.toLowerCase().includes(q) ||
        a.display_name.toLowerCase().includes(q),
    );
  }, [mentionQuery, mentionAgents]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 命令面板打开时拦截 ↑↓ Enter Esc，避免破坏 textarea 默认行为
    if (pickerOpen && entries.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((activeIndex + 1) % entries.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((activeIndex - 1 + entries.length) % entries.length);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const entry = entries[activeIndex];
        if (entry) handleEntrySelect(entry);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        handleClosePicker();
        return;
      }
    }

    // @mention 面板打开时拦截 ↑↓ Enter Esc（与命令面板互斥，同一时刻仅一个打开）
    if (mentionOpen && mentionEntries.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionActiveIndex((mentionActiveIndex + 1) % mentionEntries.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionActiveIndex(
          (mentionActiveIndex - 1 + mentionEntries.length) % mentionEntries.length,
        );
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const agent = mentionEntries[mentionActiveIndex];
        if (agent) handleMentionSelect(agent);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        handleCloseMentionPicker();
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    } else if (
      e.key === "ArrowUp" &&
      input.trim() === "" &&
      !isStreaming &&
      !pickerOpen &&
      !mentionOpen
    ) {
      // ↑ 键快速编辑上一条用户消息（参考 Cursor）
      e.preventDefault();
      const msgs = currentSession?.messages ?? [];
      const lastUser = [...msgs].reverse().find((m) => m.role === "user");
      if (lastUser) {
        // 从 parts 中的 text parts 派生文本（content 兼容字段已移除）
        const lastUserText = lastUser.parts
          .filter((p) => p.type === "text")
          .map((p) => (p.type === "text" ? p.text : ""))
          .join("");
        // 剥离 <workspace> 和 <file> 标签，与 handleSend 的 rawQuery 处理对齐
        const text = lastUserText
          .replace(/<workspace>.*?<\/workspace>\s?/g, "")
          .replace(/<file>.*?<\/file>\s?/g, "")
          .trim();
        setInput(text);
        // 删除上一条用户消息及之后的所有消息（因为即将重新发送）
        const idx = msgs.findIndex((m) => m.id === lastUser.id);
        if (idx !== -1) {
          useChatStore.getState().deleteMessagesAfter(lastUser.id);
        }
        setTimeout(() => {
          textareaRef.current?.focus();
          textareaRef.current?.select();
        }, 0);
      }
    } else if (e.key === "Escape" && pickerOpen) {
      handleClosePicker();
    } else if (e.key === "Escape" && mentionOpen) {
      handleCloseMentionPicker();
    }
  };

  const handleSubmit = () => {
    const content = input.trim();
    if (!content || isStreaming) return;
    onSend(content);
    setInput("");
    handleClosePicker();
    handleCloseMentionPicker();
  };

  const openCommandPickerManually = () => {
    setInput((s) => {
      const next = s.endsWith("/") ? s : `${s}/`;
      const prev = next.length >= 2 ? next[next.length - 2] ?? "" : "";
      if (prev === "" || /\s/.test(prev)) {
        const anchor = next.length - 1;
        setAnchor(anchor);
        setQuery("");
        setPickerOpen(true);
      }
      return next;
    });
    textareaRef.current?.focus();
  };

  // 选择 workspace 目录：弹出 OS 目录选择器，授权当前 thread 为可写 workspace，
  // 并把路径写回当前会话的 workspacePath（store 持久化）；
  // 提交消息时再以 <workspace> 标记拼到内容前面发给后端，让 LLM 看到当前工作目录。
  // 若当前无 thread，则先创建会话；授权失败走 dropError 通道统一展示。
  const handleAttachWorkspace = async () => {
    setDropError(null);
    const result = (await openFolder()) as
      | { canceled?: boolean; filePaths?: string[] }
      | undefined;
    if (!result || result.canceled || !result.filePaths || result.filePaths.length === 0) {
      return;
    }
    // 上面 length === 0 已 return，这里 [0] 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
    const dirPath = result.filePaths[0]!;
    const store = useChatStore.getState();
    let tid = store.currentId;
    if (!tid) {
      // 没有当前会话：创建并绑定到这个新 workspace（需求允许"workspace 侧新建"）
      // createSession 内部已 await 隐式授权
      tid = await store.createSession(dirPath);
    } else {
      // 把当前会话迁到新 workspace（持久化），内部已 await 隐式授权
      await store.moveSessionToWorkspace(tid, dirPath);
    }
    try {
      // 显式再授权一次用于错误提示（幂等）
      await useChatStore.getState().authorizeAndUnmark(tid, dirPath, true);
    } catch (err) {
      setDropError(
        `授权目录「${dirPath}」失败：${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
    // best-effort：授权成功后静默生成 .agentx/ 项目级配置目录。
    // 失败不阻塞工作区绑定，仅记录告警。
    // tid 来自上方 createSession / currentId，必为非空 string。
    try {
      await initProjectConfig(dirPath, tid!);
    } catch (err) {
      logger.warn("initProjectConfig failed", err);
    }
  };

  // 移除/切换 workspace chip：把当前会话迁回 Home（workspacePath=null）
  const handleRemoveWorkspace = async () => {
    const tid = useChatStore.getState().currentId;
    if (!tid) return;
    await useChatStore.getState().moveSessionToWorkspace(tid, null);
  };

  // 工作区 chip 的 tooltip：展示完整路径，Home 时附带桌面目录（来自 store）
  const workspaceChipTitle = workspacePath ?? homeWorkspacePath ?? "Home";

  // 点击 @ 按钮 → 弹出 OS 文件选择器，选中后以 <file>rel</file> 形式追加到末尾。
  // 支持多选，错误信息走现有 dropError 通道统一展示。
  const handleAttachFile = async () => {
    setDropError(null);
    try {
      const result = (await openFile({
        properties: ["openFile", "multiSelections"],
      })) as { canceled?: boolean; filePaths?: string[] } | undefined;
      const filePaths = result?.filePaths ?? [];
      if (!result || result.canceled || filePaths.length === 0) return;
      const fileNames = filePaths.map((p) => p.split(/[\\/]/).pop() ?? p);
      for (let i = 0; i < filePaths.length; i++) {
        // 循环边界 i < filePaths.length === fileNames.length；用 ! 抑制 noUncheckedIndexedAccess。
        const filePath = filePaths[i]!;
        const fileName = fileNames[i]!;
        try {
          const relPath = await saveDroppedFile(filePath, fileName);
          setInput((s) => `${s}<file>${relPath}</file> `);
        } catch (err) {
          setDropError(
            `文件「${fileName}」保存失败：${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    } catch (err) {
      setDropError(
        `文件选择失败：${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  // 文件拖拽 —— 把拖入的文件交给主进程保存，返回相对路径后以 <file> 标记追加
  // 事件挂在 .chat-composer 容器上（不再是 textarea），保证整个输入框都可接收拖入
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => setDragOver(false);

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;
    setDropError(null);
    for (const file of files) {
      try {
        const filePath = (file as File & { path: string }).path;
        const relPath = await saveDroppedFile(filePath, file.name);
        setInput((s) => `${s}<file>${relPath}</file> `);
      } catch (err) {
        setDropError(
          `文件「${file.name}」保存失败：${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  };

  const canSend = input.trim().length > 0 && !isStreaming && !isPaused;

  // 检测输入中的 /skill: 标记，用于 UI 指示
  const activeSkills = useMemo(() => {
    const matches: string[] = [];
    const regex = /\/skill:([^\s/]+)/g;
    let match;
    while ((match = regex.exec(input)) !== null) {
      matches.push(match[1]);
    }
    return matches;
  }, [input]);

  return (
    <div className="bg-surface px-3 py-2">
      <div className="mx-auto max-w-3xl">
        <div
          className={`chat-composer relative px-3 pb-1.5 pt-2 ${
            dragOver ? "is-drop-target" : ""
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {pickerOpen && (
            <CommandPicker
              onSelect={handleEntrySelect}
              onClose={handleClosePicker}
              workspacePath={workspacePath}
            />
          )}

          {mentionOpen && (
            <MentionPicker
              onSelect={handleMentionSelect}
              onClose={handleCloseMentionPicker}
            />
          )}

          <div className="relative">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              rows={2}
              placeholder="输入消息，/ 调命令与技能，@ 委派 agent（work 模式），拖拽附文件"
              aria-label="消息输入框"
              className="input-borderless relative z-20 block w-full resize-none overflow-y-auto pb-2"
              style={{ height: `${textareaHeight}px` }}
            />

            {/* 技能激活指示器：当输入包含 /skill: 标记时显示 */}
            {activeSkills.length > 0 && (
              <div className="absolute bottom-0 left-0 z-30 flex items-center gap-1.5 px-0 py-1">
                {activeSkills.map((name) => (
                  <span
                    key={name}
                    className="inline-flex items-center gap-1 rounded-md border border-accent-500/30 bg-accent-500/10 px-1.5 py-0.5 font-medium text-accent-500"
                    style={{ fontSize: 'var(--fs-composer-chip)' }}
                  >
                    <Sparkles className="h-3 w-3" />
                    {name}
                  </span>
                ))}
              </div>
            )}

            {/* 底部 Toolbar：左 = workspace，右 = context + Model + Permission + Send */}
            <div className="mt-1.5 flex items-center justify-between gap-1.5 pt-1.5">
              {/* LEFT — 模式切换 + workspace（[/] [@] 已在 M2 清理中删除，可继续以输入 "/" / 拖拽文件取代） */}
              <div className="flex items-center gap-1">
                <ModeToggle />
                {showWorkspaceChip && workspacePath ? (
                  <span
                    className="group/ws inline-flex max-w-[180px] items-center gap-1.5 rounded-lg border border-brand-500/25 bg-brand-600/10 pl-2 pr-1.5 py-1 font-medium text-brand-500 transition-colors hover:bg-brand-600/15"
                    style={{ fontSize: 'var(--fs-composer-chip)' }}
                    title={workspaceChipTitle}
                  >
                    <Folder
                      className="h-3 w-3 shrink-0 text-brand-500/80"
                      aria-hidden="true"
                    />
                    <span className="max-w-[88px] truncate">
                      {workspacePath.split(/[\\/]/).pop() || workspacePath}
                    </span>
                    <button
                      type="button"
                      className="ml-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-brand-500/70 transition-colors hover:bg-brand-500/20 hover:text-brand-500"
                      onClick={handleRemoveWorkspace}
                      title="迁回 Home"
                      aria-label="迁回 Home"
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                    <button
                      type="button"
                      className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-brand-500/70 transition-colors hover:bg-brand-500/20 hover:text-brand-500"
                      onClick={() => void handleAttachWorkspace()}
                      title="更换 workspace"
                      aria-label="更换 workspace"
                    >
                      <FolderPlus className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn-icon"
                    onClick={() => void handleAttachWorkspace()}
                    title={
                      homeWorkspacePath
                        ? `选择 workspace（Home = ${homeWorkspacePath}）`
                        : "选择 workspace 目录"
                    }
                    aria-label="选择 workspace 目录"
                  >
                    <FolderPlus className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              {/* SPACER */}
              <div className="flex-1" />

              {/* RIGHT — 状态 + 动作：ContextUsage → Model → Permission → Send */}
              <div className="flex items-center gap-1.5">
                <ContextUsage />
                <div className="flex items-center">
                  <ModelToggle />
                </div>
                <div className="flex items-center">
                  <PermissionToggle
                    workspacePath={workspacePath}
                    homeWorkspacePath={homeWorkspacePath}
                    mode={permissionMode}
                    onChange={(mode) => {
                      const sid = useChatStore.getState().currentId;
                      if (sid) setSessionPermissionMode(sid, mode);
                    }}
                  />
                </div>
                {isPaused ? (
                  <button
                    type="button"
                    onClick={onResume}
                    className="btn-send"
                    aria-label="继续生成"
                    title="继续"
                  >
                    <Play className="h-3 w-3 fill-current" />
                  </button>
                ) : isStreaming ? (
                  <button
                    type="button"
                    onClick={onPause}
                    className="btn-send is-stop"
                    aria-label="暂停生成"
                    title="暂停"
                  >
                    <Pause className="h-2.5 w-2.5 fill-current" />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={!canSend}
                    className="btn-send"
                    aria-label={isPaused ? "发送已禁用（会话已暂停）" : "发送消息"}
                    title={isPaused ? "当前会话已暂停，请先点上方「继续」按钮" : "发送 (Enter)"}
                  >
                    <Send className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
          </div>

          {dragOver && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[inherit] bg-brand-500/5 font-medium text-brand-500" style={{ fontSize: 'var(--fs-composer-input)' }}>
              <Paperclip className="mr-1.5 h-3.5 w-3.5" />
              释放以附加文件
            </div>
          )}
        </div>
      </div>
    </div>
  );
}