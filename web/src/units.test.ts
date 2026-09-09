/**
 * Selecting sentences, and copying them.
 *
 * Two things here are easy to get wrong and invisible when you do. A sentence's
 * id is a hash of its text, so a definition restated on three slides is one id
 * in three places -- key the selection on that and clicking one lights up all
 * three. And a paragraph arrives here already cut into sentences for the
 * translator, so copying it back out as four lines is not what anyone meant.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { Block, Deck, Page } from "./api.ts";
import {
  asText,
  askPages,
  pageRanges,
  range,
  selected,
  unitKey,
  units,
} from "./units.ts";

function sentence(text: string, translation: string | null = null) {
  return { id: text.slice(0, 8), text, translation, flagged: false, edited: false };
}

function block(id: string, kind: Block["kind"], texts: [string, string | null][]): Block {
  return {
    id,
    kind,
    reason: "",
    text: texts.map((t) => t[0]).join(" "),
    box: { x: 0, y: 0, w: 1, h: 1 },
    bullet: false,
    level: 0,
    font: 0,
    mono: false,
    shape: id,
    sentences: texts.map(([t, d]) => sentence(t, d)),
  };
}

function deck(pages: Block[][]): Deck {
  return {
    id: "d",
    name: "Lec",
    path: "/tmp/Lec.pptx",
    format: "pptx",
    warnings: [],
    notes: [],
    target_lang: "zh-CN",
    progress: { done: 0, total: 0, calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    source: { mode: "approximate", detail: "" },
    pages: pages.map((blocks, index): Page => ({
      index,
      title: "",
      aspect: 16 / 9,
      height_pt: 540,
      blocks,
      images: [],
    })),
  };
}

describe("selecting sentences", () => {
  it("tells two identical sentences apart", () => {
    // The regression the positional key exists for: same text, same hash.
    const repeated: [string, string | null][] = [["An entity is a thing.", "实体是一个东西。"]];
    const d = deck([[block("b1", "body", repeated)], [block("b1", "body", repeated)]]);

    const all = units(d);
    assert.equal(all.length, 2);
    assert.notEqual(all[0].key, all[1].key);
    assert.equal(all[0].sentence.id, all[1].sentence.id, "the ids really do collide");

    const one = selected(d, [all[1].key]);
    assert.equal(one.length, 1);
    assert.equal(one[0].page, 1);
  });

  it("orders sentences the way the page shows them, not the way the file had them", () => {
    // The reader puts the reading column first and gathers the diagram labels
    // underneath, so a caption found halfway down the slide is read last.
    const d = deck([[
      block("cap", "caption", [["Figure 1", "图 1"]]),
      block("body", "body", [["The prose.", "正文。"]]),
    ]]);
    assert.deepEqual(units(d).map((u) => u.sentence.text), ["The prose.", "Figure 1"]);
  });

  it("sweeps a range in that same order, across pages", () => {
    const d = deck([
      [block("a", "body", [["One.", null], ["Two.", null]])],
      [block("b", "body", [["Three.", null]])],
    ]);
    const all = units(d);
    const keys = range(d, all[0].key, all[2].key);
    assert.deepEqual(keys, all.map((u) => u.key));
  });

  it("sweeps the same range whichever end you started from", () => {
    const d = deck([[block("a", "body", [["One.", null], ["Two.", null]])]]);
    const all = units(d);
    assert.deepEqual(range(d, all[1].key, all[0].key), range(d, all[0].key, all[1].key));
  });

  it("leaves code alone, having nothing to select in it", () => {
    const d = deck([[
      block("code", "code", []),
      block("body", "body", [["Prose.", "正文。"]]),
    ]]);
    assert.equal(units(d).length, 1);
  });

  it("keeps the reading order when the keys were clicked out of order", () => {
    const d = deck([[block("a", "body", [["One.", null], ["Two.", null]])]]);
    const all = units(d);
    const chosen = selected(d, [all[1].key, all[0].key]);
    assert.deepEqual(chosen.map((u) => u.sentence.text), ["One.", "Two."]);
  });
});

describe("copying a selection", () => {
  const paragraph = deck([[
    block("p1", "body", [
      ["An entity is a thing.", "实体是一个东西。"],
      ["It has attributes.", "它有属性。"],
    ]),
    block("p2", "body", [["A schema describes it.", "模式描述它。"]]),
  ]]);
  const all = units(paragraph);

  it("puts a paragraph back together instead of one line per sentence", () => {
    const text = asText(selected(paragraph, [all[0].key, all[1].key]), "source");
    assert.equal(text, "An entity is a thing. It has attributes.");
  });

  it("separates paragraphs with a blank line", () => {
    const text = asText(units(paragraph), "source");
    assert.equal(
      text,
      "An entity is a thing. It has attributes.\n\nA schema describes it.",
    );
  });

  it("joins Chinese sentences without a space between them", () => {
    const text = asText(selected(paragraph, [all[0].key, all[1].key]), "target");
    assert.equal(text, "实体是一个东西。它有属性。");
  });

  it("pairs each paragraph with its translation", () => {
    const text = asText(units(paragraph), "both");
    assert.equal(
      text,
      "An entity is a thing. It has attributes.\n实体是一个东西。它有属性。\n\n" +
        "A schema describes it.\n模式描述它。",
    );
  });

  it("does not glue together sentences that were not adjacent", () => {
    // Picking the first and third of a paragraph is not asking for a paragraph.
    const three = deck([[
      block("p", "body", [["One.", null], ["Two.", null], ["Three.", null]]),
    ]]);
    const keys = units(three);
    const text = asText(selected(three, [keys[0].key, keys[2].key]), "source");
    assert.equal(text, "One.\n\nThree.");
  });

  it("copies what is there when a translation has not arrived", () => {
    const half = deck([[block("p", "body", [["Untranslated.", null]])]]);
    assert.equal(asText(units(half), "target"), "");
    assert.equal(asText(units(half), "both"), "Untranslated.");
  });

  it("has a key that does not depend on how it was built", () => {
    assert.equal(unitKey(2, "abc", 1), unitKey(2, "abc", 1));
    assert.notEqual(unitKey(2, "abc", 1), unitKey(3, "abc", 1));
  });
});

describe("naming the pages a question carries", () => {
  it("writes single pages as themselves", () => {
    assert.equal(pageRanges([0]), "1");
    assert.equal(pageRanges([0, 2, 4]), "1、3、5");
  });

  it("collapses a run", () => {
    assert.equal(pageRanges([4, 5, 6, 7]), "5–8");
  });

  it("spells out a run of two rather than hyphenating it", () => {
    assert.equal(pageRanges([2, 3]), "3、4");
  });

  it("mixes runs and singles the way a person would", () => {
    assert.equal(pageRanges([2, 4, 5, 6, 7, 11]), "3、5–8、12");
  });

  it("sorts and de-duplicates what it is given", () => {
    assert.equal(pageRanges([7, 2, 2, 3]), "3、4、8");
  });

  it("says nothing about nothing", () => {
    assert.equal(pageRanges([]), "");
  });
});

describe("which slides a question carries", () => {
  it("is the page being read, when nothing was picked", () => {
    assert.deepEqual(askPages([], 4), [4]);
  });

  it("always includes the page being read", () => {
    // The sentences the question is about are on it; a question whose own
    // slide is missing is not answerable.
    assert.deepEqual(askPages([9, 10], 5), [5, 9, 10]);
  });

  it("does not list it twice when it was picked too", () => {
    assert.deepEqual(askPages([4, 5], 5), [4, 5]);
  });
});
