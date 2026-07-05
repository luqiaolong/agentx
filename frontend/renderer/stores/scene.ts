import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export type Scene = "work" | "coding";

/**
 * 两套场景内置 system prompt。前端硬编码，切换场景时通过 chat.send opts.systemPrompt
 * 透传给后端 ChatRequest.system_prompt，覆盖 default_system_prompt。
 */
export const SCENE_PROMPTS: Record<Scene, string> = {
  work: `你是 AgentX 工作助手。专注文档撰写、知识检索、日程任务、邮件沟通等办公场景。
- 回答简洁友好，优先调用 RAG/搜索/读写类工具
- 涉及代码时给出解释但默认不主动改文件
- 危险操作（写文件/shell）需用户确认`,
  coding: `你是 AgentX 编程助手。专注代码开发、调试、重构、shell 操作。
- 默认假设用户在某个 workspace 目录下工作
- 优先调用 filesystem/shell/rag_retrieve 工具
- 修改代码前先读文件，给出 diff 级别说明
- 危险操作仍走审批流`,
};

interface SceneState {
  scene: Scene;
  setScene: (s: Scene) => void;
}

export const useSceneStore = create<SceneState>()(
  devtools(
    persist(
      (set) => ({
        scene: "work",
        setScene: (scene) => set({ scene }),
      }),
      {
        name: "agentx-scene",
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({ scene: s.scene }),
      },
    ),
    { name: "scene-store" },
  ),
);
