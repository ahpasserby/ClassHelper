/**
 * Right-click on the selected sentences.
 *
 * Three ways to copy, because three are genuinely wanted and no default serves
 * all of them: the English to paste into a search, the Chinese to paste into
 * notes, and both together to keep the pairing while revising.
 */

import { useEffect, useState } from "react";
import { useDeck, useStore } from "../store";
import { asText, selected as selectedUnits, type CopyMode } from "../units";

/** Where the menu is, if it is open. */
export function useCopyMenu() {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  return {
    at,
    open: (x: number, y: number) => setAt({ x, y }),
    close: () => setAt(null),
  };
}

/**
 * Put text on the clipboard.
 *
 * The async API is the right one and works here -- a page served from
 * 127.0.0.1 counts as a secure context -- but it also fails silently when the
 * document has lost focus, which a context menu can cause. The old synchronous
 * path is the fallback for exactly that case.
 */
async function copy(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  }
}

export function CopyMenu({
  deckId,
  x,
  y,
  onClose,
}: {
  deckId: string;
  x: number;
  y: number;
  onClose: () => void;
}) {
  const store = useStore();
  const state = useDeck(deckId);
  const [copied, setCopied] = useState<CopyMode | null>(null);

  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    // Registered a tick late, deliberately. React treats a right-click as
    // discrete input and can flush this effect while that very event is still
    // on its way to window -- so listening immediately means the click that
    // opened the menu is also the one that closes it, and nothing appears.
    const armed = window.setTimeout(() => {
      window.addEventListener("click", close);
      window.addEventListener("contextmenu", close);
      window.addEventListener("keydown", onKey);
    }, 0);
    return () => {
      window.clearTimeout(armed);
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  if (!state) return null;
  const chosen = selectedUnits(state.deck, state.selection.keys);
  if (!chosen.length) return null;

  const translated = chosen.filter((u) => u.sentence.translation).length;
  const many = chosen.length > 1;

  const run = async (mode: CopyMode) => {
    const ok = await copy(asText(chosen, mode));
    if (!ok) return;
    setCopied(mode);
    // Left open for a moment so the tick is seen; closing instantly reads as
    // nothing having happened.
    window.setTimeout(onClose, 450);
  };

  const Item = ({ mode, children }: { mode: CopyMode; children: React.ReactNode }) => (
    <button
      disabled={mode !== "source" && translated === 0}
      onClick={(e) => {
        e.stopPropagation();
        void run(mode);
      }}
    >
      {children}
      {copied === mode && <span className="tick">✓</span>}
    </button>
  );

  return (
    <div
      className="menu context copy-menu"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.stopPropagation()}
    >
      <div className="menu-head">
        已选 {chosen.length} 句
        {many && translated < chosen.length && (
          <span className="muted"> · {chosen.length - translated} 句还没译完</span>
        )}
      </div>
      <Item mode="source">复制原文</Item>
      <Item mode="target">复制译文</Item>
      <Item mode="both">复制原文 + 译文</Item>
      <div className="menu-rule" />
      <button
        onClick={(e) => {
          e.stopPropagation();
          store.clearSelection(deckId);
          onClose();
        }}
      >
        取消选择
      </button>
    </div>
  );
}
