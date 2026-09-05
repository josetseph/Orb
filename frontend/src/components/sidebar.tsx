"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { Home, MessageSquare, FileText, Box, Database, Settings, Wallet, Link2, Sparkles, Cpu } from "lucide-react";
import { cn } from "@/lib/utils";
import { useKB } from "@/lib/kb-context";
import { SystemStatusIndicator } from "@/components/system-status-indicator";

const navigation = [
  { name: "Home", href: "/", icon: Home },
  { name: "Chat", href: "/chat", icon: MessageSquare },
  { name: "Notes", href: "/notes", icon: FileText },
  { name: "Notes graph", href: "/notes-graph", icon: Link2 },
  { name: "Graph", href: "/graph-3d", icon: Box },
  { name: "Finance", href: "/finance", icon: Wallet },
  { name: "Knowledge Bases", href: "/kb", icon: Database },
  { name: "Models", href: "/models", icon: Cpu },
  { name: "Setup", href: "/setup", icon: Sparkles },
  { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const { currentKB, currentKBName } = useKB();
  const isNonDefault = currentKB !== "default";

  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-20 border-r border-white/10 bg-black/50 backdrop-blur-xl">
      <div className="flex h-full flex-col items-center py-8">
        {/* Logo */}
        <div className="mb-8 flex h-12 w-12 items-center justify-center rounded-xl overflow-hidden">
          <Image
            src="/logo.png"
            alt="Orb"
            width={48}
            height={48}
            loading="eager"
            className="h-full w-full object-cover"
          />
        </div>

        {/* Navigation */}
        <nav className="flex flex-1 flex-col gap-4">
          {navigation.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;
            const isKBItem = item.href === "/kb";

            return (
              <Link
                key={item.name}
                href={item.href}
                className={cn(
                  "group relative flex h-12 w-12 items-center justify-center rounded-xl transition-all duration-200",
                  isActive
                    ? "bg-white/10 text-white shadow-lg shadow-purple-500/20"
                    : "text-white/50 hover:bg-white/5 hover:text-white",
                )}
                title={isKBItem ? `Knowledge Bases (active: ${currentKB})` : item.name}
              >
                <Icon className="h-5 w-5" />
                {/* Active page indicator */}
                {isActive && (
                  <div className="absolute -left-1 top-1/2 h-8 w-1 -translate-y-1/2 rounded-r-full bg-gradient-to-b from-purple-500 to-pink-500" />
                )}
                {/* Non-default KB dot on the DB icon */}
                {isKBItem && isNonDefault && (
                  <span className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full bg-purple-500 ring-1 ring-black" />
                )}
              </Link>
            );
          })}
        </nav>

        {/* Current KB label + system pulse */}
        <div className="mt-auto flex flex-col items-center gap-2">
          <div
            className="w-12 flex items-center justify-center"
            title={`Active KB: ${currentKBName}`}
          >
            <span
              className={cn(
                "text-[9px] font-semibold text-center leading-tight px-1 truncate w-full text-center",
                isNonDefault ? "text-purple-400" : "text-white/30"
              )}
            >
              {currentKBName.length > 6 ? currentKBName.slice(0, 5) + "…" : currentKBName}
            </span>
          </div>
          <SystemStatusIndicator />
        </div>
      </div>
    </aside>
  );
}
