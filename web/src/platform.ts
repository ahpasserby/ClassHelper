/**
 * The desktop shell, as the renderer sees it.
 *
 * Everything that needs the operating system goes through here: the file
 * dialog, the Finder, and — the one thing a web page genuinely cannot do — the
 * real path of a dropped file, which is what lets the import mode apply to a
 * drop at all.
 *
 * The bridge is injected by the preload script, so it is absent in a plain
 * browser tab (a `vite dev` renderer, say). These functions say so rather than
 * pretending: there is no server-side substitute any more.
 */

interface DesktopApi {
  desktop: true;
  pickFiles(): Promise<string[]>;
  reveal(target: string): Promise<boolean>;
  pathForFile(file: File): string | null;
  onMenu(handler: (command: string) => void): () => void;
  serverLog(): Promise<string>;
}

const bridge: DesktopApi | undefined = (
  window as unknown as { classhelper?: DesktopApi }
).classhelper;

export const isDesktop = Boolean(bridge?.desktop);

/**
 * macOS puts the traffic lights over the top-left of the window content when
 * the title bar is hidden, so the first thing in the top row has to start to
 * the right of them. Marked on the root element so the stylesheet can reserve
 * the space without the layout code knowing about window chrome.
 */
if (isDesktop && navigator.platform.startsWith("Mac")) {
  document.documentElement.dataset.titlebar = "inset";
}

/** Choose files to import. Returns absolute paths. */
export async function pickFiles(): Promise<string[]> {
  if (!bridge) throw new Error("文件对话框需要在桌面应用里打开。");
  return bridge.pickFiles();
}

/**
 * The paths behind a drop, when they can be known.
 *
 * Usually all of them: a drop from the Finder carries real files. A drag from
 * somewhere that only offers content -- a mail attachment, a web page -- yields
 * nothing here, and the caller uploads the bytes instead rather than refusing
 * the drop.
 */
export function pathsFromDrop(files: File[]): string[] {
  if (!bridge) return [];
  return files.map((file) => bridge.pathForFile(file)).filter((p): p is string => !!p);
}

/** Show a file or folder in the desktop's file manager. */
export async function reveal(absolutePath: string): Promise<void> {
  if (!bridge) throw new Error("需要在桌面应用里打开。");
  await bridge.reveal(absolutePath);
}

/** Native menu commands, delivered as the same actions the top bar triggers. */
export function onMenuCommand(handler: (command: string) => void): () => void {
  return bridge ? bridge.onMenu(handler) : () => {};
}

export async function serverLog(): Promise<string> {
  return bridge ? bridge.serverLog() : "";
}
