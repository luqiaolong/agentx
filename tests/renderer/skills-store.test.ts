import { beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { useSkillsStore } from "@/stores/skills";
import { installApiMock } from "./api-mock";

const listMock = vi.fn();

beforeEach(() => {
  installApiMock({
    skills: { list: listMock },
  });
  listMock.mockReset();
  useSkillsStore.setState({ skills: [], loading: false, error: null });
});

describe("skills store", () => {
  it("fetchSkills 成功后写入 skills + 清 error", async () => {
    listMock.mockResolvedValue({
      skills: [
        {
          name: "translate",
          description: "翻译",
          trigger: "/translate",
          tools: [],
          content_preview: "...",
        },
      ],
    });
    await act(async () => {
      await useSkillsStore.getState().fetchSkills();
    });
    const s = useSkillsStore.getState();
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
    expect(s.skills).toHaveLength(1);
    expect(s.skills[0].name).toBe("translate");
  });

  it("fetchSkills 失败时写入 error + 清空 skills", async () => {
    listMock.mockRejectedValue(new Error("network down"));
    await act(async () => {
      await useSkillsStore.getState().fetchSkills();
    });
    const s = useSkillsStore.getState();
    expect(s.loading).toBe(false);
    expect(s.error).toBe("network down");
    expect(s.skills).toEqual([]);
  });

  it("fetchSkills 非 Error 异常也能序列化", async () => {
    listMock.mockRejectedValue("plain string error");
    await act(async () => {
      await useSkillsStore.getState().fetchSkills();
    });
    expect(useSkillsStore.getState().error).toBe("plain string error");
  });
});
