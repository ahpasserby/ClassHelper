/**
 * The file explorer: the folder tree, and the inbox beneath it.
 *
 * Built to behave like a file manager rather than a read-only tree, because
 * that is what it is for. Everything you would reach for is here: create a
 * folder inside the one you are looking at, rename in place, select several
 * files and move them together, right-click for the rest. Having to leave the
 * sidebar to make a folder was the thing that made the earlier version feel
 * like a diagram of a file manager instead of one.
 *
 * Rows are rendered from a flat list rather than by recursive components. A
 * tree of components makes range-selection across nesting levels awkward, and
 * shift-click over a flattened, ordered list is trivially correct.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { board as boardApi, type BoardFolder, type BoardItem } from "../api";
import { reveal } from "../platform";
import { useStore } from "../store";
import { BoardIcon, FileIcon, FolderIcon } from "../icons";

export const DRAG_ITEMS = "application/x-classhelper-items";

/** Named for whichever file manager this desktop actually has. */
const REVEAL_LABEL =
  navigator.platform.startsWith("Mac")
    ? "在 Finder 中显示"
    : navigator.platform.startsWith("Win")
      ? "在文件资源管理器中显示"
      : "在文件管理器中显示";
export const DRAG_FOLDER = "application/x-classhelper-folder";

export function allow(event: React.DragEvent) {
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
}

export function flatten(folders: BoardFolder[]): BoardFolder[] {
  return folders.flatMap((f) => [f, ...flatten(f.children)]);
}

/** Ids carried by a drag: the whole selection when the dragged file is in it. */
export function draggedItems(event: React.DragEvent): string[] {
  const raw = event.dataTransfer.getData(DRAG_ITEMS);
  if (!raw) return [];
  try {
    return JSON.parse(raw) as string[];
  } catch {
    return [];
  }
}

type Row =
  | { kind: "folder"; id: string; depth: number; folder: BoardFolder }
  | { kind: "file"; id: string; depth: number; item: BoardItem };

/** An in-place text field: either a new folder, or a rename. */
interface Editing {
  mode: "new" | "rename";
  target: string | null; // new: parent folder (null = top level); rename: the id
  isFolder: boolean;
  value: string;
}

