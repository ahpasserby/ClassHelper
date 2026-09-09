/**
 * The board page: what is in the folder you are looking at, and nothing else.
 *
 * One level, like any file manager. Rendering children recursively meant the
 * page showed the same deck twice -- once under its own course and once under
 * the semester above it -- and padded the list with "and 1 more level: Lec"
 * lines that said less than the sidebar already shows. Navigating into a
 * folder is a click, and that is the right way to see what is inside it.
 *
 * Reading, not editing: creating, renaming, moving and deleting live in the
 * explorer sidebar, which is on screen at the same time.
 */

import { useState } from "react";
import { board as boardApi, type BoardFolder, type BoardItem } from "../api";
import { useStore } from "../store";
import { allow, draggedItems, DRAG_ITEMS, flatten } from "./Explorer";
import { ScopedGlossary } from "./ScopedGlossary";

export function Board() {
  const store = useStore();
  const { board } = store;
  if (!board) return <div className="pad muted">读取课板…</div>;

  const all = flatten(board.folders);
  const folder = store.boardFolder
    ? all.find((f) => f.id === store.boardFolder) ?? null
    : null;
  const folders = folder ? folder.children : board.folders;
  const items = folder ? folder.items : [];

  const dropOn = (id: string) => (event: React.DragEvent) => {
    event.preventDefault();
    const ids = draggedItems(event);
    if (ids.length) void store.runBoard(() => boardApi.move(ids, id));
  };

  return (
    <div className="board-main">
      <Breadcrumb folders={all} current={folder} onSelect={store.setBoardFolder} />

      {folders.length === 0 && items.length === 0 ? (
        <div className="board-empty">
          <p className="muted">
            {folder
              ? `「${folder.name}」还是空的。把左侧暂存区的课件拖到它上面。`
              : "还没有文件夹，在左侧新建一个。"}
          </p>
        </div>
      ) : (
        <table className="item-table listing">
          <tbody>
            {folders.map((child) => (
              <FolderRow
                key={child.id}
                folder={child}
                onOpen={() => store.setBoardFolder(child.id)}
                onDrop={dropOn(child.id)}
              />
            ))}
            {items.map((item) => (
              <DeckRow key={item.id} item={item} />
            ))}
          </tbody>
        </table>
      )}

      <ScopedGlossary scope={folder?.id ?? null} name={folder?.name ?? "全局"} />
    </div>
  );
}

function FolderRow({
  folder,
  onOpen,
  onDrop,
}: {
  folder: BoardFolder;
  onOpen: () => void;
  onDrop: (e: React.DragEvent) => void;
}) {
  const [over, setOver] = useState(false);
  const decks = countDecks(folder);

  return (
    <tr
      className={`folder-row${over ? " drop-target" : ""}`}
      onClick={onOpen}
      onDragOver={(e) => {
        allow(e);
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        setOver(false);
        onDrop(e);
      }}
      title="点开看里面"
    >
      <td className="item-format" />
      <td className="item-name">{folder.name}</td>
      <td className="item-meta muted">
        {/* The total counts decks further down too: the point of the number is
            "is there anything in here", which a direct-children-only count
            would answer wrongly for a semester. */}
        {decks > 0 ? `${decks} 份课件` : "空"}
        {folder.children.length > 0 ? ` · ${folder.children.length} 个下级` : ""}
      </td>
    </tr>
  );
}

function DeckRow({ item }: { item: BoardItem }) {
  const store = useStore();
  return (
    <tr
      draggable={!item.missing}
      onDragStart={(e) => e.dataTransfer.setData(DRAG_ITEMS, JSON.stringify([item.id]))}
      onDoubleClick={() => void store.openItem(item)}
      className={item.missing ? "missing" : undefined}
      title={item.missing ? item.path : "双击打开"}
    >
      <td className="item-format">{item.format}</td>
      <td className="item-name">
        {item.name}
        {item.missing && (
          <span className="badge" title={item.path}>
            文件已不在原位置
          </span>
        )}
      </td>
      <td className="item-meta" />
    </tr>
  );
}

function countDecks(folder: BoardFolder): number {
  return folder.items.length + folder.children.reduce((n, c) => n + countDecks(c), 0);
}

function Breadcrumb({
  folders,
  current,
  onSelect,
}: {
  folders: BoardFolder[];
  current: BoardFolder | null;
  onSelect: (id: string | null) => void;
}) {
  const trail: BoardFolder[] = [];
  let node = current;
  while (node) {
    trail.unshift(node);
    node = node.parent ? folders.find((f) => f.id === node!.parent) ?? null : null;
  }

  return (
    <header className="crumbs">
      <span className="muted">位置:</span>
      <button className="link" onClick={() => onSelect(null)}>
        课板
      </button>
      {trail.map((f) => (
        <span key={f.id}>
          <span className="crumb-sep">/</span>
          <button className="link" onClick={() => onSelect(f.id)}>
            {f.name}
          </button>
        </span>
      ))}
      <span className="spacer" />
    </header>
  );
}
