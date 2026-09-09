/**
 * The window: a Dockview workspace, a top bar and a status bar.
 *
 * Each open deck is a tab. The slide list, the ask panel and the glossary are
 * shared and follow whichever deck is in front -- they are furniture, not
 * documents, so closing one has to be recoverable, which is what the 视图 menu
 * and the watermark below are for. Losing a panel with no way back was the
 * first thing that went wrong in real use.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DockviewReact } from "dockview-react";
import type {
  DockviewApi,
  DockviewReadyEvent,
  IDockviewPanelProps,
} from "dockview";
import { onMenuCommand } from "./platform";
import { StoreProvider, useStore } from "./store";
import { Reader } from "./panels/Reader";
import { Chapters } from "./panels/Chapters";
import { SourceSlides } from "./panels/SourceSlides";
import { Ask } from "./panels/Ask";
import { Board } from "./panels/Board";
import { Explorer } from "./panels/Explorer";
import { Settings } from "./panels/Settings";
import { Spending } from "./Spending";

const BOARD_PANEL = "board";
const SETTINGS_PANEL = "settings";

/** Panels that belong in the middle, sharing one group of tabs. */
function isCentre(id: string) {
  return id === BOARD_PANEL || id === SETTINGS_PANEL || id.startsWith("deck:");
}

/**
 * The sidebars are deliberately not Dockview panels.
 *
 * A docked group shares the window proportionally, so closing one made the
 * others grow to fill the space -- close the ask panel and the file tree took
 * half the screen. An editor's sidebars sit *outside* its editor grid for
 * exactly this reason: they are regions of fixed width, not participants in
 * the split. Dockview is left to manage the middle, where proportional
 * splitting is what you actually want.
 */
const LEFT_VIEWS = [
  { id: "explorer", title: "资源管理器" },
  { id: "chapters", title: "章节" },
  { id: "slides", title: "幻灯片" },
] as const;

const RIGHT_VIEWS = [{ id: "ask", title: "提问" }] as const;

type LeftView = (typeof LEFT_VIEWS)[number]["id"];

const SIDEBAR_MIN = 180;
/** A list only needs to be legible. A pane showing slides needs to be looked at. */
const SIDEBAR_MAX = 640;
const SIDEBAR_MAX_WIDE = 1100;

/** Below this a reading column is unusable -- text wraps to one word a line. */
const READER_MIN_WIDTH = 420;

export function App() {
  return (
    <StoreProvider>
      <Workspace />
    </StoreProvider>
  );
}

/** Where a new centre tab goes: beside the others, else between the sides. */
function readerPosition(api: DockviewApi) {
  const sibling = api.panels.find((p) => isCentre(p.id));
  if (sibling) {
    return { position: { referencePanel: sibling.id, direction: "within" as const } };
  }
  // Nothing else to anchor to: the sidebars are not Dockview panels, so an
  // empty grid means this tab is the grid.
  return {};
}

