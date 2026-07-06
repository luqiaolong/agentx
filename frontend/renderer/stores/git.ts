import { create } from "zustand";
import type { GitStatusEntry, GitCommit, GitBranch, GitRepoStatus } from "../../shared/api-types";
import { getStatus, getLog, getBranches } from "@/lib/api/git";

interface GitState {
  repoPath: string;
  entries: GitStatusEntry[];
  commits: GitCommit[];
  branches: GitBranch[];
  repoStatus: GitRepoStatus;
  loading: boolean;
  error: string | null;
  setRepoPath: (path: string) => void;
  refresh: () => Promise<void>;
  clear: () => void;
}

export const useGitStore = create<GitState>((set, get) => ({
  repoPath: "",
  entries: [],
  commits: [],
  branches: [],
  repoStatus: {
    currentBranch: "",
    ahead: 0,
    behind: 0,
    clean: true,
    isGitRepo: false,
  },
  loading: false,
  error: null,

  setRepoPath: (path) => {
    set({ repoPath: path });
    void get().refresh();
  },

  refresh: async () => {
    const { repoPath } = get();
    if (!repoPath) return;
    set({ loading: true, error: null });
    try {
      const [statusResult, logResult, branchesResult] = await Promise.all([
        getStatus(repoPath),
        getLog(repoPath, 30),
        getBranches(repoPath),
      ]);
      set({
        entries: statusResult.entries,
        repoStatus: statusResult.repoStatus,
        commits: logResult.commits,
        branches: branchesResult.branches,
        loading: false,
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },

  clear: () =>
    set({
      repoPath: "",
      entries: [],
      commits: [],
      branches: [],
      repoStatus: {
        currentBranch: "",
        ahead: 0,
        behind: 0,
        clean: true,
        isGitRepo: false,
      },
      error: null,
    }),
}));
