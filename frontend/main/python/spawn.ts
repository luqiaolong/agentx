import { spawn } from "child_process";
import type { ChildProcess, SpawnOptions } from "child_process";

export interface PythonCredentials {
  openaiApiKey?: string;
  anthropicApiKey?: string;
  milvusUser?: string;
  milvusPassword?: string;
  langsmithApiKey?: string;
  embeddingUrl?: string;
}

export interface PythonSpawnOptions {
  cwd: string;
  port: number;
  credentials: PythonCredentials;
}

/**
 * 启动 Python 后端 (`uv run python -m app.main`，缺失 uv 时回退 `python -m app.main`)。
 * 凭证通过进程 env 注入（AGENT_PY_ 前缀），MUST NOT 打印凭证。
 */
export function spawnPython(opts: PythonSpawnOptions): {
  process: ChildProcess;
  stop: () => void;
} {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    AGENT_PY_HOST: "127.0.0.1",
    AGENT_PY_PORT: String(opts.port),
  };
  const c = opts.credentials;
  if (c.openaiApiKey) env.AGENT_PY_OPENAI_API_KEY = c.openaiApiKey;
  if (c.anthropicApiKey) env.AGENT_PY_ANTHROPIC_API_KEY = c.anthropicApiKey;
  if (c.milvusUser) env.AGENT_PY_MILVUS_USER = c.milvusUser;
  if (c.milvusPassword) env.AGENT_PY_MILVUS_PASSWORD = c.milvusPassword;
  if (c.embeddingUrl) env.AGENT_PY_EMBEDDING_URL = c.embeddingUrl;
  if (c.langsmithApiKey) env.LANGSMITH_API_KEY = c.langsmithApiKey;

  const holder: { current: ChildProcess | null } = { current: null };

  const wire = (child: ChildProcess): void => {
    child.stdout?.on("data", (d: Buffer) => console.log(`[python] ${d.toString()}`));
    child.stderr?.on("data", (d: Buffer) => console.error(`[python] ${d.toString()}`));
  };

  const spawnOpts: SpawnOptions = { cwd: opts.cwd, env, stdio: ["ignore", "pipe", "pipe"] };

  const child = spawn("uv", ["run", "python", "-m", "app.main"], spawnOpts);
  holder.current = child;
  // uv 不可用时回退到系统 python
  child.on("error", () => {
    const fallback = spawn("python", ["-m", "app.main"], spawnOpts);
    holder.current = fallback;
    wire(fallback);
  });
  wire(child);

  const stop = (): void => {
    try {
      holder.current?.kill();
    } catch {
      /* ignore */
    }
  };

  return { process: child, stop };
}
