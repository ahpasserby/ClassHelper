/**
 * Keeping the slide view and the translation view on the same page.
 *
 * Two scrollers showing the same document at different lengths. Scroll either
 * and the other has to follow, and it has to follow *honestly*: mirroring raw
 * scroll offsets only works while both sides are the same height, and they
 * never are -- one page's translation runs long, the next one's is a heading.
 *
 * So position is expressed as a page and a fraction of the way down it, which
 * is a coordinate both panes understand no matter how tall each renders that
 * page. Scrolling to 40% of slide 5 means scrolling to 40% of slide 5.
 *
 * On top of that the two panes agree on a height per page -- the taller of what
 * each needs -- which is what makes a page boundary land on a page boundary
 * instead of merely nearby. That agreement is negotiated here: each pane
 * measures its own content and reads back the maximum.
 *
 * The whole thing lives outside React because it runs on every scroll event.
 * Components subscribe to the parts they render.
 */

export type Pane = "reader" | "source";

interface PaneState {
  /** The scrolling element. */
  el: HTMLElement;
  /** Natural content height per page, before any agreed minimum is applied. */
  heights: number[];
  /** Where each page starts, in the scroller's own coordinates. */
  offsets: () => { top: number; height: number }[];
}

/** A position both panes can act on: page `index`, `fraction` of the way down. */
export interface Anchor {
  index: number;
  fraction: number;
}

class Group {
  private panes = new Map<Pane, PaneState>();
  private listeners = new Set<() => void>();
  /** The agreed height per page, and a stable snapshot for useSyncExternalStore. */
  private agreed: number[] = [];
  private snapshot: readonly number[] = [];
  /**
   * Where we last put each pane, so its own scroll event can be recognised.
   *
   * A time window is not good enough here. Every follow writes the other
   * pane's scrollTop, that write fires a scroll event, and if the handler
   * cannot tell that event from a real one it turns around and writes back
   * into the pane the user is actually touching -- once per frame, for as long
   * as they keep scrolling. On a trackpad that cancels the momentum on every
   * frame, which is what "it scrolls in steps" was.
   *
   * Comparing against the value we wrote is exact: our own echo matches, a
   * hand on the trackpad does not, and a real scroll takes effect immediately
   * instead of waiting out a guard window.
   */
  private expected = new Map<Pane, number>();
  /**
   * A ceiling on how long a pane's scroll events are assumed to be ours.
   *
   * A smooth scroll emits a stream of positions we never chose, and it lasts as
   * long as the distance takes -- which for a jump across a hundred-page deck
   * is well over a second. Muting for a fixed short window meant the two panes
   * started correcting each other halfway through the animation and both
   * stopped short of the page that was clicked.
   */
  private quietUntil = new Map<Pane, number>();

