import { useState, useEffect } from "react";
import {
  useLocalParticipant,
  useRemoteParticipants,
  useRoomContext,
} from "@livekit/components-react";

export default function VoiceAgent({ agentName, photoUrl, onEnd }) {
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const remoteParticipants = useRemoteParticipants();
  const [muted, setMuted] = useState(false);
  const [agentSpeaking, setAgentSpeaking] = useState(false);

  const agentParticipant = remoteParticipants.find(
    (p) => p.identity === "talk2me-agent"
  );

  useEffect(() => {
    if (!agentParticipant) return;
    const handler = (speaking) => setAgentSpeaking(speaking);
    agentParticipant.on("isSpeakingChanged", handler);
    return () => agentParticipant.off("isSpeakingChanged", handler);
  }, [agentParticipant]);

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

        <div className={`avatar-ring ${agentSpeaking ? "avatar-ring--speaking" : ""}`}>
          <img
            src={photoUrl}
            alt={agentName}
            className="avatar-photo"
            onError={(e) => {
              e.target.style.display = "none";
              e.target.nextSibling.style.display = "flex";
            }}
          />
          <div className="avatar-fallback" style={{ display: "none" }}>{agentName[0]}</div>
        </div>

        <h2 className="call-name">{agentName}</h2>
        <p className="call-status">{statusText}</p>

        {/* Sound wave bars — animate when agent is speaking */}
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
      </div>
    </>
  );
}

const css = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body { background: #000; }

  .call-page {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    height: 100vh;
    background: #000;
    color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }

  .avatar-ring {
    width: 160px;
    height: 160px;
    border-radius: 50%;
    border: 3px solid rgba(255,255,255,0.15);
    padding: 4px;
    transition: border-color 0.3s, box-shadow 0.3s;
  }

  .avatar-ring--speaking {
    border-color: rgba(255,255,255,0.8);
    box-shadow: 0 0 0 6px rgba(255,255,255,0.08), 0 0 40px rgba(255,255,255,0.2);
  }

  .avatar-photo {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    object-fit: cover;
    object-position: top;
    display: block;
  }

  .avatar-fallback {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    align-items: center;
    justify-content: center;
    font-size: 3.5rem;
    font-weight: 700;
    color: #fff;
  }

  .call-name {
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    margin-top: 0.5rem;
  }

  .call-status {
    color: rgba(255,255,255,0.5);
    font-size: 0.95rem;
    min-height: 1.4rem;
  }

  /* Sound wave */
  .wave {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 32px;
    margin: 0.5rem 0;
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
    margin-top: 1.5rem;
  }

  .ctrl-btn {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    border: 1.5px solid rgba(255,255,255,0.2);
    background: rgba(255,255,255,0.08);
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
    background: rgba(255,255,255,0.18);
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
`;
