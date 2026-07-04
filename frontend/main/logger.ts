import { app } from "electron";
import * as fs from "fs";
import * as path from "path";

/**
 * 按日滚动日志：写入 `app.getPath("userData")/logs/agent-py-{YYYYMMDD}.log`。
 * 用 userData 而非 app.getAppPath()，因为打包后 getAppPath() 指向只读 asar。
 */
function getLogDir(): string {
  return path.join(app.getPath("userData"), "logs");
}

function formatDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

function formatTime(d: Date): string {
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function ensureLogDir(): void {
  const dir = getLogDir();
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function logFilePath(date?: string): string {
  const dateStr = date ?? formatDate(new Date());
  return path.join(getLogDir(), `agent-py-${dateStr}.log`);
}

/** 追加一行日志（自动加 [HH:MM:SS] 前缀）。 */
export function appendLog(line: string): void {
  try {
    ensureLogDir();
    const stamp = formatTime(new Date());
    const entry = `[${stamp}] ${line}\n`;
    fs.appendFileSync(logFilePath(), entry, "utf8");
  } catch {
    /* 写入失败忽略，避免拖垮主进程 */
  }
}

/** 读取指定日期日志的最后 N 行（默认 200）。不指定 date 时返回当天日志。 */
export function readLogs(date?: string, maxLines: number = 200): string[] {
  try {
    const fp = logFilePath(date);
    if (!fs.existsSync(fp)) return [];
    const content = fs.readFileSync(fp, "utf8");
    const lines = content.split(/\r?\n/).filter((l) => l.length > 0);
    return lines.slice(-maxLines);
  } catch {
    return [];
  }
}

/** 删除超过 maxDays 天的日志文件（按文件 mtime 判定）。 */
export function cleanOldLogs(maxDays: number = 7): void {
  try {
    const dir = getLogDir();
    if (!fs.existsSync(dir)) return;
    const entries = fs.readdirSync(dir);
    const now = Date.now();
    const cutoff = maxDays * 24 * 60 * 60 * 1000;
    for (const entry of entries) {
      if (!/^agent-py-\d{8}\.log$/.test(entry)) continue;
      const fp = path.join(dir, entry);
      try {
        const stat = fs.statSync(fp);
        if (now - stat.mtimeMs > cutoff) {
          fs.unlinkSync(fp);
        }
      } catch {
        /* 单个文件错误忽略 */
      }
    }
  } catch {
    /* 清理失败忽略 */
  }
}

/** 启动时调用：确保目录存在 + 清理 7 天前日志。 */
export function initLogger(): void {
  ensureLogDir();
  cleanOldLogs(7);
}
