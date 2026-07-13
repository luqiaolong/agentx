import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage, MessagePart, TeamAgentState } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { ChatEvent, TodoStatus } from "@/lib/utils";
import { chat, getCurrentTraceId } from "@/lib/api/chat";
import { stripSkillTag } from "@/lib/skillTag";

export interface TodoItem {
  content: string;
  status: TodoStatus;
  taskId?: string;
}

/**
 * 从 todo_update 事件提取 todo 列表（deepagents 原生 {content, status, task_id} schema）。
 *
 * SSE 契约: 后端发 `{"todos": [{"content": "...", "status": "pending"|"in_progress"|"completed", "task_id?": "..."}]}`。
 */
function normalizeTodos(
  todosField: unknown,
  fallbackTaskId?: string,
): TodoItem[] {
  if (!Array.isArray(todosField)) return [];
  return todosField
    .map((item): TodoItem | null => {
      if (typeof item !== "object" || item === null) return null;
      const obj = item as Record<string, unknown>;
      const content =
        typeof obj.content === "string" ? obj.content : String(obj.content ?? "");
      const status: TodoStatus =
        obj.status === "pending" ||
        obj.status === "in_progress" ||
        obj.status === "completed"
          ? obj.status
          : "pending";
      const taskId =
        typeof obj.task_id === "string" && obj.task_id.length > 0
          ? obj.task_id
          : fallbackTaskId;
      return { content, status, ...(taskId ? { taskId } : {}) };
    })
    .filter((x): x is TodoItem => x !== null);
}

export interface UseChatStreamArgs {
  threadId?: string;
  /** 当前正在流式输出的 thread id（发送时固定，不因用户切会话而漂移）。 */
  activeThreadIdRef?: MutableRefObject<string | null>;
  pendingIdRef: MutableRefObject<string | null>;
  currentTaskIdRef: MutableRefObject<string | null>;
  lastUserQueryRef: MutableRefObject<string>;
  setErrorMsg: (msg: string | null) => void;
  setPaused?: (paused: boolean) => void;
}

/**
 * 订阅 SSE 事件与审批请求（仅在挂载时绑定一次）。
 *
 * 按 event.type 分发到 part 操作（chat-rendering-trace-v2 D3）：
 * - token → appendPartText(pending, "text", data)
 * - token_rollback → removeLastTextPart(pending)（撤回误推为 token 的计划文本）
 * - reasoning_delta → appendPartText(pending, "reasoning", delta)（实时追加到当前 thinking block）
 * - reasoning → appendReasoningStep(pending, content)（完整内容，独立成 part）
 * - tool_call → addPart(pending, {type:"tool-call", ...})
 * - tool_result → addPart(pending, {type:"tool-result", ...})
 * - delegation → addPart(pending, {type:"delegation", ...})
 * - done → markReasoningDone(pending) + 结束流式
 * - todo_update → 保留底部 TodoProgress 逻辑（任务级进度，与 tool_call 并存）
 * - approval_request → 保留 ApprovalDialog 逻辑
 * - error → 保留错误处理
 */
