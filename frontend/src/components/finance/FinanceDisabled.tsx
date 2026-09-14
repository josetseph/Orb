import { Wallet } from "lucide-react";

/**
 * Shown when finance is switched off for the active KB. Deliberately not the
 * "not ready" panel: nothing is broken and there is no setup to finish, so the
 * only thing to say is where the switch lives.
 */
export function FinanceDisabled({ kbName }: { kbName: string }) {
  return (
    <div className="card-outline max-w-[560px] space-y-3 p-5">
      <div className="flex items-start gap-3">
        <Wallet className="mt-0.5 h-[18px] w-[18px] shrink-0 text-n-500" />
        <div>
          <h2 className="text-[15px] font-medium">Finance is off for {kbName}</h2>
          <p className="mt-1 text-[12.5px] text-n-500">
            Nothing has been deleted — accounts and transactions for this
            workspace are kept, and come back if you turn finance on again.
          </p>
        </div>
      </div>
      <a href="/kb" className="btn btn-primary no-underline">
        Turn it on in Workspace settings
      </a>
    </div>
  );
}
