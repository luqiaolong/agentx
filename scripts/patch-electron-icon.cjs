#!/usr/bin/env node
/**
 * 将 AgentX 图标嵌入到 electron.exe 的资源中，
 * 解决 Windows 任务栏在 dev 模式下始终显示 Electron 默认图标的问题。
 *
 * 必须在 electron.exe 未运行时执行（rcedit 会尝试独占写）。
 * 仅 Windows 平台生效。
 */
const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");

if (process.platform !== "win32") {
  console.log("[patch-electron-icon] 非 Windows 平台，跳过");
  process.exit(0);
}

const ROOT = path.resolve(__dirname, "..");
const ICO_PATH = path.join(ROOT, "build", "icon.ico");

if (!fs.existsSync(ICO_PATH)) {
  console.error(`[patch-electron-icon] 找不到 ${ICO_PATH}，请先运行 npm run icons`);
  process.exit(1);
}

// electron.exe 在 node_modules/electron/dist/electron.exe
const electronExe = path.join(ROOT, "node_modules", "electron", "dist", "electron.exe");
if (!fs.existsSync(electronExe)) {
  console.error(`[patch-electron-icon] 找不到 ${electronExe}`);
  process.exit(1);
}

const rceditExe = path.join(ROOT, "node_modules", "rcedit", "bin", "rcedit-x64.exe");
if (!fs.existsSync(rceditExe)) {
  console.error(`[patch-electron-icon] 找不到 ${rceditExe}`);
  process.exit(1);
}

// 检查 electron.exe 是否正在被占用
function isElectronRunning() {
  try {
    const out = execFileSync("tasklist", ["/FI", `IMAGENAME eq electron.exe`, "/FO", "CSV", "/NH"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return /electron\.exe/i.test(out);
  } catch {
    return false;
  }
}

if (isElectronRunning()) {
  console.warn(
    "[patch-electron-icon] electron.exe 正在运行，跳过图标嵌入（关闭应用后重试）",
  );
  process.exit(0);
}

// 通过 ICONRESOURCE 结构在 ico 中按名称指定 groupicon ID（rcedit 用 ico-group + ico-path）
console.log(`[patch-electron-icon] 嵌入图标到 electron.exe...`);
console.log(`  source: ${ICO_PATH}`);
console.log(`  target: ${electronExe}`);

try {
  execFileSync(rceditExe, [electronExe, "--set-icon", ICO_PATH], { stdio: "inherit" });
  console.log("[patch-electron-icon] 图标嵌入成功 ✓");
} catch (err) {
  console.error(`[patch-electron-icon] rcedit 失败: ${err.message}`);
  process.exit(1);
}
