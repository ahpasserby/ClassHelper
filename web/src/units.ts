/**
 * Naming and gathering the sentences a reader has selected.
 *
 * A sentence's `id` is a hash of its text, which is what makes the translation
 * cache work -- and what makes it useless as a selection key: a definition
 * restated on three slides is one id in three places, and selecting one of them
 * would light up all three. So selection is keyed by *where* a sentence is, and
 * the id is only used when talking to the server, where the text is all that
 * matters anyway.
 */

import type { Deck, Sentence } from "./api";

/** One selectable sentence, and where it sits. */
export interface Unit {
  key: string;
  page: number;
  blockId: string;
  index: number;
  sentence: Sentence;
  /** True when the sentence starts a new block, i.e. a new paragraph. */
  startsBlock: boolean;
}

export function unitKey(page: number, blockId: string, index: number): string {
  return `${page}|${blockId}|${index}`;
}

/**
 * Every selectable sentence in the deck, in the order it appears *on screen*.
 *
 * Which is not the order in `page.blocks`: the reader puts the reading column
 * first and gathers the diagram labels underneath it, so a caption that the
 * parser found halfway down the slide is read last. A shift-click sweeps what
 * lies between two points visually, so this list has to agree with the page.
 *
 * Code blocks are absent because they have no sentences -- they are shown
 * verbatim and never translated, so there is nothing here to pick out.
 */
const FLOW = new Set(["title", "body"]);
const FIGURE = new Set(["caption", "fragment"]);

export function units(deck: Deck): Unit[] {
  const out: Unit[] = [];
  for (const page of deck.pages) {
    const add = (kinds: Set<string>) => {
      for (const block of page.blocks) {
        if (!kinds.has(block.kind)) continue;
        block.sentences.forEach((sentence, index) => {
          out.push({
            key: unitKey(page.index, block.id, index),
            page: page.index,
            blockId: block.id,
            index,
            sentence,
            startsBlock: index === 0,
          });
        });
      }
    };
    add(FLOW);
    add(FIGURE);
  }
  return out;
}

/** The selected units, in reading order, whatever order they were clicked in. */
export function selected(deck: Deck, keys: readonly string[]): Unit[] {
  const wanted = new Set(keys);
  return units(deck).filter((u) => wanted.has(u.key));
}

/** Everything from one key to another, inclusive -- a shift-click. */
export function range(deck: Deck, from: string, to: string): string[] {
  const all = units(deck);
  const a = all.findIndex((u) => u.key === from);
  const b = all.findIndex((u) => u.key === to);
  if (a < 0 || b < 0) return [to];
  const [start, end] = a <= b ? [a, b] : [b, a];
  return all.slice(start, end + 1).map((u) => u.key);
}

export type CopyMode = "source" | "target" | "both";

/**
 * Selected text, laid out the way it was read.
 *
 * Sentences from one paragraph are joined back into a paragraph -- they were
 * split for the translator, not for the reader, and pasting a paragraph as
 * four lines is not what anyone meant by "copy". Paragraphs are separated by a
 * blank line, and in the bilingual form each paragraph is followed by its
 * translation, which is the shape it had on screen.
 */
export function asText(chosen: Unit[], mode: CopyMode): string {
  if (!chosen.length) return "";

  const paragraphs: { source: string[]; target: string[] }[] = [];
  let previous: Unit | null = null;
  for (const unit of chosen) {
    const broken =
      previous === null ||
      previous.blockId !== unit.blockId ||
      previous.page !== unit.page ||
      // A gap inside one paragraph: they picked sentence 1 and 3, so they are
      // not asking for a paragraph.
      unit.index !== previous.index + 1;
    if (broken) paragraphs.push({ source: [], target: [] });
    const last = paragraphs[paragraphs.length - 1]!;
    last.source.push(unit.sentence.text.trim());
    if (unit.sentence.translation) last.target.push(unit.sentence.translation.trim());
    previous = unit;
  }

  return paragraphs
    .map(({ source, target }) => {
      const src = source.join(" ");
      const dst = target.join("");  // Chinese needs no space between sentences
      if (mode === "source") return src;
      if (mode === "target") return dst;
      return dst ? `${src}\n${dst}` : src;
    })
    .filter(Boolean)
    .join("\n\n");
}

/**
 * Page numbers as a person would write them: "3、5–8、12".
 *
 * A question can carry a dozen slides, and a dozen numbers in a row is not
 * something anyone reads. Runs collapse; the numbers are 1-based, because the
 * only place these are ever shown is next to a page number on screen.
 */
export function pageRanges(pages: readonly number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  if (!sorted.length) return "";

  const runs: [number, number][] = [];
  for (const page of sorted) {
    const last = runs[runs.length - 1];
    if (last && page === last[1] + 1) last[1] = page;
    else runs.push([page, page]);
  }
  return runs
    .map(([a, b]) =>
      a === b
        ? `${a + 1}`
        // A run of two reads better as two numbers than as a range.
        : b === a + 1
          ? `${a + 1}、${b + 1}`
          : `${a + 1}–${b + 1}`,
    )
    .join("、");
}

/**
 * The slides a question will actually carry.
 *
 * The page being read is always in it, whatever was picked in the chapter
 * list: the sentences the question is about are on it, and a question whose
 * own slide is missing is not answerable. Two places show this -- the chapter
 * list and the ask panel -- and they have to agree, so neither computes it.
 */
export function askPages(picked: readonly number[], anchor: number): number[] {
  if (!picked.length) return [anchor];
  return [...new Set([...picked, anchor])].sort((a, b) => a - b);
}
