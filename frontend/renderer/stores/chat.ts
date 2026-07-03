import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  ts: number;
}

export interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
}

interface ChatState {
  messages: ChatMessage[];
  threadId: string | null;
  isStreaming: boolean;
  approvalRequest: ApprovalRequest | null;
  setThreadId: (id: string | null) => void;
  addMessage: (msg: ChatMessage) => void;
  appendMessageContent: (id: string, content: string) => void;
  clearMessages: () => void;
  setStreaming: (v: boolean) => void;
  setApprovalRequest: (req: ApprovalRequest | null) => void;
}

export const useChatStore = create<ChatState>()(
  devtools(
    persist(
      (set) => ({
        messages: [],
        threadId: null,
        isStreaming: false,
        approvalRequest: null,
        setThreadId: (id) => set({ threadId: id }),
        addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
        appendMessageContent: (id, content) =>
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === id ? { ...m, content: m.content + content } : m,
            ),
          })),
        clearMessages: () => set({ messages: [] }),
        setStreaming: (v) => set({ isStreaming: v }),
        setApprovalRequest: (req) => set({ approvalRequest: req }),
      }),
      {
        name: "agent-py-chat",
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({ threadId: s.threadId, messages: s.messages }),
      },
    ),
    { name: "chat-store" },
  ),
);
