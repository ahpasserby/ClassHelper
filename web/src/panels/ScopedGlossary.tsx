/**
 * The glossary for one scope on the board, with everything it inherits.
 *
 * Terminology is not flat. `set` means one thing in a database course and
 * another in topology, while `algorithm` means the same everywhere. So a term
 * can be pinned globally, for a semester, or for a single course, and the
 * narrower one wins.
 *
 * Inherited terms are shown, not hidden, and labelled with where they came
 * from. That is the whole point of the feature: opening a course shows the
 * terminology that already applies to it without anything having been copied,
 * so you can see at a glance what the translator is working from -- and
 * overriding one here leaves the broader entry untouched everywhere else.
 */

import { useCallback, useEffect, useState } from "react";
import { glossary as api, type ScopeLabel, type ScopedTerm } from "../api";

export function ScopedGlossary({
  scope,
  name,
}: {
  scope: string | null;
  name: string;
}) {
  const [terms, setTerms] = useState<ScopedTerm[]>([]);
  const [chain, setChain] = useState<ScopeLabel[]>([]);
  const [term, setTerm] = useState("");
  const [translation, setTranslation] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Collapsed by default. The board page is for taking the material in at a
  // glance; a course with fifty inherited terms would otherwise bury it.
  const [open, setOpen] = useState(false);

  const reload = useCallback(async () => {
    try {
      const result = await api.read(scope);
      setTerms(result.terms);
      setChain(result.chain);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }, [scope]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const save = async (word: string, rendering: string) => {
    if (!word.trim() || !rendering.trim()) return;
    setBusy(true);
    setMessage("正在重译用到这个词的句子…");
    try {
      const result = await api.set(word.trim(), rendering.trim(), scope);
      const sentences = result.decks_changed.reduce((n, d) => n + d.sentences, 0);
      setMessage(
        sentences > 0
          ? `已更新 ${sentences} 句，涉及 ${result.decks_changed.length} 份课件`
          : "已记录，下次用到时生效。",
      );
      setTerm("");
      setTranslation("");
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const ownScope = scope ?? "global";
  const own = terms.filter((t) => !t.inherited);
  const inherited = terms.filter((t) => t.inherited);

  return (
    <section className="board-section glossary-section">
      <h2>
        <button className="section-title" onClick={() => setOpen(!open)}>
          {open ? "⌄" : "›"} 术语表 · {name}
        </button>
        <span className="count">
          {own.length} 条本级
          {inherited.length > 0 && ` · ${inherited.length} 条继承`}
        </span>
      </h2>

      {open && (
        <>
          <p className="scope-chain muted">
            继承链：
            {chain.map((s, i) => (
              <span key={s.id}>
                {i > 0 && " → "}
                <span className={s.id === ownScope ? "scope-self" : undefined}>
                  {s.name}
                </span>
              </span>
            ))}
            {chain.length > 1 && "（越靠右越优先）"}
          </p>

          <form
            className="glossary-form inline"
            onSubmit={(e) => {
              e.preventDefault();
              void save(term, translation);
            }}
          >
            <input
              value={term}
              placeholder="原文术语，如 relationship"
              onChange={(e) => setTerm(e.target.value)}
            />
            <input
              value={translation}
              placeholder="你要的译法，如 联系"
              onChange={(e) => setTranslation(e.target.value)}
            />
            <button type="submit" disabled={busy}>
              固定在「{name}」
            </button>
          </form>
          {message && <p className="glossary-message muted">{message}</p>}

          {terms.length === 0 ? (
            <p className="section-empty muted">
              还没有术语。翻译过程中模型提出的术语也会自动积累到这里。
            </p>
          ) : (
            <table className="item-table glossary-table">
              <tbody>
                {terms.map((t) => (
                  <tr key={`${t.scope}:${t.term}`}>
                    <td className="term-src">{t.term}</td>
                    <td className={`term-dst${t.locked ? " locked" : ""}`}>
                      {t.translation}
                    </td>
                    <td className="term-scope">
                      {t.inherited ? (
                        <span
                          className="badge inherited"
                          title="继承自上层，在这里修改只影响本层。"
                        >
                          继承自 {label(chain, t.scope)}
                        </span>
                      ) : t.locked ? (
                        <span className="badge locked">已固定</span>
                      ) : (
                        <span className="badge auto" title="翻译时自动积累的">
                          自动
                        </span>
                      )}
                    </td>
                    <td className="item-actions">
                      {t.inherited ? (
                        <button
                          className="link"
                          onClick={() => {
                            setTerm(t.term);
                            setTranslation(t.translation);
                          }}
                        >
                          在这里覆盖
                        </button>
                      ) : (
                        <button
                          className="link danger"
                          title={
                            inherited.some((i) => i.term === t.term)
                              ? "删掉后会露出上层的译法"
                              : "删掉这条术语"
                          }
                          onClick={async () => {
                            await api.unset(t.term, scope);
                            await reload();
                          }}
                        >
                          删除
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

function label(chain: ScopeLabel[], scope: string): string {
  return chain.find((s) => s.id === scope)?.name ?? scope;
}
