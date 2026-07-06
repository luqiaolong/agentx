import { useRef, useState } from "react";
import { AlertCircle } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { useSettingsStore } from "@/stores/settings";
import { usePermissionStore } from "@/stores/permission";
import { useAgentModeStore } from "@/stores/agentMode";
import { SCENE_PROMPTS, useSceneStore } from "@/stores/scene";
import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { AssistantUIThread } from "./AssistantUIThread";
import { EmptyState } from "./EmptyState";
import { TodoProgress } from "./TodoProgress";
import { ChatComposer } from "./ChatComposer";
import {
  BUILTIN_COMMANDS,
  findBuiltinCommand,
  type BuiltinCommand,
} from "@/stores/commands";

// 稳定空数组：currentId 为 null 时避免每次 selector 返回新 [] 触发无谓重渲
const EMPTY_MESSAGES: ChatMessage[] = [];

/**
 * 本地指令执行结果：
 * - kind=info：纯文本提示（绿色气泡）
 * - kind=list：折叠的多行列表（用于 /help /version 等）
 * - kind=error：错误提示（红色气泡）
 */
type CommandResult =
  | { kind: "info"; text: string }
  | { kind: "list"; title: string; items: string[] }
  | { kind: "error"; text: string };

export function ChatView() {
  const messages = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId]?.messages ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  );
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const createSession = useChatStore((s) => s.createSession);
  const addMessage = useChatStore((s) => s.addMessage);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const deleteMessagesAfter = useChatStore((s) => s.deleteMessagesAfter);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const updateTask = useTasksStore((s) => s.updateTask);
  const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);
  const setTheme = useSettingsStore((s) => s.setTheme);

  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);


  const pendingIdRef = useRef<string>("pending");
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");

  useChatStream({ pendingIdRef, currentTaskIdRef, lastUserQueryRef, setTodos, setErrorMsg });
  const bottomRef = useAutoScroll(messages);

  // 把 CommandResult 序列化为 markdown-ish 文本塞进 assistant 消息。
  const appendCommandResult = (result: CommandResult) => {
    let text: string;
    if (result.kind === "list") {
      text = `**${result.title}**\n\n${result.items.map((it) => `- ${it}`).join("\n")}`;
    } else {
      text = result.text;
    }
    addMessage({
      id: crypto.randomUUID(),
      role: "assistant",
      content: text,
      ts: Date.now(),
    });
  };

  /**
   * 内置命令分发器。
   * 与"普通消息 + LLM 流"的区别：本地同步执行，不发后端，
   * 不走 SSE，避免对后端造成无意义的请求。
   */
  const runBuiltinCommand = async (cmd: BuiltinCommand, raw: string): Promise<boolean> => {
    const args = raw.replace(/^\/\S+/, "").trim();

    switch (cmd.name) {
      case "help": {
        const builtinLines = BUILTIN_COMMANDS.map(
          (c) => `/${c.name}${c.aliases?.length ? ` (${c.aliases.join(", ")})` : ""} — ${c.description}`,
        );
        appendCommandResult({
          kind: "list",
          title: "内置命令",
          items: builtinLines,
        });
        return true;
      }
      case "clear": {
        const resetTid = currentId ?? "";
        try {
          await window.api.chat.send({ role: "user", content: raw }, { threadId: resetTid });
        } catch {
          /* 后端不可用也允许前端清空 */
        }
        clearMessages();
        setTodos([]);
        currentTaskIdRef.current = null;
        lastUserQueryRef.current = "";
        setErrorMsg(null);
        appendCommandResult({ kind: "info", text: "已清空当前会话消息。" });
        return true;
      }
      case "compact": {
        const tid = currentId ?? "";
        if (!tid) {
          appendCommandResult({ kind: "error", text: "无当前会话，无法压缩。" });
          return true;
        }
        try {
          const result = await window.api.chat.compact(tid);
          if (result.ok) {
            appendCommandResult({
              kind: "info",
              text: `已压缩 ${result.compressed_count ?? 0} 条消息为摘要。`,
            });
          } else {
            appendCommandResult({
              kind: "info",
              text: result.error ?? "压缩失败。",
            });
          }
        } catch (err) {
          appendCommandResult({
            kind: "error",
            text: `压缩请求失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
        return true;
      }
      case "settings": {
        setSettingsOpen(true);
        appendCommandResult({ kind: "info", text: "已打开设置面板。" });
        return true;
      }
      case "theme": {
        const target = args.toLowerCase();
        if (target === "dark" || target === "light") {
          setTheme(target);
          appendCommandResult({ kind: "info", text: `已切换到 ${target === "dark" ? "深色" : "浅色"} 主题。` });
        } else if (target === "") {
          // 无参：与 toggleTheme 行为一致
          const cur = useSettingsStore.getState().theme;
          setTheme(cur === "dark" ? "light" : "dark");
          appendCommandResult({
            kind: "info",
            text: `已切换到 ${cur === "dark" ? "浅色" : "深色"} 主题。`,
          });
        } else {
          appendCommandResult({
            kind: "error",
            text: `theme 参数无效：${args}（应为 dark 或 light）`,
          });
        }
        return true;
      }
      case "model": {
        if (!args) {
          // 无参：打开设置面板让用户手动选择
          setSettingsOpen(true);
          appendCommandResult({ kind: "info", text: "已打开设置面板，请在「模型」tab 选择。" });
          return true;
        }
        try {
          const entries = await window.api.settings.getModelEntries();
          // 优先精确匹配 id，其次大小写不敏感匹配 label
          const target =
            entries.find((e) => e.id === args) ??
            entries.find((e) => e.label.toLowerCase() === args.toLowerCase());
          if (!target) {
            const available = entries
              .map((e) => `${e.label}（id: ${e.id}）`)
              .join("、");
            appendCommandResult({
              kind: "error",
              text: `未找到模型「${args}」。可用模型：${
                available || "（暂无，请在设置中添加）"
              }`,
            });
            return true;
          }
          await window.api.settings.activateModel(target.id);
          await window.api.app.reloadBackendConfig();
          appendCommandResult({
            kind: "info",
            text: `已激活模型「${target.label}」，配置已即时生效。`,
          });
        } catch (err) {
          appendCommandResult({
            kind: "error",
            text: `切换模型失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
        return true;
      }
      case "skills": {
        try {
          const { skills } = await window.api.skills.list();
          const items = skills.map(
            (s) => `${s.name}${s.description ? ` — ${s.description}` : ""}`,
          );
          appendCommandResult({
            kind: "list",
            title: `已加载技能（${skills.length}）`,
            items: items.length > 0 ? items : ["（暂无技能）"],
          });
        } catch (err) {
          appendCommandResult({
            kind: "error",
            text: `获取技能失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
        return true;
      }
      case "version": {
        try {
          const [appVer, health] = await Promise.all([
            window.api.app.getVersion(),
            window.api.health.check().catch(() => null),
          ]);
          const items = [
            `renderer 版本：${appVer}`,
            `后端状态：${health?.status ?? "unknown"}`,
          ];
          if (health?.embedding) {
            items.push(`embedding：${health.embedding.status} (${health.embedding.latency_ms ?? "?"}ms)`);
          }
          if (health?.milvus) {
            items.push(`milvus：${health.milvus.status}`);
          }
          appendCommandResult({ kind: "list", title: "应用状态", items });
        } catch (err) {
          appendCommandResult({
            kind: "error",
            text: `获取版本失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
        return true;
      }
      case "init": {
        try {
          const result = await window.api.app.initAgentsMd();
          if (result.ok) {
            appendCommandResult({
              kind: "info",
              text: result.message ?? "AGENTS.md 初始化完成。",
            });
          } else {
            appendCommandResult({
              kind: "error",
              text: result.error ?? "AGENTS.md 初始化失败。",
            });
          }
        } catch (err) {
          appendCommandResult({
            kind: "error",
            text: `初始化失败：${err instanceof Error ? err.message : String(err)}`,
          });
        }
        return true;
      }
      default:
        return false;
    }
  };

  const handleSend = async (content: string) => {
    if (!content || isStreaming) return;

    // 内置命令本地分发，不发后端
    const trimmed = content.trim();
    if (trimmed.startsWith("/")) {
      const cmd = findBuiltinCommand(trimmed);
      if (cmd) {
        const tid = currentId ?? createSession();
        addMessage({ id: crypto.randomUUID(), role: "user", content, ts: Date.now() });
        await runBuiltinCommand(cmd, trimmed);
        return;
      }
      // 不识别的 / 命令：继续走 LLM，让模型回答"该命令不存在"
    }

    // 多会话：若当前无会话先创建
    const tid = currentId ?? createSession();

    addMessage({ id: crypto.randomUUID(), role: "user", content, ts: Date.now() });
    const pendingId = `pending-${crypto.randomUUID()}`;
    pendingIdRef.current = pendingId;
    addMessage({ id: pendingId, role: "assistant", content: "", ts: Date.now() });

    // 新一轮发送：重置任务追踪状态，让 todo_update 创建新任务而非更新旧任务
    currentTaskIdRef.current = null;
    lastUserQueryRef.current = content;
    setTodos([]);
    setStreaming(true);
    setErrorMsg(null);
    // 标记当前会话进入执行状态
    setSessionRunning(tid, true);

    try {
      // 从 permission store 读取会话级权限模式（不订阅，避免无谓重渲）
      const permissionMode = usePermissionStore.getState().mode;
      // 从 agent mode store 读取用户级代理模式偏好
      const agentMode = useAgentModeStore.getState().mode;
      // 从 scene store 读取当前场景 prompt（不订阅，避免无谓重渲）
      const scene = useSceneStore.getState().scene;
      await window.api.chat.send(
        { role: "user", content },
        { threadId: tid, permissionMode, agentMode, systemPrompt: SCENE_PROMPTS[scene] },
      );
    } catch {
      setStreaming(false);
      setSessionRunning(tid, false);
      setErrorMsg("发送失败，请检查后端是否运行");
      // 失败时也标记当前任务为 failed
      const failTid = currentTaskIdRef.current;
      if (failTid) {
        updateTask(failTid, { status: "failed" });
        currentTaskIdRef.current = null;
      }
    }
  };

  const handleAbort = async () => {
    if (!currentId) return;
    try {
      await window.api.chat.abort(currentId);
    } catch {
      /* ignore */
    }
    setStreaming(false);
    // 用户中止：标记当前任务为 failed
    const tid = currentTaskIdRef.current;
    if (tid) {
      updateTask(tid, { status: "failed" });
      currentTaskIdRef.current = null;
    }
  };

  const completedTodos = todos.filter((t) => t.done).length;

  return (
    <div className="flex h-full flex-col bg-app">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <AssistantUIThread
            messages={messages}
            isStreaming={isStreaming}
            onEditSubmit={(messageId, newContent) => {
              // 就地编辑提交：删除该消息及之后的所有消息，重新发送编辑后的内容
              if (isStreaming) return;
              deleteMessagesAfter(messageId);
              setTodos([]);
              setErrorMsg(null);
              // 触发重新发送（复用 handleSend）
              void handleSend(newContent);
            }}
          />
        )}
        <div ref={bottomRef} />
      </div>

      {/* 任务进度 */}
      {todos.length > 0 && (
        <TodoProgress todos={todos} completedTodos={completedTodos} />
      )}

      {/* 错误提示 */}
      {(errorMsg || dropError) && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300" style={{ fontSize: 'var(--fs-msg-assist)' }}>
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{errorMsg ?? dropError}</span>
          </div>
        </div>
      )}

      {/* 输入区 */}
      <ChatComposer
        isStreaming={isStreaming}
        setDropError={setDropError}
        onSend={handleSend}
        onAbort={handleAbort}
      />
    </div>
  );
}