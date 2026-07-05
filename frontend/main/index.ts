import {
  app,
  BrowserWindow,
  ipcMain,
  dialog,
  shell,
  Notification,
  clipboard,
  Menu,
  nativeImage,
} from "electron";
import * as path from "path";
import * as fs from "fs";
import * as crypto from "crypto";
import { spawnPython, type PythonHandle } from "./python/spawn";
import {
  getMilvusCredentials,
  setMilvusCredentials,
  getApiKey,
  setApiKey,
  getLLMConfig,
  setLLMConfig,
  getSystemPrompt,
  setSystemPrompt,
  getApprovalConfig,
  setApprovalConfig,
  getKnowledgeConfig,
  setKnowledgeConfig,
  getSubagentsConfig,
  setSubagentsConfig,
  getCustomSubagents,
  setCustomSubagents,
  addCustomSubagent,
  removeCustomSubagent,
  getToolsConfig,
  setToolsConfig,
  getProfileAutoExtract,
  setProfileAutoExtract,
  getMcpServersConfig,
  setMcpServersConfig,
  getModelEntries,
  setModelEntries,
  getActiveModelId,
  activateModelEntry,
  migrateLegacyLLMConfig,
} from "./store";
import { appendLog, readLogs, cleanOldLogs } from "./logger";

const PYTHON_PORT = 8123;

let mainWindow: BrowserWindow | null = null;
let pythonHandle: PythonHandle | null = null;
let quitting = false;

function getIconDir(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, "build")
    : path.join(__dirname, "../../build");
}

function getAppIcon(): Electron.NativeImage | undefined {
  // 优先用 .ico（Windows 任务栏 / 资源管理器原生支持），
  // 回退到 PNG（macOS / Linux 启动器也支持）
  const iconDir = getIconDir();
  const icoPath = path.join(iconDir, "icon.ico");
  const pngPath = path.join(iconDir, "icon.png");
  if (process.platform === "win32" && fs.existsSync(icoPath)) {
    return nativeImage.createFromPath(icoPath);
  }
  if (fs.existsSync(pngPath)) {
    return nativeImage.createFromPath(pngPath);
  }
  return undefined;
}

function getIconIcoPath(): string | undefined {
  const icoPath = path.join(getIconDir(), "icon.ico");
  return fs.existsSync(icoPath) ? icoPath : undefined;
}

function getBackendCwd(): string {
  return path.resolve(app.getAppPath(), "backend");
}

/**
 * 返回 Home workspace 的根目录：用户的桌面（Desktop）。
 *
 * - Windows: %USERPROFILE%\Desktop
 * - macOS:   $HOME/Desktop
 * - Linux:   $HOME/Desktop
 *
 * 若桌面目录不存在（少数 Linux/服务器环境），回退到 home 目录，
 * 保证调用方永远拿到一个非空的可写路径。
 */
