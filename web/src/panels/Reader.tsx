/**
 * The bilingual reading view for one deck.
 *
 * Not a reproduction of the slide. A slide is laid out to be glanced at from
 * the back of a lecture hall; this is laid out to be read closely, which is a
 * different job. Prose runs in one column with each translation directly under
 * its own sentence, and the diagram labels and figures that would fragment that
 * column are grouped separately beneath it.
 *
 * All pages render in one scroll rather than being paged through, so you can
 * look back at the previous slide without losing your place -- which, once the
 * lecturer has moved on, is most of the point.
 */

import { useEffect, useRef, useState } from "react";
import { api, type Block, type Page, type Sentence } from "../api";
import { useDeck, useStore } from "../store";
import { usePaneSync } from "../usePaneSync";
import { CopyMenu, useCopyMenu } from "./CopyMenu";
import { unitKey } from "../units";

export function Reader({ deckId }: { deckId: string }) {
  const store = useStore();
  const state = useDeck(deckId);
  const pageRefs = useRef<Map<number, HTMLElement>>(new Map());
  const { group, scroller, setSection, setContent, heights } = usePaneSync(
    deckId,
    "reader",
  );
  const containerRef = scroller;

  // Clicking a chapter moves both panes, not just this one: with the slides
  // open beside it, scrolling one and leaving the other behind is the bug.
  useEffect(() => {
    store.registerScroll(deckId, (index) => group.goTo(index));
  }, [store, deckId, group]);

  // Whichever page is highest on screen is the one being read, and therefore
  // the one the server should translate next.
  useEffect(() => {
    if (!state) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) {
          const index = Number((visible.target as HTMLElement).dataset.page);
          if (!Number.isNaN(index)) store.setVisiblePage(deckId, index);
        }
      },
      { root: containerRef.current, rootMargin: "-10% 0px -70% 0px" },
    );
    pageRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [deckId, state?.deck.pages.length]);

  if (!state) return <div className="pad muted">这份课件已经关闭了。</div>;
  const { deck } = state;

  return (
    <div className="reader" ref={containerRef}>
      {deck.warnings.length > 0 && (
        <div className="notice">
          {deck.warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
        </div>
      )}
      {deck.pages.map((page) => (
        <section
          key={page.index}
          className="page"
          data-page={page.index}
          // A minimum, never a fixed height: the slide pane and this one agree
          // on the taller of what each needs, so a page starts at the same
          // place in both and nothing is clipped to make that true.
          style={heights[page.index] ? { minHeight: heights[page.index] } : undefined}
          ref={(el) => {
            if (el) pageRefs.current.set(page.index, el);
            else pageRefs.current.delete(page.index);
            setSection(page.index)(el);
          }}
        >
          <div className="page-inner" ref={setContent(page.index)}>
            <PageView page={page} deckId={deck.id} showHidden={store.showHidden} />
          </div>
        </section>
      ))}
      <div className="reader-end">— 全文结束 —</div>
    </div>
  );
}

