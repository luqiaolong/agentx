import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Check,
  RotateCw,
  AlertTriangle,
  Plus,
  User,
  Users,
  Save,
} from "lucide-react";
import type {
  SubagentConfig,
  SubagentsConfig,
  TeamSubagentsConfig,
  CustomSubagentsMap,
  CustomSubagentEntry,
} from "@/lib/utils";
import {
  getSubagentsConfig,
  setSubagentsConfig,
  getTeamSubagentsConfig,
  setTeamSubagentsConfig,
  getCustomSubagents,
  setCustomSubagents,
  addCustomSubagent,
  removeCustomSubagent,
} from "@/lib/api/settings";
import { reloadBackendConfig, restartBackend } from "@/lib/api/app";
import {
  SubagentEditModal,
  type SubagentEditModalData,
} from "../SubagentEditModal";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { logger } from "@/lib/logger";
import { humanizeError } from "@/lib/errors";
import {
  BUILTIN_SUBAGENTS,
  TEAM_SUBAGENTS,
  EMPTY_CONFIG,
  EMPTY_TEAM_CONFIG,
  normalizeSubagentsConfig,
  normalizeTeamSubagentsConfig,
  type BuiltinMeta,
  type TeamMeta,
  type BuiltinSubagentKey,
  type TeamSubagentKey,
} from "./constants";
import { SubagentCard } from "./SubagentCard";

/** 分组容器：标题可折叠整个分组。 */
function SubagentGroup({
  title,
  icon: Icon,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  icon: typeof Bot;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-1 py-1 text-left"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-c" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-c" />
        )}
        <Icon className="h-4 w-4 text-brand-500" />
        <span className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-card-title)' }}>{title}</span>
        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
          {count}
        </span>
      </button>
      {open && <div className="space-y-2">{children}</div>}
    </div>
  );
}

