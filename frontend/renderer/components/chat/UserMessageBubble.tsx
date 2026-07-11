import { memo, useState, useCallback, useRef, useEffect } from "react";
import { Pencil, Send, Sparkles } from "lucide-react";
import type { ChatMessage } from "@/stores/chat";
import { ModelToggle } from "./ModelToggle";

/**
 * 将文本中的 /skill:name 标记渲染为特殊 pill 样式。
 */
function renderTextWithSkillTags(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const regex = /(\/skill:[^\s/]+)/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    // 匹配前的普通文本
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    // skill 标记渲染为 pill
    const skillName = match[0].slice(7); // 去掉 "/skill:" 前缀
    parts.push(
      <span
        key={match.index}
        className="inline-flex items-center gap-1 rounded-md border border-accent-500/30 bg-accent-500/15 px-1.5 py-0.5 align-text-bottom font-medium text-accent-400"
        style={{ fontSize: '0.85em' }}
      >
        <Sparkles className="h-3 w-3" />
        {skillName}
      </span>
    );
    lastIndex = regex.lastIndex;
  }

  // 剩余文本
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}

/**
 * 用户消息气泡（支持就地编辑）。
 *
 * 从 AssistantUIThread.tsx 的 MessageParts user 分支迁移（T10 拆分）。
 * 将 <workspace>path</workspace> 标签替换为美观的 chip 样式；
 * 流式期间禁止编辑；Enter 提交 / Esc 取消。
 */
export const UserMessageBubble = memo(function UserMessageBubble({
  message,
  isStreaming,
  onEditSubmit,
}: {
  message: ChatMessage;
  isStreaming: boolean;
  onEditSubmit?: (messageId: string, newContent: string) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const editRef = useRef<HTMLTextAreaElement>(null);

  // 从 parts 中的 text parts 派生纯文本（content 兼容字段已移除）
  const rawText = message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("");
  // 将 <workspace>path</workspace> 标签替换为美观的 chip 样式
  const workspaceMatch = rawText.match(/<workspace>(.*?)<\/workspace>\s?(.*)/);
  const workspacePath = workspaceMatch?.[1];
  const userText = workspaceMatch?.[2] ?? rawText;

  const handleStartEdit = useCallback(() => {
    if (isStreaming) return;
    setEditText(userText);
    setIsEditing(true);
  }, [isStreaming, userText]);

  const handleEditSubmit = useCallback(() => {
    const trimmed = editText.trim();
    if (!trimmed) return;
    // 保留 workspace 标签，替换文本内容
    const newContent = workspacePath
      ? `<workspace>${workspacePath}</workspace> ${trimmed}`
      : trimmed;
    onEditSubmit?.(message.id, newContent);
    setIsEditing(false);
  }, [editText, workspacePath, message.id, onEditSubmit]);

  const handleEditKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleEditSubmit();
      } else if (e.key === "Escape") {
        e.preventDefault();
        setIsEditing(false);
      }
    },
    [handleEditSubmit],
  );

  // 进入编辑模式后自动聚焦并选中文本
  useEffect(() => {
    if (isEditing && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [isEditing]);

  if (isEditing) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] w-full relative">
          <textarea
            ref={editRef}
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={handleEditKeyDown}
            onBlur={() => setIsEditing(false)}
            rows={2}
            className="block w-full resize-none rounded-xl rounded-br-md bg-brand-700 px-3 py-2 pr-24 pb-8 leading-snug text-brand-100 shadow-soft placeholder:text-brand-300/60 focus:outline-none focus:ring-2 focus:ring-brand-400/30"
            style={{ minHeight: "48px", fontSize: 'var(--fs-msg-user)' }}
          />
          {/* 模型选择 + 发送按钮：编辑框右下角 */}
          <div className="absolute bottom-1.5 right-1.5 z-10 flex items-center gap-1">
            <ModelToggle />
            <button
              type="button"
              onClick={handleEditSubmit}
              disabled={!editText.trim()}
              className="btn-send"
              aria-label="发送消息"
              title="发送 (Enter)"
            >
              <Send className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="group flex justify-end items-start gap-1"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="relative max-w-[80%] rounded-xl rounded-br-md bg-brand-700 px-3 py-2 leading-snug text-brand-100 shadow-soft" style={{ fontSize: 'var(--fs-msg-user)' }}>
        {renderTextWithSkillTags(userText)}
      </div>
      {/* 编辑按钮：消息右侧，hover 时显示 */}
      <button
        type="button"
        onClick={handleStartEdit}
        className={`mt-1.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-muted-c transition-opacity hover:text-primary-c ${hovered ? "opacity-100" : "opacity-0"}`}
        title="重新编辑"
        aria-label="重新编辑"
      >
        <Pencil className="h-3 w-3" />
      </button>
    </div>
  );
});
