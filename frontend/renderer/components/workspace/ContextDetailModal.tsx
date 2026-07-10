import { X } from "lucide-react";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";

/* ------------------------------------------------------------------ */
/*  类型                                                                */
/* ------------------------------------------------------------------ */

export type DetailItemType = "skill" | "summary" | "memory";

export interface SkillDetail {
  type: "skill";
  name: string;
  description: string;
  trigger: string;
  tools: string[];
  content_preview: string;
  path: string;
}

export interface SummaryDetail {
  type: "summary";
  title: string;
  status: string;
  todos?: { content: string; status: string }[];
  createdAt: number;
}

export interface MemoryDetail {
  type: "memory";
  key: string;
  category: string;
  content: string;
  source: string;
  updated_at: string;
}

export type DetailItem = SkillDetail | SummaryDetail | MemoryDetail;

export interface ContextDetailModalProps {
  open: boolean;
  onClose: () => void;
  item: DetailItem | null;
}

/* ------------------------------------------------------------------ */
/*  ContextDetailModal                                                  */
/* ------------------------------------------------------------------ */

/**
 * 工作区上下文详情查看弹框（技能 / 摘要 / 记忆）。
 *
 * 设计要点：
 * - 复用 useModalDialog 获得 ESC 关闭、焦点陷阱、body overflow lock
 * - 弹窗 640px 宽，最大高度 80vh，内容区可滚动
 * - 字段按 key-value 纵向排列，长文本自动换行
 * - 加载 / 空状态在调用方控制，本组件仅负责展示
 */
export function ContextDetailModal({ open, onClose, item }: ContextDetailModalProps) {
  const { closeBtnRef, dialogRef } = useModalDialog({ open, onClose });

  if (!open || !item) return null;

  const title =
    item.type === "skill"
      ? item.name
      : item.type === "summary"
        ? item.title
        : item.key;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ctx-detail-title"
        className="flex w-full max-w-[640px] flex-col overflow-hidden rounded-xl border border-default bg-surface shadow-pop"
        style={{ maxHeight: "80vh" }}
      >
        {/* 弹窗顶栏 */}
        <div className="flex h-9 shrink-0 items-center justify-between border-b border-default bg-subtle/60 px-4">
          <span
            id="ctx-detail-title"
            className="truncate font-medium text-secondary-c"
            style={{ fontSize: "var(--fs-ws-file-name)" }}
            title={title}
          >
            {title}
          </span>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* 主体内容 */}
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {item.type === "skill" && <SkillDetailView item={item} />}
          {item.type === "summary" && <SummaryDetailView item={item} />}
          {item.type === "memory" && <MemoryDetailView item={item} />}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  子视图                                                              */
/* ------------------------------------------------------------------ */

function Field({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="space-y-1">
      <div className="text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
        {label}
      </div>
      <div
        className={`break-words text-secondary-c ${mono ? "whitespace-pre-wrap rounded bg-subtle/60 p-2 font-mono" : ""}`}
        style={{ fontSize: "var(--fs-ws-file-name)" }}
      >
        {children}
      </div>
    </div>
  );
}

function SkillDetailView({ item }: { item: SkillDetail }) {
  return (
    <div className="space-y-4">
      <Field label="名称">{item.name}</Field>
      {item.description && <Field label="描述">{item.description}</Field>}
      {item.trigger && <Field label="触发词">{item.trigger}</Field>}
      {item.tools.length > 0 && (
        <Field label="工具">
          <div className="flex flex-wrap gap-1">
            {item.tools.map((t) => (
              <span
                key={t}
                className="rounded bg-subtle px-1.5 py-px text-muted-c"
                style={{ fontSize: "var(--fs-ws-file-size)" }}
              >
                {t}
              </span>
            ))}
          </div>
        </Field>
      )}
      {item.content_preview && (
        <Field label="内容预览" mono>
          {item.content_preview}
        </Field>
      )}
      {item.path && <Field label="路径">{item.path}</Field>}
    </div>
  );
}

function SummaryDetailView({ item }: { item: SummaryDetail }) {
  const statusMap: Record<string, string> = {
    pending: "待处理",
    running: "进行中",
    done: "已完成",
    failed: "失败",
  };

  return (
    <div className="space-y-4">
      <Field label="标题">{item.title}</Field>
      <Field label="状态">
        <span
          className={`inline-flex items-center gap-1 rounded px-1.5 py-px ${
            item.status === "done"
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : item.status === "running"
                ? "bg-brand-500/10 text-brand-500 dark:text-brand-400"
                : item.status === "failed"
                  ? "bg-rose-500/10 text-rose-500"
                  : "bg-subtle text-muted-c"
          }`}
          style={{ fontSize: "var(--fs-ws-file-size)" }}
        >
          {statusMap[item.status] ?? item.status}
        </span>
      </Field>
      {item.todos && item.todos.length > 0 && (
        <Field label="待办列表">
          <div className="space-y-1">
            {item.todos.map((todo, i) => (
              <div key={i} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${
                    todo.status === "completed"
                      ? "bg-emerald-500"
                      : todo.status === "in_progress"
                        ? "bg-brand-500"
                        : "bg-muted-c"
                  }`}
                />
                <span
                  className={`text-secondary-c ${todo.status === "completed" ? "line-through opacity-60" : ""}`}
                  style={{ fontSize: "var(--fs-ws-file-name)" }}
                >
                  {todo.content}
                </span>
              </div>
            ))}
          </div>
        </Field>
      )}
      <Field label="创建时间">
        {new Date(item.createdAt).toLocaleString()}
      </Field>
    </div>
  );
}

function MemoryDetailView({ item }: { item: MemoryDetail }) {
  return (
    <div className="space-y-4">
      <Field label="键">{item.key}</Field>
      <Field label="分类">
        <span
          className="rounded bg-subtle px-1.5 py-px text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-size)" }}
        >
          {item.category}
        </span>
      </Field>
      {item.source && <Field label="来源">{item.source}</Field>}
      <Field label="内容" mono>
        {item.content}
      </Field>
      <Field label="更新时间">{new Date(item.updated_at).toLocaleString()}</Field>
    </div>
  );
}
