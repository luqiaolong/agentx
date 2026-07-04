import { spawn } from "child_process";
import type { ChildProcess, SpawnOptions } from "child_process";
import * as http from "http";
import { appendLog } from "../logger";

/**
 * 杀掉指定进程及其全部子进程。
 *
 * Windows 下 `child.kill()` 只杀直接子进程（uv），孙进程（python）会存活，
 * 导致 python 继续占用 8123 端口，下次启动后端 bind 失败（Errno 10048）。
 * 用 `taskkill /T /F` 递归杀整棵进程树。
 * Unix 下 detached 进程组用负 PID 杀整组。
 */
function killTree(pid: number): void {
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", String(pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-pid, "SIGTERM");
    }
  } catch {
    /* ignore */
  }
}

export interface PythonCredentials {
  openaiApiKey?: string;
  anthropicApiKey?: string;
  milvusUser?: string;
  milvusPassword?: string;
  langsmithApiKey?: string;
  embeddingUrl?: string;
  deepseekApiKey?: string;
  tavilyApiKey?: string;
  // T7 新增
  defaultModel?: string;
  openaiBaseUrl?: string;
  systemPrompt?: string;
  approvalMaxWait?: number;
  maxUploadBytes?: number;
  thinkFilterMaxHold?: number;
  // 知识库配置
  milvusHost?: string;
  milvusPort?: number;
  milvusDb?: string;
  milvusCollection?: string;
  milvusAuthEnabled?: boolean;
}

export type PythonStatus = "starting" | "ready" | "crashed" | "giving_up";

export interface PythonSpawnOptions {
  cwd: string;
  port: number;
  credentials: PythonCredentials;
  /** 进程状态变化回调（启动中/就绪/崩溃/放弃）。 */
  onStatus?: (status: PythonStatus) => void;
}

export interface PythonHandle {
  process: ChildProcess;
  stop: () => void;
  /** 轮询 /api/health，连续 2 次 200 视为 ready；30s 超时返回 false。 */
  waitForReady: () => Promise<boolean>;
}

const MAX_RETRIES = 3;
const HEALTH_INTERVAL_MS = 200;
const HEALTH_TIMEOUT_MS = 30_000;
const REQUIRED_CONSECUTIVE_OK = 2;

function buildEnv(opts: PythonSpawnOptions): NodeJS.ProcessEnv {
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
  if (c.deepseekApiKey) env.AGENT_PY_DEEPSEEK_API_KEY = c.deepseekApiKey;
  if (c.tavilyApiKey) env.AGENT_PY_TAVILY_API_KEY = c.tavilyApiKey;
  // T7 扩展凭证
  if (c.defaultModel) env.AGENT_PY_DEFAULT_MODEL = c.defaultModel;
  if (c.openaiBaseUrl) env.AGENT_PY_OPENAI_BASE_URL = c.openaiBaseUrl;
  if (c.systemPrompt) env.AGENT_PY_DEFAULT_SYSTEM_PROMPT = c.systemPrompt;
  if (c.approvalMaxWait !== undefined) env.AGENT_PY_APPROVAL_MAX_WAIT = String(c.approvalMaxWait);
  if (c.maxUploadBytes !== undefined) env.AGENT_PY_MAX_UPLOAD_BYTES = String(c.maxUploadBytes);
  if (c.thinkFilterMaxHold !== undefined) env.AGENT_PY_THINK_FILTER_MAX_HOLD = String(c.thinkFilterMaxHold);
  if (c.milvusHost) env.AGENT_PY_MILVUS_HOST = c.milvusHost;
  if (c.milvusPort !== undefined) env.AGENT_PY_MILVUS_PORT = String(c.milvusPort);
  if (c.milvusDb) env.AGENT_PY_MILVUS_DB = c.milvusDb;
  if (c.milvusCollection) env.AGENT_PY_MILVUS_COLLECTION = c.milvusCollection;
  if (c.milvusAuthEnabled !== undefined) env.AGENT_PY_MILVUS_AUTH_ENABLED = String(c.milvusAuthEnabled);
  return env;
}

/**
 * 启动 Python 后端 (`uv run python -m app.main`，缺失 uv 时回退 `python -m app.main`)。
 * 凭证通过进程 env 注入（AGENT_PY_ 前缀），MUST NOT 打印凭证。
 * - 启动握手：spawn 后 waitForReady() 轮询 /api/health
 * - 崩溃退避重试：非零退出指数退避（1s/2s/4s）最多 3 次
 * - 日志落盘：stdout/stderr 写入 logger
 */
