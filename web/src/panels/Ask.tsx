/**
 * The question panel.
 *
 * Built on assistant-ui's primitives rather than its prebuilt Thread: the
 * primitives carry the parts that are genuinely hard -- streaming, message
 * branching, edit-and-resend, cancellation, autoscroll -- while leaving the
 * styling to us, which keeps this looking like the rest of the app instead of
 * importing a second design system for one panel.
 *
 * The point of the panel is that a question needs no preamble. Whatever
 * sentence is selected in the reader, plus its slide and the course glossary,
 * is attached to every question automatically, so "why?" is a complete question.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
  type ChatModelAdapter,
  type ThreadHistoryAdapter,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import {
  askStream,
  chats as chatsApi,
  type ChatItem,
  type ChatSummary,
} from "../api";
import { useStore } from "../store";
import { askPages, pageRanges, selected as selectedUnits } from "../units";

/**
 * One conversation per deck.
 *
 * assistant-ui keeps a thread's messages inside its runtime hook, so a thread
 * lives exactly as long as the component holding it. Swapping the panel's
 * contents when the active deck changes would therefore throw the conversation
 * away -- and worse, showing one deck's thread while another is on screen puts
 * answers about last week's lecture under this week's slides.
 *
 * So every open deck gets its own mounted thread, and all but the active one
 * are hidden rather than unmounted.
 */
export function Ask() {
  const store = useStore();
  if (store.decks.length === 0) {
    return <div className="selection-bar muted">还没有打开课件</div>;
  }
  return (
    <div className="ask-host">
      {store.decks.map((state) => (
        <div
          key={state.deck.id}
          className="ask-slot"
          hidden={state.deck.id !== store.activeId}
        >
          <DeckThread deckId={state.deck.id} />
        </div>
      ))}
    </div>
  );
}

/**
 * One deck's conversations.
 *
 * Transcripts are kept beside the deck on disk, so a question asked last week
 * is still there this week. Switching conversation remounts the thread, which
 * is what makes the runtime reload it -- assistant-ui reads its history once,
 * when the runtime is created.
 */
function DeckThread({ deckId }: { deckId: string }) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    const { chats: list } = await chatsApi.list(deckId);
    setChats(list);
    return list;
  }, [deckId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const list = await refresh().catch(() => [] as ChatSummary[]);
      if (cancelled) return;
      // Resume the most recent conversation; start one only when there is
      // nothing to resume, so opening a deck does not pile up empty threads.
      if (list.length > 0) setActive(list[0]!.id);
      else {
        const fresh = await chatsApi.create(deckId).catch(() => null);
        if (fresh && !cancelled) {
          setChats([fresh]);
          setActive(fresh.id);
        }
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [deckId, refresh]);

  const newChat = async () => {
    const fresh = await chatsApi.create(deckId);
    await refresh();
    setActive(fresh.id);
  };

  const remove = async (id: string) => {
    await chatsApi.remove(deckId, id);
    const list = await refresh();
    if (active === id) {
      // Never leave the panel with no thread: fall back to the next one, or
      // start a fresh one if that was the last.
      if (list.length > 0) setActive(list[0]!.id);
      else await newChat();
    }
  };

  if (!ready || !active) {
    return <div className="selection-bar muted">读取对话…</div>;
  }

  return (
    <div className="ask">
      <ChatBar
        chats={chats}
        active={active}
        onSelect={setActive}
        onNew={() => void newChat()}
        onDelete={(id) => void remove(id)}
      />
      <SelectionBar deckId={deckId} />
      <Conversation
        key={active}
        deckId={deckId}
        chatId={active}
        onChanged={() => void refresh()}
      />
    </div>
  );
}

