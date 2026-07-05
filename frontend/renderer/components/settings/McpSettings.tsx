import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Plug,
  Plus,
  Pencil,
  Trash2,
  Save,
  X,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  XCircle,
  RotateCw,
  AlertTriangle,
  Terminal,
  Globe,
} from "lucide-react";
import type {
  McpServerConfig,
  McpServerStatus,
  McpTransport,
  McpTestResult,
} from "@/lib/utils";

// 名称正则与后端 McpServerConfig.name pattern 一致：^[a-zA-Z0-9_-]{1,64}$
const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

const TRANSPORTS: { value: McpTransport; label: string; desc: string }[] = [
  { value: "stdio", label: "stdio", desc: "子进程通信（本地 MCP server）" },
  { value: "sse", label: "sse", desc: "Server-Sent Events HTTP 传输" },
  {
    value: "streamable_http",
    label: "streamable_http",
    desc: "可流式 HTTP 传输（推荐 HTTP 场景）",
  },
];

function emptyServer(): McpServerConfig {
  return {
    name: "",
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: "",
    enabled: true,
    trusted: false,
  };
}

// 将 args 数组与 textarea 文本互转（每行一个参数）
function argsToText(args: string[]): string {
  return args.join("\n");
}

function parseArgsText(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

// 将 env 对象与 textarea 文本互转（KEY=VALUE 每行一个）
function envToText(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx <= 0) continue;
    const k = trimmed.slice(0, eqIdx).trim();
    const v = trimmed.slice(eqIdx + 1);
    if (k) out[k] = v;
  }
  return out;
}

interface ServerRowProps {
  status: McpServerStatus;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  testing: boolean;
  testResult: McpTestResult | null;
}

function StatusBadge({ status }: { status: McpServerStatus }): JSX.Element {
  if (!status.enabled) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5 text-[10px] text-secondary-c">
        <XCircle className="h-3 w-3" />
        已禁用
      </span>
    );
  }
  if (status.connected) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="h-3 w-3" />
        已连接 · {status.tool_count} 工具
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-600 dark:text-rose-400">
      <XCircle className="h-3 w-3" />
      连接失败
    </span>
  );
}

