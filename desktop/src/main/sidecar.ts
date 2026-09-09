/**
 * The Python server, run as a child of the desktop app.
 *
 * The parsing, translation and library layers stay in Python because that is
 * where the hard-won behaviour lives: the pptx workarounds for
 * mc:AlternateContent and OMML maths, the PDF line/paragraph/list judgement.
 * Reimplementing those in Node would mean rediscovering the same bugs on real
 * lecture decks, and JavaScript has no equivalent of python-pptx to start from.
 *
 * So the shell owns the window and the desktop integration; Python owns the
 * documents. They talk over HTTP on loopback, exactly as the browser build
 * does -- which is also why one renderer serves both.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { app } from "electron";

export interface Sidecar {
  port: number;
  origin: string;
  stop(): void;
}

/** Ask the OS for a free port, so two windows never collide. */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

/**
 * How to start the server.
 *
 * Packaged, it is a PyInstaller binary in resources, so nothing is required of
 * the machine. In development it is the project's own virtualenv, so a change
 * to the Python is one restart away rather than a rebuild.
 */
function command(port: number): { file: string; args: string[]; cwd: string } {
  const repo = path.resolve(__dirname, "../../..");

  if (app.isPackaged) {
    const binary = path.join(process.resourcesPath, "sidecar", "classhelper-server");
    return {
      file: binary,
      args: ["serve", "--port", String(port)],
      cwd: path.dirname(binary),
    };
  }

  const venv = path.join(repo, ".venv", "bin", "classhelper");
  const file = existsSync(venv) ? venv : "classhelper";
  return {
    file,
    args: ["serve", "--port", String(port)],
    cwd: repo,
  };
}

async function waitUntilReady(origin: string, timeoutMs = 40_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${origin}/api/status`);
      // Any answer means the server is up; 503 only means it is unconfigured,
      // which the settings page is there to fix.
      if (response.status < 500 || response.status === 503) return;
    } catch {
      /* not listening yet */
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error("Python 服务没有在 40 秒内启动。");
}

/**
 * Where settings live.
 *
 * In development that is the project folder, the way the double-clickable
 * build works: the program is a folder you keep, so its configuration is in
 * it. A packaged app is a signed bundle that must not be written to, so there
 * it is the standard per-user location instead. `CLASSHELPER_HOME` is the
 * knob the Python side already honours.
 */
function configHome(): string | undefined {
  // An explicit CLASSHELPER_HOME wins. Otherwise a packaged build has exactly
  // one place its settings can be, which is the whole point of the default --
  // but it also means a second library, or a test run, has nowhere to go.
  if (process.env.CLASSHELPER_HOME) return process.env.CLASSHELPER_HOME;
  return app.isPackaged ? app.getPath("userData") : undefined;
}

export async function startSidecar(
  onLog: (line: string) => void,
): Promise<Sidecar> {
  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const { file, args, cwd } = command(port);
  const home = configHome();

  let child: ChildProcess;
  try {
    child = spawn(file, args, {
      cwd,
      // A proxy configured for the wider network must not be asked to reach a
      // loopback address; the server itself still honours it for the API calls
      // it makes outward.
      env: {
        ...process.env,
        NO_PROXY: "127.0.0.1,localhost",
        PYTHONUNBUFFERED: "1",
        ...(home ? { CLASSHELPER_HOME: home } : {}),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    throw new Error(`启动 Python 服务失败：${String(error)}`);
  }

  child.stdout?.on("data", (chunk) => onLog(String(chunk).trimEnd()));
  child.stderr?.on("data", (chunk) => onLog(String(chunk).trimEnd()));

  const exited = new Promise<never>((_, reject) => {
    child.once("error", (error) => reject(new Error(`Python 服务无法启动：${error.message}`)));
    child.once("exit", (code) =>
      reject(new Error(`Python 服务退出了（代码 ${code ?? "?"}），详见日志。`)),
    );
  });

  // Whichever settles first: the server answering, or the child dying. Without
  // racing them a failed start would just hang on the readiness poll.
  await Promise.race([waitUntilReady(origin), exited]);

  return {
    port,
    origin,
    stop() {
      if (!child.killed) child.kill();
    },
  };
}
