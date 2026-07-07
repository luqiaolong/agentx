import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Cpu,
  Plus,
  RotateCw,
  Sparkles,
} from "lucide-react";
import type { ModelEntry } from "@/lib/utils";
import { useModelStore } from "@/stores/model";
import { providerLabel } from "@/lib/modelCatalog";
import {
  activateModel,
  getActiveModelId,
  getModelEntries,
  setModelEntries,
} from "@/lib/api/settings";
import { reloadBackendConfig, restartBackend } from "@/lib/api/app";
import { logger } from "@/lib/logger";
import { humanizeError } from "@/lib/errors";
import { ModelRow } from "./ModelRow";
import { ModelEditor } from "./ModelEditor";

/**
 * 模型配置面板（设置 → 模型）。
 *
 * 设计要点（AGENTS.md §9.5 / §14 + 用户偏好）：
 * - 服务商类型用 `<select>` 下拉选择，不再展示「显示名称」字段（label 改为可选向后兼容字段）
 * - 模型名称用 `<datalist>` 提供 preset 自动补全，同时支持手工输入任意模型名
 * - API Key 默认显示掩码（●●●●），点击右侧眼睛图标切换为真实明文（解密走 main process revealApiKey）
 * - 上下文容量 / 输出 token 上拉提供 250k/512k/1M 等常见选项，附「自定义」入口
 * - 整体双列布局降低纵向高度，符合高信息密度要求
 */

function emptyEntry(): ModelEntry {
  return {
    id: "",
    label: undefined,
    providerId: "deepseek",
    model: "",
    baseUrl: "",
    apiKey: "",
    createdAt: Date.now(),
    contextWindow: null,
    maxOutputTokens: null,
  };
}

