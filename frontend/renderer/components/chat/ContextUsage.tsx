import { useContextUsage } from "@/stores/contextUsage";

/**
 * 上下文使用率纯展示组件：5 条纵向黑白条纹 widget。
 *
 * 设计要点：
 * - 无任何事件监听（pure presentation），避免与同位置 ModelToggle 误触
 * - 严格二元色：已填充 = bg-brand-500（neutral-500 黑灰），未填充 = bg-neutral-700（深灰底色上略浅）
 * - 严禁引入绿/黄/红警示色（违反「黑白二元」克制视觉语言）
 * - tooltip 仅展示完整数字（percentage + 实际 token 数 + 上限 + 模型名）
 * - 5 条 ceil 映射保证 0% 也至少显示 1 条（视觉存在）
 */
export function ContextUsage() {
  const { tokens, modelMax, pct, activeLabel } = useContextUsage();
  const filledStripes = Math.min(5, Math.max(1, Math.ceil((pct / 100) * 5)));

  const tooltip =
    `${pct}% · ${tokens.toLocaleString()} / ${modelMax.toLocaleString()} tokens` +
    (activeLabel ? ` · ${activeLabel}` : "");

  return (
    <div
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex h-4 w-3 flex-col items-stretch justify-end gap-[1.5px] cursor-help"
    >
      {Array.from({ length: 5 }).map((_, i) => {
        // 从底部往上数：底部索引 = 4，顶部索引 = 0
        const filled = 4 - i < filledStripes;
        return (
          <span
            key={i}
            data-filled={filled ? "true" : "false"}
            className={[
              "h-[2px] w-full rounded-[0.5px]",
              filled ? "bg-brand-500" : "bg-neutral-700",
            ].join(" ")}
          />
        );
      })}
    </div>
  );
}