export function useChatStream(args: UseChatStreamArgs) {
  const { threadId, activeThreadIdRef, pendingIdRef, currentTaskIdRef, lastUserQueryRef, setErrorMsg, setPaused } = args;

  const appendPartText = useChatStore((s) => s.appendPartText);
  const appendReasoningStep = useChatStore((s) => s.appendReasoningStep);
  const addPart = useChatStore((s) => s.addPart);
  const upsertTeamNode = useChatStore((s) => s.upsertTeamNode);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const markRunningToolCallsComplete = useChatStore((s) => s.markRunningToolCallsComplete);
  const removeLastTextPart = useChatStore((s) => s.removeLastTextPart);
  const deleteMessage = useChatStore((s) => s.deleteMessage);
  const enqueueApprovalRequest = useChatStore((s) => s.enqueueApprovalRequest);
  const attachApprovalToToolCall = useChatStore((s) => s.attachApprovalToToolCall);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const setMessageTraceId = useChatStore((s) => s.setMessageTraceId);
  const setMessageTokenCount = useChatStore((s) => s.setMessageTokenCount);
  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);
  const currentId = useChatStore((s) => s.currentId);

  // 缓存最新 callbacks 与 threadId，避免事件处理闭包捕获旧值
  //（特别是用户切换会话后，SSE 事件仍按原 thread_id 路由）。
  const threadIdRef = useRef(threadId);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  const currentIdRef = useRef<string | null>(currentId);
  useEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);

  const callbacksRef = useRef({ setErrorMsg, setPaused });
  useEffect(() => {
    callbacksRef.current = { setErrorMsg, setPaused };
  }, [setErrorMsg, setPaused]);

  // done 看门狗：team_done 后若 done 事件 2s 内未到达，强制收尾流式状态
  const doneWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 获取 SSE 事件应归属的 thread id：优先使用发送时固定的 activeThreadIdRef。 */
  const targetThreadId = () => activeThreadIdRef?.current ?? threadIdRef.current;

  /**
   * 观测中心：把 SSE 事件携带的 trace_id 同步到 pending assistant 消息。
   *
   * - 后端沿用前端预生成的 trace_id（chat.ts::send()）→ 值相同，无变化；
   * - 后端自行生成 → 回写覆盖前端值，保证后续 feedback 写入与后端 observation_run.run_id 一致。
   *
   * token 事件 data 是纯字符串不携带 trace_id，故不调用；其余 JSON 事件
   * （reasoning / tool_call / tool_result / delegation 等）均通过 `_tid ?? trace_id`
   * 字段读取。
   */
  const syncTraceId = (e: ChatEvent) => {
    const tid = e._tid ?? e.trace_id;
    if (tid && pendingIdRef.current) {
      setMessageTraceId(pendingIdRef.current, tid);
    }
  };

  const finishRunning = (running: boolean) => {
    const cid = targetThreadId();
    if (cid) {
      setSessionRunning(cid, running);
    }
  };

  useEffect(() => {
    if (!threadId) return;
    const unsubEvents = chat.onEvent(threadId, (e: ChatEvent) => {
      // 观测中心：先把可能的 trace_id 同步到 pending 消息（每个事件都跑一次，幂等）。
      syncTraceId(e);
      switch (e.type) {
        case "token": {
          // token 事件 data 是纯字符串
          // B6 修复：后端 router 在 workspace_fallback 时 yield 的 "[工作区恢复] ..." 通知
          // token 仅用于提示用户当前 workspace 是自动恢复的历史授权（§13 SSE 契约
          // 未引入新事件类型，避免破坏前后端对齐）。
          // 前端识别此前缀后跳过渲染为 message text part，改由工作区徽章 / toast
          // 组件订阅 store 单独展示（具体组件实现由后续 PR 完成，本处仅过滤避免污染文本流）。
          const tokenStr = String(e.data ?? "");
          if (tokenStr.startsWith("[工作区恢复]")) {
            // 暂不消费，后续接入工作区徽章 / toast 时再处理
            break;
          }
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "text", tokenStr);
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "token_rollback": {
          // 模型把计划文本误推为 token 后撤回：删除当前最后一个 text part
          if (pendingIdRef.current) {
            removeLastTextPart(pendingIdRef.current);
          }
          break;
        }
        case "reasoning_delta": {
          // 主 agent 路径的实时 thinking token：追加到当前未 done 的 reasoning part
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "reasoning", String(e.delta ?? ""));
          }
          break;
        }
        case "reasoning": {
          // 多 LLM step 推理各自独立展示为独立 ReasoningBlock，
          // 不再 appendPartText（会累积合并）；走 appendReasoningStep：
          // 关闭上一个未 done 的 reasoning + push 独立新 part。
          if (pendingIdRef.current) {
            appendReasoningStep(pendingIdRef.current, e.content);
          }
          break;
        }
        case "tool_call": {
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
              type: "tool-call",
              id: e.id,
              toolName: e.name,
              args: e.args,
              source: e.source,
              status: "running",
              startedAt: Date.now(),
            });
          }
          break;
        }
        case "tool_result": {
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
              type: "tool-result",
              id: e.id,
              toolName: e.name,
              result: e.result,
              source: e.source,
              arrivedAt: Date.now(),
              ...(e.error !== undefined ? { error: e.error } : {}),
            });
          }
          break;
        }
        case "delegation": {
          if (pendingIdRef.current) {
            // aborted/timeout 事件变体：子任务中止或超时，不再创建 delegation part，
            // 而是把对应 agent 标记为 error + 记录失败原因到 summary。
            if (e.event === "aborted" || e.event === "timeout") {
              if (e.source === "team") {
                upsertTeamNode(pendingIdRef.current, {
                  agentUpdate: {
                    agent: e.target,
                    taskId: e.task_id,
                    patch: {
                      status: "error",
                      finishedAt: Date.now(),
                      summary: e.event === "aborted" ? "用户中止" : `执行超时${e.timeout ? `（${e.timeout}s）` : ""}`,
                    },
                  },
                  createIfMissing: false,
                });
              }
              break;
            }
            addPart(pendingIdRef.current, {
              type: "delegation",
              id: crypto.randomUUID(),
              target: e.target,
              source: e.source,
              message: e.message,
              ...(e.task_id ? { taskId: e.task_id } : {}),
            });
            // Team 路径的 delegation 事件（source="team"）：同步更新 TeamNodeCard 中
            // 对应 agent 的状态为 running，让用户看到子代理正在执行。
            if (e.source === "team") {
              upsertTeamNode(pendingIdRef.current, {
                agentUpdate: {
                  agent: e.target,
                  taskId: e.task_id,
                  patch: { status: "running", startedAt: Date.now() },
                },
                createIfMissing: false,
              });
            }
          }
          break;
        }
        case "classification": {
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
              type: "classification",
              id: crypto.randomUUID(),
              label: e.label,
              reason: e.reason,
            });
          }
          break;
        }
        case "paused": {
          callbacksRef.current.setPaused?.(true);
          // 方案C：后端已结束 SSE 流，前端模拟 done 事件完成当前消息
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            markRunningToolCallsComplete(pendingIdRef.current);
          }
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          pendingIdRef.current = null;
          // 任务保持 running 状态（恢复后会继续更新）
          break;
        }
        case "done": {
          // 清除 done 看门狗：done 已正常到达，无需兜底
          if (doneWatchdogRef.current) {
            clearTimeout(doneWatchdogRef.current);
            doneWatchdogRef.current = null;
          }
          // 标记 reasoning parts 完成（触发自动收缩）
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            // 兜底：流结束时仍有 status=running 的 tool-call（通常是 tool_result 事件
            // 因连接中断等原因未送达），强制 close 为 complete，让 UI 不再卡在「运行中」
            markRunningToolCallsComplete(pendingIdRef.current);
            // 后端 done 事件可能携带真实 token_count（JSON 对象）
            // 兼容两种 payload 格式：
            // 1. data 字段为对象：{ data: { token_count: N } }
            // 2. payload 展开到顶层：{ token_count: N }（e.data 为 undefined）
            const doneData =
              typeof e.data === "object" && e.data !== null
                ? (e.data as Record<string, unknown>)
                : null;
            const tc = doneData?.token_count ?? (e as Record<string, unknown>).token_count;
            if (typeof tc === "number" && Number.isFinite(tc)) {
              setMessageTokenCount(pendingIdRef.current, tc);
            }
          }
          // 只清理当前 threadId 的 streaming 状态
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          // 清理 pending message id
          pendingIdRef.current = null;
          // 标记当前任务完成 + 收尾 Team 子任务
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "done" });
            currentTaskIdRef.current = null;
            // 父任务完成时，所有 parentTaskId === tid 的 running 子任务也标记完成
            const childTasks = useTasksStore
              .getState()
              .tasks.filter(
                (t) => t.parentTaskId === tid && t.status === "running",
              );
            for (const child of childTasks) {
              updateTask(child.id, { status: "done" });
            }
          }
          callbacksRef.current.setPaused?.(false);

          // 首条有效对话（任一 agent 模式）完成后异步收敛 .agentx/ 生成（fire-and-forget）。
          // generatedAgentx 标记 + getProjectConfig 真实存在性构成双层防护；
          // 详见 stores/chat/index.ts::ensureAgentxGenerated 注释。
          // 强制使用 activeThreadIdRef（发送时固定），避免用户在 done 期间切到
          // 新会话时把 .agentx 触发错配到非流式所在会话。
          const activeTid = activeThreadIdRef?.current ?? currentIdRef.current;
          if (activeTid) {
            void useChatStore.getState().ensureAgentxGenerated(activeTid);
          }
          break;
        }
        case "error": {
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            // 兜底：error 时也清理残留的 running tool-call
            markRunningToolCallsComplete(pendingIdRef.current);
            // error 时清理空 pending assistant 消息，避免留下空白气泡
            deleteMessage(pendingIdRef.current);
            pendingIdRef.current = null;
          }
          const errData = e.data ?? e.error ?? e.message;
          // 错误消息附 trace_id：方便用户报告"任务卡死/中断"问题时直接复制
          // 提交给开发者，开发者即可 grep data/logs/backend.log 定位整条链路。
          // 优先用事件自身的 trace_id（后端注入），缺失时回退到 chat.ts 对应 threadId 的值。
          // 兼容三种 error payload 格式：
          // 1. chat.py 手工构造：{event:"error", data:"内部错误..."} → e.data 是字符串
          // 2. make_sse_event("error", {message: "..."}) → e.message 是字符串（最常见）
          // 3. 极少见：payload 里直接写 {error: "..."} → e.error 是字符串
          const baseMsg = typeof errData === "string" && errData.length > 0 ? errData : "请求出错";
          const traceId = e.trace_id ?? getCurrentTraceId(threadId) ?? null;
          const msgWithTrace = traceId ? `${baseMsg}（trace=${traceId}）` : baseMsg;
          callbacksRef.current.setErrorMsg(msgWithTrace);
          // 标记当前任务失败 + 收尾 Team 子任务
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "failed" });
            currentTaskIdRef.current = null;
            // 父任务失败时，所有 parentTaskId === tid 的 running 子任务也标记失败
            const childTasks = useTasksStore
              .getState()
              .tasks.filter(
                (t) => t.parentTaskId === tid && t.status === "running",
              );
            for (const child of childTasks) {
              updateTask(child.id, { status: "failed" });
            }
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "todo_update": {
          const taskId =
            typeof e.task_id === "string" && e.task_id.length > 0
              ? e.task_id
              : undefined;
          const parentTaskId =
            typeof e.parent_task_id === "string" && e.parent_task_id.length > 0
              ? e.parent_task_id
              : undefined;
          const source =
            typeof e.source === "string" && e.source.length > 0
              ? e.source
              : undefined;
          const incoming = normalizeTodos(e.todos, taskId);
          if (parentTaskId && source) {
            // Team 子任务路径：创建/更新子任务。
            // M14 修复：优先使用 currentTaskIdRef.current（前端主任务 UUID）作为
            // parentTaskId，确保父子链接与前端任务 ID 一致。若主任务尚未创建
            //（ref 为 null），回退到事件 parent_task_id（后端 thread_id），
            // 避免丢失子任务数据。
            const effectiveParentId = currentTaskIdRef.current ?? parentTaskId;
            const childTaskId = `${effectiveParentId}-child-${source}`;
            const sessionId = activeThreadIdRef?.current ?? currentIdRef.current ?? "";
            const existing = useTasksStore.getState().tasks.find((t) => t.id === childTaskId);
            if (existing) {
              updateTask(childTaskId, { todos: incoming, status: "running" });
            } else {
              addTask({
                id: childTaskId,
                title: source,
                status: "running",
                todos: incoming,
                createdAt: Date.now(),
                sessionId,
                parentTaskId: effectiveParentId,
                taskSource: "team",
                agentRole: source,
              });
            }
          } else {
            // 主任务路径：DeepAgent / Supervisor / Expert / Team 全局 todos
            const tid = currentTaskIdRef.current;
            if (tid) {
              updateTask(tid, { todos: incoming });
            } else if (incoming.length > 0) {
              // 优先使用事件携带的 task_id（后端 thread_id）作为任务 ID，
              // 建立 Team 子任务父子链接（子任务 parentTaskId = thread_id = 主任务 id）。
              // 若该 ID 已存在（同一会话第二次对话），复用并重置为 running。
              const existingByEventId = taskId
                ? useTasksStore.getState().tasks.find((t) => t.id === taskId)
                : undefined;
              if (existingByEventId) {
                updateTask(existingByEventId.id, {
                  todos: incoming,
                  status: "running",
                });
                currentTaskIdRef.current = existingByEventId.id;
              } else {
                const newId = taskId ?? `task-${crypto.randomUUID()}`;
                currentTaskIdRef.current = newId;
                const rawQuery = lastUserQueryRef.current
                  .replace(/<workspace>.*?<\/workspace>\s?/g, "")
                  .replace(/<file>.*?<\/file>\s?/g, "")
                  .trim();
                // 任务名隐藏 /skill:<name> 激活标记，只展示用户实际输入；
                // 剥离后为空时回退到首个技能名 → 兜底"深度任务"。
                const { text: skillStripped, fallbackSkillName } = stripSkillTag(rawQuery);
                const titleBase = skillStripped || fallbackSkillName || "";
                const title = titleBase.slice(0, 40) || "深度任务";
                // 使用当前会话 ID 作为任务归属；切换会话后任务列表自动隔离。
                // 走 currentIdRef 而非闭包 currentId —— 否则 SSE handler 永远拿到首次渲染的
                // 会话 ID,流结束后到达的延迟 todo_update 会落到 stale 闭包或 ""。
                const sessionId =
                  activeThreadIdRef?.current ?? currentIdRef.current ?? "";
                // 根据 source 推断 taskSource: "coding" 场景识别，其他默认 "work"
                const taskSource: "work" | "coding" =
                  source === "coding" ? "coding" : "work";
                addTask({
                  id: newId,
                  title,
                  status: "running",
                  todos: incoming,
                  createdAt: Date.now(),
                  sessionId,
                  taskSource,
                });
              }
            }
          }
          break;
        }
        case "team_init": {
          // team_init 事件：_plan_node 成功后发射，携带 plan + agents + summary。
          // plan 项含 {agent, description, id, depends_on}，映射为 TeamAgentState。
          if (pendingIdRef.current) {
            // 把 plan 项映射为 TeamAgentState（含 description/taskId/dependsOn）
            const initialAgents: TeamAgentState[] = e.plan.map((p) => ({
              agent: p.agent,
              description: p.description,
              taskId: p.id,
              dependsOn: p.depends_on,
              status: "pending",
            }));
            upsertTeamNode(pendingIdRef.current, {
              reasoning: e.summary,
              initialAgents,
              status: "running",
            });
          }
          if (threadId) {
            chat.setTeamMode(threadId, true);
          }
          break;
        }
        case "team_done": {
          // team_done 事件：AgentTeam 整体执行结束。
          // 仅更新已存在的 team part（由 team_init 创建）；若 team part 不存在
          //（降级路径 / plan 失败），则不创建空 team part。
          // status 语义：
          //   - "error"      → 终止态：TeamNodeCard 显示失败 + finalizeAgents
          //   - "done"       → 终止态：TeamNodeCard 显示已完成 + finalizeAgents
          //   - "replanning" → 过渡态：质量门失败但无 error，团队正在重新规划；
          //                     TeamNodeCard 保持 running，避免 finalizeAgents 把 agent
          //                     全部标记为 done 导致后续新一轮 delegation 到达时
          //                     agent 状态在 done ↔ running 间来回翻转（UI 闪烁）。
          if (!pendingIdRef.current) break;
          const agentMessages = Array.isArray(e.agents)
            ? e.agents
                .filter(
                  (a): a is { agent: string; task_id?: string; message?: string; summary?: string } =>
                    typeof a === "object" && a !== null && typeof (a as Record<string, unknown>).agent === "string",
                )
                .map((a) => ({
                  agent: a.agent,
                  ...(a.task_id !== undefined ? { taskId: a.task_id } : {}),
                  ...(a.message !== undefined ? { message: a.message } : {}),
                  ...(a.summary !== undefined ? { summary: a.summary } : {}),
                }))
            : [];
          const isReplanning = e.status === "replanning";
          const teamStatus = e.status === "error"
            ? "error"
            : isReplanning
              ? "running"
              : "done";
          upsertTeamNode(pendingIdRef.current, {
            status: teamStatus,
            finalizeAgents: e.status === "done" || e.status === "error",
            createIfMissing: false,
            agentMessages,
          });

          // FE-001 修复：replanning 不启动看门狗，只清理当前 wave 残留
          markReasoningDone(pendingIdRef.current);
          markRunningToolCallsComplete(pendingIdRef.current);

          if (isReplanning) {
            // replanning 是过渡态：不启动 done 看门狗，等待 replan 事件 + 新 delegation
            // 后续 wave 的 reasoning/tool_call 会作为新 part 创建，不受 markReasoningDone 影响
            break;
          }

          // 终态（done/error）：启动 done 看门狗
          // 安全兜底：team_done 后若 done 事件因故未到达，仍需收尾 reasoning / tool-call，
          // 避免消息卡在「运行中」状态。启动 2s done 看门狗：若 done 事件未在 2s 内
          // 到达，看门狗将强制 finishRunning(false) 收尾流式状态。
          // 防竞态：回调内检查当前会话是否仍在运行，已停止则跳过（避免误杀新消息 streaming）。
          const watchdogThreadId = targetThreadId();
          if (doneWatchdogRef.current) clearTimeout(doneWatchdogRef.current);
          doneWatchdogRef.current = setTimeout(() => {
            const cid = targetThreadId();
            // 会话已切换或已停止 → 跳过（done 已到达或用户已发新消息）
            if (!cid || cid !== watchdogThreadId) return;
            const state = useChatStore.getState();
            const session = state.sessions[cid];
            if (!session?.isRunning) return;
            finishRunning(false);
            // team_done 丢失兜底（2026-07-13 修复）：
            // 若 team_done 因网络中断未到达，TeamNodeCard 内 agent.status
            // 会永久卡在 running spinner。强制 finalize 该消息的 team part，
            // 把所有 running agent 收敛为 done，并标记 team 整体完成。
            if (pendingIdRef.current) {
              const message = session.messages.find(
                (m: ChatMessage) => m.id === pendingIdRef.current,
              );
              const teamPart = message?.parts.find(
                (p: MessagePart): p is Extract<MessagePart, { type: "team" }> => p.type === "team",
              );
              if (teamPart && teamPart.status === "running") {
                upsertTeamNode(pendingIdRef.current, {
                  status: "done",
                  finalizeAgents: true,
                  createIfMissing: false,
                });
              }
            }
          }, 2000);
          break;
        }
        case "replan": {
          // replan 事件：质量门失败后触发重规划，携带新增任务列表。
          // 追加到 TeamNodeCard 的 replanHistory + 新增 agent 行。
          if (pendingIdRef.current) {
            upsertTeamNode(pendingIdRef.current, {
              replan: {
                newTasks: e.new_tasks.map((t) => ({
                  id: t.id,
                  agent: t.agent,
                  description: t.description,
                  dependsOn: t.depends_on,
                })),
                replanCount: e.replan_count,
                reason: e.reason,
              },
              createIfMissing: false,
            });
          }
          break;
        }
        case "warning": {
          // 后端 warning 事件：未知 agent fallback / team_role 缺 system_prompt 等
          // 追加到 TeamNodeCard 的 warnings 列表，同时 console.warn 便于开发排查。
          if (e.message) {
            console.warn("[AgentTeam warning]", e.message);
            if (pendingIdRef.current) {
              upsertTeamNode(pendingIdRef.current, {
                addWarning: e.message,
                createIfMissing: false,
              });
            }
          }
          break;
        }
        default: {
          // 未知事件类型：忽略（兜底分支，避免破坏流式）
          break;
        }
      }
    });

    const unsubApproval = chat.onApprovalRequest(threadId, (req) => {
      // 内联授权：若 approval_request 携带 toolCallId，关联到对应 tool-call part
      if (req.toolCallId && pendingIdRef.current) {
        attachApprovalToToolCall(pendingIdRef.current, req.toolCallId, req);
      }
      // 只有无法关联到具体 tool-call 时（无 toolCallId），才入队走弹窗兜底
      if (!req.toolCallId) {
        enqueueApprovalRequest(req);
      }
    });

    return () => {
      // 切换会话 / 卸载前，对当前 pending 消息执行终态收尾，
      // 避免留下 reasoning 未 done / tool-call 卡 running 的不完整消息状态
      if (pendingIdRef.current) {
        markReasoningDone(pendingIdRef.current);
        markRunningToolCallsComplete(pendingIdRef.current);
      }
      // 清除 done 看门狗，避免卸载后误触发
      if (doneWatchdogRef.current) {
        clearTimeout(doneWatchdogRef.current);
        doneWatchdogRef.current = null;
      }
      unsubEvents();
      unsubApproval();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
}
