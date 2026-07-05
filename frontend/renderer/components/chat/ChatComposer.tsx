import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Square,
  Paperclip,
  Slash,
  AtSign,
  Folder,
  FolderPlus,
  X,
} from "lucide-react";
import { CommandPicker } from "./CommandPicker";
import { useAutoResizeTextarea } from "@/hooks/useAutoResizeTextarea";
import {
  buildCommandList,
  useCommandPickerStore,
  type CommandEntry,
} from "@/stores/commands";
import { useSkillsStore } from "@/stores/skills";
import { useChatStore } from "@/stores/chat";
import { usePermissionStore } from "@/stores/permission";
import { PermissionToggle } from "./PermissionToggle";
import { ModelToggle } from "./ModelToggle";

/**
 * 输入区 + 拖拽 + 命令面板（内置命令 + 技能）。
 *
 * 命令面板状态由 stores/commands.ts::useCommandPickerStore 持有：
 * - open / anchor / query / activeIndex
 * ChatComposer 只负责：检测 / 触发、键盘导航、关闭、把选中的 entry.insert 回填输入框。
 * 内置命令的"执行"由 ChatView 接管（onSend 收到完整文本后再分发）。
 */
export function ChatComposer({
  isStreaming,
  setDropError,
  onSend,
  onAbort,
}: {
  isStreaming: boolean;
  setDropError: (msg: string | null) => void;
  onSend: (content: string) => void;
  onAbort: () => void;
}) {
  const [input, setInput] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const currentId = useChatStore((s) => s.currentId);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const moveSessionToWorkspace = useChatStore((s) => s.moveSessionToWorkspace);
  // workspace 路径跟随当前会话绑定，而非本地 state，
  // 这样切换会话能正确切换 workspace；store 会持久化到 localStorage
  const workspacePath = currentSession?.workspacePath ?? null;
  const showWorkspaceChip = Boolean(workspacePath);
  const { textareaRef, textareaHeight } = useAutoResizeTextarea(input);

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
    // 切会话时复位权限模式：permission 是会话级状态，
    // 跨会话残留 full_trust 会导致新会话直接放行危险工具。
    usePermissionStore.getState().reset();
    textareaRef.current?.focus();
    // 依赖 currentId：会话变化时上述全部副作用触发一次。
    // 故意不复位 isStreaming/dragOver：流式状态由父组件控制，
    // 拖拽状态由用户当前手势决定，不应被切会话擦掉。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  const skills = useSkillsStore((s) => s.skills);
  const pickerOpen = useCommandPickerStore((s) => s.open);
  const setPickerOpen = useCommandPickerStore((s) => s.setOpen);
  const setAnchor = useCommandPickerStore((s) => s.setAnchor);
  const setQuery = useCommandPickerStore((s) => s.setQuery);
  const activeIndex = useCommandPickerStore((s) => s.activeIndex);
  const setActiveIndex = useCommandPickerStore((s) => s.setActiveIndex);
  const resetPicker = useCommandPickerStore((s) => s.reset);

  // 同步打开状态：当面板关闭时清空 query/anchor，避免残留影响下一次触发
  const handleClosePicker = () => {
    resetPicker();
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

  // 检测输入末尾为 `/`，且 `/` 处于行首或紧跟空白 → 打开命令面板
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);
    if (val.endsWith("/")) {
      const prev = val.length >= 2 ? val[val.length - 2] ?? "" : "";
      if (prev === "" || /\s/.test(prev)) {
        const anchor = val.length - 1;
        setAnchor(anchor);
        setQuery("");
        setPickerOpen(true);
      }
    } else if (pickerOpen) {
      const anchor = useCommandPickerStore.getState().anchor;
      if (anchor !== null && anchor < val.length) {
        syncQueryFromInput(val, anchor);
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

  const entries = buildCommandList(
    useCommandPickerStore.getState().query,
    skills,
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 面板打开时拦截 ↑↓ Enter Esc，避免破坏 textarea 默认行为
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

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === "Escape" && pickerOpen) {
      handleClosePicker();
    }
  };

  const handleSubmit = () => {
    const content = input.trim();
    if (!content || isStreaming) return;
    // 工作区标记：用户显式选了 workspace 就附上当前会话绑定的路径；
    // Home（null）情况下不附带 <workspace> 标签，让 LLM 知道当前不在特定目录下。
    const finalContent = workspacePath
      ? `<workspace>${workspacePath}</workspace> ${content}`
      : content;
    onSend(finalContent);
    setInput("");
    handleClosePicker();
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
    const result = (await window.api.dialog.openFolder()) as
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
      tid = store.createSession(dirPath);
    } else {
      // 把当前会话迁到新 workspace（持久化）
      store.moveSessionToWorkspace(tid, dirPath);
    }
    try {
      await useChatStore.getState().authorizeAndUnmark(tid, dirPath, true);
    } catch (err) {
      setDropError(
        `授权目录「${dirPath}」失败：${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
  };

  // 移除/切换 workspace chip：把当前会话迁回 Home（workspacePath=null）
  const handleRemoveWorkspace = () => {
    const tid = useChatStore.getState().currentId;
    if (!tid) return;
    useChatStore.getState().moveSessionToWorkspace(tid, null);
  };

  // 工作区 chip 的 tooltip：展示完整路径，Home 时附带桌面目录（来自 store）
  const workspaceChipTitle = workspacePath ?? homeWorkspacePath ?? "Home";

  // 点击 @ 按钮 → 弹出 OS 文件选择器，选中后以 <file>rel</file> 形式追加到末尾。
  // 支持多选，错误信息走现有 dropError 通道统一展示。
  const handleAttachFile = async () => {
    setDropError(null);
    try {
      const result = (await window.api.dialog.openFile({
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
          const relPath = await window.api.dialog.saveDroppedFile(filePath, fileName);
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
        const relPath = await window.api.dialog.saveDroppedFile(filePath, file.name);
        setInput((s) => `${s}<file>${relPath}</file> `);
      } catch (err) {
        setDropError(
          `文件「${file.name}」保存失败：${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  };

  const canSend = input.trim().length > 0 && !isStreaming;

  return (
    <div className="border-t border-default bg-surface px-4 py-3">
      <div className="mx-auto max-w-3xl">
        <div
          className={`chat-composer relative px-3 py-2.5 ${
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
            />
          )}

          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="输入消息，或 / 调命令与技能，@ 附文件，文件夹选 workspace"
            aria-label="消息输入框"
            className="input-borderless block max-h-40 min-h-[1.5rem] w-full pr-1"
            style={{ height: `${textareaHeight}px` }}
          />

          <div className="mt-1.5 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1 text-[11px] text-muted-c">
              <button
                type="button"
                className="btn-icon"
                onClick={openCommandPickerManually}
                title="调用命令或技能 (/ 命令)"
                aria-label="调用命令或技能"
              >
                <Slash className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="btn-icon"
                onClick={() => void handleAttachFile()}
                title="附加文件 (@)"
                aria-label="附加文件"
              >
                <AtSign className="h-3.5 w-3.5" />
              </button>
              {showWorkspaceChip && workspacePath ? (
                <span
                  className="group/ws inline-flex max-w-[220px] items-center gap-1 rounded-md border border-brand-500/25 bg-brand-600/10 pl-1.5 pr-1 py-0.5 text-[11px] font-medium text-brand-500 transition-colors hover:bg-brand-600/15"
                  title={workspaceChipTitle}
                >
                  <Folder
                    className="h-3 w-3 shrink-0 text-brand-500/80"
                    aria-hidden="true"
                  />
                  <span className="max-w-[120px] truncate">
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
            <div className="flex items-center gap-1.5">
              <ModelToggle />
              <PermissionToggle
                workspacePath={workspacePath}
                homeWorkspacePath={homeWorkspacePath}
              />
              {isStreaming ? (
                <button
                  type="button"
                  onClick={onAbort}
                  className="btn-send is-stop"
                  aria-label="中止生成"
                  title="中止"
                >
                  <Square className="h-3 w-3 fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleSubmit}
                  disabled={!canSend}
                  className="btn-send"
                  aria-label="发送消息"
                  title="发送 (Enter)"
                >
                  <ArrowUp className="h-3.5 w-3.5" strokeWidth={2.5} />
                </button>
              )}
            </div>
          </div>

          {dragOver && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[inherit] bg-brand-500/5 text-xs font-medium text-brand-500">
              <Paperclip className="mr-1.5 h-3.5 w-3.5" />
              释放以附加文件
            </div>
          )}
        </div>
      </div>
    </div>
  );
}