export function Explorer() {
  const store = useStore();
  const { board } = store;
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; row: Row | null } | null>(null);
  const [dropping, setDropping] = useState(false);

  // Open two levels to start with: semester, then course, so the folders
  // inside a course are visible and can be dropped onto. Everything below that
  // stays shut -- a term's worth of decks expanded at once is a wall of names,
  // and a collapsed tree hides the courses, which is the other way to get it
  // wrong.
  useEffect(() => {
    if (!board) return;
    setExpanded((current) =>
      current.size > 0 ? current : new Set(idsToDepth(board.folders, 2)),
    );
  }, [board]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("contextmenu", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
    };
  }, [menu]);

  const rows = useMemo(() => (board ? buildRows(board.folders, expanded) : []), [
    board,
    expanded,
  ]);

  const toggle = useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const run = store.runBoard;


  /** Where a new folder should go: inside whatever is selected in the tree. */
  const targetFolder = store.boardFolder;

  const beginNewFolder = (parent: string | null) => {
    if (parent) setExpanded((e) => new Set(e).add(parent));
    setEditing({ mode: "new", target: parent, isFolder: true, value: "" });
  };

  const commitEdit = async (value: string) => {
    const edit = editing;
    setEditing(null);
    const name = value.trim();
    if (!edit || !name) return;

    if (edit.mode === "new") {
      // Expand what was just created, so the next thing you do -- drop a deck
      // into it, or add a sub-folder -- has somewhere visible to land.
      const created = await boardApi.createFolder(name, edit.target);
      store.setBoard(created.board);
      setExpanded((e) => new Set(e).add(created.id));
      store.setBoardFolder(created.id);
      return;
    }
    if (edit.isFolder) {
      await run(() => boardApi.rename(edit.target!, name));
    } else {
      await run(() => boardApi.rename(edit.target!, name));
    }
  };

  /** Click / cmd-click / shift-click, over the flattened row order. */
  const selectFile = (id: string, event: React.MouseEvent) => {
    const fileIds = [...rows.filter((r) => r.kind === "file").map((r) => r.id),
                     ...(board?.inbox ?? []).map((i) => i.id)];
    setSelected((current) => {
      if (event.metaKey || event.ctrlKey) {
        const next = new Set(current);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }
      if (event.shiftKey && anchor) {
        const from = fileIds.indexOf(anchor);
        const to = fileIds.indexOf(id);
        if (from !== -1 && to !== -1) {
          const [lo, hi] = from < to ? [from, to] : [to, from];
          return new Set(fileIds.slice(lo, hi + 1));
        }
      }
      return new Set([id]);
    });
    if (!event.shiftKey) setAnchor(id);
  };

  /** Dragging a selected file drags the whole selection with it. */
  const startItemDrag = (id: string) => (event: React.DragEvent) => {
    const ids = selected.has(id) ? [...selected] : [id];
    if (!selected.has(id)) {
      setSelected(new Set([id]));
      setAnchor(id);
    }
    event.dataTransfer.setData(DRAG_ITEMS, JSON.stringify(ids));
    event.dataTransfer.effectAllowed = "move";
    event.stopPropagation();
  };

  const dropOn = (folderId: string | null) => async (event: React.DragEvent) => {
    event.preventDefault();
    event.stopPropagation();
    const ids = draggedItems(event);
    if (ids.length) {
      setSelected(new Set());
      return run(() => boardApi.move(ids, folderId));
    }
    const folder = event.dataTransfer.getData(DRAG_FOLDER);
    if (folder && folder !== folderId) {
      return run(() => boardApi.move([folder], folderId));
    }
  };

  const removeFolder = (folder: BoardFolder) => {
    if (
      window.confirm(
        `删除「${folder.name}」？里面的课件会回到暂存区。`,
      )
    ) {
      void run(() => boardApi.deleteFolder(folder.id));
      if (store.boardFolder === folder.id) store.setBoardFolder(folder.parent);
    }
  };

  if (!board) return <div className="pad muted">读取课板…</div>;

  return (
    <div className="explorer" onClick={() => setSelected(new Set())}>
      <div
        className="explorer-tree"
        onDragOver={allow}
        onDrop={dropOn(null)}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenu({ x: e.clientX, y: e.clientY, row: null });
        }}
      >
        <div className="explorer-head">
          <span>课板</span>
          <span className="head-actions">
            <button
              className="icon-button"
              title={
                targetFolder
                  ? "在选中的文件夹里新建一个"
                  : "新建文件夹"
              }
              onClick={(e) => {
                e.stopPropagation();
                beginNewFolder(targetFolder);
              }}
            >
              新建文件夹
            </button>
            <button
              className="icon-button"
              title="全部折叠"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(new Set());
              }}
            >
              折叠
            </button>
          </span>
        </div>

        <div
          className={`tree-row${store.boardFolder === null ? " current" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            store.setBoardFolder(null);
            setSelected(new Set());
          }}
        >
          <span className="tree-indent" />
          <span className="tree-icon"><BoardIcon /></span>
          <span className="tree-name">全部</span>
        </div>

        {editing?.mode === "new" && editing.target === null && (
          <EditRow depth={0} value={editing.value} onCommit={commitEdit}
                   onCancel={() => setEditing(null)} placeholder="学期名称" />
        )}

        {rows.map((row) =>
          row.kind === "folder" ? (
            <FolderRow
              key={row.id}
              row={row}
              open={expanded.has(row.id)}
              current={store.boardFolder === row.id}
              editing={editing}
              onToggle={toggle}
              onSelect={(id) => {
                store.setBoardFolder(id);
                setSelected(new Set());
              }}
              onDrop={dropOn}
              onMenu={(x, y) => setMenu({ x, y, row })}
              onCommit={commitEdit}
              onCancelEdit={() => setEditing(null)}
              afterChildren={
                editing?.mode === "new" && editing.target === row.id ? (
                  <EditRow depth={row.depth + 1} value={editing.value}
                           onCommit={commitEdit} onCancel={() => setEditing(null)}
                           placeholder="课程名称" />
                ) : null
              }
            />
          ) : (
            <FileRow
              key={row.id}
              row={row}
              selected={selected.has(row.id)}
              editing={editing}
              onClick={(e) => selectFile(row.id, e)}
              onDragStart={startItemDrag(row.id)}
              onOpen={() => void store.openItem(row.item)}
              onMenu={(x, y) => {
                if (!selected.has(row.id)) setSelected(new Set([row.id]));
                setMenu({ x, y, row });
              }}
              onCommit={commitEdit}
              onCancelEdit={() => setEditing(null)}
            />
          ),
        )}

        {board.folders.length === 0 && !editing && (
          <p className="tree-empty">
            还没有文件夹，点上面「新建文件夹」。
          </p>
        )}
      </div>

      <section
        className={`explorer-inbox${dropping ? " dropping" : ""}`}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("Files")) {
            allow(e);
            setDropping(true);
          }
        }}
        onDragLeave={() => setDropping(false)}
        onDrop={(e) => {
          setDropping(false);
          const files = Array.from(e.dataTransfer.files);
          if (files.length) {
            e.preventDefault();
            e.stopPropagation();
            void store.importFiles(files);
            return;
          }
          // A deck dragged back down here is being unfiled.
          void dropOn(null)(e);
        }}
      >
        <div className="explorer-head">
          <span>暂存区{board.inbox.length > 0 ? ` (${board.inbox.length})` : ""}</span>
          <button
            className="icon-button"
            onClick={(e) => {
              e.stopPropagation();
              void store.importFromDialog();
            }}
          >
            导入…
          </button>
        </div>

        {board.inbox.length === 0 ? (
          <p className="tree-empty">
            拖课件到这里导入，归档前会停在这里。
          </p>
        ) : (
          <div className="inbox-list">
            {board.inbox.map((item) => (
              <div
                key={item.id}
                className={`inbox-row${item.missing ? " missing" : ""}${
                  selected.has(item.id) ? " selected" : ""
                }${item.readable ? "" : " unreadable"}`}
                draggable={!item.missing}
                onClick={(e) => {
                  e.stopPropagation();
                  selectFile(item.id, e);
                }}
                onDragStart={startItemDrag(item.id)}
                onDoubleClick={() => void store.openItem(item)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (!selected.has(item.id)) setSelected(new Set([item.id]));
                  setMenu({
                    x: e.clientX,
                    y: e.clientY,
                    row: { kind: "file", id: item.id, depth: 0, item },
                  });
                }}
                title={
                  item.missing
                    ? item.path
                    : item.readable
                      ? "拖到上面归档 · 双击打开"
                      : "课程资料，打不开，可在访达里查看"
                }
              >
                <span className="file-ext">{item.format}</span>
                <span className="inbox-name">{item.name}</span>
              </div>
            ))}
          </div>
        )}
        {selected.size > 1 && (
          <p className="tree-empty selection-count">
            已选中 {selected.size} 份 · 一起拖到课程里
          </p>
        )}
      </section>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          row={menu.row}
          selectedCount={selected.size}
          onClose={() => setMenu(null)}
          onNewFolder={(parent) => beginNewFolder(parent)}
          onRename={(id, isFolder, value) =>
            setEditing({ mode: "rename", target: id, isFolder, value })
          }
          onDeleteFolder={removeFolder}
          onOpen={(item) => void store.openItem(item)}
          onReveal={(path) => {
            const absolute = [board.root, path].filter(Boolean).join("/");
            void reveal(absolute).catch(() => {});
          }}
          onUnfile={() => {
            const ids = [...selected];
            setSelected(new Set());
            void run(() => boardApi.move(ids, null));
          }}
          onDelete={() => {
            const ids = [...selected];
            setSelected(new Set());
            // Sequential, not parallel: each call returns the whole board, and
            // racing them means the last reply to land wins regardless of order.
            void (async () => {
              for (const id of ids) await run(() => boardApi.deleteItem(id));
            })();
          }}
        />
      )}
    </div>
  );
}

/** Folder ids down to `levels` deep, counting the outermost as level one. */
function idsToDepth(folders: BoardFolder[], levels: number): string[] {
  if (levels <= 0) return [];
  return folders.flatMap((folder) => [
    folder.id,
    ...idsToDepth(folder.children, levels - 1),
  ]);
}

function buildRows(folders: BoardFolder[], expanded: Set<string>, depth = 0): Row[] {
  return folders.flatMap((folder) => {
    const rows: Row[] = [{ kind: "folder", id: folder.id, depth, folder }];
    if (expanded.has(folder.id)) {
      rows.push(...buildRows(folder.children, expanded, depth + 1));
      rows.push(
        ...folder.items.map<Row>((item) => ({
          kind: "file",
          id: item.id,
          depth: depth + 1,
          item,
        })),
      );
    }
    return rows;
  });
}

function FolderRow({
  row,
  open,
  current,
  editing,
  onToggle,
  onSelect,
  onDrop,
  onMenu,
  onCommit,
  onCancelEdit,
  afterChildren,
}: {
  row: Extract<Row, { kind: "folder" }>;
  open: boolean;
  current: boolean;
  editing: Editing | null;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  onDrop: (id: string | null) => (e: React.DragEvent) => void;
  onMenu: (x: number, y: number) => void;
  onCommit: (value: string) => void;
  onCancelEdit: () => void;
  afterChildren: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const spring = useRef<number | null>(null);
  const { folder, depth } = row;
  const hasChildren = folder.children.length > 0 || folder.items.length > 0;

  // Spring-loaded folders: hovering over a collapsed one mid-drag opens it, so
  // a deck can be filed into a nested course without dropping it somewhere else
  // first to free a hand.
  const startSpring = () => {
    if (open || !hasChildren || spring.current !== null) return;
    spring.current = window.setTimeout(() => {
      spring.current = null;
      onToggle(folder.id);
    }, 600);
  };
  const cancelSpring = () => {
    if (spring.current !== null) {
      window.clearTimeout(spring.current);
      spring.current = null;
    }
  };
  useEffect(() => cancelSpring, []);

  if (editing?.mode === "rename" && editing.target === folder.id) {
    return (
      <EditRow depth={depth} value={folder.name} onCommit={onCommit}
               onCancel={onCancelEdit} placeholder="名称" />
    );
  }

  return (
    <>
      <div
        className={`tree-row${current ? " current" : ""}${over ? " drop-target" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(DRAG_FOLDER, folder.id);
          e.stopPropagation();
        }}
        onDragOver={(e) => {
          allow(e);
          setOver(true);
          startSpring();
        }}
        onDragLeave={() => {
          setOver(false);
          cancelSpring();
        }}
        onDrop={(e) => {
          setOver(false);
          cancelSpring();
          void onDrop(folder.id)(e);
        }}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(folder.id);
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onSelect(folder.id);
          onMenu(e.clientX, e.clientY);
        }}
      >
        <button
          className="tree-chevron"
          onClick={(e) => {
            e.stopPropagation();
            onToggle(folder.id);
          }}
          aria-label={open ? "折叠" : "展开"}
        >
          {hasChildren ? (open ? "⌄" : "›") : ""}
        </button>
        <span className="tree-icon"><FolderIcon open={open && hasChildren} /></span>
        <span className="tree-name">{folder.name}</span>
        {folder.items.length > 0 && (
          <span className="tree-count">{folder.items.length}</span>
        )}
      </div>
      {afterChildren}
    </>
  );
}

