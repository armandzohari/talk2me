import { useState, useCallback, useEffect } from "react";
import { LiveKitRoom } from "@livekit/components-react";
import VoiceAgent from "./components/VoiceAgent";

const AGENT_NAME = "Armando";
const PHOTO_URL = "/bugs bunny chews.gif";
const GIFS = ["/bugs bunny chews.gif", "/bugs bunny drinks.gif"];

// Preload both GIFs at module load and keep references so they
// are not garbage-collected before the browser finishes fetching.
const _preloaded = GIFS.map((src) => { const img = new Image(); img.src = src; return img; });

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleStart = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: "visitor" }),
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setSession(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleDisconnect = useCallback(() => {
    setSession(null);
  }, []);

  if (session) {
    return (
      <LiveKitRoom
        token={session.token}
        serverUrl={session.url}
        connect={true}
        audio={true}
        video={false}
        onDisconnected={handleDisconnect}
      >
        <VoiceAgent agentName={AGENT_NAME} photoUrl={PHOTO_URL} onEnd={handleDisconnect} />
      </LiveKitRoom>
    );
  }

  return <Landing onStart={handleStart} loading={loading} error={error} />;
}

function Landing({ onStart, loading, error }) {
  const [hovered, setHovered] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // Fade in on load
    requestAnimationFrame(() => setMounted(true));
  }, []);

  return (
    <>
      <style>{css}</style>
      <div className={`page ${mounted ? "page--visible" : ""}`}>
        <div className="photo-wrap">
          <img
            src={PHOTO_URL}
            alt={AGENT_NAME}
            className="photo"
            onError={(e) => {
              // Graceful fallback if photo not added yet
              e.target.style.display = "none";
              e.target.nextSibling.style.display = "flex";
            }}
          />
          <div className="photo-fallback" style={{ display: "none" }}>
            {AGENT_NAME[0]}
          </div>
        </div>

        <div className="bottom">
          <h1 className="name">{AGENT_NAME}</h1>

          {error && <p className="error">{error}</p>}

          <button
            className={`cta ${hovered ? "cta--hovered" : ""} ${loading ? "cta--loading" : ""}`}
            onClick={onStart}
            disabled={loading}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            {loading ? (
              <span className="spinner" />
            ) : (
              <>
                <span className="cta-icon">◉</span>
                Talk to me
              </>
            )}
          </button>
        </div>
      </div>
    </>
  );
}

const css = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: #000;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    height: 100vh;
    overflow: hidden;
  }

  .page {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    height: 100vh;
    opacity: 0;
    transition: opacity 0.6s ease;
    position: relative;
  }

  .page--visible {
    opacity: 1;
  }

  /* Photo fills the upper portion of the viewport */
  .photo-wrap {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: flex-start;
    justify-content: center;
  }

  .photo {
    width: 100%;
    height: 100vh;
    object-fit: cover;
    object-position: top center;
    display: block;
    /* subtle vignette so the bottom text stays readable */
    mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
    -webkit-mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
  }

  .photo-fallback {
    width: 180px;
    height: 180px;
    border-radius: 50%;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    align-items: center;
    justify-content: center;
    font-size: 5rem;
    font-weight: 700;
    color: #fff;
    margin-top: 10vh;
  }

  /* Bottom overlay */
  .bottom {
    position: relative;
    z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1.25rem;
    padding-bottom: 10vh;
    width: 100%;
  }

  .name {
    font-size: clamp(2rem, 6vw, 4rem);
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #fff;
    text-shadow: 0 2px 40px rgba(0,0,0,0.8);
  }

  .error {
    color: #f87171;
    font-size: 0.875rem;
  }

  /* CTA button */
  .cta {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    background: rgba(255, 255, 255, 0.12);
    border: 1.5px solid rgba(255, 255, 255, 0.3);
    color: #fff;
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    padding: 0.85rem 2.25rem;
    border-radius: 999px;
    cursor: pointer;
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    transition: background 0.2s, border-color 0.2s, transform 0.15s, box-shadow 0.2s;
    min-width: 200px;
    justify-content: center;
  }

  .cta--hovered {
    background: rgba(255, 255, 255, 0.22);
    border-color: rgba(255, 255, 255, 0.6);
    transform: translateY(-2px);
    box-shadow: 0 8px 40px rgba(0,0,0,0.4);
  }

  .cta--loading {
    opacity: 0.7;
    cursor: wait;
  }

  .cta:disabled {
    cursor: wait;
  }

  .cta-icon {
    font-size: 0.85rem;
    opacity: 0.8;
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 0.5; }
    50% { opacity: 1; }
  }

  /* Loading spinner */
  .spinner {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: inline-block;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  @media (max-width: 480px) {
    .name { font-size: 2.25rem; }
    .cta { font-size: 1rem; padding: 0.75rem 1.75rem; }
  }
`;
