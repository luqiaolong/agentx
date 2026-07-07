import { memo, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Lock,
  Pencil,
  Trash2,
  User,
} from "lucide-react";
import type {
  CustomSubagentEntry,
  SubagentConfig,
  ToolsConfig,
} from "@/lib/utils";
import { getToolsConfig } from "@/lib/api/settings";
import { emptyToolsConfig } from "@/lib/subagentConstants";
import type { BuiltinMeta, TeamMeta } from "./constants";

/**
 * 统一的子代理卡片组件，合并原 BuiltinCard 与 CustomCard。
 *
 * 通过 `variant` 区分两种模式：
 * - `builtin`：只读，显示描述和工具列表，使用 meta.Icon 与品牌色
 * - `custom`：可编辑/删除，使用 User 图标与紫色，标题后追加 @key 后缀
 */
export type SubagentCardProps =
  | {
      variant: "builtin";
      meta: BuiltinMeta | TeamMeta;
      cfg: SubagentConfig;
      onEnabledChange: (enabled: boolean) => void;
      onEdit: () => void;
    }
  | {
      variant: "custom";
      entry: CustomSubagentEntry;
      onEnabledChange: (enabled: boolean) => void;
      onEdit: () => void;
      onRemove: () => void;
    };

function SubagentCardImpl(props: SubagentCardProps) {
  const isCustom = props.variant === "custom";
  // 通过条件表达式让 TS 在同一作用域内对 props 做联合类型收窄
  const rawCfg = isCustom ? props.entry : props.cfg;
  const cfg: SubagentConfig = {
    enabled: typeof rawCfg?.enabled === "boolean" ? rawCfg.enabled : true,
    temperature:
      typeof rawCfg?.temperature === "number" && !Number.isNaN(rawCfg.temperature)
        ? rawCfg.temperature
        : 0.2,
    systemPrompt: rawCfg?.systemPrompt ?? "",
    tools: Array.isArray(rawCfg?.tools)
      ? rawCfg.tools.filter((t): t is string => typeof t === "string")
      : [],
    triggerDescription: rawCfg?.triggerDescription ?? "",
  };
  const name = isCustom ? props.entry.name : props.meta.label;
  const HeaderIcon = isCustom ? User : props.meta.Icon;
  const headerIconColor = isCustom ? "text-purple-500" : "text-brand-500";
  const customKey = isCustom ? props.entry.key : undefined;
  const onRemove = isCustom ? props.onRemove : undefined;
  const { onEnabledChange, onEdit } = props;

  const [open, setOpen] = useState(false);
  const [toolsConfig, setToolsConfig] = useState<ToolsConfig>(
    emptyToolsConfig(),
  );

  useEffect(() => {
    void (async () => {
      try {
        const tc = await getToolsConfig();
        setToolsConfig(tc);
      } catch {
        // 后端未就绪时保留空对象
      }
    })();
  }, []);

  // 绑定的工具在全局均被禁用时触发警告
  const allToolsOff =
    cfg.tools.length > 0 &&
    cfg.tools.every((t) => toolsConfig[t as keyof ToolsConfig] === false);

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        cfg.enabled ? "border-default" : "border-default opacity-60"
      }`}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-c shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-c shrink-0" />
          )}
          <HeaderIcon className={`h-4 w-4 ${headerIconColor} shrink-0`} />
          <span className="font-semibold text-primary-c truncate" style={{ fontSize: 'var(--fs-card-title)' }}>
            {name}
          </span>
          {isCustom && customKey && (
            <span className="font-mono text-muted-c shrink-0" style={{ fontSize: 'var(--fs-card-meta)' }}>
              @{customKey}
            </span>
          )}
        </button>

        {/* 开关（始终显示） */}
        <button
          type="button"
          role="switch"
          aria-checked={cfg.enabled}
          data-checked={cfg.enabled}
          onClick={() => onEnabledChange(!cfg.enabled)}
          className="switch-track shrink-0"
        >
          <span className="switch-thumb" data-checked={cfg.enabled} />
        </button>

        {/* 编辑按钮 */}
        <button
          type="button"
          onClick={onEdit}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-brand-500 shrink-0"
          aria-label="编辑"
          title="编辑"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>

        {/* 删除按钮 — 仅自定义模式 */}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-rose-500 shrink-0"
            aria-label="删除"
            title="删除"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="space-y-2 border-t border-default px-3 py-2.5">
          <div className="flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
            {isCustom ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-1.5 py-0.5 text-purple-600 dark:text-purple-400">
                <User className="h-2.5 w-2.5" />
                自定义
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5">
                <Lock className="h-2.5 w-2.5" />
                内置
              </span>
            )}
            <span>温度 {cfg.temperature.toFixed(1)}</span>
            <span>·</span>
            <span>工具 {cfg.tools.length}</span>
            <span>·</span>
            <span>条件</span>
          </div>
          {allToolsOff && (
            <div className="flex items-center gap-1 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              <AlertTriangle className="h-3 w-3" />
              绑定的工具全部被禁用，子代理将不可用
            </div>
          )}
          {cfg.tools.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {cfg.tools.map((t) => (
                <span
                  key={t}
                  className="rounded bg-subtle px-1.5 py-0.5 font-mono text-secondary-c"
                  style={{ fontSize: 'var(--fs-card-meta)' }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {cfg.triggerDescription && (
            <div className="rounded bg-brand-500/10 px-2 py-1 text-brand-600 dark:text-brand-400 line-clamp-2" style={{ fontSize: 'var(--fs-card-meta)' }}>
              {cfg.triggerDescription}
            </div>
          )}
          {cfg.systemPrompt && (
            <div className="space-y-1">
              <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>系统提示词</div>
              <div className="rounded bg-subtle/40 px-2 py-1 text-muted-c line-clamp-3" style={{ fontSize: 'var(--fs-card-desc)' }}>
                {cfg.systemPrompt}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 使用 React.memo 包裹，避免父组件渲染时子卡片无谓重渲染。
 *
 * 注意：父组件传入的 `onEnabledChange` / `onEdit` / `onRemove` 多为内联箭头函数，
 * 实际 memo 命中率受限于这些回调的稳定性；如需进一步优化，可在父组件中使用
 * useCallback 稳定回调。当前实现保持与原 BuiltinCard/CustomCard 完全等价的运行时行为。
 */
export const SubagentCard = memo(SubagentCardImpl);