function ChatBar({
  chats,
  active,
  onSelect,
  onNew,
  onDelete,
}: {
  chats: ChatSummary[];
  active: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = chats.find((c) => c.id === active);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  return (
    <div className="chat-bar">
      <button
        className="chat-current"
        title="切换对话"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        <span className="chat-title">{current?.title ?? "对话"}</span>
        <span className="chat-caret">⌄</span>
      </button>
      <button className="link" onClick={onNew} title="新建对话">
        + 新对话
      </button>

      {open && (
        <div className="menu chat-menu" onClick={(e) => e.stopPropagation()}>
          {chats.length === 0 && <div className="pad muted">还没有对话</div>}
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={`chat-entry${chat.id === active ? " on" : ""}`}
            >
              <button
                className="chat-entry-name"
                onClick={() => {
                  onSelect(chat.id);
                  setOpen(false);
                }}
              >
                <span className="chat-entry-title">{chat.title}</span>
                <span className="chat-entry-count muted">{chat.count} 条</span>
              </button>
              <button
                className="chat-delete"
                title="删除这段对话"
                onClick={() => {
                  if (window.confirm(`删除对话「${chat.title}」？`)) onDelete(chat.id);
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Conversation({
  deckId,
  chatId,
  onChanged,
}: {
  deckId: string;
  chatId: string;
  onChanged: () => void;
}) {
  const store = useStore();
  const active = store.decks.find((d) => d.deck.id === deckId) ?? null;
  const currentPage = active?.currentPage ?? 0;
  const chosen = active ? selectedUnits(active.deck, active.selection.keys) : [];

  // The adapter is created once, but every run needs the selection as it is at
  // the moment the question is sent, not as it was when the panel mounted.
  const context = useRef({
    deckId: "",
    page: 0,
    sentenceIds: [] as string[],
    pages: [] as number[],
  });
  context.current = {
    deckId,
    // A question about a selection is a question about the page it is on.
    page: chosen[0]?.page ?? currentPage,
    sentenceIds: chosen.map((u) => u.sentence.id),
    pages: active?.context.pages ?? [],
  };

  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({ messages, abortSignal }) {
        const { deckId: id, page, sentenceIds, pages } = context.current;
        if (!id) throw new Error("先打开一份课件再提问。");

        const asText = (m: (typeof messages)[number]) =>
          m.content
            .filter((part): part is { type: "text"; text: string } => part.type === "text")
            .map((part) => part.text)
            .join("");

        const question = asText(messages[messages.length - 1]!);
        const history = messages.slice(0, -1).map((m) => ({
          role: m.role,
          content: asText(m),
        }));

        let text = "";
        for await (const chunk of askStream(
          id,
          { page, question, sentence_ids: sentenceIds, pages, history },
          abortSignal,
        )) {
          text += chunk;
          yield { content: [{ type: "text", text }] };
        }
      },
    }),
    [],
  );

  // Persistence. `load` runs when the runtime is created -- which is why
  // switching conversation remounts this component -- and `append` writes each
  // message as it settles, so an answer survives the window closing mid-run.
  const history = useMemo<ThreadHistoryAdapter>(
    () => ({
      async load() {
        try {
          const { messages } = await chatsApi.read(deckId, chatId);
          return { messages: messages as never[] };
        } catch {
          return { messages: [] };
        }
      },
      async append(item) {
        await chatsApi
          .append(deckId, chatId, item as unknown as ChatItem)
          .catch(() => {});
        onChanged();
      },
    }),
    [deckId, chatId, onChanged],
  );

  const runtime = useLocalRuntime(adapter, { adapters: { history } });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="thread-viewport" autoScroll>
          <ThreadPrimitive.Empty>
            <div className="thread-empty">
              <p>在左边点选一句，然后直接问。</p>
              <p className="muted">
                提问会自动带上整页原文与译文、以及本课程的术语表。
              </p>
            </div>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages
            components={{ UserMessage, AssistantMessage }}
          />
          {/* A reasoning model works through the problem before it writes
              anything, which is a long silence to sit through. Say what is
              happening rather than showing an empty bubble that reads as a
              hang. */}
          <ThreadPrimitive.If running>
            <div className="thinking">思考中…</div>
          </ThreadPrimitive.If>
        </ThreadPrimitive.Viewport>

        <ComposerPrimitive.Root className="composer">
          <ComposerPrimitive.Input
            className="composer-input"
            placeholder="问点什么…"
            autoFocus
          />
          <ThreadPrimitive.If running={false}>
            <ComposerPrimitive.Send className="composer-send">
              发送
            </ComposerPrimitive.Send>
          </ThreadPrimitive.If>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel className="composer-send cancel">
              停止
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

/** Shows what the next question will be attached to, so it is never a guess. */
function SelectionBar({ deckId }: { deckId: string }) {
  const store = useStore();
  const active = store.decks.find((d) => d.deck.id === deckId) ?? null;
  if (!active) {
    return <div className="selection-bar muted">还没有打开课件</div>;
  }

  const chosen = selectedUnits(active.deck, active.selection.keys);
  const page = (chosen[0]?.page ?? active.currentPage) + 1;
  const context = active.context.pages;

  const sending = pageRanges(askPages(context, page - 1));

  const scope = (
    <span className="scope" title="在「章节」里 ⌘ 点加选、shift 点连选，可指定要发送的页">
      上下文：第 {sending} 页
      {context.length > 0 && (
        <button className="link" onClick={() => store.clearContext(active.deck.id)}>
          只发本页
        </button>
      )}
    </span>
  );

  if (!chosen.length) {
    return (
      <div className="selection-bar">
        <span className="muted">未选中句子 · 整页为上下文</span>
        <span className="spacer" />
        {scope}
      </div>
    );
  }

  return (
    <div className="selection-bar">
      <span className="tag">
        第 {page} 页{chosen.length > 1 && ` · ${chosen.length} 句`}
      </span>
      <span className="quote">
        {chosen.length === 1
          ? chosen[0]!.sentence.text
          : chosen.map((u) => u.sentence.text).join(" ⏎ ")}
      </span>
      {scope}
      <button className="link" onClick={() => store.clearSelection(active.deck.id)}>
        取消
      </button>
    </div>
  );
}

function UserMessage() {
  return (
    <div className="msg user">
      <MessagePrimitive.Content />
    </div>
  );
}

/**
 * Answers come back as Markdown -- bold terms, lists, the occasional table --
 * so they are rendered as Markdown. Left as plain text the asterisks show up
 * literally, which looks like a bug in the answer rather than in the renderer.
 */
function AssistantMessage() {
  return (
    <div className="msg assistant">
      <MessagePrimitive.Content components={{ Text: MarkdownText }} />
    </div>
  );
}

function MarkdownText() {
  return <MarkdownTextPrimitive />;
}
