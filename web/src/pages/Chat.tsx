import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { describeError } from "../api/errors";
import type { ChatsResponse, ChatSummary, HistoryMessage, MessageResponse, PendingAction } from "../api/types";
import { Icon } from "../components/Icon";
import { Markdown } from "../components/Markdown";
import { useDictation } from "../lib/dictation";
import { useVoice } from "../lib/voice";
import logo from "../assets/fox-logo.png";

const REPLY_TIMEOUT_MS = 60_000; // le serveur coupe à 120 s ; on abandonne avant
const SLOW_HINT_MS = 10_000;

type ActionState = "open" | "working" | "confirmed" | "cancelled";

interface ChatAction extends PendingAction {
  state: ActionState;
}

interface Bubble {
  id: string;
  role: "user" | "assistant";
  text: string;
  actions?: ChatAction[];
}

let localId = 0;
const nextId = () => `local-${++localId}`;

function chatTitle(chat: ChatSummary): string {
  return chat.title?.trim() || "Conversation";
}

function formatDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

export function Chat() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [privateMode, setPrivateMode] = useState(false);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState("");
  const [lastText, setLastText] = useState("");
  const [showList, setShowList] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [listenId, setListenId] = useState<string | null>(null);
  const voiceModeRef = useRef(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const sendRef = useRef<(message: string) => Promise<void>>(async () => undefined);
  const voice = useVoice();
  const dictation = useDictation((spoken) => {
    // En conversation orale, ce qui est dit part tout de suite ; sinon, le texte attend dans le champ.
    if (voiceModeRef.current) void sendRef.current(spoken);
    else setText((current) => (current.trim() ? `${current.trim()} ${spoken}` : spoken));
  });

  const title = chats.find((c) => c.id === activeId);

  const refreshChats = useCallback(async () => {
    const data = await api<ChatsResponse>("/chats");
    setChats(data.chats);
    setPrivateMode(data.private);
    return data;
  }, []);

  const loadMessages = useCallback(async (chatId: number) => {
    const data = await api<{ messages: HistoryMessage[] }>(`/chats/${chatId}/messages`);
    const history: Bubble[] = data.messages.map((m) => ({ id: m.id, role: m.role, text: m.text ?? "" }));
    // Les actions encore en attente (le rechargement de la page ne doit pas les faire disparaître).
    try {
      const open = await api<{ actions: PendingAction[] }>("/actions/pending");
      if (open.actions.length > 0) {
        history.push({
          id: nextId(),
          role: "assistant",
          text: open.actions.length > 1 ? "Ces actions attendent toujours votre confirmation :" : "Cette action attend toujours votre confirmation :",
          actions: open.actions.map((p) => ({ ...p, state: "open" as const })),
        });
      }
    } catch {
      // Sans cette liste, la conversation reste utilisable.
    }
    setBubbles(history);
  }, []);

  // Au premier affichage : la liste des conversations, puis la conversation active s'il y en a une.
  useEffect(() => {
    let cancelled = false;
    refreshChats()
      .then(async (data) => {
        if (cancelled || data.current === null) return;
        setActiveId(data.current);
        await loadMessages(data.current);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshChats, loadMessages]);

  useEffect(() => {
    let cancelled = false;
    api<{ enabled: boolean }>("/voice/config")
      .then((data) => {
        if (!cancelled) setVoiceEnabled(data.enabled === true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Le logo vibre au rythme du volume de la voix : le niveau (0 à 1) est copié dans une variable CSS.
  useEffect(() => {
    const el = mainRef.current;
    if (!el || voice.state !== "playing") {
      el?.style.setProperty("--voice-level", "0");
      return;
    }
    let raf = 0;
    const tick = () => {
      el.style.setProperty("--voice-level", voice.levelRef.current.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      el.style.setProperty("--voice-level", "0");
    };
  }, [voice.state, voice.levelRef]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [bubbles, sending]);

  async function openChat(chatId: number) {
    setError("");
    setShowList(false);
    try {
      await api(`/chats/${chatId}/open`, { method: "POST" });
      setActiveId(chatId);
      setPrivateMode(false);
      await loadMessages(chatId);
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function newChat() {
    setError("");
    setShowList(false);
    try {
      const data = await api<{ chat_id: number }>("/chats", { method: "POST" });
      setActiveId(data.chat_id);
      setBubbles([]);
      setPrivateMode(false);
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function removeChat(chatId: number) {
    setError("");
    try {
      await api(`/chats/${chatId}`, { method: "DELETE" });
      if (chatId === activeId) {
        setActiveId(null);
        setBubbles([]);
      }
      await refreshChats();
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function togglePrivate() {
    setError("");
    try {
      const data = await api<{ private: boolean }>("/private", { method: "POST", body: { on: !privateMode } });
      setPrivateMode(data.private);
      if (data.private) setBubbles([]);
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function send(message: string) {
    const clean = message.trim();
    if (!clean || sending) return;
    setError("");
    setSending(true);
    setSlow(false);
    setLastText(clean);
    setText("");
    setBubbles((current) => [...current, { id: nextId(), role: "user", text: clean }]);

    const controller = new AbortController();
    const abort = window.setTimeout(() => controller.abort(), REPLY_TIMEOUT_MS);
    const slowTimer = window.setTimeout(() => setSlow(true), SLOW_HINT_MS);
    try {
      const body: { text: string; chat_id?: number; voice?: boolean } = { text: clean };
      if (voiceModeRef.current) body.voice = true; // FOX répond court, fait pour être dit à voix haute
      if (activeId !== null && !privateMode) body.chat_id = activeId;
      const data = await api<MessageResponse>("/message", { method: "POST", body, signal: controller.signal });
      setPrivateMode(data.private);
      if (data.chat_id !== null) setActiveId(data.chat_id);
      setBubbles((current) => [
        ...current,
        {
          id: nextId(),
          role: "assistant",
          text: data.reply,
          actions: data.pending.map((p) => ({ ...p, state: "open" as const })),
        },
      ]);
      setLastText("");
      void refreshChats().catch(() => undefined);
      if (voiceModeRef.current) {
        // Conversation orale : FOX répond à voix haute, puis écoute de nouveau (sauf s'il attend une confirmation).
        const finished = await voice.play(data.reply);
        if (finished && voiceModeRef.current && data.pending.length === 0) dictation.start();
      }
    } catch (e) {
      // Le message a peut-être été traité : on garde le texte pour « Réessayer » sans le perdre.
      setError(describeError(e));
    } finally {
      window.clearTimeout(abort);
      window.clearTimeout(slowTimer);
      setSending(false);
      setSlow(false);
    }
  }

  async function decide(bubbleId: string, actionId: string, accept: boolean) {
    const update = (state: ActionState) =>
      setBubbles((current) =>
        current.map((b) =>
          b.id === bubbleId
            ? { ...b, actions: b.actions?.map((a) => (a.id === actionId ? { ...a, state } : a)) }
            : b,
        ),
      );
    setError("");
    update("working");
    try {
      const data = await api<{ reply: string }>(`/actions/${actionId}/${accept ? "confirm" : "cancel"}`, { method: "POST" });
      update(accept ? "confirmed" : "cancelled");
      setBubbles((current) => [...current, { id: nextId(), role: "assistant", text: data.reply }]);
    } catch (e) {
      update("open");
      setError(describeError(e));
    }
  }

  sendRef.current = send;

  function toggleVoiceMode() {
    if (voiceMode) {
      voiceModeRef.current = false;
      setVoiceMode(false);
      voice.stop();
      dictation.stop();
      return;
    }
    voiceModeRef.current = true;
    setVoiceMode(true);
    dictation.start();
  }

  async function listen(bubble: Bubble) {
    if (listenId === bubble.id) {
      voice.stop();
      setListenId(null);
      return;
    }
    setListenId(bubble.id);
    await voice.play(bubble.text);
    setListenId((current) => (current === bubble.id ? null : current));
  }

  const lastAssistantIndex = bubbles.map((b) => b.role).lastIndexOf("assistant");

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(text);
  }

  return (
    <div className="chat-layout">
      <section className={`chat-list${showList ? " open" : ""}`} aria-label="Conversations">
        <div className="chat-list-head">
          <h2>Conversations</h2>
          <button type="button" className="primary small" onClick={() => void newChat()}>
            Nouvelle
          </button>
        </div>
        <div className="chat-items">
          {chats.length === 0 && <p className="muted small-text">Aucune conversation enregistrée.</p>}
          {chats.map((chat) => (
            <div key={chat.id} className={`chat-item${chat.id === activeId ? " on" : ""}`}>
              <button type="button" className="chat-open" onClick={() => void openChat(chat.id)}>
                <span className="chat-name">{chatTitle(chat)}</span>
                <span className="chat-meta">
                  {formatDay(chat.updated_at)} · {chat.messages} message{chat.messages > 1 ? "s" : ""}
                </span>
              </button>
              <button
                type="button"
                className="icon-btn subtle"
                aria-label={`Supprimer la conversation ${chatTitle(chat)}`}
                onClick={() => void removeChat(chat.id)}
              >
                <Icon name="trash" size={18} />
              </button>
            </div>
          ))}
        </div>
        <div className="private-box">
          <div className="private-row">
            <strong>Mode privé</strong>
            <button
              type="button"
              role="switch"
              aria-checked={privateMode}
              aria-label="Mode privé"
              className={`switch${privateMode ? " on" : ""}`}
              onClick={() => void togglePrivate()}
            />
          </div>
          <span>En mode privé, rien n'est enregistré : ni historique, ni mémoire.</span>
        </div>
      </section>

      <main className="chat-main" ref={mainRef}>
        <header className="chat-head">
          <button type="button" className="icon-btn list-toggle" aria-label="Conversations" onClick={() => setShowList((v) => !v)}>
            <Icon name="list" />
          </button>
          <h1>{privateMode ? "Mode privé" : title ? chatTitle(title) : "Nouvelle conversation"}</h1>
          {privateMode && <span className="pill">Rien n'est enregistré</span>}
        </header>

        <div className="messages" aria-live="polite">
          {bubbles.length === 0 && !sending && (
            <div className="empty">
              <img src={logo} alt="" width={72} />
              <p>Que puis-je faire pour vous ?</p>
              <span>Demandez un rendez-vous, une tâche, un devis ou un e-mail.</span>
            </div>
          )}
          {bubbles.map((bubble, index) =>
            bubble.role === "user" ? (
              <div key={bubble.id} className="bubble user">
                {bubble.text}
              </div>
            ) : (
              <div
                key={bubble.id}
                className={`assistant${voice.state === "playing" && (listenId ? listenId === bubble.id : voiceMode && index === lastAssistantIndex) ? " speaking" : ""}`}
              >
                <img src={logo} alt="FOX" width={32} />
                <div className="assistant-body">
                  <div className="bubble-text"><Markdown text={bubble.text} /></div>
                  {voiceEnabled && bubble.text && (
                    <button
                      type="button"
                      className="listen-btn"
                      aria-label={listenId === bubble.id ? "Arrêter la lecture" : "Écouter la réponse"}
                      disabled={voice.state === "loading" && listenId === bubble.id}
                      onClick={() => void listen(bubble)}
                    >
                      <Icon name={listenId === bubble.id && voice.state !== "idle" ? "stop" : "speaker"} size={16} />
                      {listenId === bubble.id && voice.state === "loading" ? "Préparation…" : listenId === bubble.id && voice.state === "playing" ? "Arrêter" : "Écouter"}
                    </button>
                  )}
                  {bubble.actions?.map((action) => (
                    <ActionCard key={action.id} action={action} onDecide={(accept) => void decide(bubble.id, action.id, accept)} />
                  ))}
                </div>
              </div>
            ),
          )}
          {sending && (
            <div className="assistant" role="status">
              <img src={logo} alt="FOX" width={32} />
              <div className="assistant-body">
                <div className="thinking">
                  FOX réfléchit<span className="dots" aria-hidden="true"><i /><i /><i /></span>
                </div>
                {slow && <span className="muted small-text">Cela prend plus de temps que d'habitude…</span>}
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {error && (
          <div className="banner-error inline" role="alert">
            <Icon name="alert" />
            <span>{error}</span>
            {lastText && (
              <button type="button" className="link-btn" onClick={() => void send(lastText)}>
                Réessayer
              </button>
            )}
          </div>
        )}

        <form className="composer" onSubmit={onSubmit} aria-label="Écrire à FOX">
          {(voiceMode || voice.state !== "idle") && (
            <div className={`voice-stage${voice.state === "playing" ? " speaking" : ""}`} role="status">
              <img className="voice-logo" src={logo} alt="" width={56} />
              <span>
                {voice.state === "loading"
                  ? voiceMode ? "FOX prépare sa réponse…" : "FOX prépare sa voix…"
                  : voice.state === "playing" ? "FOX parle…"
                  : sending ? "FOX réfléchit…"
                  : dictation.listening ? "Je vous écoute, parlez…" : "Conversation orale active"}
              </span>
              {voice.state !== "idle" && (
                <button type="button" className="listen-btn" aria-label="Couper la voix" onClick={() => { voice.stop(); setListenId(null); }}>
                  <Icon name="stop" size={16} /> Couper
                </button>
              )}
            </div>
          )}
          <div className="composer-box">
            <label htmlFor="message" className="sr-only">
              Message
            </label>
            <input
              id="message"
              type="text"
              placeholder="Écrivez à FOX…"
              autoComplete="off"
              maxLength={4000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            {dictation.supported && voiceEnabled && (
              <button
                type="button"
                className={voiceMode ? "voice-toggle on" : "voice-toggle"}
                aria-label={voiceMode ? "Arrêter la conversation orale" : "Démarrer la conversation orale"}
                aria-pressed={voiceMode}
                onClick={toggleVoiceMode}
              >
                <Icon name="wave" />
              </button>
            )}
            {dictation.supported && (
              <button
                type="button"
                className={dictation.listening ? "mic on" : "mic"}
                aria-label={dictation.listening ? "Arrêter la dictée" : "Dicter un message"}
                aria-pressed={dictation.listening}
                onClick={() => (dictation.listening ? dictation.stop() : dictation.start())}
              >
                <Icon name="mic" />
              </button>
            )}
            <button type="submit" className="send" aria-label="Envoyer" disabled={sending || !text.trim()}>
              <Icon name="send" />
            </button>
          </div>
          {dictation.problem && <span className="banner error" role="alert">{dictation.problem}</span>}
          {voice.problem && <span className="banner error" role="alert">{voice.problem}</span>}
          <span className="muted small-text">Les actions importantes demandent toujours votre confirmation.</span>
        </form>
      </main>
    </div>
  );
}

function ActionCard({ action, onDecide }: { action: ChatAction; onDecide: (accept: boolean) => void }) {
  const done = action.state === "confirmed" || action.state === "cancelled";
  return (
    <div className="action-card">
      <span className="action-label">{done ? (action.state === "confirmed" ? "Action confirmée" : "Action annulée") : "Action en attente"}</span>
      <div className="action-summary">{action.summary}</div>
      {!done && (
        <div className="action-buttons">
          <button type="button" className="primary" disabled={action.state === "working"} onClick={() => onDecide(true)}>
            Confirmer
          </button>
          <button type="button" className="secondary-btn" disabled={action.state === "working"} onClick={() => onDecide(false)}>
            Annuler
          </button>
        </div>
      )}
      {!done && <span className="muted small-text">Expire dans 10 minutes.</span>}
    </div>
  );
}