function FileRow({
  row,
  selected,
  editing,
  onClick,
  onDragStart,
  onOpen,
  onMenu,
  onCommit,
  onCancelEdit,
}: {
  row: Extract<Row, { kind: "file" }>;
  selected: boolean;
  editing: Editing | null;
  onClick: (e: React.MouseEvent) => void;
  onDragStart: (e: React.DragEvent) => void;
  onOpen: () => void;
  onMenu: (x: number, y: number) => void;
  onCommit: (value: string) => void;
  onCancelEdit: () => void;
}) {
  const { item, depth } = row;

  if (editing?.mode === "rename" && editing.target === item.id) {
    return (
      <EditRow depth={depth} value={item.name} onCommit={onCommit}
               onCancel={onCancelEdit} placeholder="名称" icon={<FileIcon />} />
    );
  }

  return (
    <div
      className={`tree-row tree-file${selected ? " selected" : ""}${
        item.missing ? " missing" : ""
      }${item.readable ? "" : " unreadable"}`}
      style={{ paddingLeft: 22 + depth * 14 }}
      draggable={!item.missing}
      onClick={(e) => {
        e.stopPropagation();
        onClick(e);
      }}
      onDragStart={onDragStart}
      onDoubleClick={onOpen}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onMenu(e.clientX, e.clientY);
      }}
      title={
        item.missing
          ? "文件已不在原位置"
          : item.readable
            ? "双击打开"
            : "课程资料，打不开，可在访达里查看"
      }
    >
      <span className="tree-icon"><FileIcon /></span>
      <span className="tree-name">{item.name}</span>
      {/* Names on the board are shown without their extension, so this is the
          only place the format appears -- and on a file the reader cannot open
          it is also the answer to "why not". */}
      <span className="file-ext">{item.format}</span>
    </div>
  );
}