function PageView({
  page,
  deckId,
  showHidden,
}: {
  page: Page;
  deckId: string;
  showHidden: boolean;
}) {
  const flow = page.blocks.filter(
    (b) => b.kind === "title" || b.kind === "body" || b.kind === "code",
  );
  const figures = page.blocks.filter(
    (b) => b.kind === "caption" || b.kind === "fragment",
  );
  const hidden = page.blocks.filter((b) => b.kind === "chrome");

  return (
    <>
      <header className="page-head">
        <span className="page-number">{page.index + 1}</span>
        {page.title && <span className="page-title">{page.title}</span>}
      </header>

      {flow.map((block) => (
        <BlockView key={block.id} block={block} page={page.index} deckId={deckId} />
      ))}

      {(figures.length > 0 || page.images.length > 0) && (
        <div className="figures">
          <div className="figures-label">图示</div>
          <div className="figure-images">
            {page.images.map((image) => (
              <img
                key={image.id}
                src={api.imageUrl(deckId, image.id)}
                alt=""
                loading="lazy"
                decoding="async"
                /*
                 * Its shape on the slide, so the space is reserved before the
                 * bytes arrive. Without this a lazily loaded diagram grows the
                 * page under a scroll already in flight -- which is what made
                 * clicking a chapter land hundreds of pixels short of it.
                 */
                style={{
                  aspectRatio: String(
                    (image.box.w / Math.max(image.box.h, 0.001)) * page.aspect,
                  ),
                }}
              />
            ))}
          </div>
          <div className="figure-terms">
            {figures.map((block) => (
              <FigureTerm
                key={block.id}
                block={block}
                page={page.index}
                deckId={deckId}
              />
            ))}
          </div>
        </div>
      )}

      {showHidden && hidden.length > 0 && (
        <div className="hidden-blocks">
          <div className="figures-label">
            已折叠：判定为每页重复的导航条或页眉页脚
          </div>
          {hidden.map((block) => (
            <div key={block.id} className="hidden-block">
              <span>{block.text}</span>
              <span className="reason">{block.reason}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function BlockView({
  block,
  page,
  deckId,
}: {
  block: Block;
  page: number;
  deckId: string;
}) {
  if (block.kind === "code") {
    // Verbatim: an equation or a code listing. Shown as written, with no
    // translation underneath, because translating it would damage it.
    return <pre className="code">{block.text}</pre>;
  }
  // A bulleted line is shown as one: the source said it was a list, and a
  // slide's bullets run together into nonsense when flattened into prose.
  return (
    <div
      className={`block block-${block.kind}${block.bullet ? " bullet" : ""}`}
      style={block.bullet ? { marginLeft: block.level * 22 } : undefined}
    >
      {block.bullet && <span className="bullet-marker" aria-hidden />}
      <div className="block-body">
        {block.sentences.map((sentence, index) => (
          <SentenceView
            key={`${block.id}:${index}`}
            sentence={sentence}
            page={page}
            deckId={deckId}
            blockId={block.id}
            index={index}
            heading={block.kind === "title"}
          />
        ))}
      </div>
    </div>
  );
}

function SentenceView({
  sentence,
  page,
  deckId,
  blockId,
  index,
  heading,
}: {
  sentence: Sentence;
  page: number;
  deckId: string;
  blockId: string;
  index: number;
  heading: boolean;
}) {
  const store = useStore();
  const state = useDeck(deckId);
  const menu = useCopyMenu();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(sentence.translation ?? "");
  const key = unitKey(page, blockId, index);
  const keys = state?.selection.keys ?? [];
  const selected = keys.includes(key);
  // The actions belong under the sentence you clicked, not under all five.
  const soleSelection = selected && keys.length === 1;

  const commit = () => {
    setEditing(false);
    if (draft.trim() && draft !== sentence.translation) {
      void store.editTranslation(deckId, sentence, draft.trim());
    }
  };

  return (
    <div
      className={`sentence${selected ? " selected" : ""}`}
      onClick={(event) =>
        store.select(
          deckId,
          key,
          // The conventions every file list uses, because this is a list of
          // things you pick out of: hold cmd to add one, shift to sweep a run.
          event.metaKey || event.ctrlKey
            ? "toggle"
            : event.shiftKey
              ? "range"
              : "replace",
        )
      }
      onContextMenu={(event) => {
        event.preventDefault();
        // Right-clicking outside the selection moves it here first, the way it
        // does everywhere else -- otherwise "copy" copies something else.
        if (!selected) store.select(deckId, key, "replace");
        menu.open(event.clientX, event.clientY);
      }}
    >
      <div className={heading ? "source heading" : "source"}>{sentence.text}</div>

      {editing ? (
        <textarea
          className="target editing"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              commit();
            }
            if (e.key === "Escape") setEditing(false);
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : sentence.translation ? (
        <div
          className={`target${sentence.flagged ? " flagged" : ""}${
            sentence.edited ? " edited" : ""
          }`}
          title={
            sentence.edited
              ? "你修改过这一句"
              : sentence.flagged
                ? "这句译文可疑，建议核对"
                : "双击可以直接改"
          }
          onDoubleClick={(e) => {
            e.stopPropagation();
            setDraft(sentence.translation ?? "");
            setEditing(true);
          }}
        >
          {sentence.translation}
        </div>
      ) : (
        <div className="target pending">翻译中…</div>
      )}

      {menu.at && (
        <CopyMenu deckId={deckId} x={menu.at.x} y={menu.at.y} onClose={menu.close} />
      )}

      {soleSelection && (
        <div className="sentence-actions" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => void store.retranslate(deckId, sentence)}>
            重新翻译
          </button>
          <button
            onClick={() => {
              setDraft(sentence.translation ?? "");
              setEditing(true);
            }}
          >
            手动修改
          </button>
          <span className="hint">
            在右侧面板直接提问 · ⌘ 点可多选，右键复制
          </span>
        </div>
      )}
    </div>
  );
}

function FigureTerm({
  block,
  page,
  deckId,
}: {
  block: Block;
  page: number;
  deckId: string;
}) {
  const store = useStore();
  const state = useDeck(deckId);
  const menu = useCopyMenu();
  const sentence = block.sentences[0];
  if (!sentence) return null;
  const key = unitKey(page, block.id, 0);
  const selected = state?.selection.keys.includes(key) ?? false;
  return (
    <div
      className={`figure-term${selected ? " selected" : ""}`}
      onClick={(event) =>
        store.select(
          deckId,
          key,
          event.metaKey || event.ctrlKey
            ? "toggle"
            : event.shiftKey
              ? "range"
              : "replace",
        )
      }
      onContextMenu={(event) => {
        event.preventDefault();
        if (!selected) store.select(deckId, key, "replace");
        menu.open(event.clientX, event.clientY);
      }}
      title={block.reason}
    >
      <span className="source">{sentence.text}</span>
      <span className="target">{sentence.translation ?? "…"}</span>
      {menu.at && (
        <CopyMenu deckId={deckId} x={menu.at.x} y={menu.at.y} onClose={menu.close} />
      )}
    </div>
  );
}
