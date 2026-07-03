import { useEffect, useState } from "react";
import type { HealthStatus } from "@/lib/utils";

export function StatusIndicator() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const p = window.api?.health?.check?.();
    if (!p || typeof p.then !== "function") {
      return;
    }
    p
      .then((h) => {
        if (cancelled) return;
        setHealth(h ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setErr("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const embOk = health?.embedding?.status === "healthy";
  const milvusOk = health?.milvus?.status === "healthy";

  const renderBadge = (ok: boolean, label: string, subitem?: { error_code?: string; error?: string }) => {
    const tip = ok ? "正常" : subitem?.error_code ? `${subitem.error_code}: ${subitem.error ?? ""}` : err ?? "异常";
    return (
      <span
        title={tip}
        className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs ${
          ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
        }`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`}
        />
        {label} {ok ? "正常" : "异常"}
      </span>
    );
  };

  return (
    <div className="flex items-center gap-2">
      {renderBadge(embOk, "Embedding", health?.embedding)}
      {renderBadge(milvusOk, "Milvus", health?.milvus)}
    </div>
  );
}
