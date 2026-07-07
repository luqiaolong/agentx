/**
 * 统一时间格式化函数，消除 9 个文件中的重复实现。
 *
 * 支持三种模式：
 * - absolute: YYYY-MM-DD HH:mm（默认）
 * - relative: "3 分钟前" / "刚刚"
 * - hhmm: HH:mm
 *
 * 输入支持 ISO 字符串或毫秒时间戳。
 */
export function formatTime(
  input: string | number,
  mode: "absolute" | "relative" | "hhmm" = "absolute",
): string {
  if (!input && input !== 0) return "-";
  const d = typeof input === "number" ? new Date(input) : new Date(input);
  if (Number.isNaN(d.getTime())) return String(input);

  if (mode === "hhmm") {
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  if (mode === "relative") {
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    if (diffMins < 1) return "刚刚";
    if (diffMins < 60) return `${diffMins} 分钟前`;
    if (diffHours < 24) return `${diffHours} 小时前`;
    if (diffDays < 7) return `${diffDays} 天前`;
    return formatDate(d);
  }

  return d.toLocaleString();
}

/**
 * 格式化日期为 YYYY-MM-DD。
 */
export function formatDate(input: string | number | Date): string {
  const d = new Date(input);
  if (Number.isNaN(d.getTime())) return String(input);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * 统一文件大小格式化，含 GB 分支。
 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
