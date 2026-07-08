import { describe, expect, it } from "vitest";
import {
  applySceneChange,
  getSceneFromMode,
  type Scene,
} from "@/stores/scene";
import type { AgentMode } from "../../../shared/api-types";

/**
 * scene helper 单测：从 mode 派生 scene + 顶部 tab 联动 mode 的规则。
 *
 * 设计约束：
 * - scene 不是独立可变的状态，由 agent_mode.mode 派生
 * - 顶部 tab 切场景时通过 applySceneChange 算出新 mode，写回 agentMode store
 * - work 场景禁止 Team（Supervisor + Expert 架构约束）
 */
describe("getSceneFromMode", () => {
  const cases: [AgentMode, Scene][] = [
    ["work", "work"],
    ["coding", "coding"],
    ["coding_team", "coding"],
  ];

  it.each(cases)("mode=%s → scene=%s", (mode, expectedScene) => {
    expect(getSceneFromMode(mode)).toBe(expectedScene);
  });
});

describe("applySceneChange", () => {
  describe("切到 work 场景", () => {
    it("work → work: 保持 work", () => {
      expect(applySceneChange("work", "work")).toBe("work");
    });

    it("coding → work: 强制降级为 work（work 不允许 Team）", () => {
      expect(applySceneChange("work", "coding")).toBe("work");
    });

    it("coding_team → work: 强制降级为 work（work 不允许 Team）", () => {
      expect(applySceneChange("work", "coding_team")).toBe("work");
    });
  });

  describe("切到 coding 场景", () => {
    it("work → coding: 升级为 coding（默认 Coding Agent，不自动选 Team）", () => {
      expect(applySceneChange("coding", "work")).toBe("coding");
    });

    it("coding → coding: 保持 coding", () => {
      expect(applySceneChange("coding", "coding")).toBe("coding");
    });

    it("coding_team → coding: 保持 coding_team（用户已选 Team，不自动降级）", () => {
      expect(applySceneChange("coding", "coding_team")).toBe("coding_team");
    });
  });
});

describe("scene 与 mode 的不变量", () => {
  it("work 场景永远对应 mode=work", () => {
    const scene = getSceneFromMode("work");
    expect(scene).toBe("work");
  });

  it("coding 场景对应 mode=coding 或 coding_team", () => {
    expect(getSceneFromMode("coding")).toBe("coding");
    expect(getSceneFromMode("coding_team")).toBe("coding");
  });
});