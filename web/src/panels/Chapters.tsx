/**
 * The chapter list: one row per page, titled, in order.
 *
 * Called 章节 rather than 幻灯片 because that name now belongs to the panel that
 * shows the slides themselves. This is the table of contents -- what the deck
 * covers and where you are in it -- and it doubles as the progress display: a
 * page still waiting on the translator is marked, so the state of the deck is
 * visible at a glance rather than only as a number in the status bar.
 *
 * It is also where you say which slides a question should carry. A definition
 * given on slide 12 and used on slide 20 is a pairing only the reader knows
 * about, so picking the pages by hand beats any window the program could guess
 * -- and picking them here, in the list that already names them, means not
 * having to describe them somewhere else.
 */

import { useStore } from "../store";
import { askPages, pageRanges } from "../units";

export function Chapters() {
  const { active, goToPage, selectContextPage, clearContext } = useStore();
  if (!active) return <div className="pad muted">还没有打开课件</div>;
  const { deck, currentPage, context } = active;
  const chosen = new Set(context.pages);

  return (
    <div className="chapters">
      {chosen.size > 0 && (
        <div className="chapters-context">
          {/* What will really be sent, which includes the page being read even
              when it was not one of the ones picked. Saying anything else here
              would contradict the ask panel, which says the same thing. */}
          <span>
            提问将带上第 {pageRanges(askPages(context.pages, currentPage))} 页
          </span>
          <button className="link" onClick={() => clearContext(deck.id)}>
            清除
          </button>
        </div>
      )}

      {deck.pages.map((page) => {
        const units = page.blocks
          .filter((b) => b.kind !== "chrome" && b.kind !== "code")
          .flatMap((b) => b.sentences);
        const pending = units.filter((s) => s.translation === null).length;
        const flagged = units.filter((s) => s.flagged).length;
        const picked = chosen.has(page.index);

        return (
          <button
            key={page.index}
            className={
              `chapter${page.index === currentPage ? " current" : ""}` +
              (picked ? " picked" : "")
            }
            title={picked ? "会作为提问上下文发送" : undefined}
            onClick={(event) => {
              // Plain click is what it always was: go there, and send that page
              // alone. The modifiers are the ones every list uses -- and they
              // choose pages to *send*, not a page to read, so they never
              // scroll the reader out from under you.
              if (event.metaKey || event.ctrlKey) {
                selectContextPage(deck.id, page.index, "toggle");
              } else if (event.shiftKey) {
                selectContextPage(deck.id, page.index, "range");
              } else {
                clearContext(deck.id);
                goToPage(page.index);
              }
            }}
          >
            <span className="n">{page.index + 1}</span>
            <span className="t">{page.title || <em>无标题</em>}</span>
            {picked && <span className="pick" aria-hidden>✓</span>}
            {pending > 0 && <span className="dot pending" title={`${pending} 句待翻译`} />}
            {flagged > 0 && <span className="dot flagged" title={`${flagged} 句存疑`} />}
          </button>
        );
      })}
    </div>
  );
}