export function getHomeWorkspaceDir(): string {
  const home = app.getPath("home");
  const candidates = [
    process.platform === "win32" ? path.join(home, "Desktop") : path.join(home, "Desktop"),
  ];
  for (const c of candidates) {
    try {
      if (fs.existsSync(c) && fs.statSync(c).isDirectory()) return c;
    } catch {
      /* ignore */
    }
  }
  return home;
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    frame: false,
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    titleBarOverlay: false,
    icon: getAppIcon(),
    backgroundColor: "#020617",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  // 移除默认菜单栏（Windows/Linux）
  Menu.setApplicationMenu(null);

  mainWindow.on("ready-to-show", () => mainWindow?.show());

  // 窗口最大化状态变化时通知 renderer，以便更新最大化按钮图标
  mainWindow.on("maximize", () => {
    mainWindow?.webContents.send("window:maximized-change", true);
  });
  mainWindow.on("unmaximize", () => {
    mainWindow?.webContents.send("window:maximized-change", false);
  });

  // electron-vite 在 dev 注入 ELECTRON_RENDERER_URL，生产环境加载打包产物
  // 注意: app.isPackaged 在某些 dev 场景下可能为 true（如 electron-vite 缓存），
  // 故以 ELECTRON_RENDERER_URL 是否存在作为 dev 模式判据
  const devUrl = process.env["ELECTRON_RENDERER_URL"];
  if (devUrl) {
    void mainWindow.loadURL(devUrl);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

function startPython(): void {
  if (quitting) return;
  const milvus = getMilvusCredentials();
  const llm = getLLMConfig();
  const approval = getApprovalConfig();
  const knowledge = getKnowledgeConfig();
  const systemPrompt = getSystemPrompt();
  const subagentsConfig = getSubagentsConfig();
  const customSubagentsConfig = getCustomSubagents();
  const toolsConfig = getToolsConfig();
  const profileAutoExtract = getProfileAutoExtract();
  const mcpServersConfig = getMcpServersConfig();

  appendLog("[main] starting python backend");
  pythonHandle = spawnPython({
    cwd: getBackendCwd(),
    port: PYTHON_PORT,
    credentials: {
      openaiApiKey: getApiKey("openai") ?? undefined,
      anthropicApiKey: getApiKey("anthropic") ?? undefined,
      milvusUser: milvus.user ?? undefined,
      milvusPassword: milvus.password ?? undefined,
      deepseekApiKey: getApiKey("deepseek") ?? undefined,
      tavilyApiKey: getApiKey("tavily") ?? undefined,
      defaultModel: llm.defaultModel || undefined,
      openaiBaseUrl: llm.openaiBaseUrl || undefined,
      systemPrompt: systemPrompt || undefined,
      approvalMaxWait: approval.approvalMaxWait,
      maxUploadBytes: approval.maxUploadBytes,
      embeddingUrl: knowledge.embeddingUrl || undefined,
      milvusHost: knowledge.milvusHost || undefined,
      milvusPort: knowledge.milvusPort,
      milvusDb: knowledge.milvusDb || undefined,
      milvusCollection: knowledge.milvusCollection || undefined,
      milvusAuthEnabled: knowledge.milvusAuthEnabled,
      subagentsConfig,
      customSubagentsConfig,
      toolsConfig,
      profileAutoExtract,
      mcpServersConfig,
    },
    onStatus: (status) => {
      appendLog(`[main] python status: ${status}`);
      mainWindow?.webContents.send("python:status", status);
    },
  });

  // 启动握手：轮询 / 根健康端点；超时仅记录，由 onStatus("giving_up") 兜底
  void pythonHandle.waitForReady().then((ok) => {
    if (!ok) {
      appendLog("[main] python waitForReady returned false (timeout or stopped)");
    }
  });
}

function registerIpc(): void {
  ipcMain.handle("dialog:openFile", async (_e, opts) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      properties: ["openFile"],
      ...(opts as Electron.OpenDialogOptions),
    });
    return result;
  });
  ipcMain.handle("dialog:openFolder", async () => {
    return dialog.showOpenDialog(mainWindow!, { properties: ["openDirectory"] });
  });
  ipcMain.handle("dialog:saveFile", async (_e, opts) => {
    return dialog.showSaveDialog(mainWindow!, (opts as Electron.SaveDialogOptions) ?? {});
  });
  // T2 文件拖拽上传：复制源文件到 data/uploads/{uuid}_{fileName}，返回相对路径
  ipcMain.handle(
    "dialog:saveDroppedFile",
    async (_e, filePath: string, fileName: string): Promise<string> => {
      const maxBytes = getApprovalConfig().maxUploadBytes;
      // 安全：源 filePath 系统目录黑名单（防 renderer XSS 后借 copyFile 读取系统敏感文件）
      const resolvedSrc = path.resolve(String(filePath));
      const lowerSrc = resolvedSrc.toLowerCase();
      const systemPrefixes = [
        "c:\\windows\\", "c:\\program files\\", "c:\\program files (x86)\\",
        "c:\\programdata\\", "c:\\system volume information\\",
        "/etc/", "/usr/", "/bin/", "/sbin/", "/var/", "/boot/",
        "/proc/", "/sys/", "/system/", "/private/",
      ];
      if (systemPrefixes.some((p) => lowerSrc.startsWith(p) || lowerSrc === p.slice(0, -1))) {
        appendLog(`[main] saveDroppedFile rejected system path: ${resolvedSrc}`);
        throw new Error("不允许读取系统目录文件");
      }
      let size = 0;
      try {
        const stat = fs.statSync(filePath);
        size = stat.size;
      } catch (err) {
        appendLog(`[main] saveDroppedFile stat failed: ${(err as Error).message}`);
        throw new Error(`无法读取源文件: ${fileName}`);
      }
      if (size > maxBytes) {
        appendLog(`[main] saveDroppedFile rejected: ${fileName} size=${size} > max=${maxBytes}`);
        throw new Error(`文件大小 ${size} 超过上限 ${maxBytes} 字节`);
      }
      const uploadDir = path.join(getBackendCwd(), "data", "uploads");
      // main 进程写文件前兜底创建目录（后端 ensure_runtime_dirs 也可能尚未运行）
      if (!fs.existsSync(uploadDir)) {
        fs.mkdirSync(uploadDir, { recursive: true });
      }
      // 安全：用 path.basename 清洗 fileName，防止路径穿越（如 ../foo 或绝对路径）
      const safeName = path.basename(String(fileName));
      if (!safeName || safeName === "." || safeName === "..") {
        throw new Error("非法文件名");
      }
      const id = crypto.randomUUID();
      const destName = `${id}_${safeName}`;
      const destPath = path.join(uploadDir, destName);
      await fs.promises.copyFile(filePath, destPath);
      appendLog(`[main] saveDroppedFile saved ${safeName} -> data/uploads/${destName}`);
      return `data/uploads/${destName}`;
    },
  );

  ipcMain.handle("shell:openInEditor", async (_e, p: string) => shell.openPath(p));
  ipcMain.handle("shell:openExternal", async (_e, url: string) => shell.openExternal(url));
  // T4 workspace-panel：在文件管理器中显示文件
  ipcMain.handle("shell:revealInFolder", (_e, p: string) => {
    shell.showItemInFolder(p);
  });

  ipcMain.handle("notify:show", async (_e, opts: { title: string; body: string }) => {
    new Notification(opts).show();
  });

  ipcMain.handle("clipboard:read", () => clipboard.readText());
  ipcMain.handle("clipboard:write", (_e, text: string) => clipboard.writeText(text));

  ipcMain.handle("app:getVersion", () => app.getVersion());
  ipcMain.handle("app:quit", () => app.quit());
  // 全量重启 Electron（仅用于 ErrorBoundary 渲染错误恢复）
  ipcMain.handle("app:restart", () => {
    app.relaunch();
    app.quit();
  });
  // 仅重启 Python 后端（不重启 Electron 窗口），用于设置页"重启后端"按钮
  ipcMain.handle("app:restartBackend", async () => {
    appendLog("[main] restarting python backend (no relaunch)");
    if (pythonHandle) {
      pythonHandle.stop();
      pythonHandle = null;
    }
    // 等待端口释放（Windows TCP TIME_WAIT）
    await new Promise((r) => setTimeout(r, 800));
    startPython();
    // 轮询轻量端点 GET / 等待后端就绪（不用 /api/health，它串行调 TEI+Milvus 慢）
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(`http://127.0.0.1:${PYTHON_PORT}/`);
        if (res.ok) {
          appendLog("[main] python backend restarted and ready");
          return { ok: true };
        }
      } catch {
        // 尚未就绪
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    appendLog("[main] python backend restart timeout");
    return { ok: false, message: "backend restart timeout" };
  });
  // 热更新后端配置（无需重启进程）：从 electron-store 读取最新配置，
  // POST 到 /api/config/reload，后端清除 get_settings lru_cache 后立即生效
  ipcMain.handle("app:reloadBackendConfig", async () => {
    const llm = getLLMConfig();
    const approval = getApprovalConfig();
    const systemPrompt = getSystemPrompt();
    const subagentsConfig = getSubagentsConfig();
    const customSubagentsConfig = getCustomSubagents();
    const toolsConfig = getToolsConfig();
    const profileAutoExtract = getProfileAutoExtract();
    const mcpServersConfig = getMcpServersConfig();
    const openaiKey = getApiKey("openai");
    const deepseekKey = getApiKey("deepseek");
    const tavilyKey = getApiKey("tavily");

    const payload: Record<string, unknown> = {};
    if (llm.defaultModel) payload.default_model = llm.defaultModel;
    if (llm.openaiBaseUrl) payload.openai_base_url = llm.openaiBaseUrl;
    if (openaiKey) payload.openai_api_key = openaiKey;
    if (deepseekKey) payload.deepseek_api_key = deepseekKey;
    if (tavilyKey) payload.tavily_api_key = tavilyKey;
    payload.approval_max_wait = approval.approvalMaxWait;
    payload.max_upload_bytes = approval.maxUploadBytes;
    payload.auto_approve_after_seconds = approval.autoApproveAfterSeconds;
    if (systemPrompt) payload.default_system_prompt = systemPrompt;
    payload.subagents_config = subagentsConfig;
    payload.custom_subagents_config = customSubagentsConfig;
    payload.tools_config = toolsConfig;
    payload.profile_auto_extract = profileAutoExtract;
    payload.mcp_servers_config = mcpServersConfig;

    try {
      const res = await fetch(`http://127.0.0.1:${PYTHON_PORT}/api/config/reload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}: ${text}`);
      }
      return await res.json();
    } catch (e) {
      appendLog(`[main] reloadBackendConfig failed: ${(e as Error).message}`);
      throw new Error(e instanceof Error ? e.message : String(e));
    }
  });
  // Home workspace：返回桌面目录路径，renderer 用作"未显式选 workspace"时的默认归属
  ipcMain.handle("app:getHomeWorkspaceDir", () => getHomeWorkspaceDir());

  ipcMain.handle("settings:setMilvusCredentials", async (_e, user: string, password: string) => {
    setMilvusCredentials(user, password);
    return { ok: true };
  });
  ipcMain.handle("settings:getMilvusCredentials", async () => getMilvusCredentials());
  ipcMain.handle("settings:getApiKey", (_e, provider: string) => getApiKey(provider));
  ipcMain.handle("settings:setApiKey", (_e, provider: string, key: string) => {
    setApiKey(provider, key);
    return { ok: true };
  });

  // T7 settings-completion：非凭证配置 IPC handler，renderer 通过 window.api.settings 读写
  ipcMain.handle("settings:setLLMConfig", (_e, model: string, baseUrl: string) => {
    setLLMConfig(model, baseUrl);
    return { ok: true };
  });
  ipcMain.handle("settings:getLLMConfig", () => getLLMConfig());
  ipcMain.handle("settings:setSystemPrompt", (_e, prompt: string) => {
    setSystemPrompt(prompt);
    return { ok: true };
  });
  ipcMain.handle("settings:getSystemPrompt", () => getSystemPrompt());
  ipcMain.handle("settings:setApprovalConfig", (_e, cfg: Parameters<typeof setApprovalConfig>[0]) => {
    setApprovalConfig(cfg);
    return { ok: true };
  });
  ipcMain.handle("settings:getApprovalConfig", () => getApprovalConfig());
  ipcMain.handle("settings:setKnowledgeConfig", (_e, cfg: Parameters<typeof setKnowledgeConfig>[0]) => {
    setKnowledgeConfig(cfg);
    return { ok: true };
  });
  ipcMain.handle("settings:getKnowledgeConfig", () => getKnowledgeConfig());

  // T11/T12/T13 子代理 + 工具 + 用户画像自动抽取配置 IPC handler
  // renderer 通过 window.api.settings 读写 electron-store，后端启动时从 env 注入
  ipcMain.handle("settings:getSubagentsConfig", () => getSubagentsConfig());
  ipcMain.handle("settings:setSubagentsConfig", (_e, cfg: Parameters<typeof setSubagentsConfig>[0]) => {
    setSubagentsConfig(cfg);
    return { ok: true };
  });
  // 自定义子代理 CRUD IPC handler
  ipcMain.handle("settings:getCustomSubagents", () => getCustomSubagents());
  ipcMain.handle("settings:setCustomSubagents", (_e, cfg: Parameters<typeof setCustomSubagents>[0]) => {
    setCustomSubagents(cfg);
    return { ok: true };
  });
  ipcMain.handle(
    "settings:addCustomSubagent",
    (_e, input: Parameters<typeof addCustomSubagent>[0]) => {
      try {
        return addCustomSubagent(input);
      } catch (e) {
        // 抛给 renderer 的错误信息保持原样
        throw new Error(e instanceof Error ? e.message : String(e));
      }
    },
  );
  ipcMain.handle("settings:removeCustomSubagent", (_e, key: string) => removeCustomSubagent(key));
  ipcMain.handle("settings:getToolsConfig", () => getToolsConfig());
  ipcMain.handle("settings:setToolsConfig", (_e, cfg: Parameters<typeof setToolsConfig>[0]) => {
    setToolsConfig(cfg);
    return { ok: true };
  });
  ipcMain.handle("settings:getProfileAutoExtract", () => getProfileAutoExtract());
  ipcMain.handle("settings:setProfileAutoExtract", (_e, v: boolean) => {
    setProfileAutoExtract(v);
    return { ok: true };
  });
  // MCP server 配置 IPC handler：renderer 通过 window.api.settings 读写 electron-store，
  // 后端启动时从 AGENTX_MCP_SERVERS_CONFIG env 注入
  ipcMain.handle("settings:getMcpServersConfig", () => getMcpServersConfig());
  ipcMain.handle(
    "settings:setMcpServersConfig",
    (_e, servers: Parameters<typeof setMcpServersConfig>[0]) => {
      setMcpServersConfig(servers);
      return { ok: true };
    },
  );

  // 模型条目 IPC handler：renderer 通过 window.api.settings 读写 electron-store，
  // "激活"时写入 legacy 槽位（llm.* + apikey.*），后端 spawn 时从 env 读取
  ipcMain.handle("settings:getModelEntries", () => getModelEntries());
  ipcMain.handle(
    "settings:setModelEntries",
    (_e, entries: Parameters<typeof setModelEntries>[0]) => {
      setModelEntries(entries);
      return { ok: true };
    },
  );
  ipcMain.handle("settings:getActiveModelId", () => getActiveModelId());
  ipcMain.handle("settings:activateModel", (_e, id: string) => {
    try {
      activateModelEntry(id);
      return { ok: true };
    } catch (e) {
      throw new Error(e instanceof Error ? e.message : String(e));
    }
  });

  // T6 process-resilience：读取日志（默认当天，最后 200 行）
  ipcMain.handle("logs:read", (_e, date?: string, maxLines?: number) => {
    return readLogs(date, maxLines);
  });

  // 窗口控制：无边框窗口需要 renderer 自己实现标题栏按钮
  ipcMain.handle("window:minimize", () => {
    mainWindow?.minimize();
  });
  ipcMain.handle("window:maximize", () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
  ipcMain.handle("window:close", () => {
    app.quit();
  });
  ipcMain.handle("window:isMaximized", () => mainWindow?.isMaximized() ?? false);
}

