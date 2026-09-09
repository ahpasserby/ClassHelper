/**
 * The React side of the two-pane sync. See sync.ts for what it is doing.
 *
 * A pane hands this three things -- its scroller, its page sections, and the
 * element inside each section whose natural height should be honoured -- and
 * gets back the agreed height per page. Measuring the inner element rather
 * than the section is what stops the negotiation from chasing its own tail: a
 * minimum applied to the section never changes what the content inside it
 * needs.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { type Pane, syncGroup } from "./sync";

export function usePaneSync(deckId: string, pane: Pane) {
  const group = syncGroup(deckId);
  const scroller = useRef<HTMLDivElement | null>(null);
  const sections = useRef(new Map<number, HTMLElement>());
  const contents = useRef(new Map<number, HTMLElement>());

  const heights = useSyncExternalStore(group.subscribe, group.heights, group.heights);

  // Page positions, rebuilt only when the layout actually moved. Reading
  // offsetTop for every page on every scroll event is a forced layout per page
  // per frame, and a deck is routinely a hundred pages.
  const cache = useRef<{ top: number; height: number }[]>([]);
  const cachedFor = useRef(-1);

  const remeasureNow = useCallback(() => {
    const measured: number[] = [];
    contents.current.forEach((el, index) => {
      measured[index] = el.getBoundingClientRect().height;
    });
    cachedFor.current = -1;
    group.measure(pane, measured);
  }, [group, pane]);

  // Coalesced to one measurement per frame: translations land page by page and
  // each one would otherwise walk every page in the deck.
  const pendingMeasure = useRef(0);
  const remeasure = useCallback(() => {
    if (pendingMeasure.current) return;
    pendingMeasure.current = requestAnimationFrame(() => {
      pendingMeasure.current = 0;
      remeasureNow();
    });
  }, [remeasureNow]);

  // Attach to the group, and tell it how to find this pane's pages. Offsets
  // are read live: a page's position changes whenever a translation lands.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const detach = group.attach(pane, {
      el,
      heights: [],
      offsets: () => {
        // One cheap read stands in for "has anything moved": any change to a
        // page's height changes the scroller's total.
        const total = el.scrollHeight;
        if (cachedFor.current === total) return cache.current;

        const list: { top: number; height: number }[] = [];
        sections.current.forEach((section, index) => {
          list[index] = { top: section.offsetTop, height: section.offsetHeight };
        });
        // A gap would make the anchor search walk off the end of the array.
        for (let i = 0; i < list.length; i++) {
          if (!list[i]) list[i] = { top: 0, height: 1 };
        }
        cache.current = list;
        cachedFor.current = total;
        return list;
      },
    });
    remeasureNow();
    // A frame later, so the pane has been laid out and its offsets are real.
    const adopt = requestAnimationFrame(() => group.adopt(pane));
    return () => {
      cancelAnimationFrame(adopt);
      detach();
    };
  }, [group, pane, remeasureNow]);

  // Content grows as translations arrive, so heights are renegotiated
  // continuously rather than once on mount.
  useEffect(() => {
    const observer = new ResizeObserver(() => remeasure());
    contents.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [remeasure, heights.length]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let frame = 0;
    const onScroll = () => {
      // One follow per frame: a trackpad fires scroll events far faster than
      // the other pane can be laid out.
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        group.follow(pane);
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [group, pane]);

  const setSection = useCallback(
    (index: number) => (el: HTMLElement | null) => {
      if (el) sections.current.set(index, el);
      else sections.current.delete(index);
    },
    [],
  );

  const setContent = useCallback(
    (index: number) => (el: HTMLElement | null) => {
      if (el) contents.current.set(index, el);
      else contents.current.delete(index);
    },
    [],
  );

  useEffect(
    () => () => {
      if (pendingMeasure.current) cancelAnimationFrame(pendingMeasure.current);
    },
    [],
  );

  return { group, scroller, setSection, setContent, heights, remeasure };
}