/** The in-place text field used for both creating and renaming. */
function EditRow({
  depth,
  value,
  onCommit,
  onCancel,
  placeholder,
  icon = <FolderIcon />,
}: {
  depth: number;
  value: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
  placeholder: string;
  icon?: React.ReactNode;
}) {
  const [draft, setDraft] = useState(value);
  return (
    <div className="tree-row editing" style={{ paddingLeft: 8 + depth * 14 }}>
      <span className="tree-indent" />
      <span className="tree-icon">{icon}</span>
      <input
        className="tree-input"
        autoFocus
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        // Blur commits, the way an editor's explorer does: clicking away from a
        // half-typed name should keep it, not silently discard the typing.
        onBlur={() => onCommit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onCommit(draft);
          if (e.key === "Escape") onCancel();
        }}
      />
    </div>
  );
}

function ContextMenu({
  x,
  y,
  row,
  selectedCount,
  onClose,
  onNewFolder,
  onRename,
  onDeleteFolder,
  onOpen,
  onReveal,
  onUnfile,
  onDelete,
}: {
  x: number;
  y: number;
  row: Row | null;
  selectedCount: number;
  onClose: () => void;
  onNewFolder: (parent: string | null) => void;
  onRename: (id: string, isFolder: boolean, value: string) => void;
  onDeleteFolder: (folder: BoardFolder) => void;
  onOpen: (item: BoardItem) => void;
  onReveal: (path: string) => void;
  onUnfile: () => void;
  onDelete: () => void;
}) {
  const many = selectedCount > 1;

  /**
   * Every entry closes the menu after acting.
   *
   * The container stops click propagation so that choosing an entry is not also
   * a click on the row underneath -- which means the window-level listener that
   * dismisses the menu never fires, and without this the menu stayed on screen
   * swallowing every subsequent click.
   */
  const Item = ({
    children,
    onSelect,
    danger,
  }: {
    children: React.ReactNode;
    onSelect: () => void;
    danger?: boolean;
  }) => (
    <button
      className={danger ? "danger" : undefined}
      onClick={() => {
        onClose();
        onSelect();
      }}
    >
      {children}
    </button>
  );

  return (
    <div className="menu context" style={{ left: x, top: y }}
         onClick={(e) => e.stopPropagation()}>
      {row?.kind === "folder" && (
        <>
          <Item onSelect={() => onNewFolder(row.folder.id)}>在此新建文件夹</Item>
          <Item onSelect={() => onRename(row.folder.id, true, row.folder.name)}>
            重命名
          </Item>
          <Item onSelect={() => onReveal(row.folder.id)}>{REVEAL_LABEL}</Item>
          <div className="menu-rule" />
          <Item danger onSelect={() => onDeleteFolder(row.folder)}>
            删除文件夹
          </Item>
        </>
      )}
      {row?.kind === "file" && (
        <>
          {!many && (
            <>
              <Item onSelect={() => onOpen(row.item)}>打开</Item>
              <Item onSelect={() => onRename(row.item.id, false, row.item.name)}>
                重命名
              </Item>
              <Item onSelect={() => onReveal(row.item.id)}>{REVEAL_LABEL}</Item>
              <div className="menu-rule" />
            </>
          )}
          <Item onSelect={onUnfile}>
            移回暂存区{many ? `（${selectedCount} 份）` : ""}
          </Item>
          {/* The library owns these files now, so this really deletes -- into
              the library's .trash, where a mis-click is recoverable. */}
          <Item danger onSelect={onDelete}>
            删除{many ? `（${selectedCount} 份）` : ""}
          </Item>
        </>
      )}
      {!row && (
        <>
          <Item onSelect={() => onNewFolder(null)}>新建学期</Item>
          <Item onSelect={() => onReveal("")}>{REVEAL_LABEL}</Item>
        </>
      )}
    </div>
  );
}