// Windows 任务栏：必须设置 AppUserModelID，否则任务栏会从 electron.exe 取默认图标
// 必须在 app.whenReady() 之前调用
if (process.platform === "win32") {
  app.setAppUserModelId("com.agentx.desktop");
}

app.whenReady().then(() => {
  cleanOldLogs(7);

  // Windows：调用 setJumpList 把窗口与 AppUserModelID 关联，任务栏图标立即生效。
  // iconPath 必须指向固定的 .ico，避免从 electron.exe 取图标时被主题色影响。
  if (process.platform === "win32") {
    const icon = getAppIcon();
    const icoPath = getIconIcoPath();
    if (icon && !icon.isEmpty() && icoPath) {
      try {
        app.setJumpList([
          {
            type: "tasks",
            items: [
              {
                type: "task",
                title: "AgentX",
                program: process.execPath,
                args: "--new-window",
                description: "打开 AgentX",
                iconPath: icoPath,
                iconIndex: 0,
              },
            ],
          },
        ]);
      } catch (err) {
        appendLog(`[main] setJumpList failed: ${(err as Error).message}`);
      }
    }
  }

  createWindow();
  // 迁移 legacy LLM 配置到 model entries（老用户首次升级时种子一条默认条目）
  migrateLegacyLLMConfig();
  startPython();
  registerIpc();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  pythonHandle?.stop();
});
