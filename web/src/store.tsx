/**
 * Reader state, across however many decks are open.
 *
 * Several decks can be open at once because that is how a week of lectures is
 * actually read -- Lec01 next to Lec02, with the exercise sheet beside them.
 * Each keeps its own scroll position, selection and progress; the side panels
 * follow whichever one is in front.
 *
 * Dockview mounts every panel separately, so panels cannot pass props to each
 * other. This context is how a sentence selected in one reader reaches the ask
 * panel.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { pathsFromDrop, pickFiles } from "./platform";
import { range } from "./units";
import {
  api,
  board as boardApi,
  subscribe,
  type BoardData,
  type BoardItem,
  type Deck,
  type Progress,
  type Sentence,
} from "./api";

export type Theme = "dark" | "light";

export interface DeckState {
  deck: Deck;
  progress: Progress;
  currentPage: number;
  /**
   * The sentences the reader has picked out, by position (see units.ts).
   * `anchor` is where a shift-click measures its range from.
   */
  selection: { keys: string[]; anchor: string | null };
  /**
   * Slides picked out in the chapter list, to be sent with a question.
   *
   * Empty is the ordinary case and means "the page I am reading" -- so the
   * cheap thing is what happens when you do nothing, and the expensive thing
   * is something you asked for by name.
   */
  context: { pages: number[]; anchor: number | null };
}

/** How a click changes the selection. */
export type SelectMode = "replace" | "toggle" | "range";

interface Store {
  decks: DeckState[];
  /** The board, shared by the explorer sidebar and the board tab. */
  board: BoardData | null;
  boardFolder: string | null;
  activeId: string | null;
  active: DeckState | null;
  showHidden: boolean;
  theme: Theme;
  error: string | null;
  busy: boolean;

  openPaths: (paths: string[]) => Promise<void>;
  /** Import onto the board's inbox. Never opens anything. */
  importFiles: (files: File[]) => Promise<void>;
  importFromDialog: () => Promise<void>;
  openItem: (item: BoardItem) => Promise<void>;
  reloadBoard: () => Promise<void>;
  /** Apply a board the caller already has, without another round trip. */
  setBoard: (board: BoardData) => void;
  setBoardFolder: (id: string | null) => void;
  runBoard: (
    action: () => Promise<BoardData | { board: BoardData }>,
  ) => Promise<void>;
  closeDeck: (id: string) => void;
  activate: (id: string) => void;

  goToPage: (index: number) => void;
  setVisiblePage: (deckId: string, index: number) => void;
  /** Click a sentence. `mode` comes from the modifier keys. */
  select: (deckId: string, key: string, mode: SelectMode) => void;
  clearSelection: (deckId: string) => void;
  /** Click a chapter. Same modifiers, but choosing pages to send, not to read. */
  selectContextPage: (deckId: string, index: number, mode: SelectMode) => void;
  clearContext: (deckId: string) => void;
  setShowHidden: (value: boolean) => void;
  setTheme: (theme: Theme) => void;
  dismissError: () => void;

  lockTerm: (term: string, translation: string) => Promise<number>;
  retranslate: (deckId: string, sentence: Sentence) => Promise<void>;
  editTranslation: (deckId: string, sentence: Sentence, text: string) => Promise<void>;
  registerScroll: (deckId: string, fn: (index: number) => void) => void;
}

const StoreContext = createContext<Store | null>(null);

export function useStore(): Store {
  const store = useContext(StoreContext);
  if (!store) throw new Error("useStore used outside the provider");
  return store;
}

