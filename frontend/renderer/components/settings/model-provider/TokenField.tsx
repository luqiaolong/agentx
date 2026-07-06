/** 下拉的「自定义」哨兵值 */
const CUSTOM_SENTINEL = "__custom__";

/** 把 k tokens 选项格式化成 64k / 250k / 1M 这种紧凑展示 */
function formatK(k: number): string {
  if (k >= 1024) {
    const m = k / 1024;
    return Number.isInteger(m) ? `${m}M` : `${m.toFixed(1)}M`;
  }
  return `${k}k`;
}

interface TokenFieldProps {
  label: React.ReactNode;
  unitSuffix?: string;
  /** k tokens 选项列表 */
  options: number[];
  /** 当前值（实际 token 数，可为 null） */
  value: number | null | undefined;
  /** 默认 placeholder（k tokens 字符串，如 "128"） */
  placeholderK: string;
  /** 值变更回调（k tokens → 实际 token × 1000） */
  onChange: (tokens: number | null) => void;
  hint?: string;
  err?: string;
}

/**
 * 上下文容量 / 输出 token 上限：下拉选择 + 自定义输入。
 * 选中具体值时存为实际 token 数（k × 1000）；选「自定义」时显示一个 number input。
 */
export function TokenField({
  label,
  unitSuffix,
  options,
  value,
  placeholderK,
  onChange,
  hint,
  err,
}: TokenFieldProps): JSX.Element {
  // 当前 k 值（actual tokens ÷ 1000）
  const currentK =
    typeof value === "number" && value > 0 ? Math.round(value / 1000) : null;
  // 是否在预设列表中
  const inPreset = currentK !== null && options.includes(currentK);
  // 下拉显示值：预设 → 当前 k；自定义 → 哨兵；空 → ""
  const selectValue =
    currentK === null ? "" : inPreset ? String(currentK) : CUSTOM_SENTINEL;

  return (
    <div>
      <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
        {label}
        {unitSuffix && <span className="ml-1 text-muted-c">（{unitSuffix}）</span>}
      </label>
      <div className="flex gap-1.5">
        <select
          value={selectValue}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") onChange(null);
            else if (v === CUSTOM_SENTINEL) {
              // 切到自定义：保留当前值（如果不是预设）或取 placeholder
              onChange(currentK && !inPreset ? currentK * 1000 : null);
            } else {
              onChange(Number(v) * 1000);
            }
          }}
          className="input-field flex-1 font-mono"
          style={{ fontSize: 'var(--fs-settings-form-input)' }}
        >
          <option value="">默认</option>
          {options.map((k) => (
            <option key={k} value={k}>
              {formatK(k)}
            </option>
          ))}
          <option value={CUSTOM_SENTINEL}>自定义...</option>
        </select>
        {selectValue === CUSTOM_SENTINEL && (
          <input
            type="number"
            min="1"
            step="1"
            value={currentK !== null ? String(currentK) : ""}
            onChange={(e) => {
              const k = e.target.value ? Number(e.target.value) : null;
              onChange(k && k > 0 ? k * 1000 : null);
            }}
            placeholder={placeholderK}
            className="input-field w-24 font-mono"
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
        )}
      </div>
      {value && value > 0 ? (
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          ≈ {value.toLocaleString()} tokens
          {hint ? ` · ${hint}` : ""}
        </p>
      ) : (
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          留空使用默认值
        </p>
      )}
      {err && (
        <p className="mt-1 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          {err}
        </p>
      )}
    </div>
  );
}