export function ModelProviderSettings(): JSX.Element {
  const [entries, setEntries] = useState<ModelEntry[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [hotReloaded, setHotReloaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<{
    entry: ModelEntry;
    isNew: boolean;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, active] = await Promise.all([
        getModelEntries(),
        getActiveModelId(),
      ]);
      setEntries(list);
      setActiveId(active);
    } catch (e) {
      logger.warn("ModelProviderSettings.load failed", e);
      setErrMsg(humanizeError(e));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
      setLoaded(true);
    })();
  }, [load]);

  const activeEntry = useMemo(
    () => entries.find((e) => e.id === activeId) ?? null,
    [entries, activeId],
  );

  const startNew = (): void => {
    setEditing({ entry: emptyEntry(), isNew: true });
  };

  const startEdit = (entry: ModelEntry): void => {
    setEditing({ entry: { ...entry }, isNew: false });
  };

  const cancelEdit = (): void => {
    setEditing(null);
  };

  const saveEdit = async (entry: ModelEntry): Promise<void> => {
    setErrMsg(null);
    try {
      const wasActive = !editing?.isNew && activeId === entry.id;
      let next: ModelEntry[];
      if (editing?.isNew) {
        next = [...entries, entry];
      } else {
        next = entries.map((e) => (e.id === entry.id ? entry : e));
      }
      await setModelEntries(next);
      setEntries(next);
      setEditing(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
      // 同步刷新 ModelToggle 的 useModelStore，确保输入框模型列表即时更新
      await useModelStore.getState().load();
      // 若编辑的是当前激活条目，重新写入 legacy 槽位以同步新配置，并热更新后端
      if (wasActive) {
        try {
          await activateModel(entry.id);
          await reloadBackendConfig();
          setHotReloaded(true);
          window.setTimeout(() => setHotReloaded(false), 2000);
        } catch (e) {
          setErrMsg(e instanceof Error ? e.message : String(e));
        }
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteEntry = async (id: string): Promise<void> => {
    setErrMsg(null);
    try {
      const next = entries.filter((e) => e.id !== id);
      await setModelEntries(next);
      setEntries(next);
      // 同步刷新 ModelToggle 的 useModelStore
      await useModelStore.getState().load();
      // 若删除的是当前激活条目，清空激活标记（后端仍保留旧 env，直到激活其他条目）
      if (activeId === id) {
        setActiveId(null);
        setErrMsg(
          "已删除当前激活的模型，后端仍使用旧配置运行。请激活其他模型以切换。",
        );
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const activateEntry = async (id: string): Promise<void> => {
    setErrMsg(null);
    setActivatingId(id);
    try {
      await activateModel(id);
      setActiveId(id);
      // 同步刷新 ModelToggle 的 useModelStore（激活状态变更）
      await useModelStore.getState().load();
      // 热更新后端配置，无需重启
      await reloadBackendConfig();
      setHotReloaded(true);
      window.setTimeout(() => setHotReloaded(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setActivatingId(null);
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

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-32 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 当前激活模型 banner */}
      <div className="flex items-center justify-between rounded-lg border border-brand-500/30 bg-brand-500/5 px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-500/10">
            <Sparkles className="h-4 w-4 text-brand-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span
                className="font-medium uppercase tracking-wide text-muted-c"
                style={{ fontSize: 'var(--fs-settings-desc)' }}
              >
                当前模型
              </span>
              {activeEntry ? (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-600 dark:text-emerald-400"
                  style={{ fontSize: 'var(--fs-settings-badge)' }}
                >
                  <CheckCircle2 className="h-2.5 w-2.5" />
                  已激活
                </span>
              ) : (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400"
                  style={{ fontSize: 'var(--fs-settings-badge)' }}
                >
                  <AlertCircle className="h-2.5 w-2.5" />
                  未配置
                </span>
              )}
            </div>
            <div
              className="mt-0.5 truncate font-mono text-primary-c"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              {activeEntry ? activeEntry.model : "尚未激活，请添加模型并点击「设为默认」"}
            </div>
            {activeEntry && (
              <div
                className="mt-0.5 truncate text-muted-c"
                style={{ fontSize: 'var(--fs-settings-desc)' }}
              >
                {providerLabel(activeEntry.providerId)}
                {activeEntry.baseUrl ? ` · ${activeEntry.baseUrl}` : ""}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 错误提示 */}
      {errMsg && (
        <div
          className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
          style={{ fontSize: 'var(--fs-settings-form-hint)' }}
        >
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {/* 模型列表 */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Cpu className="h-3 w-3 text-muted-c" />
            <h4
              className="font-semibold uppercase tracking-wide text-muted-c"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              已添加模型
            </h4>
            <span
              className="rounded-full bg-subtle px-1.5 py-0.5 text-secondary-c"
              style={{ fontSize: 'var(--fs-card-meta)' }}
            >
              {entries.length}
            </span>
          </div>
          <button
            type="button"
            className="btn-primary"
            onClick={startNew}
            disabled={editing !== null}
          >
            <Plus className="h-3.5 w-3.5" />
            添加模型
          </button>
        </div>

        {entries.length === 0 && !editing && (
          <div className="rounded-lg border border-dashed border-default px-3 py-6 text-center">
            <Cpu className="mx-auto mb-2 h-6 w-6 text-muted-c/50" />
            <p className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
              暂无模型配置
            </p>
            <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              点击「添加模型」选择服务商并填入密钥
            </p>
          </div>
        )}

        {entries.length > 0 && !editing && (
          <ul className="space-y-1.5">
            {entries.map((entry) => (
              <ModelRow
                key={entry.id}
                entry={entry}
                isActive={entry.id === activeId}
                hasKey={Boolean(entry.apiKey)}
                onActivate={() => activateEntry(entry.id)}
                onEdit={() => startEdit(entry)}
                onDelete={() => deleteEntry(entry.id)}
                activating={activatingId === entry.id}
              />
            ))}
          </ul>
        )}

        {editing && (
          <ModelEditor
            initial={editing.entry}
            isNew={editing.isNew}
            existingIds={entries.map((e) => e.id)}
            onSave={saveEdit}
            onCancel={cancelEdit}
          />
        )}

        {saved && (
          <div
            className="mt-2 flex items-center gap-1 text-emerald-600 dark:text-emerald-400"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            <CheckCircle2 className="h-3 w-3" />
            已保存
          </div>
        )}
      </div>

      {/* 热更新成功提示 */}
      {hotReloaded && (
        <div className="flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-900/50 dark:bg-emerald-950/30">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <span
            className="text-emerald-700 dark:text-emerald-300"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            模型配置已生效
          </span>
        </div>
      )}

      {/* 手动重启后端（兜底） */}
      <div className="flex items-center justify-between rounded-lg border border-default bg-subtle/30 px-3 py-2">
        <div
          className="flex items-center gap-1.5 text-muted-c"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
        >
          <RotateCw className="h-3 w-3 shrink-0" />
          <span>如遇异常可手动重启后端</span>
        </div>
        <button
          type="button"
          onClick={restart}
          className="btn-secondary"
          disabled={restarting}
        >
          <RotateCw className={`h-3 w-3 ${restarting ? "animate-spin" : ""}`} />
          {restarting ? "重启中…" : "重启后端"}
        </button>
      </div>

      {/* 说明 */}
      <p
        className="rounded-md bg-subtle/50 px-3 py-2 leading-relaxed text-muted-c"
        style={{ fontSize: 'var(--fs-settings-desc)' }}
      >
        后端通过 OpenAI 兼容协议调用 LLM。点击「设为默认」会将该模型的密钥与配置同步到后端并即时生效，
        无需重启。各模型条目的密钥经 safeStorage 加密存储于本地，切换模型时不会丢失。
      </p>
    </div>
  );
}
