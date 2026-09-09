/**
 * Settings.
 *
 * A page rather than switches in the title bar: the title bar should say what
 * you are looking at, not carry controls you touch once a term.
 *
 * The translation service is editable here, not only in a config file. Asking
 * someone to hand-edit TOML to change a model is asking them to learn the
 * program's filing system before they can use it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  KEEP_KEY,
  settings as api,
  type ProviderChoice,
  type SettingsData,
} from "../api";
import { useStore } from "../store";

/** The save body. Key order is fixed so it can be compared as text. */
function saveBody(form: SettingsData, key: string, data: SettingsData) {
  return {
    user_data_path: form.user_data_path,
    import_mode: form.import_mode,
    window_mode: form.window_mode,
    provider: form.provider,
    api_key: key || (data.api_key_set ? KEEP_KEY : ""),
    base_url: form.base_url,
    model: form.model,
    ask_model: form.ask_model,
    target_lang: form.target_lang,
  };
}

export function Settings() {
  const store = useStore();
  const { theme, setTheme } = store;
  const [data, setData] = useState<SettingsData | null>(null);
  const [form, setForm] = useState<SettingsData | null>(null);
  const [key, setKey] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  /**
   * What is on disk, as the autosave would have sent it.
   *
   * A "have we loaded yet" flag looked like it did this job and did not: the
   * ref was set the moment the fetch resolved, which is *before* React renders
   * the state it just set, so the very first render with a form in hand already
   * counted as a change and wrote the file. A fresh install came up saying
   * "已保存" and configured, having saved nothing but its own defaults.
   *
   * Comparing against the loaded values instead cannot drift: no change, no
   * write, whatever the order of the renders.
   */
  const stored = useRef<string | null>(null);

  const load = useCallback(async () => {
    const fresh = await api.read();
    stored.current = JSON.stringify(saveBody(fresh, "", fresh));
    setData(fresh);
    setForm(fresh);
    setKey("");
  }, []);

  useEffect(() => {
    void load().catch((e) => setMessage({ ok: false, text: String(e) }));
  }, [load]);

  // Ask the provider what it actually has. Hardcoded names go stale -- the
  // DeepSeek defaults once named models the service had already replaced.
  useEffect(() => {
    if (!form || !data) return;
    let cancelled = false;
    void api
      .models({
        provider: form.provider,
        api_key: key || (data.api_key_set ? KEEP_KEY : ""),
        base_url: form.base_url,
        model: form.model,
        ask_model: form.ask_model,
        target_lang: form.target_lang,
      })
      .then((r) => !cancelled && setModels(r.models))
      .catch(() => !cancelled && setModels([]));
    return () => {
      cancelled = true;
    };
  }, [form?.provider, form?.base_url, data?.api_key_set, key]);

  /**
   * Every change saves itself.
   *
   * A single Save button at the bottom of one section read as saving only that
   * section -- and the theme and reading toggles already applied instantly, so
   * the page had two contradictory models of what a change means. Now there is
   * one: you change something, it is stored.
   *
   * Debounced, because a text field would otherwise write once per keystroke,
   * and a half-typed API key is exactly what the length guard rejects.
   */
  useEffect(() => {
    if (!form || !data) return;
    // A key being typed is not a key yet; wait for something plausible.
    if (key && key.length < 8) return;
    const body = saveBody(form, key, data);
    const sig = JSON.stringify(body);
    if (sig === stored.current) return;

    const timer = window.setTimeout(async () => {
      setStatus("saving");
      try {
        await api.save(body);
        stored.current = sig;
        setStatus("saved");
        setMessage(null);
      } catch (e) {
        setStatus("error");
        setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [form, key, data]);

  if (!form || !data) return <div className="settings pad muted">读取设置…</div>;

  const provider = form.providers.find((p) => p.id === form.provider);
  const payload = {
    user_data_path: form.user_data_path,
    import_mode: form.import_mode,
    window_mode: form.window_mode,
    provider: form.provider,
    // An untouched field means "keep what is stored"; the page is never told
    // the key, so it cannot send it back.
    api_key: key || (data.api_key_set ? KEEP_KEY : ""),
    base_url: form.base_url,
    model: form.model,
    ask_model: form.ask_model,
    target_lang: form.target_lang,
  };

  const pickProvider = (choice: ProviderChoice) =>
    setForm({
      ...form,
      provider: choice.id,
      // Follow the chosen provider's defaults, so switching does not leave
      // last provider's model names behind pointing at the wrong service.
      base_url: choice.base_url,
      model: choice.model,
      ask_model: choice.ask_model,
    });

  const test = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const result = await api.test(payload);
      setMessage({ ok: result.ok, text: result.detail });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="settings">
      <SaveStatus status={status} />
      {models.length > 0 && (
        <datalist id={MODEL_LIST_ID}>
          {models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      )}
      <h1>设置</h1>
      <p className="muted small settings-note">改动即时生效，不用手动保存。</p>

      <section>
        <h2>外观</h2>
        <Row label="主题">
          <Segmented
            value={theme}
            options={[
              { value: "light", label: "浅色" },
              { value: "dark", label: "深色" },
            ]}
            onChange={setTheme}
          />
        </Row>

        <Row
          label="启动时的窗口"
          hint="下次启动生效"
        >
          <Segmented
            value={form.window_mode}
            options={[
              { value: "fullscreen", label: "全屏" },
              { value: "maximized", label: "最大化" },
            ]}
            onChange={(v) => setForm({ ...form, window_mode: v })}
          />
        </Row>
      </section>

      <section>
        <h2>阅读</h2>
        <Row
          label="被折叠的页眉页脚"
          hint="每页重复的页眉、页码会被折叠"
        >
          <Segmented
            value={store.showHidden ? "show" : "hide"}
            options={[
              { value: "hide", label: "隐藏" },
              { value: "show", label: "显示" },
            ]}
            onChange={(v) => store.setShowHidden(v === "show")}
          />
        </Row>
      </section>

      <section>
        <h2>课板</h2>
        <Row
          label="用户信息位置"
          hint="课件、术语表和缓存都在这个目录下"
        >
          <input
            className="field wide"
            value={form.user_data_path}
            onChange={(e) => setForm({ ...form, user_data_path: e.target.value })}
          />
        </Row>
        <p className="muted small">
          课板：<code>{form.library_path}</code>
          <br />
          用户配置：<code>{form.state_path}</code>
        </p>

        <Row
          label="导入方式"
          hint={
            form.import_modes.find((m) => m.id === form.import_mode)?.hint ?? ""
          }
        >
          <Segmented
            value={form.import_mode}
            options={form.import_modes.map((m) => ({
              value: m.id,
              label: m.label,
            }))}
            onChange={(v) => setForm({ ...form, import_mode: v })}
          />
        </Row>
        <p className="muted small">
          只作用于从「导入…」选择的文件；拖进窗口的文件一律复制。
        </p>
      </section>

      <section>
        <h2>翻译服务</h2>

        <Row label="服务商" hint="下面这些都用同一套 OpenAI 兼容协议">
          <select
            className="field"
            value={form.provider}
            onChange={(e) =>
              pickProvider(form.providers.find((p) => p.id === e.target.value)!)
            }
          >
            {form.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </Row>

        <Row label="API key">
          <div className="field-with-button">
            <input
              className="field wide"
              type={revealed ? "text" : "password"}
              // A browser autofilling a saved password here once overwrote a
              // working key on save. "new-password" is the one value they treat
              // as "do not fill this".
              autoComplete="new-password"
              name="classhelper-api-key"
              value={key}
              placeholder={
                provider?.keyless
                  ? "本地模型不需要"
                  : data.api_key_set
                    ? "已保存，留空表示不修改"
                    : "sk-…"
              }
              onChange={(e) => setKey(e.target.value)}
            />
            <button
              type="button"
              className="reveal"
              title={revealed ? "隐藏" : "显示已保存的 key"}
              onClick={async () => {
                if (revealed) {
                  setRevealed(false);
                  return;
                }
                if (!key && data.api_key_set) {
                  const { api_key } = await api.reveal();
                  setKey(api_key);
                }
                setRevealed(true);
              }}
            >
              {revealed ? "隐藏" : "显示"}
            </button>
          </div>
        </Row>

        <Row label="接口地址" hint="可换成自建或代理地址">
          <input
            className="field wide"
            value={form.base_url}
            placeholder="https://api.example.com/v1"
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          />
        </Row>

        <Row label="翻译模型" hint="调用量大，用便宜的">
          <ModelField
            value={form.model}
            models={models}
            onChange={(v) => setForm({ ...form, model: v })}
          />
        </Row>

        <Row label="提问模型" hint="一次一问，值得用会推理的">
          <ModelField
            value={form.ask_model}
            models={models}
            onChange={(v) => setForm({ ...form, ask_model: v })}
          />
        </Row>

        <Row label="目标语言" hint="译文语言，如 zh-CN、en、ja">
          <input
            className="field"
            value={form.target_lang}
            onChange={(e) => setForm({ ...form, target_lang: e.target.value })}
          />
        </Row>

        {/* Cached translations are keyed by model, because a different model
            gives different translations and serving the old one after a
            deliberate switch would be wrong. That is correct but not free, and
            silently re-translating a term's worth of decks is not something to
            discover from the bill. */}
        {data.configured &&
          (form.model !== data.model || form.target_lang !== data.target_lang) && (
            <p className="settings-warn">
              换翻译模型或目标语言后，已有译文作废，重开课件会重新翻译并计费。
            </p>
          )}

        <div className="settings-actions">
          <button className="ghost" disabled={testing} onClick={() => void test()}>
            {testing ? "测试中…" : "测试连接"}
          </button>
          {message && (
            <span className={message.ok ? "settings-ok" : "settings-bad"}>
              {message.text}
            </span>
          )}
        </div>

      </section>

      <section>
        <h2>隐私</h2>
        <p className="muted">
          课件文字会逐页发送到你配置的服务商。自己的课件无所谓，但如果要处理未发表的
          研究、公司内部材料或有版权的教材，请先想清楚——那些内容会离开这台机器。
          选「Ollama（本地）」则完全不出本机。
        </p>
      </section>
    </div>
  );
}

/** A quiet acknowledgement that a change landed, since there is no button. */
function SaveStatus({ status }: { status: "idle" | "saving" | "saved" | "error" }) {
  if (status === "idle") return null;
  return (
    <div className={`save-status ${status}`}>
      {status === "saving" ? "保存中…" : status === "saved" ? "已保存" : "保存失败"}
    </div>
  );
}

/**
 * A model name: chosen from what the provider reports, or typed if it does not
 * report anything. A datalist rather than a select, because not every
 * OpenAI-compatible service implements /models and the field has to keep
 * working when the list is empty.
 *
 * Both model fields share one list. Rendering a datalist per field put two
 * elements with the same id in the document, which is invalid and leaves which
 * one a field resolves to up to the browser.
 */
const MODEL_LIST_ID = "classhelper-models";

function ModelField({
  value,
  models,
  onChange,
}: {
  value: string;
  models: string[];
  onChange: (value: string) => void;
}) {
  return (
    <input
      className="field wide"
      list={models.length ? MODEL_LIST_ID : undefined}
      value={value}
      placeholder={models.length ? "从下拉里选，或直接填" : "模型名"}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/**
 * A segmented control whose highlight slides between options.
 *
 * The moving block is one element positioned by index rather than a background
 * swapped between buttons, so the motion is the state changing rather than an
 * animation played alongside it.
 */
function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  const index = Math.max(0, options.findIndex((o) => o.value === value));
  return (
    <div
      className="segmented"
      style={
        {
          "--count": options.length,
          "--index": index,
        } as React.CSSProperties
      }
    >
      <span className="segmented-thumb" aria-hidden />
      {options.map((option) => (
        <button
          key={option.value}
          className={option.value === value ? "on" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="setting-row">
      <div className="setting-label">
        <div>{label}</div>
        {hint && <div className="setting-hint muted">{hint}</div>}
      </div>
      <div className="setting-control">{children}</div>
    </div>
  );
}
