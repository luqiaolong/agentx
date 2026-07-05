import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  Database,
  Globe,
  Save,
  Check,
  RotateCw,
  AlertTriangle,
  Plus,
  Pencil,
  Trash2,
  Lock,
  User,
} from "lucide-react";
import type {
  SubagentConfig,
  SubagentsConfig,
  ToolsConfig,
  CustomSubagentsMap,
  CustomSubagentEntry,
} from "@/lib/utils";
import {
  SubagentEditModal,
  type SubagentEditModalData,
} from "./SubagentEditModal";

// 子代理键名与后端 backend/app/config.py _default_subagents() 一致
type BuiltinSubagentKey = "code" | "rag" | "web";

// 内置子代理可选工具清单（与 backend _ALL_TOOLS 一致，但不含危险工具）
const ALL_TOOLS: string[] = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "web_search",
  "rag_retrieve",
];

interface BuiltinMeta {
  key: BuiltinSubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

const BUILTIN_SUBAGENTS: BuiltinMeta[] = [
  {
    key: "code",
    label: "Code 子代理",
    desc: "代码检索与文件系统只读操作",
    Icon: Code2,
  },
  {
    key: "rag",
    label: "RAG 子代理",
    desc: "知识库与向量检索",
    Icon: Database,
  },
  {
    key: "web",
    label: "Web 子代理",
    desc: "联网搜索",
    Icon: Globe,
  },
];

const EMPTY_CONFIG: SubagentsConfig = {
  code: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
};

interface BuiltinCardProps {
  meta: BuiltinMeta;
  cfg: SubagentConfig;
  onEnabledChange: (enabled: boolean) => void;
  onEdit: () => void;
}

/** 内置子代理卡片：默认折叠，仅显示开关 + 编辑按钮。 */
function BuiltinCard({ meta, cfg, onEnabledChange, onEdit }: BuiltinCardProps) {
  const [open, setOpen] = useState(false);
  const [toolsConfig, setToolsConfig] = useState<ToolsConfig>(
    {} as ToolsConfig,
  );

  useEffect(() => {
    void (async () => {
      try {
        const tc = await window.api.settings.getToolsConfig();
        setToolsConfig(tc);
      } catch {
        // 后端未就绪时保留空对象
      }
    })();
  }, []);

  // 绑定的工具在全局均被禁用时触发警告
  const allToolsOff =
    cfg.tools.length > 0 &&
    cfg.tools.every((t) => toolsConfig[t as keyof ToolsConfig] === false);

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        cfg.enabled ? "border-default" : "border-default opacity-60"
      }`}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-c shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-c shrink-0" />
          )}
          <meta.Icon className="h-4 w-4 text-brand-500 shrink-0" />
          <span className="text-xs font-semibold text-primary-c truncate">
            {meta.label}
          </span>
          <span className="truncate text-[11px] text-muted-c">{meta.desc}</span>
        </button>

        {/* 开关（始终显示） */}
        <button
          type="button"
          role="switch"
          aria-checked={cfg.enabled}
          data-checked={cfg.enabled}
          onClick={() => onEnabledChange(!cfg.enabled)}
          className="switch-track shrink-0"
        >
          <span className="switch-thumb" data-checked={cfg.enabled} />
        </button>

        {/* 编辑按钮 */}
        <button
          type="button"
          onClick={onEdit}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-brand-500 shrink-0"
          aria-label="编辑"
          title="编辑"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>

      {open && (
        <div className="space-y-2 border-t border-default px-3 py-2.5">
          <div className="flex items-center gap-2 text-[11px] text-muted-c">
            <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5">
              <Lock className="h-2.5 w-2.5" />
              内置
            </span>
            <span>温度 {cfg.temperature.toFixed(1)}</span>
            <span>·</span>
            <span>工具 {cfg.tools.length}</span>
            <span>·</span>
            <span>关键词 {cfg.keywords.length}</span>
          </div>
          {allToolsOff && (
            <div className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3 w-3" />
              绑定的工具全部被禁用，子代理将不可用
            </div>
          )}
          {cfg.tools.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {cfg.tools.map((t) => (
                <span
                  key={t}
                  className="rounded bg-subtle px-1.5 py-0.5 font-mono text-[10px] text-secondary-c"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {cfg.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {cfg.keywords.slice(0, 6).map((k) => (
                <span
                  key={k}
                  className="rounded bg-brand-500/10 px-1.5 py-0.5 text-[10px] text-brand-600 dark:text-brand-400"
                >
                  {k}
                </span>
              ))}
              {cfg.keywords.length > 6 && (
                <span className="text-[10px] text-muted-c">
                  +{cfg.keywords.length - 6}
                </span>
              )}
            </div>
          )}
          {cfg.systemPrompt && (
            <div className="space-y-1">
              <div className="text-[10px] font-medium text-secondary-c">系统提示词</div>
              <div className="rounded bg-subtle/40 px-2 py-1 text-[10px] text-muted-c line-clamp-3">
                {cfg.systemPrompt}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface CustomCardProps {
  entry: CustomSubagentEntry;
  onEnabledChange: (enabled: boolean) => void;
  onEdit: () => void;
  onRemove: () => void;
}

/** 自定义子代理卡片：默认折叠，显示开关 + 编辑 + 删除按钮。 */
function CustomCard({
  entry,
  onEnabledChange,
  onEdit,
  onRemove,
}: CustomCardProps) {
  const [open, setOpen] = useState(false);
  const [toolsConfig, setToolsConfig] = useState<ToolsConfig>(
    {} as ToolsConfig,
  );

  useEffect(() => {
    void (async () => {
      try {
        const tc = await window.api.settings.getToolsConfig();
        setToolsConfig(tc);
      } catch {
        // 后端未就绪时保留空对象
      }
    })();
  }, []);

  const allToolsOff =
    entry.tools.length > 0 &&
    entry.tools.every((t) => toolsConfig[t as keyof ToolsConfig] === false);

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        entry.enabled ? "border-default" : "border-default opacity-60"
      }`}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-c shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-c shrink-0" />
          )}
          <User className="h-4 w-4 text-purple-500 shrink-0" />
          <span className="text-xs font-semibold text-primary-c truncate">
            {entry.name}
          </span>
          <span className="font-mono text-[10px] text-muted-c shrink-0">
            @{entry.key}
          </span>
          {entry.description && (
            <span className="truncate text-[11px] text-muted-c">
              {entry.description}
            </span>
          )}
        </button>

        {/* 开关 */}
        <button
          type="button"
          role="switch"
          aria-checked={entry.enabled}
          data-checked={entry.enabled}
          onClick={() => onEnabledChange(!entry.enabled)}
          className="switch-track shrink-0"
        >
          <span className="switch-thumb" data-checked={entry.enabled} />
        </button>

        {/* 编辑 */}
        <button
          type="button"
          onClick={onEdit}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-brand-500 shrink-0"
          aria-label="编辑"
          title="编辑"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>

        {/* 删除 */}
        <button
          type="button"
          onClick={onRemove}
          className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-rose-500 shrink-0"
          aria-label="删除"
          title="删除"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {open && (
        <div className="space-y-2 border-t border-default px-3 py-2.5">
          <div className="flex items-center gap-2 text-[11px] text-muted-c">
            <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-1.5 py-0.5 text-purple-600 dark:text-purple-400">
              <User className="h-2.5 w-2.5" />
              自定义
            </span>
            <span>温度 {entry.temperature.toFixed(1)}</span>
            <span>·</span>
            <span>工具 {entry.tools.length}</span>
            <span>·</span>
            <span>关键词 {entry.keywords.length}</span>
          </div>
          {allToolsOff && (
            <div className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3 w-3" />
              绑定的工具全部被禁用，子代理将不可用
            </div>
          )}
          {entry.tools.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {entry.tools.map((t) => (
                <span
                  key={t}
                  className="rounded bg-subtle px-1.5 py-0.5 font-mono text-[10px] text-secondary-c"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {entry.keywords.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {entry.keywords.slice(0, 6).map((k) => (
                <span
                  key={k}
                  className="rounded bg-brand-500/10 px-1.5 py-0.5 text-[10px] text-brand-600 dark:text-brand-400"
                >
                  {k}
                </span>
              ))}
              {entry.keywords.length > 6 && (
                <span className="text-[10px] text-muted-c">
                  +{entry.keywords.length - 6}
                </span>
              )}
            </div>
          )}
          {entry.systemPrompt && (
            <div className="space-y-1">
              <div className="text-[10px] font-medium text-secondary-c">系统提示词</div>
              <div className="rounded bg-subtle/40 px-2 py-1 text-[10px] text-muted-c line-clamp-3">
                {entry.systemPrompt}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

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
        <span className="text-xs font-semibold text-primary-c">{title}</span>
        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-[10px] text-muted-c">
          {count}
        </span>
      </button>
      {open && <div className="space-y-2">{children}</div>}
    </div>
  );
}

export function SubagentsSettings() {
  const [config, setConfig] = useState<SubagentsConfig>(EMPTY_CONFIG);
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
        const [cfg, custom] = await Promise.all([
          window.api.settings.getSubagentsConfig(),
          window.api.settings.getCustomSubagents(),
        ]);
        setConfig(cfg);
        setCustomMap(custom);
      } catch {
        // 后端未就绪时保留默认值
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

  const updateCustom = (key: string, next: CustomSubagentEntry): void => {
    setCustomMap((s) => ({ ...s, [key]: next }));
  };

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await Promise.all([
        window.api.settings.setSubagentsConfig(config),
        window.api.settings.setCustomSubagents(customMap),
      ]);
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
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

  // 打开编辑弹窗（内置）
  const openEditBuiltin = (meta: BuiltinMeta): void => {
    const cfg = config[meta.key];
    setModalData({
      builtinKey: meta.key,
      name: meta.label,
      description: meta.desc,
      enabled: cfg.enabled,
      temperature: cfg.temperature,
      systemPrompt: cfg.systemPrompt,
      tools: cfg.tools,
      keywords: cfg.keywords,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开编辑弹窗（自定义已存在）
  const openEditCustom = (entry: CustomSubagentEntry): void => {
    setModalData({
      customKey: entry.key,
      name: entry.name,
      description: entry.description,
      enabled: entry.enabled,
      temperature: entry.temperature,
      systemPrompt: entry.systemPrompt,
      tools: entry.tools,
      keywords: entry.keywords,
    });
    setModalIsNew(false);
    setModalOpen(true);
  };

  // 打开新建弹窗
  const openNewCustom = (): void => {
    setModalData({
      customKey: "",
      name: "",
      description: "",
      enabled: true,
      temperature: 0.2,
      systemPrompt: "",
      tools: [],
      keywords: [],
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
        keywords: data.keywords,
      });
    } else if (data.customKey) {
      // 提取到局部 const 以便 TS 在 async 闭包内正确收窄类型
      const customKey = data.customKey;
      if (modalIsNew) {
        // 新建：调用 IPC addCustomSubagent（会校验 key 唯一性）
        void (async () => {
          try {
            const entry = await window.api.settings.addCustomSubagent({
              key: customKey,
              name: data.name,
              description: data.description,
              enabled: data.enabled,
              temperature: data.temperature,
              systemPrompt: data.systemPrompt,
              tools: data.tools,
              keywords: data.keywords,
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
          description: data.description,
          enabled: data.enabled,
          temperature: data.temperature,
          systemPrompt: data.systemPrompt,
          tools: data.tools,
          keywords: data.keywords,
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
        await window.api.settings.removeCustomSubagent(key);
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
      [config.code, config.rag, config.web].filter((c) => !c.enabled).length,
    [config],
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
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-[11px] text-muted-c">
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置内置（Code/RAG/Web）与自定义子代理。所有卡片默认折叠，点击展开查看详情或编辑。
          保存后需重启后端生效。
        </span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {(builtinDisabledCount > 0 || customDisabledCount > 0) && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>
            有 {builtinDisabledCount + customDisabledCount} 个子代理被禁用，相关路由将回退到主代理。
          </span>
        </div>
      )}

      {/* 内置子代理分组 */}
      <SubagentGroup
        title="内置子代理"
        icon={Bot}
        count={BUILTIN_SUBAGENTS.length}
      >
        {BUILTIN_SUBAGENTS.map((meta) => (
          <BuiltinCard
            key={meta.key}
            meta={meta}
            cfg={config[meta.key]}
            onEnabledChange={(enabled) =>
              updateBuiltin(meta.key, { ...config[meta.key], enabled })
            }
            onEdit={() => openEditBuiltin(meta)}
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
          <div className="rounded-lg border border-dashed border-default px-3 py-4 text-center text-[11px] text-muted-c">
            暂无自定义子代理，点击下方按钮新建
          </div>
        ) : (
          Object.values(customMap).map((entry) => (
            <CustomCard
              key={entry.key}
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
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-brand-500/50 px-3 py-2 text-xs text-brand-600 hover:bg-brand-500/5 dark:text-brand-400"
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
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
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
