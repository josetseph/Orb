"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import {
  Check,
  ChevronsUpDown,
  MessageCircle,
  Network,
  NotebookPen,
  Plus,
  Search,
  Settings,
  Wallet,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { kbSlug, useKB } from "@/lib/kb-context";
import { ActivityStatus } from "@/components/system-status-indicator";
import { openCommandPalette } from "@/components/command-palette";

const SWATCHES = ["bg-accent-700", "bg-accent-800", "bg-n-700", "bg-accent-600"];

function vaultFolderName(path?: string): string {
  if (!path) return "";
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
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

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { currentKB, currentKBName, currentKBRecord, kbs, setCurrentKB, refreshKBs } =
    useKB();
  const [wsOpen, setWsOpen] = useState(false);
  const wsRef = useDismiss(wsOpen, () => setWsOpen(false));

  const financeOn = currentKBRecord ? currentKBRecord.finance_enabled !== false : true;

  const nav = [
    { label: "Notes", href: "/notes", icon: NotebookPen, match: ["/notes"] },
    { label: "Ask", href: "/chat", icon: MessageCircle, match: ["/chat"] },
    { label: "Graph", href: "/graph-3d", icon: Network, match: ["/graph-3d", "/notes-graph"] },
    ...(financeOn
      ? [{ label: "Finance", href: "/finance", icon: Wallet, match: ["/finance"] }]
      : []),
  ];
  const settingsActive = ["/kb", "/models", "/setup", "/settings"].some((p) =>
    pathname.startsWith(p),
  );

  return (
    <aside className="flex w-[232px] shrink-0 flex-col border-r border-n-900 bg-bg-deep/40">
      {/* Workspace switcher */}
      <div ref={wsRef} className="relative px-3 pb-2.5 pt-3.5">
        <button
          type="button"
          onClick={() => {
            setWsOpen((v) => !v);
            if (!wsOpen) void refreshKBs();
          }}
          className="flex w-full items-center gap-2.5 rounded-md border border-transparent px-2 py-1.5 text-left hover:bg-n-900"
          title="Switch workspace"
        >
          <Image
            src="/logo-icon.png"
            alt="Orb"
            width={26}
            height={26}
            loading="eager"
            className="h-[26px] w-[26px] shrink-0 rounded-[7px] object-cover"
          />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium leading-tight">
              {currentKBName}
            </div>
            <div className="truncate text-[11px] text-n-500">
              {vaultFolderName(currentKBRecord?.vault_path) || "Workspace"}
            </div>
          </div>
          <ChevronsUpDown className="h-3.5 w-3.5 text-n-500" />
        </button>

        {wsOpen && (
          <div className="popover absolute left-3 right-3 top-[58px] z-40">
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
                    <span className="block truncate font-mono text-[11px] text-n-500">
                      {kb.vault_path || "—"}
                    </span>
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
                router.push("/kb?new=1");
              }}
            >
              <Plus className="h-3.5 w-3.5" /> New workspace
            </button>
            <button
              type="button"
              className="menu-item text-n-400"
              onClick={() => {
                setWsOpen(false);
                router.push("/kb");
              }}
            >
              <Settings className="h-3.5 w-3.5" /> Workspace settings
            </button>
          </div>
        )}
      </div>

      {/* Search / jump */}
      <button
        type="button"
        onClick={() => openCommandPalette()}
        className="mx-3 mb-3.5 flex items-center gap-2 rounded-md border border-n-900 bg-surface px-2.5 py-[7px] text-left text-[12px] text-n-500 hover:border-n-700"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="flex-1">Search or jump to…</span>
        <kbd className="rounded border border-n-800 px-1.5 py-px font-mono text-[10px] text-n-600">
          ⌘K
        </kbd>
      </button>

      {/* Navigation */}
      <nav className="flex flex-col gap-0.5 px-3">
        {nav.map((item) => {
          const Icon = item.icon;
          const active = item.match.some((m) => pathname.startsWith(m));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium hover:bg-n-900",
                active ? "bg-n-900 text-text" : "text-n-400",
              )}
            >
              <Icon className={cn("h-[17px] w-[17px]", active ? "text-accent" : "text-n-500")} />
              <span className="flex-1">{item.label}</span>
              {active && (
                <span className="absolute -left-3 bottom-[9px] top-[9px] w-0.5 rounded-r-sm bg-accent" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Activity + settings */}
      <div className="mt-auto flex flex-col gap-2 p-3">
        <div className="hr-fade" />
        <ActivityStatus />
        <Link
          href="/kb"
          className={cn(
            "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] hover:bg-n-900",
            settingsActive ? "bg-n-900 text-text" : "text-n-400",
          )}
        >
          <Settings className={cn("h-[17px] w-[17px]", settingsActive && "text-accent")} />
          Settings
        </Link>
      </div>
    </aside>
  );
}