function ServerRow({
  status,
  onEdit,
  onDelete,
  onTest,
  testing,
  testResult,
}: ServerRowProps): JSX.Element {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const TransportIcon = status.transport === "stdio" ? Terminal : Globe;

  return (
    <li className="rounded-lg border border-default bg-surface px-3 py-2.5 text-xs">
      <div className="flex items-start gap-2">
        <TransportIcon className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold text-primary-c">
              {status.name}
            </span>
            <StatusBadge status={status} />
            {status.trusted && (
              <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-400">
                trusted
              </span>
            )}
          </div>
          <div className="mt-1 truncate text-[11px] text-muted-c">
            {status.transport === "stdio" ? (
              <>
                <span className="font-mono">{status.command || "?"}</span>
                {status.args.length > 0 && (
                  <span className="font-mono"> {status.args.join(" ")}</span>
                )}
              </>
            ) : (
              <span className="font-mono">{status.url || "?"}</span>
            )}
          </div>
          {status.error && (
            <div className="mt-1 flex items-start gap-1 text-[10px] text-rose-600 dark:text-rose-400">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-all">{status.error}</span>
            </div>
          )}
          {testResult && (
            <div
              className={`mt-1.5 rounded-md border px-2 py-1 text-[10px] ${
                testResult.ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300"
                  : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
              }`}
            >
              {testResult.ok ? (
                <>
                  <div className="font-medium">
                    测试成功 · 发现 {testResult.tool_count ?? testResult.tools.length} 个工具
                  </div>
                  {testResult.tools.length > 0 && (
                    <div className="mt-0.5 font-mono break-all">
                      {testResult.tools.slice(0, 5).map((t) => t.name).join(", ")}
                      {testResult.tools.length > 5 && " …"}
                    </div>
                  )}
                </>
              ) : (
                <span>测试失败: {testResult.error ?? "未知错误"}</span>
              )}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={onTest}
            disabled={testing}
            aria-label="测试连接"
            title="测试连接"
          >
            {testing ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <Plug className="h-3 w-3" />
            )}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={onEdit}
            aria-label="编辑"
            title="编辑"
          >
            <Pencil className="h-3 w-3" />
          </button>
          {confirmDelete ? (
            <>
              <button
                type="button"
                className="rounded px-1.5 py-0.5 text-[10px] text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                onClick={() => {
                  onDelete();
                  setConfirmDelete(false);
                }}
              >
                确认
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setConfirmDelete(false)}
                aria-label="取消"
              >
                <X className="h-3 w-3" />
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setConfirmDelete(true)}
              aria-label="删除"
              title="删除"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

interface EditorProps {
  initial: McpServerConfig;
  isNew: boolean;
  existingNames: string[];
  onSave: (cfg: McpServerConfig) => Promise<void>;
  onCancel: () => void;
}

function ServerEditor({
  initial,
  isNew,
  existingNames,
  onSave,
  onCancel,
}: EditorProps): JSX.Element {
  const [draft, setDraft] = useState<McpServerConfig>(initial);
  const [argsText, setArgsText] = useState(argsToText(initial.args));
  const [envText, setEnvText] = useState(envToText(initial.env));
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const isStdio = draft.transport === "stdio";

  const validateName = (name: string): string | null => {
    if (!name) return "名称不能为空";
    if (!NAME_RE.test(name)) {
      return "名称只能含字母、数字、下划线、连字符，长度 1-64";
    }
    // 编辑时允许保留原名；新建时不允许与现有重名
    if (isNew && existingNames.includes(name)) {
      return "名称已存在";
    }
    if (!isNew && name !== initial.name && existingNames.includes(name)) {
      return "名称已存在";
    }
    return null;
  };

  const handleSave = async (): Promise<void> => {
    const err = validateName(draft.name.trim());
    if (err) {
      setNameErr(err);
      return;
    }
    setNameErr(null);
    setSaving(true);
    try {
      const cleaned: McpServerConfig = {
        ...draft,
        name: draft.name.trim(),
        command: isStdio ? draft.command?.trim() || null : null,
        args: isStdio ? parseArgsText(argsText) : [],
        env: isStdio ? parseEnvText(envText) : {},
        url: !isStdio ? draft.url?.trim() || null : null,
      };
      await onSave(cleaned);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-default bg-surface p-3">
      {/* 名称 */}
      <div className="flex items-center gap-2">
        <label className="w-20 shrink-0 text-xs font-medium text-secondary-c">
          名称
        </label>
        <input
          type="text"
          value={draft.name}
          onChange={(e) => setDraft((s) => ({ ...s, name: e.target.value }))}
          placeholder="filesystem"
          className="input-field font-mono text-[11px]"
          // name 在编辑已有 server 时不可改：
          // - 后端 key 用 name 索引（MultiServerMCPClient）
          // - 已连接 stdio 改名会留孤儿进程，与 runtime_dangerous tracking 不一致
          // 原条件 "!isNew && !isStdio ? false : !isNew" 允许 HTTP 改名，与下行提示
          // "编辑时不可改名" 不一致且与 saveEdit 的 originalName 跟踪脱节 → 简化为总锁定
          disabled={!isNew}
        />
        {!isNew && (
          <span className="text-[10px] text-muted-c">编辑时不可改名</span>
        )}
      </div>
      {nameErr && <p className="ml-22 text-[11px] text-rose-500">{nameErr}</p>}

      {/* 传输方式 */}
      <div className="flex items-start gap-2">
        <label className="w-20 shrink-0 pt-1 text-xs font-medium text-secondary-c">
          传输方式
        </label>
        <div className="flex-1 space-y-1">
          {TRANSPORTS.map((t) => {
            const selected = draft.transport === t.value;
            return (
              <label
                key={t.value}
                className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 text-[11px] ${
                  selected
                    ? "border-brand-500 bg-brand-600/5"
                    : "border-default bg-subtle/40 hover:bg-hover-soft"
                }`}
              >
                <input
                  type="radio"
                  name="mcp-transport"
                  checked={selected}
                  onChange={() =>
                    setDraft((s) => ({ ...s, transport: t.value }))
                  }
                  className="mt-0.5 h-3 w-3 accent-brand-500"
                />
                <div className="min-w-0">
                  <div className="font-mono font-medium text-primary-c">
                    {t.label}
                  </div>
                  <div className="text-[10px] text-muted-c">{t.desc}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      {/* stdio 传输字段 */}
      {isStdio && (
        <>
          <div className="flex items-center gap-2">
            <label className="w-20 shrink-0 text-xs font-medium text-secondary-c">
              command
            </label>
            <input
              type="text"
              value={draft.command ?? ""}
              onChange={(e) =>
                setDraft((s) => ({ ...s, command: e.target.value }))
              }
              placeholder="npx"
              className="input-field font-mono text-[11px]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              args（每行一个参数）
            </label>
            <textarea
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              rows={3}
              placeholder={"-y\n@modelcontextprotocol/server-filesystem\nd:/workspace"}
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              env（KEY=VALUE 每行一个，可选）
            </label>
            <textarea
              value={envText}
              onChange={(e) => setEnvText(e.target.value)}
              rows={2}
              placeholder={"API_KEY=xxx\nDEBUG=true"}
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
          </div>
        </>
      )}

      {/* HTTP 传输字段 */}
      {!isStdio && (
        <div className="flex items-center gap-2">
          <label className="w-20 shrink-0 text-xs font-medium text-secondary-c">
            URL
          </label>
          <input
            type="text"
            value={draft.url ?? ""}
            onChange={(e) => setDraft((s) => ({ ...s, url: e.target.value }))}
            placeholder="http://localhost:8000/mcp"
            className="input-field font-mono text-[11px]"
          />
        </div>
      )}

      {/* 开关 */}
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-secondary-c">启用</label>
          <button
            type="button"
            role="switch"
            aria-checked={draft.enabled}
            onClick={() => setDraft((s) => ({ ...s, enabled: !s.enabled }))}
            className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
              draft.enabled ? "bg-brand-600" : "bg-subtle"
            }`}
          >
            <span
              className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                draft.enabled ? "translate-x-3.5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-secondary-c">
            trusted（可信）
          </label>
          <button
            type="button"
            role="switch"
            aria-checked={draft.trusted}
            onClick={() => setDraft((s) => ({ ...s, trusted: !s.trusted }))}
            className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
              draft.trusted ? "bg-brand-600" : "bg-subtle"
            }`}
          >
            <span
              className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                draft.trusted ? "translate-x-3.5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
      </div>
      {!draft.trusted && (
        <div className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[10px] text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            未标记 trusted 的 server，其工具调用将经 DeepAgent 审批流（interrupt_before）。
            标记 trusted 后自动放行，仅用于完全可信的 MCP server。
          </span>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary"
        >
          {saving ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          保存
        </button>
        <button type="button" onClick={onCancel} className="btn-secondary">
          <X className="h-3.5 w-3.5" />
          取消
        </button>
      </div>
    </div>
  );
}

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
      const cfg = await window.api.settings.getMcpServersConfig();
      setServers(cfg);
    } catch {
      // 后端未就绪时保留空列表
    }
  }, []);

  const loadStatuses = useCallback(async () => {
    try {
      const result = await window.api.mcp.listServers();
      setStatuses(result.servers ?? []);
    } catch {
      // 后端未就绪时清空
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
      await window.api.settings.setMcpServersConfig(next);
      setServers(next);
      setEditing(null);
      // 热更新后端配置（含 MCP server 重连），无需重启
      await window.api.app.reloadBackendConfig();
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
      await window.api.settings.setMcpServersConfig(next);
      setServers(next);
      // 清除该 server 的测试结果
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      // 热更新后端配置（含 MCP server 重连），无需重启
      await window.api.app.reloadBackendConfig();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const testServer = async (cfg: McpServerConfig): Promise<void> => {
    setErrMsg(null);
    setTestingName(cfg.name);
    try {
      const result = await window.api.mcp.testServer(cfg);
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
      const result = await window.api.app.restartBackend();
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
      const result = await window.api.mcp.refresh();
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
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-[11px] text-muted-c">
        <Plug className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置外部 MCP (Model Context Protocol) server。MCP 工具仅暴露给 DeepAgent
          （路径 C），未标记 trusted 的 server 工具调用需用户审批。保存后即时生效。
        </span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-c">
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
          <button type="button" className="btn-primary px-2.5 py-1.5" onClick={startNew}>
            <Plus className="h-3.5 w-3.5" />
            新建
          </button>
        </div>
      </div>

      {servers.length === 0 && !editing && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-xs text-muted-c">
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
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>
    </div>
  );
}
