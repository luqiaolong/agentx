import { memo, useState } from "react";
import {
  AlertCircle,
  Check,
  Cpu,
  Pencil,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import type { ModelEntry } from "@/lib/utils";
import {
  getProviderPreset,
  modelDisplayName,
  providerBadgeColor,
  providerLabel,
} from "@/lib/modelCatalog";

interface ModelRowProps {
  entry: ModelEntry;
  isActive: boolean;
  hasKey: boolean;
  onActivate: () => void;
  onEdit: () => void;
  onDelete: () => void;
  activating: boolean;
}

function ModelRowImpl({
  entry,
  isActive,
  hasKey,
  onActivate,
  onEdit,
  onDelete,
  activating,
}: ModelRowProps): JSX.Element {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const displayLabel = modelDisplayName(entry);
  const preset = getProviderPreset(entry.providerId);
  const baseUrlDisplay =
    entry.baseUrl || (preset ? preset.baseUrl : "—");

  return (
    <li
      className={`rounded-lg border px-3 py-2 transition-colors ${
        isActive
          ? "border-brand-500/40 bg-brand-500/5"
          : "border-default bg-surface hover:bg-hover-soft"
      }`}
    >
      <div className="flex items-start gap-2">
        <Cpu
          className={`mt-0.5 h-4 w-4 shrink-0 ${
            isActive ? "text-brand-500" : "text-muted-c"
          }`}
        />
        <div className="min-w-0 flex-1">
          {/* 第一行：displayLabel + provider 徽章 + 状态 */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-primary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
              {displayLabel}
            </span>
            <span
              className={`rounded-full px-1.5 py-0.5 font-medium ${providerBadgeColor(
                entry.providerId,
              )}`}
              style={{ fontSize: 'var(--fs-settings-badge)' }}
            >
              {providerLabel(entry.providerId)}
            </span>
            {isActive && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 font-medium text-brand-500"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <Sparkles className="h-2.5 w-2.5" />
                使用中
              </span>
            )}
            {hasKey ? (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-600 dark:text-emerald-400"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <Check className="h-2.5 w-2.5" />
                密钥已配置
              </span>
            ) : (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <AlertCircle className="h-2.5 w-2.5" />
                未配置密钥
              </span>
            )}
          </div>
          {/* 第二行：模型名 + Base URL */}
          <div
            className="mt-0.5 truncate font-mono text-muted-c"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            <span className="text-secondary-c">
              {entry.model || "（未设置模型名）"}
            </span>
            <span className="mx-1.5 text-muted-c/50">·</span>
            <span className="break-all">{baseUrlDisplay}</span>
          </div>
        </div>
        {/* 操作按钮 */}
        <div className="flex shrink-0 items-center gap-1">
          {!isActive && (
            <button
              type="button"
              className="btn-ghost"
              onClick={onActivate}
              disabled={activating}
              aria-label="设为默认"
              title="设为默认模型"
            >
              {activating ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={onEdit}
            aria-label="编辑"
            title="编辑"
          >
            <Pencil className="h-3 w-3" />
          </button>
          {confirmDelete ? (
            <>
              <button
                type="button"
                className="cursor-pointer rounded px-1.5 py-0.5 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                style={{ fontSize: 'var(--fs-settings-form-hint)' }}
                onClick={() => {
                  onDelete();
                  setConfirmDelete(false);
                }}
              >
                确认
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setConfirmDelete(false)}
                aria-label="取消"
              >
                <X className="h-3 w-3" />
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setConfirmDelete(true)}
              aria-label="删除"
              title="删除"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export const ModelRow = memo(ModelRowImpl);
