import { useState } from "react";
import {
  Save,
  X,
  RefreshCw,
  AlertTriangle,
} from "lucide-react";
import type { McpServerConfig } from "@/lib/utils";
import { NAME_RE } from "@/lib/validators";
import {
  TRANSPORTS,
  argsToText,
  parseArgsText,
  envToText,
  parseEnvText,
} from "./utils";

// 名称正则与后端 McpServerConfig.name pattern 一致：^[a-zA-Z0-9_-]{1,64}$ — 已迁移到 @/lib/validators

interface EditorProps {
  initial: McpServerConfig;
  isNew: boolean;
  existingNames: string[];
  onSave: (cfg: McpServerConfig) => Promise<void>;
  onCancel: () => void;
}

export function ServerEditor({
  initial,
  isNew,
  existingNames,
  onSave,
  onCancel,
}: EditorProps): JSX.Element {
  const [draft, setDraft] = useState<McpServerConfig>(initial);
  const [argsText, setArgsText] = useState(argsToText(initial.args));
  const [envText, setEnvText] = useState(envToText(initial.env));
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const isStdio = draft.transport === "stdio";

  const validateName = (name: string): string | null => {
    if (!name) return "名称不能为空";
    if (!NAME_RE.test(name)) {
      return "名称只能含字母、数字、下划线、连字符，长度 1-64";
    }
    // 编辑时允许保留原名；新建时不允许与现有重名
    if (isNew && existingNames.includes(name)) {
      return "名称已存在";
    }
    if (!isNew && name !== initial.name && existingNames.includes(name)) {
      return "名称已存在";
    }
    return null;
  };

  const handleSave = async (): Promise<void> => {
    const err = validateName(draft.name.trim());
    if (err) {
      setNameErr(err);
      return;
    }
    setNameErr(null);
    setSaving(true);
    try {
      const cleaned: McpServerConfig = {
        ...draft,
        name: draft.name.trim(),
        command: isStdio ? draft.command?.trim() || null : null,
        args: isStdio ? parseArgsText(argsText) : [],
        env: isStdio ? parseEnvText(envText) : {},
        url: !isStdio ? draft.url?.trim() || null : null,
      };
      await onSave(cleaned);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-default bg-surface p-3">
      {/* 名称 */}
      <div className="flex items-center gap-2">
        <label className="w-20 shrink-0 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
          名称
        </label>
        <input
          type="text"
          value={draft.name}
          onChange={(e) => setDraft((s) => ({ ...s, name: e.target.value }))}
          placeholder="filesystem"
          className="input-field font-mono"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
          // name 在编辑已有 server 时不可改：
          // - 后端 key 用 name 索引（MultiServerMCPClient）
          // - 已连接 stdio 改名会留孤儿进程，与 runtime_dangerous tracking 不一致
          // 原条件 "!isNew && !isStdio ? false : !isNew" 允许 HTTP 改名，与下行提示
          // "编辑时不可改名" 不一致且与 saveEdit 的 originalName 跟踪脱节 → 简化为总锁定
          disabled={!isNew}
        />
        {!isNew && (
          <span className="text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>编辑时不可改名</span>
        )}
      </div>
      {nameErr && <p className="ml-22 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{nameErr}</p>}

      {/* 传输方式 */}
      <div className="flex items-start gap-2">
        <label className="w-20 shrink-0 pt-1 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
          传输方式
        </label>
        <div className="flex-1 space-y-1">
          {TRANSPORTS.map((t) => {
            const selected = draft.transport === t.value;
            return (
              <label
                key={t.value}
                className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 ${
                  selected
                    ? "border-brand-500 bg-brand-600/5"
                    : "border-default bg-subtle/40 hover:bg-hover-soft"
                }`}
              >
                <input
                  type="radio"
                  name="mcp-transport"
                  checked={selected}
                  onChange={() =>
                    setDraft((s) => ({ ...s, transport: t.value }))
                  }
                  className="mt-0.5 h-3 w-3 accent-brand-500"
                />
                <div className="min-w-0">
                  <div className="font-mono font-medium text-primary-c">
                    {t.label}
                  </div>
                  <div className="text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{t.desc}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      {/* stdio 传输字段 */}
      {isStdio && (
        <>
          <div className="flex items-center gap-2">
            <label className="w-20 shrink-0 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              command
            </label>
            <input
              type="text"
              value={draft.command ?? ""}
              onChange={(e) =>
                setDraft((s) => ({ ...s, command: e.target.value }))
              }
              placeholder="npx"
              className="input-field font-mono"
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
          </div>
          <div>
            <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              args（每行一个参数）
            </label>
            <textarea
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              rows={3}
              placeholder={"-y\n@modelcontextprotocol/server-filesystem\nd:/workspace"}
              className="input-field resize-y font-mono leading-relaxed"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            />
          </div>
          <div>
            <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              env（KEY=VALUE 每行一个，可选）
            </label>
            <textarea
              value={envText}
              onChange={(e) => setEnvText(e.target.value)}
              rows={2}
              placeholder={"API_KEY=xxx\nDEBUG=true"}
              className="input-field resize-y font-mono leading-relaxed"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            />
          </div>
        </>
      )}

      {/* HTTP 传输字段 */}
      {!isStdio && (
        <div className="flex items-center gap-2">
          <label className="w-20 shrink-0 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
            URL
          </label>
          <input
            type="text"
            value={draft.url ?? ""}
            onChange={(e) => setDraft((s) => ({ ...s, url: e.target.value }))}
            placeholder="http://localhost:8000/mcp"
            className="input-field font-mono"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          />
        </div>
      )}

      {/* 开关 */}
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>启用</label>
          <button
            type="button"
            role="switch"
            aria-checked={draft.enabled}
            data-checked={draft.enabled}
            onClick={() => setDraft((s) => ({ ...s, enabled: !s.enabled }))}
            className="switch-track"
          >
            <span className="switch-thumb" data-checked={draft.enabled} />
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
            trusted（可信）
          </label>
          <button
            type="button"
            role="switch"
            aria-checked={draft.trusted}
            data-checked={draft.trusted}
            onClick={() => setDraft((s) => ({ ...s, trusted: !s.trusted }))}
            className="switch-track"
          >
            <span className="switch-thumb" data-checked={draft.trusted} />
          </button>
        </div>
      </div>
      {!draft.trusted && (
        <div className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            未标记 trusted 的 server，其工具调用将经 DeepAgent 审批流（interrupt_before）。
            标记 trusted 后自动放行，仅用于完全可信的 MCP server。
          </span>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary"
        >
          {saving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          保存
        </button>
        <button type="button" onClick={onCancel} className="btn-secondary">
          <X className="h-3.5 w-3.5" />
          取消
        </button>
      </div>
    </div>
  );
}
