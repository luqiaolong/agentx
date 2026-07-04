import { useRef, useState } from "react";
import { ArrowUp, Square, Paperclip, Slash, AtSign } from "lucide-react";
import { SkillPicker } from "./SkillPicker";
import { useAutoResizeTextarea } from "@/hooks/useAutoResizeTextarea";

/**
 * 输入区 + 拖拽 + 技能触发。
 * 拥有输入文本、拖拽、技能选择器、textarea 撑高等局部状态；
 * 通过 onSend/onAbort 把发送/中止交给 ChatView 协调 store 与 SSE。
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
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const skillAnchorRef = useRef<number | null>(null);
  const { textareaRef, textareaHeight } = useAutoResizeTextarea(input);

  const handleSubmit = () => {
    const content = input.trim();
    if (!content || isStreaming) return;
    onSend(content);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    // Esc 关闭技能选择器，避免遮挡视线
    if (e.key === "Escape" && skillPickerOpen) {
      setSkillPickerOpen(false);
    }
  };

  // 输入末尾为 `/` 且整串仍处于命令模式（行首 / 紧跟空白）时，打开技能选择器，
  // 锚点记录 `/` 位置以便回填。仅插入"技能名称"本身，不带前缀（与 /reset /clear 风格区分）。
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);
    if (val.endsWith("/")) {
      const prev = val.length >= 2 ? val[val.length - 2] : "";
      if (prev === "" || /\s/.test(prev)) {
        skillAnchorRef.current = val.length - 1;
        setSkillPickerOpen(true);
      }
    }
  };

  const handleSkillSelect = (name: string) => {
    const pos = skillAnchorRef.current;
    // 仅插入技能名称 + 空格，与 /reset /clear 等带斜杠的命令视觉区分
    const replacement = `${name} `;
    if (pos === null) {
      setInput((s) => `${s}${replacement}`);
    } else {
      setInput((s) => `${s.slice(0, pos)}${replacement}${s.slice(pos + 1)}`);
    }
    skillAnchorRef.current = null;
    setSkillPickerOpen(false);
    textareaRef.current?.focus();
  };

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
        const filePath = filePaths[i];
        const fileName = fileNames[i];
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
          {skillPickerOpen && (
            <SkillPicker
              onSelect={handleSkillSelect}
              onClose={() => setSkillPickerOpen(false)}
            />
          )}

          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="输入消息，或 / 调技能，@ 附文件"
            aria-label="消息输入框"
            className="input-borderless block max-h-40 min-h-[1.5rem] w-full pr-1"
            style={{ height: `${textareaHeight}px` }}
          />

          <div className="mt-1.5 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1 text-[11px] text-muted-c">
              <button
                type="button"
                className="btn-icon"
                onClick={() => {
                  setInput((s) => {
                    const next = s.endsWith("/") ? s : `${s}/`;
                    // 仅在行首或空白后触发命令模式
                    const prev = next.length >= 2 ? next[next.length - 2] : "";
                    if (prev === "" || /\s/.test(prev)) {
                      skillAnchorRef.current = next.length - 1;
                      setSkillPickerOpen(true);
                    }
                    return next;
                  });
                  textareaRef.current?.focus();
                }}
                title="调用技能 (/ 命令)"
                aria-label="调用技能"
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
              <span className="hidden sm:inline">
                / 调用技能 · @ 附加文件 · 拖入文件也支持
              </span>
            </div>
            <div className="flex items-center gap-1.5">
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
