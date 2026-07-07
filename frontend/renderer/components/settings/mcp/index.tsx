import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Plug,
  Plus,
  RefreshCw,
  CheckCircle2,
  RotateCw,
} from "lucide-react";
import type {
  McpServerConfig,
  McpServerStatus,
  McpTestResult,
} from "@/lib/utils";
import { mcp } from "@/lib/api/http";
import { getMcpServersConfig, setMcpServersConfig } from "@/lib/api/settings";
import { reloadBackendConfig, restartBackend } from "@/lib/api/app";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { logger } from "@/lib/logger";
import { humanizeError } from "@/lib/errors";
import { emptyServer } from "./utils";
import { ServerRow } from "./ServerRow";
import { ServerEditor } from "./ServerEditor";

export function McpSettings(): JSX.Element {
  const [servers, setServers] = useState<McpServerConfig[]>([]);
  const [statuses, setStatuses] = useState<McpServerStatus[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<{
    cfg: McpServerConfig;
    isNew: boolean;
    originalName?: string;
  } | null>(null);
  const [testingName, setTestingName] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>({});

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await getMcpServersConfig();
      setServers(cfg);
    } catch (e) {
      logger.warn("McpSettings.loadConfig failed", e);
      setErrMsg(humanizeError(e));
    }
  }, []);

  const loadStatuses = useCallback(async () => {
    try {
      const result = await mcp.listServers();
      setStatuses(result.servers ?? []);
    } catch (e) {
      logger.warn("McpSettings.loadStatuses failed", e);
      setErrMsg(humanizeError(e));
      setStatuses([]);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await loadConfig();
      await loadStatuses();
      setLoaded(true);
    })();
  }, [loadConfig, loadStatuses]);

  const statusByName = useMemo(() => {
    const map: Record<string, McpServerStatus> = {};
    for (const s of statuses) map[s.name] = s;
    return map;
  }, [statuses]);

  const startNew = (): void => {
    setEditing({ cfg: emptyServer(), isNew: true });
  };

  const startEdit = (cfg: McpServerConfig): void => {
    setEditing({ cfg: { ...cfg }, isNew: false, originalName: cfg.name });
  };

  const cancelEdit = (): void => {
    setEditing(null);
  };

  const saveEdit = async (cfg: McpServerConfig): Promise<void> => {
    setErrMsg(null);
    try {
      let next: McpServerConfig[];
      if (editing?.isNew) {
        next = [...servers, cfg];
      } else {
        next = servers.map((s) =>
          s.name === editing?.originalName ? cfg : s,
        );
      }
      await setMcpServersConfig(next);
      setServers(next);
      setEditing(null);
      // 热更新后端配置（含 MCP server 重连），无需重启
      await reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteServer = async (name: string): Promise<void> => {
    setErrMsg(null);
    try {
      const next = servers.filter((s) => s.name !== name);
      await setMcpServersConfig(next);
      setServers(next);
      // 清除该 server 的测试结果
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      // 热更新后端配置（含 MCP server 重连），无需重启
      await reloadBackendConfig();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const testServer = async (cfg: McpServerConfig): Promise<void> => {
    setErrMsg(null);
    setTestingName(cfg.name);
    try {
      const result = await mcp.testServer(cfg);
      setTestResults((prev) => ({ ...prev, [cfg.name]: result }));
    } catch (e) {
      setTestResults((prev) => ({
        ...prev,
        [cfg.name]: {
          ok: false,
          error: e instanceof Error ? e.message : String(e),
          tools: [],
        },
      }));
    } finally {
      setTestingName(null);
    }
  };

  const restart = async (): Promise<void> => {
    setErrMsg(null);
    try {
      setRestarting(true);
      const result = await restartBackend();
      if (!result.ok) {
        setErrMsg(result.message ?? "重启后端超时");
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRestarting(false);
    }
  };

  const refreshStatuses = async (): Promise<void> => {
    setErrMsg(null);
    try {
      const result = await mcp.refresh();
      setStatuses(result.servers ?? []);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const enabledCount = servers.filter((s) => s.enabled).length;
  const connectedCount = statuses.filter((s) => s.connected).length;

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-32 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <Plug className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置外部 MCP (Model Context Protocol) server。MCP 工具仅暴露给 DeepAgent
          （路径 C），未标记 trusted 的 server 工具调用需用户审批。保存后即时生效。
        </span>
      </div>

      {errMsg && <ErrorBanner message={errMsg} />}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
          <span className="rounded-full bg-subtle px-2 py-0.5">
            {servers.length} 个 server
          </span>
          <span>·</span>
          <span>{enabledCount} 启用</span>
          <span>·</span>
          <span>{connectedCount} 已连接</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={refreshStatuses}
            aria-label="刷新状态"
            title="刷新连接状态"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button type="button" className="btn-primary" onClick={startNew}>
            <Plus className="h-3.5 w-3.5" />
            新建
          </button>
        </div>
      </div>

      {servers.length === 0 && !editing && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
          暂无 MCP server，点击「新建」添加第一个 server
        </p>
      )}

      {servers.length > 0 && !editing && (
        <ul className="space-y-1.5">
          {servers.map((cfg) => {
            const status = statusByName[cfg.name] ?? {
              ...cfg,
              connected: false,
              error: null,
              tool_count: 0,
            };
            return (
              <ServerRow
                key={cfg.name}
                status={status}
                onEdit={() => startEdit(cfg)}
                onDelete={() => deleteServer(cfg.name)}
                onTest={() => testServer(cfg)}
                testing={testingName === cfg.name}
                testResult={testResults[cfg.name] ?? null}
              />
            );
          })}
        </ul>
      )}

      {editing && (
        <ServerEditor
          initial={editing.cfg}
          isNew={editing.isNew}
          existingNames={servers.map((s) => s.name)}
          onSave={saveEdit}
          onCancel={cancelEdit}
        />
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={restart}
          className="btn-secondary"
          disabled={restarting}
        >
          <RotateCw className="h-3.5 w-3.5" />
          {restarting ? "重启中…" : "保存并重启后端"}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
            <CheckCircle2 className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>
    </div>
  );
}
