/**
 * 通用 CRUD 列表 hook，统一 5 个 memory 子组件的 list/add/update/delete/reset 逻辑。
 *
 * 抽取自 PreferenceManager / ProfileManager / ProjectMemoryManager / SkillsManager /
 * SessionManager 中重复的 entries + draft + errMsg + refresh + saveDraft + remove 模式。
 *
 * 设计要点：
 * - T 为列表项类型（也兼作编辑草稿），调用方通过 initialItem 提供空草稿
 * - isNew 通过内部 boolean 跟踪，避免依赖 id 是否为空做推断
 * - validate 返回错误消息字符串；返回 null 表示通过
 * - startEdit 为同步 setter；若需异步拉取详情（如 SkillsManager.getSkill），
 *   调用方自行 await 后再 setEditing
 */
import { useCallback, useEffect, useState } from "react";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";

export interface UseCrudListOptions<T> {
  /** 拉取列表 */
  fetcher: () => Promise<T[]>;
  /** 新建条目 */
  creator: (item: T) => Promise<T | void>;
  /** 更新条目 */
  updater: (item: T) => Promise<T | void>;
  /** 删除条目（按 id） */
  deleter: (id: string) => Promise<unknown>;
  /** 新建空草稿 */
  initialItem: () => T;
  /** 校验函数，返回错误消息或 null */
  validate?: (item: T) => string | null;
}

export interface UseCrudListReturn<T> {
  items: T[];
  /** 是否完成首次加载 */
  loaded: boolean;
  /** 列表级错误（fetch/remove/save 失败） */
  error: string | null;
  /** 当前编辑草稿；null 表示未在编辑 */
  editing: T | null;
  /** 是否为新建模式（true=creator / false=updater） */
  isNew: boolean;
  /** 草稿校验错误 */
  draftErr: string | null;
  /** 设置编辑草稿（视为编辑已有条目，isNew=false） */
  setEditing: (item: T | null) => void;
  /** 更新草稿字段（保留 isNew 状态，用于表单输入双向绑定） */
  updateDraft: (item: T) => void;
  /** 开始新建（用 initialItem 填充草稿） */
  startNew: () => void;
  /** 保存草稿（isNew 决定走 creator 或 updater） */
  save: () => Promise<void>;
  /** 按 id 删除条目 */
  remove: (id: string) => Promise<void>;
  /** 取消编辑，清空草稿 */
  reset: () => void;
  /** 重新拉取列表 */
  refresh: () => Promise<void>;
}

export function useCrudList<T>(opts: UseCrudListOptions<T>): UseCrudListReturn<T> {
  const [items, setItems] = useState<T[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditingState] = useState<T | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [draftErr, setDraftErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const result = await opts.fetcher();
      setItems(result);
    } catch (e) {
      const msg = humanizeError(e);
      setError(msg);
      logger.warn("useCrudList.fetcher failed", e);
    } finally {
      setLoaded(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setEditing = useCallback((item: T | null) => {
    setEditingState(item);
    setDraftErr(null);
    setIsNew(false);
  }, []);

  const updateDraft = useCallback((item: T) => {
    setEditingState(item);
  }, []);

  const startNew = useCallback(() => {
    setEditingState(opts.initialItem());
    setDraftErr(null);
    setIsNew(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = useCallback(async () => {
    if (!editing) return;
    if (opts.validate) {
      const err = opts.validate(editing);
      if (err) {
        setDraftErr(err);
        return;
      }
    }
    setDraftErr(null);
    try {
      if (isNew) {
        await opts.creator(editing);
      } else {
        await opts.updater(editing);
      }
      setEditingState(null);
      setIsNew(false);
      await refresh();
    } catch (e) {
      const msg = humanizeError(e);
      setError(msg);
      logger.warn("useCrudList.save failed", e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, isNew, refresh]);

  const remove = useCallback(
    async (id: string) => {
      setError(null);
      try {
        await opts.deleter(id);
        await refresh();
      } catch (e) {
        const msg = humanizeError(e);
        setError(msg);
        logger.warn("useCrudList.remove failed", e);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [refresh],
  );

  const reset = useCallback(() => {
    setEditingState(null);
    setDraftErr(null);
    setIsNew(false);
  }, []);

  return {
    items,
    loaded,
    error,
    editing,
    isNew,
    draftErr,
    setEditing,
    updateDraft,
    startNew,
    save,
    remove,
    reset,
    refresh,
  };
}
