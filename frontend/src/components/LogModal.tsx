import { useEffect, useState } from "react";
import * as api from "../api";

export function LogModal({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [text, setText] = useState("loading…");
  useEffect(() => {
    let live = true;
    fetch(api.logUrl(sessionId))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`log ${r.status}`))))
      .then((t) => live && setText(t))
      .catch((e) => live && setText(String(e)));
    return () => {
      live = false;
    };
  }, [sessionId]);
  return (
    <div className="modal-backdrop" role="dialog" aria-label="session log">
      <div className="modal log-modal">
        <button onClick={onClose}>close</button>
        <pre>{text}</pre>
      </div>
    </div>
  );
}
