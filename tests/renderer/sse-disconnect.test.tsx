import { beforeEach, describe, expect, it, vi } from "vitest";
import { chat } from "@/lib/api/chat";

const API_URL = "http://127.0.0.1:8123/api/chat";

function makeStreamResponse(chunks: Uint8Array[], status = 200, ok = true): Response {
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(chunks[index]);
      index += 1;
    },
  });
  return {
    ok,
    status,
    body: stream,
    text: () => Promise.resolve(""),
    json: () => Promise.resolve({}),
    headers: new Headers(),
    redirected: false,
    statusText: "",
    type: "basic",
    url: API_URL,
    clone: () => makeStreamResponse(chunks, status, ok),
    blob: () => Promise.resolve(new Blob()),
    arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
    formData: () => Promise.resolve(new FormData()),
    bytes: () => Promise.resolve(new Uint8Array()),
  } as unknown as Response;
}

function encodeSSE(events: { event?: string; data: string }[]): Uint8Array {
  const lines = events
    .map((e) => {
      const eventLine = e.event ? `event: ${e.event}\n` : "";
      return `${eventLine}data: ${e.data}\n\n`;
    })
    .join("");
  return new TextEncoder().encode(lines);
}

describe("chat.send SSE 断开感知", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("流在未收到 done 事件时结束，触发 onError", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeStreamResponse([
        encodeSSE([{ event: "token", data: "hello" }]),
      ]),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onError = vi.fn();
    await chat.send({ role: "user", content: "hi" }, { onError });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![0]).toBeInstanceOf(Error);
    expect((onError.mock.calls[0]![0] as Error).message).toContain("未收到完成事件");
  });

  it("收到 done 事件后正常结束，不触发 onError", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeStreamResponse([
        encodeSSE([
          { event: "token", data: "hello" },
          { event: "done", data: "{}" },
        ]),
      ]),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onError = vi.fn();
    await chat.send({ role: "user", content: "hi" }, { onError });

    expect(onError).not.toHaveBeenCalled();
  });

  it("HTTP 非 2xx 时触发 onError 并携带状态码", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      text: () => Promise.resolve("service unavailable"),
      body: null,
    } as unknown as Response);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onError = vi.fn();
    await chat.send({ role: "user", content: "hi" }, { onError });

    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0]![0] as Error).message).toContain("503");
  });

  it("fetch 抛网络错误时触发 onError", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("net::ERR_FAILED"));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onError = vi.fn();
    await chat.send({ role: "user", content: "hi" }, { onError });

    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0]![0] as Error).message).toContain("网络请求失败");
  });

  it("SSE 读取过程中 reader 抛错，触发 onError", async () => {
    const stream = new ReadableStream<Uint8Array>({
      pull() {
        throw new Error("stream broken");
      },
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: stream,
      text: () => Promise.resolve(""),
    } as unknown as Response);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onError = vi.fn();
    await chat.send({ role: "user", content: "hi" }, { onError });

    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0]![0] as Error).message).toContain("SSE 连接中断");
  });
});
