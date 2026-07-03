import { app, BrowserWindow, ipcMain, dialog, shell, Notification, clipboard } from "electron";
import * as path from "path";
import type { ChildProcess } from "child_process";
import { spawnPython } from "./python/spawn";
import { getMilvusCredentials, setMilvusCredentials, getApiKey, setApiKey } from "./store";

const PYTHON_PORT = 8123;

let mainWindow: BrowserWindow | null = null;
let pythonHandle: { process: ChildProcess; stop: () => void } | null = null;
let quitting = false;

function getBackendCwd(): string {
  return path.resolve(app.getAppPath(), "backend");
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  mainWindow.on("ready-to-show", () => mainWindow?.show());

  // electron-vite 在 dev 注入 ELECTRON_RENDERER_URL，生产环境加载打包产物
  const devUrl = process.env["ELECTRON_RENDERER_URL"];
  if (!app.isPackaged && devUrl) {
    void mainWindow.loadURL(devUrl);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

function startPython(): void {
  if (quitting) return;
  const milvus = getMilvusCredentials();
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
    },
  });

  // 崩溃自动重启（仅在进程确实运行后非零退出时）
  pythonHandle.process.on("exit", (code) => {
    if (quitting) return;
    if (code !== null && code !== 0) {
      new Notification({
        title: "AgentPy",
        body: `Python 进程异常退出 (code=${code})，正在重启...`,
      }).show();
      setTimeout(() => startPython(), 1000);
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

  ipcMain.handle("shell:openInEditor", async (_e, p: string) => shell.openPath(p));
  ipcMain.handle("shell:openExternal", async (_e, url: string) => shell.openExternal(url));

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
}

app.whenReady().then(() => {
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