/** The deck a panel belongs to, or the active one for the shared side panels. */
export function useDeck(deckId?: string): DeckState | null {
  const { decks, active } = useStore();
  if (!deckId) return active;
  return decks.find((d) => d.deck.id === deckId) ?? null;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [decks, setDecks] = useState<DeckState[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [board, setBoard] = useState<BoardData | null>(null);
  const [boardFolder, setBoardFolder] = useState<string | null>(null);
  // Remembered like the theme: a reading preference the user set once should
  // not reset every launch.
  const [showHidden, setShowHiddenState] = useState(
    () => localStorage.getItem("classhelper-show-hidden") === "1",
  );
  const setShowHidden = useCallback((value: boolean) => {
    setShowHiddenState(value);
    localStorage.setItem("classhelper-show-hidden", value ? "1" : "0");
  }, []);
  /**
   * The theme lives here rather than being passed to panels as a prop.
   *
   * Dockview fixes a panel's component when the panel is created, so a prop
   * threaded through the components map is frozen at whatever it was then --
   * the theme changed everywhere except in the control that changed it, which
   * read as a switch that did not work.
   *
   * Light by default rather than following the system: slides are written on
   * white and the reading column mirrors them, so matching a dark desktop
   * would invert the one thing meant to look like the original.
   */
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem("classhelper-theme") as Theme) ?? "light",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("classhelper-theme", theme);
  }, [theme]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scrollers = useRef<Map<string, (index: number) => void>>(new Map());
  /** The page each deck was last reported to be on, to keep from saying it twice. */
  const focused = useRef<Map<string, number>>(new Map());
  const focusTimer = useRef<Map<string, number>>(new Map());

  const patch = useCallback(
    (id: string, change: (state: DeckState) => DeckState) =>
      setDecks((current) =>
        current.map((d) => (d.deck.id === id ? change(d) : d)),
      ),
    [],
  );

  const add = useCallback((opened: Deck[]) => {
    if (!opened.length) return;
    setDecks((current) => {
      const existing = new Set(current.map((d) => d.deck.id));
      const fresh = opened
        .filter((deck) => !existing.has(deck.id))
        .map((deck) => ({
          deck,
          progress: deck.progress,
          currentPage: 0,
          selection: { keys: [], anchor: null },
          context: { pages: [], anchor: null },
        }));
      return [...current, ...fresh];
    });
    setActiveId(opened[opened.length - 1]!.id);
  }, []);

  // One event stream per open deck. Each carries whole pages rather than
  // deltas, so a reconnection needs no replay to end up correct.
  useEffect(() => {
    const stops = decks.map((state) =>
      subscribe(state.deck.id, {
        onSync: (fresh) =>
          patch(state.deck.id, (d) => ({
            ...d,
            progress: fresh.progress,
            deck: { ...d.deck, pages: fresh.pages },
          })),
        onProgress: (progress) =>
          patch(state.deck.id, (d) => ({ ...d, progress })),
        onPage: (event) =>
          patch(state.deck.id, (d) => {
            const pages = d.deck.pages.slice();
            pages[event.page] = { ...pages[event.page]!, blocks: event.blocks };
            return { ...d, progress: event.progress, deck: { ...d.deck, pages } };
          }),
      }),
    );
    return () => stops.forEach((stop) => stop());
  }, [decks.map((d) => d.deck.id).join(","), patch]);

  const openPaths = useCallback(
    async (paths: string[]) => {
      if (!paths.length) return;
      setBusy(true);
      setError(null);
      const opened: Deck[] = [];
      const problems: string[] = [];
      for (const path of paths) {
        try {
          opened.push(await api.open(path));
        } catch (e) {
          problems.push(
            `${path.split("/").pop()}：${e instanceof Error ? e.message : e}`,
          );
        }
      }
      add(opened);
      // Partial success is normal when several files are opened at once, so
      // failures are reported without discarding what did work.
      if (problems.length) setError(problems.join("\n"));
      setBusy(false);
    },
    [add],
  );

  const reloadBoard = useCallback(async () => {
    try {
      setBoard(await boardApi.get());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reloadBoard();
  }, [reloadBoard]);

  /**
   * Notice the board being rearranged outside the app.
   *
   * It is a real directory tree, and that is the point -- so a folder made in
   * the Finder, or a deck dropped into a course from anywhere else, is a normal
   * thing to do and the reader was the last to know about it.
   *
   * A poll rather than a file watcher: the server answers with sixteen bytes
   * derived from a scan that takes a tenth of a millisecond, a watcher that
   * misses an event leaves the tree wrong until the next restart with nothing
   * to recover it, and this way there is no platform-specific code at all. The
   * check is skipped while the window is in the background, and runs the moment
   * it comes forward -- which is exactly when you get back from the Finder.
   */
  /** Board changes in flight. The poll stands aside rather than racing them. */
  const mutating = useRef(0);
  const seenVersion = useRef<string | null>(null);
  useEffect(() => {
    seenVersion.current = board?.version ?? seenVersion.current;
  }, [board?.version]);

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      if (stopped || document.hidden || mutating.current > 0) return;
      try {
        const { version } = await boardApi.version();
        if (stopped || version === seenVersion.current) return;
        seenVersion.current = version;
        await reloadBoard();
      } catch {
        /* The board is not worth an error message for a poll that missed. */
      }
    };
    const timer = window.setInterval(check, 2500);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [reloadBoard]);

  const runBoard = useCallback(
    async (action: () => Promise<BoardData | { board: BoardData }>) => {
      setError(null);
      mutating.current += 1;
      try {
        const result = await action();
        setBoard("board" in result ? result.board : result);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        mutating.current -= 1;
      }
    },
    [],
  );

  /**
   * Import dropped files onto the inbox.
   *
   * Nothing is opened. Filing and reading are separate decisions, and dropping
   * a term's worth of lectures should not bury the window in reader tabs.
   *
   * In the desktop shell the drop carries real paths, so the configured import
   * mode applies -- move, copy or link. A browser is only told the contents,
   * so there the bytes are uploaded and there is no original to move.
   */
  const importFiles = useCallback(async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      const paths = pathsFromDrop(files);
      const { failed, board: fresh } =
        paths.length === files.length
          ? await api.importPaths(paths)
          : await api.upload(files);
      setBoard(fresh);
      if (failed.length) {
        setError(failed.map((f) => `${f.name}：${f.error}`).join("\n"));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const importFromDialog = useCallback(async () => {
    setBusy(true);
    try {
      const paths = await pickFiles();
      if (!paths.length) return;
      const { failed, board: fresh } = await api.importPaths(paths);
      setBoard(fresh);
      if (failed.length) {
        setError(failed.map((f) => `${f.name}：${f.error}`).join("\n"));
      }
    } catch (e) {
      setError(
        "系统文件对话框打不开。" +
          (e instanceof Error ? `（${e.message}）` : "") +
          "你也可以直接把文件拖进窗口。",
      );
    } finally {
      setBusy(false);
    }
  }, []);

  const closeDeck = useCallback((id: string) => {
    setDecks((current) => {
      const remaining = current.filter((d) => d.deck.id !== id);
      setActiveId((active) =>
        active === id ? (remaining[remaining.length - 1]?.deck.id ?? null) : active,
      );
      return remaining;
    });
    scrollers.current.delete(id);
    focused.current.delete(id);
    window.clearTimeout(focusTimer.current.get(id));
    focusTimer.current.delete(id);
    void api.close(id).catch(() => {});
  }, []);

  const value = useMemo<Store>(() => {
    const active = decks.find((d) => d.deck.id === activeId) ?? null;

    return {
      decks,
      board,
      boardFolder,
      activeId,
      active,
      showHidden,
      theme,
      error,
      busy,
      openPaths,
      importFiles,
      importFromDialog,
      reloadBoard,
      setBoard,
      setBoardFolder,
      runBoard,
      openItem: async (item) => {
        if (item.missing) {
          setError(`${item.name} 的文件已经不在原来的位置了。`);
          return;
        }
        await openPaths([item.path]);
      },
      closeDeck,
      activate: setActiveId,
      setShowHidden,
      setTheme: setThemeState,
      dismissError: () => setError(null),

      goToPage: (index) => {
        if (activeId) scrollers.current.get(activeId)?.(index);
      },
      registerScroll: (deckId, fn) => {
        scrollers.current.set(deckId, fn);
      },

      setVisiblePage: (deckId, index) => {
        // The observer that calls this fires several times a second while the
        // reader is scrolling, and it used to post to the server every single
        // time -- an HTTP request per frame of a flick, to say the same thing.
        // Only a page that actually changed is worth telling anyone about.
        if (focused.current.get(deckId) === index) return;
        focused.current.set(deckId, index);

        patch(deckId, (d) =>
          d.currentPage === index ? d : { ...d, currentPage: index },
        );

        // Held back a moment. A flick down a long deck crosses fifty pages,
        // and the scheduler only wants to know the one you stopped on --
        // telling it about the forty-nine you flew past reorders the queue
        // fifty times to arrive at the same answer.
        window.clearTimeout(focusTimer.current.get(deckId));
        focusTimer.current.set(
          deckId,
          window.setTimeout(() => {
            // Fire and forget: this only reprioritises a queue, and a dropped
            // request costs nothing worth telling the user about.
            void api.focus(deckId, index).catch(() => {});
          }, 200),
        );
      },

      select: (deckId, key, mode) =>
        patch(deckId, (d) => {
          const current = d.selection.keys;
          if (mode === "toggle") {
            const keys = current.includes(key)
              ? current.filter((k) => k !== key)
              : [...current, key];
            return { ...d, selection: { keys, anchor: key } };
          }
          if (mode === "range" && d.selection.anchor) {
            return {
              ...d,
              selection: {
                keys: range(d.deck, d.selection.anchor, key),
                anchor: d.selection.anchor,
              },
            };
          }
          // A plain click on the only selected sentence clears it, which is how
          // you put a question back to "about this whole page".
          const only = current.length === 1 && current[0] === key;
          return {
            ...d,
            selection: { keys: only ? [] : [key], anchor: only ? null : key },
          };
        }),

      clearSelection: (deckId) =>
        patch(deckId, (d) => ({ ...d, selection: { keys: [], anchor: null } })),

      selectContextPage: (deckId, index, mode) =>
        patch(deckId, (d) => {
          const current = d.context.pages;
          if (mode === "toggle") {
            const pages = current.includes(index)
              ? current.filter((p) => p !== index)
              : [...current, index].sort((a, b) => a - b);
            return { ...d, context: { pages, anchor: index } };
          }
          if (mode === "range" && d.context.anchor !== null) {
            const [lo, hi] =
              d.context.anchor <= index
                ? [d.context.anchor, index]
                : [index, d.context.anchor];
            const pages = [];
            for (let i = lo; i <= hi; i++) pages.push(i);
            return { ...d, context: { pages, anchor: d.context.anchor } };
          }
          return { ...d, context: { pages: [index], anchor: index } };
        }),

      clearContext: (deckId) =>
        patch(deckId, (d) => ({ ...d, context: { pages: [], anchor: null } })),

      lockTerm: async (term, translation) => {
        if (!active) return 0;
        setBusy(true);
        try {
          // The rebuilt pages arrive over the event stream, so there is nothing
          // to merge here; the count is only for the confirmation message.
          const result = await api.lockTerm(active.deck.id, term, translation);
          return result.sentences;
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          return 0;
        } finally {
          setBusy(false);
        }
      },

      retranslate: async (deckId, sentence) => {
        try {
          const updated = await api.retranslate(deckId, sentence.id);
          patch(deckId, (d) => ({
            ...d,
            deck: {
              ...d.deck,
              pages: d.deck.pages.map((page) => ({
                ...page,
                blocks: page.blocks.map((block) => ({
                  ...block,
                  sentences: block.sentences.map((s) =>
                    s.id === updated.id
                      ? { ...s, translation: updated.translation,
                          flagged: updated.flagged, edited: false }
                      : s,
                  ),
                })),
              })),
            },
          }));
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      },

      editTranslation: async (deckId, sentence, text) => {
        patch(deckId, (d) => ({
          ...d,
          deck: {
            ...d.deck,
            pages: d.deck.pages.map((page) => ({
              ...page,
              blocks: page.blocks.map((block) => ({
                ...block,
                sentences: block.sentences.map((s) =>
                  s.id === sentence.id
                    ? { ...s, translation: text, edited: true, flagged: false }
                    : s,
                ),
              })),
            })),
          },
        }));
        await api.editSentence(deckId, sentence.id, text).catch((e) => {
          setError(e instanceof Error ? e.message : String(e));
        });
      },
    };
  }, [decks, board, boardFolder, activeId, showHidden, theme, error, busy, openPaths,
      importFiles, importFromDialog, reloadBoard, runBoard, closeDeck, patch]);

  // The launcher can name a file to open on startup, so double-clicking a deck
  // lands in the reader instead of at a prompt.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    const wanted = new URLSearchParams(window.location.search).getAll("open");
    if (wanted.length) void openPaths(wanted);
  }, [openPaths]);

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
