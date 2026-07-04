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
} from "./store";
import { appendLog, readLogs, cleanOldLogs } from "./logger";

const PYTHON_PORT = 8123;

let mainWindow: BrowserWindow | null = null;
let pythonHandle: PythonHandle | null = null;
let quitting = false;

function getAppIcon(): Electron.NativeImage | undefined {
  // 优先用 .ico（Windows 任务栏 / 资源管理器原生支持），
  // 回退到 PNG（macOS / Linux 启动器也支持）
  const iconDir = app.isPackaged
    ? path.join(process.resourcesPath, "build")
    : path.join(__dirname, "../../build");
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

function getBackendCwd(): string {
  return path.resolve(app.getAppPath(), "backend");
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
    },
    onStatus: (status) => {
      appendLog(`[main] python status: ${status}`);
      mainWindow?.webContents.send("python:status", status);
    },
  });

  // 启动握手：轮询 /api/health；超时仅记录，由 onStatus("giving_up") 兜底
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
  ipcMain.handle("app:restart", () => {
    app.relaunch();
    app.quit();
  });

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
    mainWindow?.close();
  });
  ipcMain.handle("window:isMaximized", () => mainWindow?.isMaximized() ?? false);
}

// Windows 任务栏：必须设置 AppUserModelID，否则任务栏会从 electron.exe 取默认图标
// 必须在 app.whenReady() 之前调用
if (process.platform === "win32") {
  app.setAppUserModelId("com.agentpy.desktop");
}

app.whenReady().then(() => {
  cleanOldLogs(7);

  // Windows：调用 setJumpList 把窗口与 AppUserModelID 关联，任务栏图标立即生效
  if (process.platform === "win32") {
    const icon = getAppIcon();
    if (icon && !icon.isEmpty()) {
      try {
        app.setJumpList([
          {
            type: "tasks",
            items: [
              {
                type: "task",
                title: "AgentPy",
                program: process.execPath,
                args: "--new-window",
                description: "打开 AgentPy",
                iconPath: process.execPath,
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
