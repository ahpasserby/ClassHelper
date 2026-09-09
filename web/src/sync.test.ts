/**
 * The two-pane scroll sync.
 *
 * One test here matters more than the rest: that following a pane does not
 * come back around and move the pane the user is touching. It did, once per
 * frame, and on a trackpad that cancels the momentum every frame -- which is
 * not obviously a sync bug when you meet it. It reads as the app stuttering.
 *
 * Run with `npm test` in web/. No test framework: node runs TypeScript itself,
 * and this file needs nothing from a browser but an object with a scrollTop.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { syncGroup, dropSyncGroup, type Pane } from "./sync.ts";

/**
 * A scroller and its pages, standing in for a rendered pane.
 *
 * It counts writes rather than only holding a value. Asserting on the final
 * position is not enough: a write-back usually lands on the position the pane
 * was already at, so it is invisible in the value and perfectly visible on a
 * trackpad, where writing scrollTop at all is what cancels the momentum.
 */
const VIEWPORT = 600;

function pane(heights: number[]) {
  let value = 0;
  let writes = 0;
  const el = {
    get scrollTop() {
      return value;
    },
    set scrollTop(v: number) {
      writes++;
      value = v;
    },
    // A real scroller has an end, and the sync clamps to it -- a target past
    // the bottom is one the pane can never reach.
    scrollHeight: heights.reduce((a, b) => a + b, 0) + VIEWPORT,
    clientHeight: VIEWPORT,
    scrollTo(options: { top: number; behavior?: ScrollBehavior }) {
      // A smooth scroll does not arrive; it starts. The test steps the
      // position afterwards, which is the whole point -- an animation is the
      // only thing there is to interrupt.
      if (options.behavior === "smooth") {
        smoothTarget = options.top;
        return;
      }
      writes++;
      value = options.top;
    },
  } as unknown as HTMLElement;
  let smoothTarget: number | null = null;
  const offsets = () => {
    const list: { top: number; height: number }[] = [];
    let top = 0;
    for (const height of heights) {
      list.push({ top, height });
      top += height;
    }
    return list;
  };
  return {
    el,
    offsets,
    heights: [] as number[],
    writes: () => writes,
    clear: () => {
      writes = 0;
    },
    /** Where a smooth scroll was told to go, once it has been started. */
    smoothTarget: () => smoothTarget,
  };
}

let seq = 0;
function group(a: number[], b: number[]) {
  const id = `deck-${seq++}`;
  dropSyncGroup(id);
  const g = syncGroup(id);
  const reader = pane(a);
  const source = pane(b);
  g.attach("reader", reader);
  g.attach("source", source);
  return { g, reader, source };
}

/** What a real pane does: scrolling fires a scroll event, which calls follow. */
function scroll(g: ReturnType<typeof syncGroup>, p: Pane,
                moved: ReturnType<typeof pane>, to: number) {
  moved.el.scrollTop = to;
  moved.clear(); // the user's own scroll is not a write by us
  g.follow(p);
}