export function spawnPython(opts: PythonSpawnOptions): PythonHandle {
  const { onStatus } = opts;
  const env = buildEnv(opts);
  // TEMP DEBUG: 验证凭证是否从 electron-store 正确读取并注入
  appendLog(`[python:debug] credentials: openaiApiKey=${opts.credentials.openaiApiKey ? "SET(" + opts.credentials.openaiApiKey.length + " chars)" : "MISSING"} defaultModel=${opts.credentials.defaultModel ?? "MISSING"} openaiBaseUrl=${opts.credentials.openaiBaseUrl ?? "MISSING"} milvusAuthEnabled=${opts.credentials.milvusAuthEnabled}`);
  appendLog(`[python:debug] env.AGENT_PY_OPENAI_API_KEY=${env.AGENT_PY_OPENAI_API_KEY ? "SET" : "NOT SET"} env.AGENT_PY_OPENAI_BASE_URL=${env.AGENT_PY_OPENAI_BASE_URL ?? "NOT SET"} env.AGENT_PY_DEFAULT_MODEL=${env.AGENT_PY_DEFAULT_MODEL ?? "NOT SET"}`);
  // Unix 下 detached 形成独立进程组，便于 stop() 用负 PID 杀整组；
  // Windows 不设 detached（会弹新控制台窗口），改用 taskkill /T 杀进程树
  const spawnOpts: SpawnOptions = {
    cwd: opts.cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  };

  const holder: { current: ChildProcess | null } = { current: null };
  let attempt = 0;
  let stopped = false;
  let currentCommand: "uv" | "python" = "uv";

  const wire = (child: ChildProcess): void => {
    child.stdout?.on("data", (d: Buffer) => {
      appendLog(`[python] ${d.toString().trimEnd()}`);
    });
    child.stderr?.on("data", (d: Buffer) => {
      appendLog(`[python:err] ${d.toString().trimEnd()}`);
    });
  };

  const launch = (cmd: "uv" | "python" = currentCommand): ChildProcess => {
    currentCommand = cmd;
    onStatus?.("starting");
    const args = cmd === "uv" ? ["run", "python", "-m", "app.main"] : ["-m", "app.main"];
    appendLog(`[python] launching ${cmd} ${args.join(" ")} (attempt ${attempt + 1}/${MAX_RETRIES + 1})`);
    const child = spawn(cmd, args, spawnOpts);
    holder.current = child;
    wire(child);
    child.on("exit", handleExit);
    child.on("error", handleError);
    return child;
  };

  const scheduleRetry = (): void => {
    attempt += 1;
    if (attempt > MAX_RETRIES) {
      appendLog(`[python] giving up after ${MAX_RETRIES} retries`);
      onStatus?.("giving_up");
      return;
    }
    onStatus?.("crashed");
    const delay = Math.pow(2, attempt - 1) * 1000; // 1s, 2s, 4s
    appendLog(`[python] retrying in ${delay}ms (attempt ${attempt + 1})`);
    setTimeout(() => {
      if (stopped) return;
      launch();
    }, delay);
  };

  const handleExit = (code: number | null): void => {
    if (stopped) return;
    if (code === null || code === 0) return;
    appendLog(`[python] process exited with code=${code}`);
    scheduleRetry();
  };

  const handleError = (err: Error): void => {
    if (stopped) return;
    appendLog(`[python] spawn error: ${err.message}`);
    // uv 不可用时切换到系统 python，后续重试沿用 python（不再回到已失败的 uv）
    if (currentCommand === "uv") {
      appendLog(`[python] switching to python -m app.main due to uv spawn failure`);
      currentCommand = "python";
    }
    scheduleRetry();
  };

  const child = launch();

  const stop = (): void => {
    stopped = true;
    const child = holder.current;
    if (child && child.pid) {
      killTree(child.pid);
    } else {
      try {
        child?.kill();
      } catch {
        /* ignore */
      }
    }
  };

  const waitForReady = (): Promise<boolean> => {
    return new Promise((resolve) => {
      const deadline = Date.now() + HEALTH_TIMEOUT_MS;
      let consecutive = 0;
      const timer = setInterval(() => {
        if (stopped) {
          clearInterval(timer);
          resolve(false);
          return;
        }
        if (Date.now() > deadline) {
          clearInterval(timer);
          appendLog(`[python] waitForReady timed out after ${HEALTH_TIMEOUT_MS}ms`);
          resolve(false);
          return;
        }
        const req = http.get(`http://127.0.0.1:${opts.port}/api/health`, (res) => {
          res.resume();
          if (res.statusCode === 200) {
            consecutive += 1;
            if (consecutive >= REQUIRED_CONSECUTIVE_OK) {
              clearInterval(timer);
              onStatus?.("ready");
              resolve(true);
            }
          } else {
            consecutive = 0;
          }
        });
        req.on("error", () => {
          consecutive = 0;
        });
        req.setTimeout(2000, () => {
          req.destroy();
        });
      }, HEALTH_INTERVAL_MS);
    });
  };

  return { process: child, stop, waitForReady };
}
