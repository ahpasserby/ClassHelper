/**
 * Client for the local classhelper server.
 *
 * Every call here talks to 127.0.0.1. There is no remote service and no auth:
 * the server is the other half of this application, started by the same
 * launcher.
 */

export type BlockKind =
  | "title"
  | "body"
  | "code"
  | "caption"
  | "fragment"
  | "chrome";

export interface Sentence {
  id: string;
  text: string;
  translation: string | null;
  flagged: boolean;
  edited: boolean;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Block {
  id: string;
  kind: BlockKind;
  reason: string;
  text: string;
  box: Box;
  /** The source marked this as a list item. */
  bullet: boolean;
  /** Outline depth, 0 being the outermost level. */
  level: number;
  /** Type size in points, as the source had it. 0 when unknown. */
  font: number;
  mono: boolean;
  /** The shape this came from. Blocks sharing one share its position. */
  shape: string;
  sentences: Sentence[];
}

export interface PageImage {
  id: string;
  box: Box;
}

export interface Page {
  index: number;
  title: string;
  /** Width over height of the source page, so a 4:3 deck is shown as 4:3. */
  aspect: number;
  /** The page's height in points, which is what `Block.font` is measured in. */
  height_pt: number;
  blocks: Block[];
  images: PageImage[];
}

/** Whether the slide view can show the file itself. See render.py. */
export interface SourceStatus {
  mode: "exact" | "building" | "approximate" | "none";
  detail: string;
}

export interface Progress {
  done: number;
  total: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface Deck {
  id: string;
  name: string;
  path: string;
  format: string;
  warnings: string[];
  notes: string[];
  target_lang: string;
  pages: Page[];
  progress: Progress;
  source: SourceStatus;
}

/** A folder on the board. Nests freely; depth 0 reads as a semester. */
export interface BoardFolder {
  id: string;
  name: string;
  parent: string | null;
  depth: number;
  items: BoardItem[];
  children: BoardFolder[];
}

export interface BoardItem {
  id: string;
  name: string;
  path: string;
  folder: string | null;
  /** The file was moved or deleted on disk after being filed. */
  missing: boolean;
  format: string;
  /** False for course material the reader cannot open, which is still listed. */
  readable: boolean;
}

export interface BoardData {
  /** Changes when the tree on disk does. See `board.version`. */
  version: string;
  folders: BoardFolder[];
  inbox: BoardItem[];
  formats: string[];
  /** The library directory these folders really are. */
  root: string;
}

/**
 * The result of importing. Importing never opens anything: a folder of twelve
 * lectures dropped on the window means "file these", not "read all of them".
 */
export interface ImportResult {
  added: BoardItem[];
  failed: { name: string; error: string }[];
  board: BoardData;
}

export interface ScopeLabel {
  id: string;
  name: string;
}

export interface ScopedTerm {
  term: string;
  translation: string;
  locked: boolean;
  count: number;
  /** Which scope supplied this rendering. */
  scope: string;
  /** True when it comes from a broader scope than the one being viewed. */
  inherited: boolean;
}

export interface Term {
  term: string;
  translation: string;
  locked: boolean;
  count: number;
}

export interface SpendByModel {
  model: string;
  kind: string;
  calls: number;
  cny: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface PricingStatus {
  updated_at: number | null;
  source: string;
  note: string;
  models: string[];
  fx: Record<string, number>;
}

export interface Spend {
  total: number;
  month: number;
  today: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  /** Calls made with a model we have no published price for. Never estimated. */
  unpriced_calls: number;
  unpriced_models: string[];
  since: number | null;
  currency: string;
  pricing?: PricingStatus;
  by_model: SpendByModel[];
  daily: { day: string; cny: number }[];
}

/** The server writes its error messages to be shown to the user unchanged. */
export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* a non-JSON error body tells us nothing extra */
    }
    throw new ApiError(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () =>
    request<{ configured: boolean; detail?: string; model?: string; ask_model?: string }>(
      "/api/status",
    ),

  /** Put files on the board's inbox by path. Nothing is opened. */
  importPaths: (paths: string[]) =>
    request<ImportResult>("/api/import", {
      method: "POST",
      body: JSON.stringify({ paths }),
    }),

  /**
   * Open dropped files by sending their contents.
   *
   * A browser never reveals a dropped file's path, but it hands over the bytes
   * without complaint -- and the bytes are what the parser actually needs.
   */
  upload: async (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const response = await fetch("/api/upload", { method: "POST", body: form });
    if (!response.ok) {
      let detail = `${response.status}`;
      try {
        detail = (await response.json())?.detail ?? detail;
      } catch {
        /* non-JSON body */
      }
      throw new ApiError(detail);
    }
    return response.json() as Promise<ImportResult>;
  },

  close: (id: string) =>
    request<{ ok: boolean }>(`/api/deck/${id}`, { method: "DELETE" }),

  open: (path: string) =>
    request<Deck>("/api/open", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  deck: (id: string) => request<Deck>(`/api/deck/${id}`),

  /** Tell the scheduler which page is on screen so it is translated next. */
  focus: (id: string, page: number) =>
    request<{ progress: Progress }>(`/api/deck/${id}/focus/${page}`, {
      method: "POST",
    }),

  glossary: (id: string) => request<{ terms: Term[] }>(`/api/deck/${id}/glossary`),

  lockTerm: (id: string, term: string, translation: string) =>
    request<{ pages_changed: number[]; sentences: number }>(
      `/api/deck/${id}/glossary`,
      { method: "POST", body: JSON.stringify({ term, translation }) },
    ),

  unlockTerm: (id: string, term: string) =>
    request<{ ok: boolean }>(`/api/deck/${id}/glossary/${encodeURIComponent(term)}`, {
      method: "DELETE",
    }),

  retranslate: (id: string, sentenceId: string) =>
    request<{ id: string; translation: string; flagged: boolean }>(
      `/api/deck/${id}/retranslate/${sentenceId}`,
      { method: "POST" },
    ),

  editSentence: (id: string, sentenceId: string, translation: string) =>
    request<{ ok: boolean }>(`/api/deck/${id}/sentence/${sentenceId}`, {
      method: "PUT",
      body: JSON.stringify({ translation }),
    }),

  imageUrl: (id: string, imageId: string) => `/api/deck/${id}/image/${imageId}`,

  /** A picture of one source page. 404s when only an approximation is possible. */
  sourceUrl: (id: string, page: number, width: number) =>
    `/api/deck/${id}/source/${page}?w=${width}`,

  sourceStatus: (id: string) => request<SourceStatus>(`/api/deck/${id}/source`),

  spend: () => request<Spend>("/api/spend"),

  /** Fetch the vendor's price list now instead of waiting for the weekly check. */
  refreshPrices: () => request<Spend>("/api/spend/prices", { method: "POST" }),
};

export interface ProviderChoice {
  id: string;
  label: string;
  base_url: string;
  model: string;
  ask_model: string;
  keyless: boolean;
}

export interface ImportModeChoice {
  id: string;
  label: string;
  hint: string;
}

export interface SettingsData {
  configured: boolean;
  path: string;
  user_data_path: string;
  library_path: string;
  state_path: string;
  import_mode: string;
  window_mode: string;
  import_modes: ImportModeChoice[];
  provider: string;
  base_url: string;
  model: string;
  ask_model: string;
  target_lang: string;
  api_key_set: boolean;
  api_key_hint: string;
  providers: ProviderChoice[];
}

/** Sent in place of the key to mean "keep the one already stored". */
export const KEEP_KEY = "__unchanged__";

export interface SettingsInput {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
  ask_model: string;
  target_lang: string;
  user_data_path?: string;
  import_mode?: string;
  window_mode?: string;
}

export const settings = {
  read: () => request<SettingsData>("/api/settings"),

  /** The stored key in full. Only fetched when the reveal control is used. */
  reveal: () => request<{ api_key: string }>("/api/settings/key"),

  /** What the provider says it has, rather than what we guessed it has. */
  models: (body: SettingsInput) =>
    request<{ models: string[]; detail: string }>("/api/settings/models", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  save: (body: SettingsInput) =>
    request<{ ok: boolean; open_decks: number }>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  /** Try the settings against the real service before committing to them. */
  test: (body: SettingsInput) =>
    request<{ ok: boolean; detail: string }>("/api/settings/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export const board = {
  /** Sixteen bytes saying whether the tree changed. Polled; see the store. */
  version: () => request<{ version: string }>("/api/board/version"),

  get: () => request<BoardData>("/api/board"),

  createFolder: (name: string, parent: string | null) =>
    request<{ id: string; board: BoardData }>("/api/board/folders", {
      method: "POST",
      body: JSON.stringify({ name, parent }),
    }),

  // Paths travel in the body, not the URL: a library-relative path contains
  // slashes, and encoding those into a path parameter only invites the two
  // sides to disagree about escaping.
  rename: (path: string, name: string) =>
    request<{ id: string; board: BoardData }>("/api/board/rename", {
      method: "POST",
      body: JSON.stringify({ path, name }),
    }),

  /** Move a whole selection at once, so the board is never half-moved. */
  move: (paths: string[], dest: string | null) =>
    request<{ moved: string[]; board: BoardData }>("/api/board/move", {
      method: "POST",
      body: JSON.stringify({ paths, dest }),
    }),

  deleteFolder: (path: string) =>
    request<{ returned_to_inbox: number; board: BoardData }>(
      "/api/board/delete-folder",
      { method: "POST", body: JSON.stringify({ path }) },
    ),

  deleteItem: (path: string) =>
    request<{ board: BoardData }>("/api/board/delete-item", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
};

export const glossary = {
  /** Terms applying at `scope`, inherited ones included. */
  read: (scope: string | null) =>
    request<{ scope: string; chain: ScopeLabel[]; terms: ScopedTerm[] }>(
      `/api/glossary${scope ? `?scope=${encodeURIComponent(scope)}` : ""}`,
    ),

  set: (term: string, translation: string, scope: string | null) =>
    request<{ decks_changed: { deck: string; sentences: number }[] }>(
      "/api/glossary",
      { method: "POST", body: JSON.stringify({ term, translation, scope }) },
    ),

  unset: (term: string, scope: string | null) =>
    request<{ ok: boolean }>(
      `/api/glossary/${encodeURIComponent(term)}` +
        (scope ? `?scope=${encodeURIComponent(scope)}` : ""),
      { method: "DELETE" },
    ),
};

export interface ChatSummary {
  id: string;
  title: string;
  created: number;
  updated: number;
  count: number;
}

/** assistant-ui's exported repository item, stored verbatim on the server. */
export interface ChatItem {
  parentId: string | null;
  message: Record<string, unknown>;
}

export const chats = {
  list: (deckId: string) =>
    request<{ chats: ChatSummary[] }>(`/api/deck/${deckId}/chats`),

  create: (deckId: string) =>
    request<ChatSummary>(`/api/deck/${deckId}/chats`, { method: "POST" }),

  read: (deckId: string, chatId: string) =>
    request<{ id: string; title: string; messages: ChatItem[] }>(
      `/api/deck/${deckId}/chats/${chatId}`,
    ),

  append: (deckId: string, chatId: string, item: ChatItem) =>
    request<ChatSummary>(`/api/deck/${deckId}/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify(item),
    }),

  rename: (deckId: string, chatId: string, title: string) =>
    request<{ ok: boolean }>(`/api/deck/${deckId}/chats/${chatId}`, {
      method: "PUT",
      body: JSON.stringify({ title }),
    }),

  remove: (deckId: string, chatId: string) =>
    request<{ ok: boolean }>(`/api/deck/${deckId}/chats/${chatId}`, {
      method: "DELETE",
    }),
};

export interface PageEvent {
  page: number;
  blocks: Block[];
  errors: string[];
  progress: Progress;
}

/**
 * Subscribe to translation progress.
 *
 * Returns an unsubscribe function. EventSource reconnects on its own, and
 * because every event carries the page's full contents rather than a delta,
 * a reconnection needs no replay to be correct.
 */
export function subscribe(
  deckId: string,
  handlers: {
    onSync?: (deck: Deck) => void;
    onPage?: (e: PageEvent) => void;
    onProgress?: (p: Progress) => void;
  },
): () => void {
  const source = new EventSource(`/api/deck/${deckId}/events`);
  // Sent immediately on connect, and again after any reconnection.
  source.addEventListener("sync", (e) =>
    handlers.onSync?.(JSON.parse((e as MessageEvent).data)),
  );
  source.addEventListener("page", (e) =>
    handlers.onPage?.(JSON.parse((e as MessageEvent).data)),
  );
  source.addEventListener("progress", (e) =>
    handlers.onProgress?.(JSON.parse((e as MessageEvent).data)),
  );
  source.addEventListener("idle", (e) =>
    handlers.onProgress?.(JSON.parse((e as MessageEvent).data)),
  );
  return () => source.close();
}

/**
 * Stream an answer. Yields text fragments as they arrive.
 *
 * Errors raised after the stream opens arrive in-band, since the HTTP status
 * has already been sent by then.
 */
export async function* askStream(
  deckId: string,
  body: {
    page: number;
    question: string;
    sentence_id?: string | null;
    sentence_ids?: string[];
    /** Slides picked out in the chapter list. Empty means `page` alone. */
    pages?: number[];
    history?: { role: string; content: string }[];
  },
  signal?: AbortSignal,
): AsyncGenerator<string> {
  const response = await fetch(`/api/deck/${deckId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(`Could not start the answer (${response.status}).`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Server-sent events are separated by a blank line; a chunk can split one
    // in half, so anything after the last separator stays buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") return;
      const parsed = JSON.parse(payload);
      if (parsed.error) throw new ApiError(parsed.error);
      if (parsed.text) yield parsed.text as string;
    }
  }
}