describe("scroll sync", () => {
  it("moves the other pane to the same place on the same page", () => {
    const { g, reader, source } = group([1000, 1000], [400, 400]);
    scroll(g, "reader", reader, 1500); // half way down page 2
    assert.equal(source.el.scrollTop, 600); // half way down its page 2
  });

  it("does not move the pane the user is scrolling", () => {
    // The regression. The follow writes the other pane, that pane emits its own
    // scroll event, and if that event is not recognised as our own the handler
    // turns around and writes back into the pane under the user's finger.
    // Deliberately awkward page heights, so a round trip through the other
    // pane does not land back on the same number.
    const { g, reader, source } = group([1000, 1000], [333, 333]);
    scroll(g, "reader", reader, 1500);
    const settled = reader.el.scrollTop;

    g.follow("source"); // the echo the browser delivers a frame later

    assert.equal(reader.writes(), 0, "scrolling one pane wrote back into it");
    assert.equal(reader.el.scrollTop, settled);
  });

  it("still follows a real scroll of the pane it just moved", () => {
    // The echo must be recognised by position, not by muting the pane for a
    // while: a hand on the other trackpad has to work immediately.
    const { g, reader, source } = group([1000, 1000], [400, 400]);
    scroll(g, "reader", reader, 1500);

    scroll(g, "source", source, 200); // half way down page 1, by hand
    assert.equal(reader.el.scrollTop, 500);
  });

  it("keeps the two in step when the pages are different heights", () => {
    const { g, reader, source } = group([300, 2000, 500], [400, 400, 400]);
    scroll(g, "reader", reader, 300 + 1000); // half way down page 2
    assert.equal(source.el.scrollTop, 400 + 200);
  });

  it("agrees on the taller of what each pane needs", () => {
    const { g } = group([0, 0], [0, 0]);
    g.measure("reader", [900, 100]);
    g.measure("source", [400, 700]);
    assert.deepEqual([...g.heights()], [900, 700]);
  });

  it("reports heights as one stable value until they change", () => {
    const { g } = group([0], [0]);
    g.measure("reader", [500]);
    const first = g.heights();
    g.measure("source", [500]); // no change to the maximum
    assert.equal(g.heights(), first, "an unchanged value must not re-render");
  });

  it("sends every pane to the top of a page", () => {
    const { g, reader, source } = group([1000, 1000], [400, 400]);
    g.goTo(1, "auto");
    assert.equal(reader.el.scrollTop, 1000);
    assert.equal(source.el.scrollTop, 400);
  });

  it("does not let a jump correct itself halfway through", () => {
    // A smooth scroll of a long distance outlasts any fixed mute, and the two
    // panes then started nudging each other mid-animation. Writing scrollTop
    // cancels a smooth scroll in a real browser, so both stopped short of the
    // page that was clicked -- by hundreds of pixels, on a long deck.
    const { g, reader, source } = group(
      Array(40).fill(500),
      Array(40).fill(300),
    );
    g.goTo(30, "smooth");

    const target = {
      reader: reader.smoothTarget()!,
      source: source.smoothTarget()!,
    };
    assert.ok(target.reader > 0 && target.source > 0);
    assert.equal(reader.el.scrollTop, 0, "a smooth scroll has not arrived yet");

    // Both panes animate at once, and both report every frame. Through all of
    // it the sync must write neither: a write to scrollTop cancels a smooth
    // scroll, which is how a jump across a long deck ended hundreds of pixels
    // short of the page that was clicked.
    for (let step = 1; step <= 10; step++) {
      // The browser moves both panes, then delivers both scroll events. Doing
      // it in that order matters: clearing a counter between the two would
      // erase the very write being looked for.
      reader.el.scrollTop = Math.round(target.reader * (step / 10));
      // Deliberately not in lockstep. Two scrollers easing over different
      // distances never are, and a correction only shows up as a write when
      // the pane is not already where the other one thinks it should be.
      source.el.scrollTop = Math.round(target.source * ((step - 1) / 10));
      reader.clear();
      source.clear();

      g.follow("reader");
      g.follow("source");

      assert.equal(
        reader.writes() + source.writes(),
        0,
        `the jump was corrected at frame ${step}`,
      );
    }

    // The lagging pane finishes a frame later.
    source.el.scrollTop = target.source;
    source.clear();
    g.follow("source");

    assert.equal(reader.writes() + source.writes(), 0, "the jump was interrupted");
    assert.equal(reader.el.scrollTop, target.reader);
    assert.equal(source.el.scrollTop, target.source);
  });

  it("brings a pane that has just appeared to where the other one is", () => {
    // The two left-hand views share a sidebar, so the slide pane is built from
    // nothing while the reader is already halfway down the deck.
    const id = `deck-${seq++}`;
    dropSyncGroup(id);
    const g = syncGroup(id);
    const reader = pane(Array(20).fill(500));
    g.attach("reader", reader);
    reader.el.scrollTop = 5000; // page 11
    g.follow("reader");

    const source = pane(Array(20).fill(300));
    g.attach("source", source);
    assert.equal(source.el.scrollTop, 0, "not until it is adopted");
    g.adopt("source");
    assert.equal(source.el.scrollTop, 3000, "same page, not the top");
  });

  it("clamps a target to the end of the scroller", () => {
    // Otherwise the last page is a position the pane can never reach, and the
    // arrival check waits for something that never happens.
    const { g, reader } = group([1000, 1000], [400, 400]);
    g.goTo(1, "auto");
    assert.ok(reader.el.scrollTop <= 2000 + 600 - 600);
  });
});
