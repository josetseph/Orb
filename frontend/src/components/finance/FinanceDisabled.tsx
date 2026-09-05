import { Wallet } from "lucide-react";

/**
 * Shown when finance is switched off for the active KB. Deliberately not the
 * "not ready" panel: nothing is broken and there is no setup to finish, so the
 * only thing to say is where the switch lives.
 */
export function FinanceDisabled({ kbName }: { kbName: string }) {
  return (
    <div className="space-y-3 rounded-2xl border border-white/10 bg-black/40 p-6 backdrop-blur">
      <div className="flex items-start gap-3">
        <Wallet className="mt-0.5 h-5 w-5 shrink-0 text-white/40" />
        <div>
          <h2 className="text-lg font-medium">Finance is off for {kbName}</h2>
          <p className="mt-1 text-sm text-white/50">
            Nothing has been deleted — accounts and transactions for this
            knowledge base are kept, and come back if you turn finance on again.
          </p>
        </div>
      </div>
      <a
        href="/kb"
        className="inline-flex items-center gap-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-2 text-sm text-teal-200 transition hover:bg-teal-500/20"
      >
        Turn it on in Knowledge Bases →
      </a>
    </div>
  );
}
