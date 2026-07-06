import { useEffect, useState } from "react";
import { Database, Cpu } from "lucide-react";
import type { HealthStatus } from "@/lib/utils";
import { health } from "@/lib/api/http";
import { logger } from "@/lib/logger";

export function StatusIndicator() {
  const [healthState, setHealth] = useState<HealthStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      try {
        const status = await health.check();
        if (!cancelled) setHealth(status ?? null);
      } catch (e) {
        if (!cancelled) {
          setErr("unavailable");
          logger.warn("StatusIndicator health check failed", e);
        }
      }
    };
    fetchHealth();
    const timer = setInterval(fetchHealth, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const embOk = healthState?.embedding?.status === "healthy";
  const milvusOk = healthState?.milvus?.status === "healthy";

  const renderBadge = (
    ok: boolean,
    label: string,
    Icon: typeof Database,
    subitem?: { error_code?: string; error?: string },
  ) => {
    const tip = ok
      ? "正常"
      : subitem?.error_code
        ? `${subitem.error_code}: ${subitem.error ?? ""}`
        : err ?? "异常";
    return (
      <span
        title={tip}
        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium transition-colors ${
          ok
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : "bg-rose-500/10 text-rose-600 dark:text-rose-400"
        }`}
        style={{ fontSize: 'var(--fs-settings-badge)' }}
      >
        <Icon className="h-2.5 w-2.5" />
        {label}
        <span
          className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`}
        />
      </span>
    );
  };

  return (
    <div className="flex items-center gap-1.5">
      {renderBadge(embOk, "TEI", Cpu, healthState?.embedding)}
      {renderBadge(milvusOk, "Milvus", Database, healthState?.milvus)}
    </div>
  );
}
