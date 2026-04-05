import { useState, useEffect, useRef } from "react";
import {
  useLocalParticipant,
  useRemoteParticipants,
  useRoomContext,
} from "@livekit/components-react";
import { RoomEvent } from "livekit-client";

const GIFS = ["/bugs bunny chews.gif", "/bugs bunny drinks.gif"];

export default function VoiceAgent({ agentName, onEnd }) {
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const remoteParticipants = useRemoteParticipants();
  const [muted, setMuted] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [transcript, setTranscript] = useState([]);
  const [copied, setCopied] = useState(false);
  const transcriptEndRef = useRef(null);
  const [gifIndex, setGifIndex] = useState(() => Math.round(Math.random()));
  const [gifReady, setGifReady] = useState(false);

  const agentParticipant = remoteParticipants.find(
    (p) => p.identity === "talk2me-agent"
  );

  useEffect(() => {
    if (!agentParticipant) return;
    const handler = (speaking) => setAgentSpeaking(speaking);
    agentParticipant.on("isSpeakingChanged", handler);
    return () => agentParticipant.off("isSpeakingChanged", handler);
  }, [agentParticipant]);

  // Listen for transcript data packets from the backend
  useEffect(() => {
    if (!room) return;
    const handler = (payload, participant, kind, topic) => {
      if (topic && topic !== "transcript") return;
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload));
        if (msg.speaker && msg.text) {
          setTranscript(prev => [...prev, { ...msg, id: Date.now() + Math.random() }]);
        }
      } catch (_) {}
    };
    room.on(RoomEvent.DataReceived, handler);
    return () => room.off(RoomEvent.DataReceived, handler);
  }, [room]);

  // Swap GIF each time Armando finishes a phrase
  useEffect(() => {
    if (transcript.length === 0) return;
    const last = transcript[transcript.length - 1];
    if (last.speaker === "agent") {
      setGifReady(false);
      setGifIndex(i => (i + 1) % GIFS.length);
    }
  }, [transcript]);

  // Auto-scroll transcript to bottom on new messages
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  const copyTranscript = () => {
    const text = transcript
      .map(m => `${m.speaker === "agent" ? agentName : "Visitor"}: ${m.text}`)
      .join("\n\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const toggleMute = async () => {
    await localParticipant.setMicrophoneEnabled(muted);
    setMuted(!muted);
  };

  const handleEnd = async () => {
    await room.disconnect();
    onEnd();
  };

  const statusText = agentParticipant
    ? agentSpeaking ? "Speaking…" : "Listening…"
    : "Connecting…";

  return (
    <>
      <style>{css}</style>
      <div className="call-page">
        {/* Render hidden audio for agent tracks */}
        {agentParticipant &&
          Array.from(agentParticipant.audioTrackPublications.values()).map(
            (pub) =>
              pub.track ? (
                <audio
                  key={pub.trackSid}
                  ref={(el) => el && pub.track.attach(el)}
                  autoPlay
                />
              ) : null
          )}

        {/* Full-screen GIF background — swaps on each Armando phrase */}
        <img
          key={gifIndex}
          src={GIFS[gifIndex]}
          alt={agentName}
          className="bg-gif"
          style={{ opacity: gifReady ? 1 : 0 }}
          onLoad={() => setGifReady(true)}
          onError={() => {
            // If the new gif fails, fall back to the other one
            setGifIndex(i => (i + 1) % GIFS.length);
          }}
        />

        {/* Bottom overlay */}
        <div className="bottom">
          <h2 className="call-name">{agentName}</h2>
          <p className="call-status">{statusText}</p>

          {/* Sound wave bars */}
          <div className={`wave ${agentSpeaking ? "wave--active" : ""}`}>
            {[...Array(5)].map((_, i) => (
              <div key={i} className="wave-bar" style={{ animationDelay: `${i * 0.1}s` }} />
            ))}
          </div>

          <div className="controls">
            <button
              className={`ctrl-btn ${muted ? "ctrl-btn--muted" : ""}`}
              onClick={toggleMute}
              title={muted ? "Unmute" : "Mute"}
            >
              {muted ? "🔇" : "🎙️"}
            </button>
            <button className="ctrl-btn ctrl-btn--end" onClick={handleEnd}>
              End
            </button>
          </div>

          {/* Transcript panel */}
          <div className="transcript-wrap">
            <div className="transcript-header">
              <span className="transcript-title">Transcript</span>
              <button
                className={`copy-btn ${copied ? "copy-btn--done" : ""}`}
                onClick={copyTranscript}
                title="Copy transcript"
                disabled={transcript.length === 0}
              >
                {copied ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                )}
              </button>
            </div>
            <div className="transcript-body">
              {transcript.length === 0 ? (
                <p className="transcript-empty">Conversation transcript will appear here…</p>
              ) : (
                transcript.map(msg => (
                  <div
                    key={msg.id}
                    className={`transcript-msg ${msg.speaker === "agent" ? "transcript-msg--agent" : "transcript-msg--visitor"}`}
                  >
                    <span className="transcript-speaker">
                      {msg.speaker === "agent" ? agentName : "You"}
                    </span>
                    <span className="transcript-text">{msg.text}</span>
                  </div>
                ))
              )}
              <div ref={transcriptEndRef} />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

const css = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body { background: #000; }

  .call-page {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    min-height: 100vh;
    color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    overflow: hidden;
  }

  /* Full-screen GIF — same treatment as landing page */
  .bg-gif {
    position: fixed;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top center;
    mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
    -webkit-mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
    z-index: 0;
    transition: opacity 0.3s ease;
  }

  /* Bottom overlay sits above the GIF */
  .bottom {
    position: relative;
    z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 0 1rem 8vh;
    width: 100%;
  }

  .call-name {
    font-size: clamp(2rem, 6vw, 3.5rem);
    font-weight: 700;
    letter-spacing: -0.02em;
    text-shadow: 0 2px 40px rgba(0,0,0,0.8);
  }

  .call-status {
    color: rgba(255,255,255,0.6);
    font-size: 0.95rem;
    min-height: 1.4rem;
  }

  /* Sound wave */
  .wave {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 32px;
  }

  .wave-bar {
    width: 4px;
    height: 6px;
    border-radius: 2px;
    background: rgba(255,255,255,0.3);
    transition: background 0.3s;
  }

  .wave--active .wave-bar {
    background: #fff;
    animation: wave 0.8s ease-in-out infinite alternate;
  }

  @keyframes wave {
    from { height: 6px; }
    to   { height: 28px; }
  }

  /* Controls */
  .controls {
    display: flex;
    gap: 1rem;
    margin-top: 0.5rem;
  }

  .ctrl-btn {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    border: 1.5px solid rgba(255,255,255,0.2);
    background: rgba(255,255,255,0.1);
    color: #fff;
    font-size: 1.4rem;
    cursor: pointer;
    backdrop-filter: blur(8px);
    transition: background 0.2s, transform 0.15s;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .ctrl-btn:hover {
    background: rgba(255,255,255,0.2);
    transform: scale(1.05);
  }

  .ctrl-btn--muted {
    background: rgba(248,113,113,0.2);
    border-color: rgba(248,113,113,0.5);
  }

  .ctrl-btn--end {
    background: rgba(248,113,113,0.15);
    border-color: rgba(248,113,113,0.4);
    font-size: 0.85rem;
    font-weight: 600;
    letter-spacing: 0.05em;
  }

  .ctrl-btn--end:hover {
    background: rgba(248,113,113,0.35);
  }

  /* Transcript panel */
  .transcript-wrap {
    width: 100%;
    max-width: 560px;
    margin-top: 0.5rem;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    background: rgba(0,0,0,0.45);
    backdrop-filter: blur(12px);
    overflow: hidden;
  }

  .transcript-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }

  .transcript-title {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.4);
  }

  .copy-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.12);
    background: transparent;
    color: rgba(255,255,255,0.45);
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }

  .copy-btn:hover:not(:disabled) {
    background: rgba(255,255,255,0.1);
    color: #fff;
  }

  .copy-btn:disabled { opacity: 0.3; cursor: default; }
  .copy-btn--done { color: #4ade80; border-color: rgba(74,222,128,0.3); }

  .transcript-body {
    padding: 0.75rem 1rem 1rem;
    max-height: 220px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,0.15) transparent;
  }

  .transcript-body::-webkit-scrollbar { width: 4px; }
  .transcript-body::-webkit-scrollbar-track { background: transparent; }
  .transcript-body::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 2px; }

  .transcript-empty {
    color: rgba(255,255,255,0.25);
    font-size: 0.85rem;
    text-align: center;
    padding: 1.5rem 0;
  }

  .transcript-msg { display: flex; flex-direction: column; gap: 0.2rem; }

  .transcript-speaker {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .transcript-msg--agent .transcript-speaker { color: rgba(139,92,246,0.9); }
  .transcript-msg--visitor .transcript-speaker { color: rgba(255,255,255,0.45); }

  .transcript-text {
    font-size: 0.9rem;
    line-height: 1.55;
    color: rgba(255,255,255,0.85);
  }

  .transcript-msg--visitor .transcript-text { color: rgba(255,255,255,0.65); }
`;
