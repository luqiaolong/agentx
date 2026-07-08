import { useCallback, useEffect, useState } from "react";
import { getProjectConfig, initProjectConfig } from "@/lib/api/projectConfig";
import { logger } from "@/lib/logger";

/**
 * .agentx/ 项目级配置目录状态徽章。
 *
 * - workspacePath 为 null 时返回 null（Home 工作区不展示）
 * - 绿色圆点 + "已配置"  = .agentx/ 存在
 * - 灰色圆点 + "未配置"  = .agentx/ 不存在
 * - 点击徽章 → 调用 initProjectConfig 补缺失文件，成功后刷新状态
 *
 * 状态查询与初始化均为 best-effort：失败仅记录告警，不阻塞 UI。
 */
export function ProjectConfigBadge({
  workspacePath,
  threadId,
}: {
  workspacePath: string | null;
  threadId: string;
}) {
  const [exists, setExists] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(
    async (path: string) => {
      try {
        const status = await getProjectConfig(path, threadId);
        setExists(status.exists);
      } catch (err) {
        logger.warn("getProjectConfig failed", err);
        setExists(null);
      }
    },
    [threadId],
  );

  useEffect(() => {
    if (!workspacePath) {
      setExists(null);
      return;
    }
    void refresh(workspacePath);
  }, [workspacePath, refresh]);

  if (!workspacePath) {
    return null;
  }

  const configured = exists === true;

  const handleClick = async () => {
    if (busy || !workspacePath) return;
    setBusy(true);
    try {
      await initProjectConfig(workspacePath, threadId);
      await refresh(workspacePath);
    } catch (err) {
      logger.warn("initProjectConfig failed", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      title={
        configured
          ? ".agentx/ 已配置 — 点击重新生成（补缺失文件）"
          : ".agentx/ 未配置 — 点击生成"
      }
      className="inline-flex items-center gap-1 rounded px-1 py-px text-xs text-muted-c transition-colors hover:bg-hover-soft hover:text-secondary-c disabled:cursor-not-allowed disabled:opacity-60"
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          configured
            ? "bg-emerald-500"
            : exists === false
              ? "bg-muted-c/60"
              : "bg-muted-c/30"
        }`}
      />
      <span>{configured ? "已配置" : "未配置"}</span>
    </button>
  );
}
