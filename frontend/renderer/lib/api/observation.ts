/**
 * 观测中心分析流：复盘 / 自进化 SSE 接收。
 *
 * 对应后端 POST /api/observation/review 和 POST /api/observation/self-evolve。
 * 事件契约与 /api/chat 一致（reasoning / token / done / error），但独立连接，
 * 不经过 chat.ts 的连接池（避免与正常对话流互斥中止）。
 *
 * 调用方通过 handlers 回调接收事件，自行写入 chat store。
 */
import { API_BASE } from "../api-constants";

/** 分析流事件回调（调用方实现，写入 chat store parts）。 */
export interface AnalysisStreamHandlers {
  /** reasoning 事件：一次性思考方向说明（独立 part）。 */
  onReasoning: (content: string) => void;
  /** token 事件：LLM 正文增量（追加到 text part）。 */
  onToken: (text: string) => void;
  /** done 事件：流结束（标记 reasoning done + 清理 running 态）。 */
  onDone: () => void;
  /** error 事件：异常（含消息）。 */
  onError: (msg: string) => void;
}

interface StreamOpts {
  runId: string;
  threadId?: string;
  signal?: AbortSignal;
  handlers: AnalysisStreamHandlers;
}

/** 解析 SSE 流并分发到 handlers。复用 chat.ts 的事件解析逻辑。 */
async function consumeSse(
  res: Response,
  handlers: AnalysisStreamHandlers,
): Promise<void> {
  const body = res.body;
  if (!body) {
    handlers.onError("响应体为空，无法读取 SSE 流");
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedDone = false;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";
      for (const rawEvent of events) {
        const lines = rawEvent.split(/\r?\n/);
        let eventType = "message";
        const dataParts: string[] = [];
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataParts.push(line.slice(5).replace(/^ /, ""));
          }
        }
        const dataStr = dataParts.join("\n");
        if (!dataStr && eventType === "message") continue;
        if (eventType === "ping") continue;

        switch (eventType) {
          case "reasoning": {
            try {
              const obj = JSON.parse(dataStr) as { content?: unknown };
              handlers.onReasoning(String(obj.content ?? ""));
            } catch {
              handlers.onReasoning(dataStr);
            }
            break;
          }
          case "token": {
            handlers.onToken(dataStr);
            break;
          }
          case "done": {
            receivedDone = true;
            handlers.onDone();
            break;
          }
          case "error": {
            let msg = dataStr;
            try {
              const obj = JSON.parse(dataStr) as { message?: unknown };
              msg = String(obj.message ?? dataStr);
            } catch {
              /* 保持 dataStr */
            }
            handlers.onError(msg);
            break;
          }
          default:
            break;
        }
      }
    }
  } finally {
    try {
      reader.cancel();
    } catch {
      /* ignore */
    }
  }
  if (!receivedDone) {
    handlers.onError("连接中断，未收到完成事件");
  }
}

/** 复盘指定 run 的执行轨迹（SSE 流式）。 */
export async function streamTraceReview(opts: StreamOpts): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/observation/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: opts.runId, thread_id: opts.threadId ?? null }),
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    opts.handlers.onError(
      err instanceof Error ? `网络请求失败：${err.message}` : "网络请求失败",
    );
    return;
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    opts.handlers.onError(`后端错误 ${res.status}${detail ? `：${detail.slice(0, 200)}` : ""}`);
    return;
  }
  await consumeSse(res, opts.handlers);
}

/** 自进化：结合 agentx 代码分析轨迹问题（SSE 流式）。 */
export async function streamTraceSelfEvolve(opts: StreamOpts): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/observation/self-evolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: opts.runId, thread_id: opts.threadId ?? null }),
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    opts.handlers.onError(
      err instanceof Error ? `网络请求失败：${err.message}` : "网络请求失败",
    );
    return;
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    opts.handlers.onError(`后端错误 ${res.status}${detail ? `：${detail.slice(0, 200)}` : ""}`);
    return;
  }
  await consumeSse(res, opts.handlers);
}
