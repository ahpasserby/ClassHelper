/**
 * The bridge between the reader and the desktop.
 *
 * Deliberately small: three things the browser build has to work around, and
 * nothing more. The renderer stays the same code either way -- it checks
 * whether this object exists and falls back to the HTTP endpoints when it does
 * not.
 */

import { contextBridge, ipcRenderer, webUtils } from "electron";

const api = {
  /** True when running inside the desktop shell rather than a browser tab. */
  desktop: true,

  /** The system file dialog. */
  pickFiles: (): Promise<string[]> => ipcRenderer.invoke("dialog:pick"),

  /** Show a file or folder in the Finder / File Explorer. */
  reveal: (target: string): Promise<boolean> =>
    ipcRenderer.invoke("shell:reveal", target),

  /**
   * The real path of a dropped file.
   *
   * The one thing a browser genuinely cannot do: a web page is told the
   * contents of a dropped file but never where it came from, so the browser
   * build has to upload the bytes and can never move, copy or link the
   * original. Here the import mode applies to dropped files too.
   */
  pathForFile: (file: File): string | null => {
    try {
      return webUtils.getPathForFile(file) || null;
    } catch {
      return null;
    }
  },

  /** Menu commands, so the native menu drives the same actions as the top bar. */
  onMenu: (handler: (command: string) => void): (() => void) => {
    const commands = ["board", "settings", "left", "right", "reset", "import"];
    const listeners = commands.map((command) => {
      const channel = `menu:${command}`;
      const listener = () => handler(command);
      ipcRenderer.on(channel, listener);
      return () => ipcRenderer.off(channel, listener);
    });
    return () => listeners.forEach((off) => off());
  },

  /** The sidecar's output, for diagnosing a bad start. */
  serverLog: (): Promise<string> => ipcRenderer.invoke("app:log"),
};

contextBridge.exposeInMainWorld("classhelper", api);

export type DesktopApi = typeof api;
