/**
 * The slides themselves, scrolling in step with the translation beside them.
 *
 * The reading view answers "what does this say"; this answers "where on the
 * slide is it". Both questions come up in the same minute of a lecture, which
 * is why they are two panes rather than two modes.
 *
 * What gets shown depends on what can honestly be shown. A PDF is already a
 * picture of itself. A .pptx is not, and rendering one properly means being
 * PowerPoint -- so if LibreOffice is installed the server borrows it, and if
 * not the page is drawn here from the geometry the parser extracted: every
 * text box and picture at its real position and size. That drawing is labelled
 * as what it is. Passing an approximation off as the file would be worse than
 * not showing one.
 */

import { useEffect, useLayoutEffect, useState } from "react";
import { api, type Block, type Page, type SourceStatus } from "../api";
import { useDeck, useStore } from "../store";
import { usePaneSync } from "../usePaneSync";

/** Rasterised at twice the pane width, so the image is sharp on a retina panel. */
const RENDER_SCALE = 2;
const MAX_RENDER_WIDTH = 2400;

export function SourceSlides() {
  const store = useStore();
  const deckId = store.activeId;
  if (!deckId) return <div className="pad muted">还没有打开课件</div>;
  return <SourceFor deckId={deckId} key={deckId} />;
}

function SourceFor({ deckId }: { deckId: string }) {
  const state = useDeck(deckId);
  const { scroller, setSection, setContent, heights } = usePaneSync(deckId, "source");
  const [width, setWidth] = useState(0);
  const [status, setStatus] = useState<SourceStatus | null>(
    state?.deck.source ?? null,
  );

  // A .pptx conversion runs on a thread, so the answer can change from
  // "building" to "exact" a few seconds after the deck opens. Poll only while
  // it is actually building; once settled, it stays settled.
  useEffect(() => {
    if (!status || status.mode !== "building") return;
    const timer = window.setInterval(async () => {
      try {
        const fresh = await api.sourceStatus(deckId);
        setStatus(fresh);
      } catch {
        /* the deck was closed */
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [deckId, status?.mode]);

  // The rendered image is requested at a width, so the pane's width is part of
  // the request. Rounded into steps to avoid re-fetching on every pixel of a
  // sidebar drag.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const raw = el.clientWidth;
      setWidth(raw > 0 ? Math.ceil(raw / 100) * 100 : 0);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [scroller]);

  if (!state) return <div className="pad muted">这份课件已经关闭了。</div>;
  const { deck } = state;
  const exact = status?.mode === "exact";

  return (
    <div className="source-pane" ref={scroller}>
      {status && status.mode !== "exact" && status.detail && (
        <div className="source-note">{status.detail}</div>
      )}

      {deck.pages.map((page) => (
        <section
          key={page.index}
          className={`source-page${
            page.index === state.currentPage ? " current" : ""
          }`}
          ref={setSection(page.index)}
          style={heights[page.index] ? { minHeight: heights[page.index] } : undefined}
        >
          <div className="source-inner" ref={setContent(page.index)}>
            <div className="source-index">{page.index + 1}</div>
            <div className="source-frame" style={{ aspectRatio: String(page.aspect) }}>
              {exact && width > 0 ? (
                <img
                  src={api.sourceUrl(
                    deck.id,
                    page.index,
                    Math.min(MAX_RENDER_WIDTH, width * RENDER_SCALE),
                  )}
                  alt={`第 ${page.index + 1} 页`}
                  loading="lazy"
                  // Decoded off the main thread: a page of a text-heavy PDF is
                  // a megapixel of PNG, and decoding it inline is a hitch in
                  // the middle of a scroll.
                  decoding="async"
                  draggable={false}
                />
              ) : (
                <Approximation page={page} deckId={deck.id} />
              )}
            </div>
          </div>
        </section>
      ))}
      <div className="source-end">— 到底了 —</div>
    </div>
  );
}

/**
 * The page drawn from what the parser found.
 *
 * Positions and sizes are percentages of the page, and type is sized in `cqh`
 * -- a hundredth of the frame's height -- so a point size taken from the file
 * stays proportionally right at any pane width, with no measuring in
 * JavaScript and nothing to keep in sync on a resize.
 */
function Approximation({ page, deckId }: { page: Page; deckId: string }) {
  // 540pt is a 7.5in slide -- the right guess when the file did not say.
  const pageHeightPt = page.height_pt > 0 ? page.height_pt : 540;
  return (
    <div className="approx">
      {page.images.map((image) => (
        <img
          key={image.id}
          className="approx-image"
          src={api.imageUrl(deckId, image.id)}
          alt=""
          loading="lazy"
          style={{
            left: `${image.box.x * 100}%`,
            top: `${image.box.y * 100}%`,
            width: `${image.box.w * 100}%`,
            height: `${image.box.h * 100}%`,
          }}
        />
      ))}
      {groupByShape(page.blocks.filter((b) => b.kind !== "chrome")).map((shape) => (
        <div
          key={shape.key}
          className="approx-shape"
          style={{
            left: `${shape.box.x * 100}%`,
            top: `${shape.box.y * 100}%`,
            width: `${shape.box.w * 100}%`,
          }}
        >
          {shape.blocks.map((block) => (
            <ApproxBlock
              key={block.id}
              block={block}
              pageHeightPt={pageHeightPt}
              aspect={page.aspect}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Type size when the file did not state one.
 *
 * PowerPoint inherits size from the layout far more often than it sets it, and
 * the parser reports 0 rather than guess -- a wrong size there would mislabel a
 * title. Here a guess is harmless and doing without one is not: drawn at a
 * single size, a slide is a wall of identical text and the shape that makes it
 * findable is gone.
 */
const DEFAULT_PT: Record<string, number> = {
  title: 30,
  body: 18,
  code: 14,
  caption: 13,
  fragment: 13,
  chrome: 11,
};

/**
 * Blocks that came from the same shape, kept together and in order.
 *
 * A bulleted list is one text box on the slide and five blocks in the model,
 * every one of them carrying the box's position. Positioned individually they
 * land on top of each other; positioned once and stacked, they are the list.
 */
function groupByShape(blocks: Block[]) {
  const groups: { key: string; box: Block["box"]; blocks: Block[] }[] = [];
  for (const block of blocks) {
    const last = groups[groups.length - 1];
    if (last && last.key === block.shape) last.blocks.push(block);
    else groups.push({ key: block.shape, box: block.box, blocks: [block] });
  }
  return groups;
}

function ApproxBlock({
  block,
  pageHeightPt,
  aspect,
}: {
  block: Block;
  pageHeightPt: number;
  aspect: number;
}) {
  const points = block.font > 0 ? block.font : (DEFAULT_PT[block.kind] ?? 16);
  // Sized against the container's *width*, converted through the aspect ratio.
  // The natural unit here is a fraction of the page's height, but cqh needs
  // size containment on every frame, and inline-size containment is much
  // cheaper for the same result.
  const fraction = (points / pageHeightPt) * 100;
  return (
    <div
      className={`approx-block approx-${block.kind}${block.mono ? " mono" : ""}`}
      style={{ fontSize: `${fraction / (aspect || 1.777)}cqw` }}
    >
      {block.text}
    </div>
  );
}