export function SubagentsSettings() {
  const [config, setConfig] = useState<SubagentsConfig>(EMPTY_CONFIG);
  const [teamConfig, setTeamConfig] = useState<TeamSubagentsConfig>(EMPTY_TEAM_CONFIG);
  const [customMap, setCustomMap] = useState<CustomSubagentsMap>({});
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // 编辑弹窗状态
  const [modalOpen, setModalOpen] = useState(false);
  const [modalData, setModalData] = useState<SubagentEditModalData | null>(
    null,
  );
  const [modalIsNew, setModalIsNew] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [cfg, team, custom] = await Promise.all([
          getSubagentsConfig(),
          getTeamSubagentsConfig(),
          getCustomSubagents(),
        ]);
        setConfig(normalizeSubagentsConfig(cfg));
        setTeamConfig(normalizeTeamSubagentsConfig(team));
        setCustomMap(custom ?? {});
      } catch (e) {
        logger.warn("SubagentsSettings.load failed", e);
        setErrMsg(humanizeError(e));
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const updateBuiltin = (
    key: BuiltinSubagentKey,
    next: SubagentConfig,
  ): void => {
    setConfig((s) => ({ ...s, [key]: next }));
  };

  const updateTeam = (
    key: TeamSubagentKey,
    next: SubagentConfig,
  ): void => {
    setTeamConfig((s) => ({ ...s, [key]: next }));
  };

  const updateCustom = (key: string, next: CustomSubagentEntry): void => {
    setCustomMap((s) => ({ ...s, [key]: next }));
  };

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await Promise.all([
        setSubagentsConfig(config),
        setTeamSubagentsConfig(teamConfig),
        setCustomSubagents(customMap),
      ]);
      // 热更新后端配置，无需重启
      await reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const restart = async (): Promise<void> => {
    setErrMsg(null);
    try {
      setRestarting(true);
      await save();
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

  // 打开编辑弹窗（内置）
  const openEditBuiltin = (meta: BuiltinMeta): void => {
    const cfg = config[meta.key];
    setModalData({
      builtinKey: meta.key,
      name: meta.label,
      enabled: cfg.enabled,
      temperature: cfg.temperature,
      systemPrompt: cfg.systemPrompt,
      tools: cfg.tools,
      triggerDescription: cfg.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开编辑弹窗（团队角色）
  const openEditTeam = (meta: TeamMeta): void => {
    const cfg = teamConfig[meta.key];
    setModalData({
      teamKey: meta.key,
      name: meta.label,
      enabled: cfg.enabled,
      temperature: cfg.temperature,
      systemPrompt: cfg.systemPrompt,
      tools: cfg.tools,
      triggerDescription: cfg.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开编辑弹窗（自定义已存在）
  const openEditCustom = (entry: CustomSubagentEntry): void => {
    setModalData({
      customKey: entry.key,
      name: entry.name,
      enabled: entry.enabled,
      temperature: entry.temperature,
      systemPrompt: entry.systemPrompt,
      tools: entry.tools,
      triggerDescription: entry.triggerDescription,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开新建弹窗
  const openNewCustom = (): void => {
    setModalData({
      customKey: "",
      name: "",
      enabled: true,
      temperature: 0.2,
      systemPrompt: "",
      tools: [],
      triggerDescription: "",
    });
    setModalIsNew(true);
    setModalOpen(true);
  };

  // 弹窗保存
  const handleModalSave = (data: SubagentEditModalData): void => {
    if (data.builtinKey) {
      // 内置：仅更新可编辑字段
      updateBuiltin(data.builtinKey, {
        enabled: data.enabled,
        temperature: data.temperature,
        systemPrompt: data.systemPrompt,
        tools: data.tools,
        triggerDescription: data.triggerDescription,
      });
      setModalOpen(false);
    } else if (data.teamKey) {
      // 团队角色
      updateTeam(data.teamKey, {
        enabled: data.enabled,
        temperature: data.temperature,
        systemPrompt: data.systemPrompt,
        tools: data.tools,
        triggerDescription: data.triggerDescription,
      });
      setModalOpen(false);
    } else if (data.customKey) {
      // 提取到局部 const 以便 TS 在 async 闭包内正确收窄类型
      const customKey = data.customKey;
      if (modalIsNew) {
        // 新建：调用 IPC addCustomSubagent（会校验 key 唯一性）
        void (async () => {
          try {
            const entry = await addCustomSubagent({
              key: customKey,
              name: data.name,
              enabled: data.enabled,
              temperature: data.temperature,
              systemPrompt: data.systemPrompt,
              tools: data.tools,
              triggerDescription: data.triggerDescription,
            });
            setCustomMap((s) => ({ ...s, [entry.key]: entry }));
            setModalOpen(false);
          } catch (e) {
            setErrMsg(e instanceof Error ? e.message : String(e));
          }
        })();
      } else {
        // 编辑现有自定义
        updateCustom(customKey, {
          key: customKey,
          name: data.name,
          enabled: data.enabled,
          temperature: data.temperature,
          systemPrompt: data.systemPrompt,
          tools: data.tools,
          triggerDescription: data.triggerDescription,
        });
        setModalOpen(false);
      }
    } else {
      setErrMsg("无效的弹窗数据");
    }
  };

  // 删除自定义
  const handleRemoveCustom = (key: string): void => {
    void (async () => {
      try {
        await removeCustomSubagent(key);
        setCustomMap((s) => {
          const next = { ...s };
          delete next[key];
          return next;
        });
      } catch (e) {
        setErrMsg(e instanceof Error ? e.message : String(e));
      }
    })();
  };

  const builtinDisabledCount = useMemo(
    () =>
      [config.rag, config.web].filter((c) => !c.enabled).length,
    [config],
  );

  const teamDisabledCount = useMemo(
    () => Object.values(teamConfig).filter((c) => !c.enabled).length,
    [teamConfig],
  );

  const customDisabledCount = useMemo(
    () => Object.values(customMap).filter((c) => !c.enabled).length,
    [customMap],
  );

  const existingCustomKeys = useMemo(
    () => Object.keys(customMap),
    [customMap],
  );

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置内置（RAG/Web）与自定义子代理。所有卡片默认折叠，点击展开查看详情或编辑。
          保存后需重启后端生效。
        </span>
      </div>

      {errMsg && <ErrorBanner message={errMsg} />}

      {(builtinDisabledCount > 0 || teamDisabledCount > 0 || customDisabledCount > 0) && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>
            有 {builtinDisabledCount + teamDisabledCount + customDisabledCount} 个子代理被禁用，相关路由将回退到主代理。
          </span>
        </div>
      )}

      {/* 内置子代理分组 */}
      <SubagentGroup
        title="内置子代理"
        icon={Bot}
        count={BUILTIN_SUBAGENTS.length}
        defaultOpen={false}
      >
        {BUILTIN_SUBAGENTS.map((meta) => (
          <SubagentCard
            key={meta.key}
            variant="builtin"
            meta={meta}
            cfg={config[meta.key]}
            onEnabledChange={(enabled) =>
              updateBuiltin(meta.key, { ...config[meta.key], enabled })
            }
            onEdit={() => openEditBuiltin(meta)}
          />
        ))}
      </SubagentGroup>

      {/* 软件开发专家团分组 — 场景化架构下始终展示（与运行时场景解耦）*/}
      <SubagentGroup
        title="软件开发专家团"
        icon={Users}
        count={TEAM_SUBAGENTS.length}
        defaultOpen={false}
      >
          {TEAM_SUBAGENTS.map((meta) => (
            <SubagentCard
              key={meta.key}
              variant="builtin"
              meta={meta}
              cfg={teamConfig[meta.key]}
              onEnabledChange={(enabled) =>
                updateTeam(meta.key, { ...teamConfig[meta.key], enabled })
              }
              onEdit={() => openEditTeam(meta)}
            />
          ))}
      </SubagentGroup>

      {/* 自定义子代理分组 */}
      <SubagentGroup
        title="自定义子代理"
        icon={User}
        count={Object.keys(customMap).length}
      >
        {Object.values(customMap).length === 0 ? (
          <div className="rounded-lg border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
            暂无自定义子代理，点击下方按钮新建
          </div>
        ) : (
          Object.values(customMap).map((entry) => (
            <SubagentCard
              key={entry.key}
              variant="custom"
              entry={entry}
              onEnabledChange={(enabled) =>
                updateCustom(entry.key, { ...entry, enabled })
              }
              onEdit={() => openEditCustom(entry)}
              onRemove={() => handleRemoveCustom(entry.key)}
            />
          ))
        )}
        <button
          type="button"
          onClick={openNewCustom}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-brand-500/50 px-3 py-2 text-brand-600 hover:bg-brand-500/5 dark:text-brand-400"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
        >
          <Plus className="h-3.5 w-3.5" />
          新建自定义子代理
        </button>
      </SubagentGroup>

      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
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
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            <Check className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>

      <SubagentEditModal
        open={modalOpen}
        initial={modalData}
        existingCustomKeys={existingCustomKeys}
        isNew={modalIsNew}
        onClose={() => setModalOpen(false)}
        onSave={handleModalSave}
      />
    </div>
  );
}
