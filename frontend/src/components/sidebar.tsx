import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Check,
  ChevronsUpDown,
  Files,
  MessageCircle,
  Network,
  PanelLeft,
  Plus,
  Search,
  Settings,
  Wallet,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { kbSlug, useKB } from "@/lib/kb-context";
import { ActivityStatus } from "@/components/system-status-indicator";
import { openCommandPalette } from "@/components/command-palette";
import { availableUpdate } from "@/lib/desktop";

const SWATCHES = ["bg-accent-700", "bg-accent-800", "bg-n-700", "bg-accent-600"];

function vaultFolderName(path?: string): string {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Close a popover when clicking outside of it or pressing Escape. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

const SETTINGS_PATHS = ["/kb", "/models", "/setup", "/settings"];

/** Checked once at launch; installing always waits for a click in About. */
function useUpdateVersion() {
  const [update, setUpdate] = useState<string | null>(null);
  useEffect(() => {
    availableUpdate().then((u) => setUpdate(u?.version ?? null), () => {});
  }, []);
  return update;
}

function SettingsButton({ className }: { className?: string }) {
  const { pathname } = useLocation();
  const update = useUpdateVersion();
  const active = SETTINGS_PATHS.some((p) => pathname.startsWith(p));
  return (
    <Link
      to={update ? "/settings" : "/kb"}
      data-tip={update ? `Settings · Orb ${update} is available` : "Settings"}
      aria-label="Settings"
      className={cn(
        "relative grid h-8 w-8 shrink-0 place-items-center rounded-md hover:bg-n-900",
        active ? "text-accent" : "text-n-500 hover:text-text",
        className,
      )}
    >
      <Settings className="h-[17px] w-[17px]" />
      {update && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-accent" />}
    </Link>
  );
}

/**
 * The far-left icon strip: explorer toggle, search, and the app's views.
 * Names show on hover. Settings joins it while the explorer (which holds the
 * settings button) is collapsed.
 */
export function Ribbon({
  explorerOpen,
  onToggleExplorer,
}: {
  explorerOpen: boolean;
  onToggleExplorer: () => void;
}) {
  const { pathname } = useLocation();
  const { currentKBRecord } = useKB();
  const financeOn = currentKBRecord ? currentKBRecord.finance_enabled !== false : true;
  const nav = [
    { label: "Notes", href: "/notes", icon: Files, match: ["/notes"] },
    { label: "Graph", href: "/graph-3d", icon: Network, match: ["/graph-3d", "/notes-graph"] },
    { label: "Ask", href: "/chat", icon: MessageCircle, match: ["/chat"] },
    ...(financeOn ? [{ label: "Finance", href: "/finance", icon: Wallet, match: ["/finance"] }] : []),
  ];
  const item = "grid h-8 w-8 place-items-center rounded-md hover:bg-n-900";

  return (
    <nav className="flex w-11 shrink-0 flex-col items-center gap-1 border-r border-n-900 bg-bg-deep/60 py-2.5">
      <button
        type="button"
        onClick={onToggleExplorer}
        data-tip={explorerOpen ? "Hide file explorer (⌘\\)" : "Show file explorer (⌘\\)"}
        aria-label="Toggle file explorer"
        className={cn(item, "text-n-500 hover:text-text")}
      >
        <PanelLeft className="h-[17px] w-[17px]" />
      </button>
      <button
        type="button"
        onClick={() => openCommandPalette()}
        data-tip="Search (⌘K)"
        aria-label="Search"
        className={cn(item, "text-n-500 hover:text-text")}
      >
        <Search className="h-[17px] w-[17px]" />
      </button>
      <div className="my-1 h-px w-5 bg-n-900" />
      {nav.map((n) => {
        const Icon = n.icon;
        const active = n.match.some((m) => pathname.startsWith(m));
        return (
          <Link
            key={n.href}
            to={n.href}
            data-tip={n.label}
            aria-label={n.label}
            className={cn(item, "relative", active ? "bg-n-900 text-accent" : "text-n-500 hover:text-text")}
          >
            <Icon className="h-[17px] w-[17px]" />
            {active && <span className="absolute -left-[6px] bottom-2 top-2 w-0.5 rounded-r-sm bg-accent" />}
          </Link>
        );
      })}
      <div className="mt-auto flex flex-col items-center gap-1">
        <ActivityStatus compact />
        {!explorerOpen && <SettingsButton />}
      </div>
    </nav>
  );
}

/** Explorer footer: the workspace switcher with settings beside it. */
export function WorkspaceFooter() {
  const navigate = useNavigate();
  const { currentKB, currentKBName, currentKBRecord, kbs, setCurrentKB, refreshKBs } = useKB();
  const [wsOpen, setWsOpen] = useState(false);
  const wsRef = useDismiss(wsOpen, () => setWsOpen(false));

  return (
    <div className="flex shrink-0 items-center gap-1 border-t border-n-900 px-2 py-2">
      <div ref={wsRef} className="relative min-w-0 flex-1">
        <button
          type="button"
          onClick={() => {
            setWsOpen((v) => !v);
            if (!wsOpen) void refreshKBs();
          }}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-n-900"
          title={currentKBRecord?.vault_path || "Switch workspace"}
        >
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-n-500" />
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{currentKBName}</span>
          <span className="truncate text-[11px] text-n-600">{vaultFolderName(currentKBRecord?.vault_path)}</span>
        </button>

        {wsOpen && (
          <div className="popover absolute bottom-full left-0 z-40 mb-1.5 w-[280px]">
            <div className="kicker px-2.5 pb-1 pt-2">Workspaces</div>
            {kbs.map((kb, i) => {
              const slug = kbSlug(kb);
              const active = slug === currentKB || kb.name === currentKB;
              return (
                <button
                  key={kb.id}
                  type="button"
                  className="menu-item py-2"
                  onClick={() => {
                    setCurrentKB(slug, kb.name);
                    setWsOpen(false);
                  }}
                >
                  <span
                    className={cn(
                      "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-[6px] text-[11px] font-medium text-accent-100",
                      SWATCHES[i % SWATCHES.length],
                    )}
                  >
                    {kb.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]">{kb.name}</span>
                    <span className="block truncate font-mono text-[11px] text-n-500">{kb.vault_path || "—"}</span>
                  </span>
                  {active && <Check className="h-3.5 w-3.5 text-accent" />}
                </button>
              );
            })}
            <div className="hr-fade mx-2.5 my-1.5" />
            <button
              type="button"
              className="menu-item text-n-400"
              onClick={() => {
                setWsOpen(false);
                navigate("/kb?new=1");
              }}
            >
              <Plus className="h-3.5 w-3.5" /> New workspace
            </button>
            <button
              type="button"
              className="menu-item text-n-400"
              onClick={() => {
                setWsOpen(false);
                navigate("/kb");
              }}
            >
              <Settings className="h-3.5 w-3.5" /> Workspace settings
            </button>
          </div>
        )}
      </div>
      <SettingsButton />
    </div>
  );
}