  attach(pane: Pane, state: PaneState): () => void {
    this.panes.set(pane, state);
    return () => {
      if (this.panes.get(pane) === state) this.panes.delete(pane);
      this.recompute();
    };
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** The agreed page heights. Identity is stable until they actually change. */
  heights = (): readonly number[] => this.snapshot;

  /** A pane reports what its own content needs. */
  measure(pane: Pane, heights: number[]): void {
    const state = this.panes.get(pane);
    if (!state) return;
    state.heights = heights;
    this.recompute();
  }

  private recompute(): void {
    const all = [...this.panes.values()].map((p) => p.heights);
    const count = Math.max(0, ...all.map((h) => h.length));
    const next: number[] = [];
    for (let i = 0; i < count; i++) {
      // The taller of what either side needs. Anything less would clip one of
      // them; anything more would open a gap in both.
      next.push(Math.max(0, ...all.map((h) => h[i] ?? 0)));
    }
    const same =
      next.length === this.agreed.length &&
      next.every((v, i) => Math.abs(v - this.agreed[i]) < 1);
    if (same) return;
    this.agreed = next;
    this.snapshot = next.slice();
    this.listeners.forEach((fn) => fn());
  }

  // -- position ----------------------------------------------------------

  /** Where a pane is looking, as a page and a fraction of the way down it. */
  anchorOf(pane: Pane): Anchor | null {
    const state = this.panes.get(pane);
    if (!state) return null;
    const top = state.el.scrollTop;
    const pages = state.offsets();
    if (!pages.length) return null;

    for (let i = pages.length - 1; i >= 0; i--) {
      const page = pages[i];
      if (top >= page.top - 1 || i === 0) {
        const height = page.height || 1;
        return {
          index: i,
          fraction: Math.max(0, Math.min(1, (top - page.top) / height)),
        };
      }
    }
    return null;
  }

  /** Where a pane would have to scroll to put that anchor at its top. */
  private targetFor(state: PaneState, anchor: Anchor): number | null {
    const pages = state.offsets();
    const page = pages[Math.max(0, Math.min(pages.length - 1, anchor.index))];
    if (!page) return null;
    // Clamped, because a target past the end is a target the pane will never
    // reach -- and an arrival check that can never succeed is a stuck mute.
    const limit = Math.max(0, state.el.scrollHeight - state.el.clientHeight);
    return Math.min(limit, Math.round(page.top + anchor.fraction * page.height));
  }

  /** Put a pane at that position. */
  scrollTo(pane: Pane, anchor: Anchor, behavior: ScrollBehavior = "auto"): void {
    const state = this.panes.get(pane);
    if (!state) return;
    const target = this.targetFor(state, anchor);
    if (target === null) return;
    state.el.scrollTo({ top: target, behavior });
  }

  /** One pane moved; move the other to match -- unless we moved it ourselves. */
  follow(source: Pane): void {
    const state = this.panes.get(source);
    if (!state) return;
    if (this.isOurs(source, state.el)) return;
    // A scroll that is not ours ends whatever we thought was in flight.
    this.expected.delete(source);
    this.quietUntil.delete(source);

    const anchor = this.anchorOf(source);
    if (!anchor) return;
    for (const [pane, other] of this.panes) {
      if (pane !== source) this.apply(pane, other, anchor);
    }
  }

  private isOurs(pane: Pane, el: HTMLElement): boolean {
    const want = this.expected.get(pane);
    if (want !== undefined && Math.abs(el.scrollTop - want) <= 2) {
      // Arrived. The mute ends here rather than running out, so a hand on the
      // trackpad the moment the animation lands is not swallowed.
      this.expected.delete(pane);
      this.quietUntil.delete(pane);
      return true;
    }
    return performance.now() < (this.quietUntil.get(pane) ?? 0);
  }

  private apply(pane: Pane, state: PaneState, anchor: Anchor): void {
    const top = this.targetFor(state, anchor);
    if (top === null) return;
    // Already there. Writing anyway would be one more scroll event to
    // recognise, and one more chance to disturb a scroll in progress.
    if (Math.abs(state.el.scrollTop - top) < 1) return;
    state.el.scrollTop = top;
    // Read back rather than trusting the write: the browser clamps at the ends.
    this.expected.set(pane, state.el.scrollTop);
    this.quietUntil.delete(pane);
  }

  /** Send every pane to the top of a page -- what clicking a chapter does. */
  goTo(index: number, behavior: ScrollBehavior = "smooth"): void {
    // Muted until each pane arrives, with a ceiling in case it never does --
    // the page could be the last one, where the scroller runs out first.
    const ceiling = performance.now() + (behavior === "smooth" ? 3000 : 150);
    for (const [pane, state] of this.panes) {
      const target = this.targetFor(state, { index, fraction: 0 });
      if (target === null) continue;
      this.quietUntil.set(pane, ceiling);
      this.expected.set(pane, target);
      state.el.scrollTo({ top: target, behavior });
    }
  }

  /**
   * Bring a pane that has just appeared to where the others already are.
   *
   * The two views share one sidebar, so opening the slides means the pane is
   * built from nothing while the reader is already halfway down the deck.
   * Without this it mounts at the top, and the first thing it does is disagree
   * with the page you are reading.
   */
  adopt(pane: Pane): void {
    const state = this.panes.get(pane);
    if (!state) return;
    for (const [other] of this.panes) {
      if (other === pane) continue;
      const anchor = this.anchorOf(other);
      if (!anchor) continue;
      this.apply(pane, state, anchor);
      return;
    }
  }

  has(pane: Pane): boolean {
    return this.panes.has(pane);
  }
}

const groups = new Map<string, Group>();

/** The sync group for one deck. Created on demand, one per open deck. */
export function syncGroup(deckId: string): Group {
  let group = groups.get(deckId);
  if (!group) {
    group = new Group();
    groups.set(deckId, group);
  }
  return group;
}

export function dropSyncGroup(deckId: string): void {
  groups.delete(deckId);
}

export type SyncGroup = Group;