function Workspace() {
  const store = useStore();
  const apiRef = useRef<DockviewApi | null>(null);
  const [ready, setReady] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [leftView, setLeftView] = useState<LeftView>("explorer");
  const left = useSidebar("left", 250);
  const right = useSidebar("right", 400);
  const theme = store.theme;

  const components = useMemo(
    () => ({
      reader: (props: IDockviewPanelProps<{ deckId: string }>) => (
        <Reader deckId={props.params.deckId} />
      ),
      board: () => <Board />,
      ask: () => <Ask />,
      settings: () => <Settings />,
    }),
    [],
  );

  const onReady = useCallback((event: DockviewReadyEvent) => {
    apiRef.current = event.api;

    // Closing a deck's tab closes the deck. Side panels are not documents, so
    // they simply disappear until restored from the 视图 menu.
    event.api.onDidRemovePanel((panel) => {
      if (panel.id.startsWith("deck:")) {
        store.closeDeck(panel.id.slice(5));
      }
    });

    event.api.onDidActivePanelChange(({ panel }) => {
      if (panel?.id.startsWith("deck:")) store.activate(panel.id.slice(5));
    });

    setReady(true);
  }, [store]);

  /** Open a centre tab, or bring it to the front if it is already there. */
  const showCentre = useCallback((id: string, component: string, title: string) => {
    const api = apiRef.current;
    if (!api) return;
    const existing = api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
    api
      .addPanel({ id, component, title, ...readerPosition(api) })
      .group.api.setConstraints({ minimumWidth: READER_MIN_WIDTH });
  }, []);

  const showBoard = useCallback(
    () => showCentre(BOARD_PANEL, "board", "课板"),
    [showCentre],
  );
  const showSettings = useCallback(
    () => showCentre(SETTINGS_PANEL, "settings", "设置"),
    [showCentre],
  );

  /** Add or remove reader tabs so the layout matches the open decks. */
  useEffect(() => {
    const api = apiRef.current;
    if (!api || !ready) return;

    const wanted = new Map(store.decks.map((d) => [`deck:${d.deck.id}`, d]));

    for (const panel of api.panels) {
      if (panel.id.startsWith("deck:") && !wanted.has(panel.id)) {
        api.removePanel(panel);
      }
    }

    for (const [panelId, state] of wanted) {
      if (api.getPanel(panelId)) continue;
      const panel = api.addPanel({
        id: panelId,
        component: "reader",
        title: state.deck.name,
        params: { deckId: state.deck.id },
        ...readerPosition(api),
      });
      // Without a floor, the reading column can be dragged down to a sliver
      // where every line wraps after one word.
      panel.group.api.setConstraints({ minimumWidth: READER_MIN_WIDTH });
    }

    const active = store.activeId ? api.getPanel(`deck:${store.activeId}`) : null;
    if (active && !active.api.isActive) active.api.setActive();
  }, [store.decks, store.activeId, ready]);

  /** Put the window back the way it starts: both sidebars out, board in front. */
  const resetLayout = useCallback(() => {
    left.setOpen(true);
    right.setOpen(true);
    left.setWidth(250);
    right.setWidth(400);
    setLeftView("explorer");
    showBoard();
  }, [left, right, showBoard]);

  // The native menu drives the same actions as the top bar -- one set of
  // behaviours, two ways to reach them. A no-op in a browser tab.
  useEffect(
    () =>
      onMenuCommand((command) => {
        if (command === "board") showBoard();
        else if (command === "settings") showSettings();
        else if (command === "left") left.setOpen(!left.open);
        else if (command === "right") right.setOpen(!right.open);
        else if (command === "reset") resetLayout();
        else if (command === "import") void store.importFromDialog();
      }),
    [showBoard, showSettings, left, right, resetLayout, store],
  );

  // The board is the way in, so it is the tab you land on.
  const laidOut = useRef(false);
  useEffect(() => {
    if (!ready || laidOut.current) return;
    laidOut.current = true;
    showBoard();
  }, [ready, showBoard]);

  const onDrop = async (event: React.DragEvent) => {
    event.preventDefault();
    setDragging(false);
    // Dropping anywhere imports to the inbox. It never opens: a folder of
    // twelve lectures dropped on the window means "file these".
    const files = Array.from(event.dataTransfer.files);
    if (files.length) await store.importFiles(files);
  };

  return (
    <div
      className={`app${dragging ? " dropping" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (e.dataTransfer.types.includes("Files")) setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <TopBar
        onShowBoard={showBoard}
        onShowSettings={showSettings}
        onResetLayout={resetLayout}
        leftOpen={left.open}
        rightOpen={right.open}
        onToggleLeft={() => left.setOpen(!left.open)}
        onToggleRight={() => right.setOpen(!right.open)}
      />
      <div className="workspace">
        {left.open && (
          <Sidebar
            side="left"
            views={LEFT_VIEWS}
            active={leftView}
            width={left.width}
            wide={leftView === "slides"}
            onActivate={(id) => setLeftView(id as LeftView)}
            onWidth={left.setWidth}
            onClose={() => left.setOpen(false)}
          >
            {leftView === "explorer" ? (
            <Explorer />
          ) : leftView === "chapters" ? (
            <Chapters />
          ) : (
            <SourceSlides />
          )}
          </Sidebar>
        )}

        <DockviewReact
          className={theme === "dark" ? "dockview-theme-dark" : "dockview-theme-light"}
          components={components}
          // Close everything and you get an empty workspace, the way an editor
          // does. A panel offering to sell the app back to you is noise once
          // you already know what it is.
          watermarkComponent={Empty}
          onReady={onReady}
        />

        {right.open && (
          <Sidebar
            side="right"
            views={RIGHT_VIEWS}
            active="ask"
            width={right.width}
            onActivate={() => {}}
            onWidth={right.setWidth}
            onClose={() => right.setOpen(false)}
          >
            <Ask />
          </Sidebar>
        )}
      </div>
      {dragging && (
        <div className="drop-overlay">
          <div>松手导入到暂存区（.pptx / .pdf）</div>
        </div>
      )}
      <StatusBar />
    </div>
  );
}

/** Dockview's watermark. Deliberately blank -- see the comment at its use. */
function Empty() {
  return <div className="watermark-empty" />;
}

/** One sidebar's open/closed state and width, remembered between sessions. */
function useSidebar(side: "left" | "right", initialWidth: number) {
  const [open, setOpen] = useState(
    () => localStorage.getItem(`classhelper-${side}`) !== "closed",
  );
  const [width, setWidth] = useState(
    () => Number(localStorage.getItem(`classhelper-${side}-width`)) || initialWidth,
  );
  useEffect(() => {
    localStorage.setItem(`classhelper-${side}`, open ? "open" : "closed");
  }, [side, open]);
  useEffect(() => {
    localStorage.setItem(`classhelper-${side}-width`, String(width));
  }, [side, width]);
  return { open, setOpen, width, setWidth };
}

/**
 * A fixed-width region on one edge of the workspace.
 *
 * Outside the Dockview grid on purpose: inside it, each of these was a group
 * like any other and grew to absorb whatever space its neighbours gave back --
 * close the ask panel and the file tree took half the window. A sidebar keeps
 * the width you gave it no matter what happens beside it.
 */
function Sidebar({
  side,
  views,
  active,
  width,
  wide,
  onActivate,
  onWidth,
  onClose,
  children,
}: {
  side: "left" | "right";
  views: readonly { id: string; title: string }[];
  active: string;
  width: number;
  /** This view is something you look at rather than scan; let it get bigger. */
  wide?: boolean;
  onActivate: (id: string) => void;
  onWidth: (width: number) => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const [dragging, setDragging] = useState(false);
  const max = wide ? SIDEBAR_MAX_WIDE : SIDEBAR_MAX;

  useEffect(() => {
    if (!dragging) return;
    const move = (event: MouseEvent) => {
      const raw = side === "left" ? event.clientX : window.innerWidth - event.clientX;
      // Never wider than the window can spare: a sidebar that squeezes the
      // reading column to nothing is the layout bug this file exists to avoid.
      const ceiling = Math.min(max, window.innerWidth - READER_MIN_WIDTH - 60);
      onWidth(Math.min(ceiling, Math.max(SIDEBAR_MIN, raw)));
    };
    const stop = () => setDragging(false);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
    // The pointer regularly leaves the 4px handle mid-drag; a body-wide cursor
    // and a no-select guard keep it feeling like one continuous motion.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [dragging, side, onWidth, max]);

  const resizer = (
    <div
      className={`sidebar-resizer ${side}${dragging ? " dragging" : ""}`}
      onMouseDown={() => setDragging(true)}
      role="separator"
      aria-orientation="vertical"
    />
  );

  return (
    <>
      {side === "right" && resizer}
      <aside className={`sidebar ${side}`} style={{ width }}>
        <div className="sidebar-tabs">
          {views.map((view) => (
            <button
              key={view.id}
              className={view.id === active ? "on" : ""}
              onClick={() => onActivate(view.id)}
            >
              {view.title}
            </button>
          ))}
          <span className="spacer" />
          <button className="sidebar-close" onClick={onClose} title="收起">
            ✕
          </button>
        </div>
        <div className="sidebar-body">{children}</div>
      </aside>
      {side === "left" && resizer}
    </>
  );
}

function TopBar({
  onShowBoard,
  onShowSettings,
  onResetLayout,
  leftOpen,
  rightOpen,
  onToggleLeft,
  onToggleRight,
}: {
  onShowBoard: () => void;
  onShowSettings: () => void;
  onResetLayout: () => void;
  leftOpen: boolean;
  rightOpen: boolean;
  onToggleLeft: () => void;
  onToggleRight: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuOpen]);

  return (
    <header className="topbar">
      <span className="brand">ClassHelper</span>
      {/* Importing, filing and terminology all live on the board, so this is
          the way in rather than a bare file dialog. */}
      <button className="link strong" onClick={onShowBoard}>
        课板
      </button>
      <button className="link" onClick={onShowSettings}>
        设置
      </button>

      <div className="menu-anchor">
        <button
          className="link"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen(!menuOpen);
          }}
        >
          视图 ▾
        </button>
        {menuOpen && (
          <div className="menu" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => {
                onToggleLeft();
                setMenuOpen(false);
              }}
            >
              {leftOpen ? "隐藏" : "显示"}左侧边栏
            </button>
            <button
              onClick={() => {
                onToggleRight();
                setMenuOpen(false);
              }}
            >
              {rightOpen ? "隐藏" : "显示"}提问面板
            </button>
            <div className="menu-rule" />
            <button
              onClick={() => {
                onShowBoard();
                setMenuOpen(false);
              }}
            >
              显示「课板」
            </button>
            <div className="menu-rule" />
            <button
              onClick={() => {
                onResetLayout();
                setMenuOpen(false);
              }}
            >
              重置布局
            </button>
          </div>
        )}
      </div>

      <span className="spacer" />
    </header>
  );
}

function StatusBar() {
  const { active, decks, error, dismissError, busy } = useStore();
  // Every provider call the session has made. Handed to the cost display as
  // its cue to refetch: it moves while a deck translates and stops when it
  // stops, which is exactly when the number can have changed.
  const activity = active ? active.progress.calls : 0;

  if (error) {
    return (
      <footer className="statusbar">
        <span className="status-error">{error.split("\n")[0]}</span>
        {error.includes("\n") && (
          <span className="muted">（还有 {error.split("\n").length - 1} 条）</span>
        )}
        <span className="spacer" />
        <button className="link" onClick={dismissError}>
          知道了
        </button>
      </footer>
    );
  }

  if (!active) {
    return (
      <footer className="statusbar">
        {busy && <span>正在打开…</span>}
        <span className="spacer" />
        <Spending activity={0} />
      </footer>
    );
  }

  const { progress, deck } = active;
  const tokens = progress.prompt_tokens + progress.completion_tokens;

  return (
    <footer className="statusbar">
      {progress.done < progress.total ? (
        <span>翻译中 {progress.done}/{progress.total} 页</span>
      ) : (
        <span>已翻译 {progress.total}/{progress.total} 页</span>
      )}
      {decks.length > 1 && (
        <span className="muted">共打开 {decks.length} 份</span>
      )}
      <span className="spacer" />
      {progress.calls > 0 && (
        <span className="muted">
          本次 {progress.calls} 次调用 · {tokens.toLocaleString()} tokens
        </span>
      )}
      <span className="muted">{deck.target_lang}</span>
      <Spending activity={activity} />
    </footer>
  );
}
