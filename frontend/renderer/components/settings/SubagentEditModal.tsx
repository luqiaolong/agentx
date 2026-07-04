import { useEffect, useMemo, useState } from "react";
import { X, Save, AlertTriangle, Lock } from "lucide-react";

// 全部可选工具清单（与 SubagentsSettings 一致；危险工具对内置 subagent 也禁用绑定）
const ALL_TOOLS: string[] = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "web_search",
  "rag_retrieve",
];

// 关键词分隔符支持「逗号 / 换行」
function parseKeywords(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function keywordsToText(keywords: string[]): string {
  return keywords.join(", ");
}

export interface SubagentEditModalData {
  /** 内置子代理 key（"code"/"rag"/"web"）；自定义子代理为 undefined */
  builtinKey?: "code" | "rag" | "web";
  /** 自定义子代理 key（创建后不可改）；内置子代理为 undefined */
  customKey?: string;
  /** 显示名称（内置只读，自定义可编辑） */
  name: string;
  /** 描述（内置只读，自定义可编辑） */
  description: string;
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  keywords: string[];
}

interface Props {
  open: boolean;
  initial: SubagentEditModalData | null;
  /** 已存在的自定义 key 列表（用于新建时校验唯一性） */
  existingCustomKeys: string[];
  /** 是否为新建模式（新建时 key 可编辑，已存在时 key 只读） */
  isNew?: boolean;
  onClose: () => void;
  onSave: (data: SubagentEditModalData) => void;
}

export function SubagentEditModal({
  open,
  initial,
  existingCustomKeys,
  isNew = false,
  onClose,
  onSave,
}: Props) {
  const [data, setData] = useState<SubagentEditModalData | null>(initial);
  const [keywordText, setKeywordText] = useState("");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // 当 initial 变化（打开/切换）时同步本地状态
  useEffect(() => {
    if (initial) {
      setData(initial);
      setKeywordText(keywordsToText(initial.keywords));
      setErrMsg(null);
    } else {
      setData(null);
      setKeywordText("");
      setErrMsg(null);
    }
  }, [initial, open]);

  const isBuiltin = data?.builtinKey !== undefined;

  // 校验：新建自定义时 key 必须合法且唯一
  const keyError = useMemo(() => {
    if (!data || !isNew || isBuiltin) return null;
    const key = data.customKey?.trim() ?? "";
    if (!key) return "key 不能为空";
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(key)) {
      return "key 仅允许字母数字/下划线/连字符，1-64 字符";
    }
    if (existingCustomKeys.includes(key)) {
      return `key "${key}" 已存在`;
    }
    if (["code", "rag", "web"].includes(key)) {
      return `key "${key}" 与内置子代理冲突`;
    }
    return null;
  }, [data, isNew, isBuiltin, existingCustomKeys]);

  if (!open || !data) return null;

  const update = (patch: Partial<SubagentEditModalData>): void => {
    setData((d) => (d ? { ...d, ...patch } : d));
  };

  const toggleTool = (tool: string): void => {
    const has = data.tools.includes(tool);
    const next = has
      ? data.tools.filter((t) => t !== tool)
      : [...data.tools, tool];
    update({ tools: next });
  };

  const handleSave = (): void => {
    if (keyError) {
      setErrMsg(keyError);
      return;
    }
    if (!data.name.trim()) {
      setErrMsg("名称不能为空");
      return;
    }
    onSave({
      ...data,
      customKey: isBuiltin ? undefined : data.customKey?.trim(),
      name: data.name.trim(),
      keywords: parseKeywords(keywordText),
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[560px] max-h-[85vh] overflow-y-auto rounded-lg border border-default bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-default px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-primary-c">
              {isNew
                ? "新建子代理"
                : `编辑子代理：${data.name || data.builtinKey || data.customKey}`}
            </span>
            {isBuiltin && (
              <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5 text-[10px] text-muted-c">
                <Lock className="h-2.5 w-2.5" />
                内置
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 内容 */}
        <div className="space-y-3 px-4 py-3">
          {errMsg && (
            <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{errMsg}</span>
            </div>
          )}

          {/* key 字段（仅自定义子代理显示；新建时可编辑） */}
          {!isBuiltin && (
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary-c">
                Key（唯一标识，{isNew ? "创建后不可修改" : "不可修改"}）
              </label>
              <input
                type="text"
                value={data.customKey ?? ""}
                onChange={(e) => update({ customKey: e.target.value })}
                disabled={!isNew}
                placeholder="如：my_helper"
                className={`input-field font-mono text-[11px] ${
                  !isNew ? "cursor-not-allowed opacity-60" : ""
                }`}
              />
              <p className="mt-1 text-[11px] text-muted-c">
                Router 按此 key 加载自定义子代理；仅字母数字 / 下划线 / 连字符。
              </p>
            </div>
          )}

          {/* 名称 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              名称{isBuiltin ? "（内置只读）" : ""}
            </label>
            <input
              type="text"
              value={data.name}
              onChange={(e) => update({ name: e.target.value })}
              disabled={isBuiltin}
              className={`input-field text-xs ${
                isBuiltin ? "cursor-not-allowed opacity-60" : ""
              }`}
            />
          </div>

          {/* 描述 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              描述{isBuiltin ? "（内置只读）" : ""}
            </label>
            <input
              type="text"
              value={data.description}
              onChange={(e) => update({ description: e.target.value })}
              disabled={isBuiltin}
              placeholder="如：用于代码检索与文件读取"
              className={`input-field text-xs ${
                isBuiltin ? "cursor-not-allowed opacity-60" : ""
              }`}
            />
          </div>

          {/* 启用开关 */}
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-secondary-c">启用</label>
            <button
              type="button"
              role="switch"
              aria-checked={data.enabled}
              onClick={() => update({ enabled: !data.enabled })}
              className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
                data.enabled ? "bg-brand-600" : "bg-subtle"
              }`}
            >
              <span
                className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                  data.enabled ? "translate-x-3.5" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>

          {/* 温度 */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-secondary-c">
                Temperature
              </label>
              <span className="rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-primary-c">
                {data.temperature.toFixed(1)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={data.temperature}
              onChange={(e) => update({ temperature: Number(e.target.value) })}
              className="w-full accent-brand-500"
            />
          </div>

          {/* 系统提示词 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              系统提示词（留空使用后端默认）
            </label>
            <textarea
              value={data.systemPrompt}
              onChange={(e) => update({ systemPrompt: e.target.value })}
              rows={3}
              placeholder="对该子代理的额外指令"
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
          </div>

          {/* 工具复选框 */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-secondary-c">
              绑定工具（危险工具已禁用）
            </label>
            <div className="grid grid-cols-2 gap-1.5">
              {ALL_TOOLS.map((tool) => {
                const checked = data.tools.includes(tool);
                return (
                  <label
                    key={tool}
                    className="flex cursor-pointer items-center gap-1.5 rounded border border-default bg-subtle/40 px-2 py-1 text-[11px] hover:bg-hover-soft"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTool(tool)}
                      className="h-3 w-3 rounded border-strong accent-brand-500"
                    />
                    <span className="font-mono text-secondary-c">{tool}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* 关键词 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              触发关键词（逗号分隔）
            </label>
            <textarea
              value={keywordText}
              onChange={(e) => setKeywordText(e.target.value)}
              rows={2}
              placeholder="如：知识库, 检索, rag"
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
            <p className="mt-1 text-[11px] text-muted-c">
              Router 按这些关键词判断是否路由到该子代理。
            </p>
          </div>
        </div>

        {/* 底部按钮 */}
        <div className="flex items-center justify-end gap-2 border-t border-default px-4 py-3">
          <button type="button" onClick={onClose} className="btn-secondary">
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="btn-primary"
            disabled={!!keyError}
          >
            <Save className="h-3.5 w-3.5" />
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
