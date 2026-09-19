import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, X } from "lucide-react";
import { api } from "@/lib/api";

/** Bottom toast shown while no AI is configured (notes still work). */
export function AiLimitedBanner() {
  const [show, setShow] = useState(false);
  const [needsDownload, setNeedsDownload] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSetupStatus()
      .then((s) => {
        if (cancelled) return;
        setShow(!s.ai_configured);
        setNeedsDownload(Boolean(s.needs_model_download));
      })
      .catch(() => {
        if (!cancelled) setShow(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!show) return null;

  return (
    <div className="fixed bottom-[22px] left-1/2 z-[70] flex max-w-[640px] -translate-x-1/2 animate-rise items-center gap-2.5 rounded-md bg-surface px-3 py-2 text-[12.5px] shadow-md">
      <AlertTriangle className="h-4 w-4 shrink-0 text-accent" />
      <span className="flex-1">
        {needsDownload
          ? "Local models are still downloading — chat and ingest wait until they finish."
          : "No model configured yet — notes work; chat, ingest and the graph need one."}
      </span>
      <Link to="/models" className="font-medium text-accent no-underline">
        {needsDownload ? "Continue download" : "Choose a model"}
      </Link>
      <button
        type="button"
        onClick={() => setShow(false)}
        className="grid h-6 w-6 place-items-center rounded text-n-500 hover:bg-n-900"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
