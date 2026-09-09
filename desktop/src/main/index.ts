/**
 * The desktop shell.
 *
 * Owns the window, the menu and everything that needs the operating system:
 * the file dialog, revealing a file in the Finder, and — the one thing a
 * browser genuinely cannot do — the real path of a dropped file. The reader
 * itself is the same web app, and every operation behaves as it did there.
 */

import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from "electron";
import { startSidecar, type Sidecar } from "./sidecar";

let sidecar: Sidecar | null = null;
let mainWindow: BrowserWindow | null = null;
const log: string[] = [];

function remember(line: string): void {
  if (!line) return;
  log.push(line);
  // Enough to diagnose a failed start, not enough to grow without bound.
  if (log.length > 400) log.splice(0, log.length - 400);
}

/** Ask the server how the window should open, before there is a window. */
async function windowMode(origin: string): Promise<"fullscreen" | "maximized"> {
  try {
    const response = await fetch(`${origin}/api/settings`);
    const body = await response.json();
    return body.window_mode === "maximized" ? "maximized" : "fullscreen";
  } catch {
    return "fullscreen";
  }
}

async function createWindow(origin: string): Promise<BrowserWindow> {
  const mode = await windowMode(origin);

  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    title: "ClassHelper",
    // The traffic lights float over the app's own title row, the way an editor
    // does it, rather than costing a separate 28px strip.
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    backgroundColor: "#ffffff",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      // The renderer is our own build, but it renders slide text from files the
      // user was given. It gets no Node, and reaches the OS only through the
      // narrow surface in the preload.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  window.once("ready-to-show", () => {
    if (mode === "fullscreen") window.setFullScreen(true);
    else window.maximize();
    window.show();
  });

  // Links to the outside world open in the real browser, never in here.
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) && !url.startsWith(origin)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  await window.loadURL(origin);
  return window;
}

function buildMenu(window: BrowserWindow): void {
  const send = (channel: string) => () => window.webContents.send(channel);
  const isMac = process.platform === "darwin";

  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac
      ? [{ role: "appMenu" as const }]
      : []),
    {
      label: "文件",
      submenu: [
        { label: "导入课件…", accelerator: "CmdOrCtrl+O", click: send("menu:import") },
        { type: "separator" },
        isMac ? { role: "close" as const } : { role: "quit" as const },
      ],
    },
    { label: "编辑", role: "editMenu" },
    {
      label: "视图",
      submenu: [
        { label: "课板", accelerator: "CmdOrCtrl+1", click: send("menu:board") },
        { label: "设置", accelerator: "CmdOrCtrl+,", click: send("menu:settings") },
        { type: "separator" },
        { label: "左侧边栏", accelerator: "CmdOrCtrl+B", click: send("menu:left") },
        { label: "提问面板", accelerator: "CmdOrCtrl+J", click: send("menu:right") },
        { label: "重置布局", click: send("menu:reset") },
        { type: "separator" },
        { role: "togglefullscreen" },
        { role: "reload" },
        { role: "toggleDevTools" },
      ],
    },
    { label: "窗口", role: "windowMenu" },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc(): void {
  /**
   * The system file dialog, native rather than driven through AppleScript.
   * Same result as the browser build, without shelling out.
   */
  ipcMain.handle("dialog:pick", async () => {
    const result = await dialog.showOpenDialog({
      title: "选择要导入的课件",
      properties: ["openFile", "multiSelections"],
      filters: [{ name: "课件", extensions: ["pptx", "pdf"] }],
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle("shell:reveal", (_event, target: string) => {
    shell.showItemInFolder(target);
    return true;
  });

  ipcMain.handle("app:log", () => log.join("\n"));
}

async function boot(): Promise<void> {
  try {
    sidecar = await startSidecar(remember);
  } catch (error) {
    const detail = `${String(error)}\n\n${log.slice(-25).join("\n")}`;
    dialog.showErrorBox("ClassHelper 启动失败", detail);
    app.quit();
    return;
  }

  registerIpc();
  mainWindow = await createWindow(sidecar.origin);
  buildMenu(mainWindow);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// One window is one library; a second instance would fight the first over the
// same files, so the running one is raised instead.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(boot);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && sidecar) {
      void createWindow(sidecar.origin).then((window) => {
        mainWindow = window;
        buildMenu(window);
      });
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  // The child does not outlive the app -- a stray server holding the library
  // would make the next launch fail in a confusing way.
  app.on("before-quit", () => sidecar?.stop());
  process.on("exit", () => sidecar?.stop());
}
