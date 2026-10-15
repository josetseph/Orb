import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Ribbon } from "@/components/sidebar";
import { FileExplorer, NotesOverlays } from "@/components/file-explorer";
import { ResizeHandle, useStored } from "@/components/resize-handle";
import { NotesProvider } from "@/lib/notes-context";
import { CommandPalette } from "@/components/command-palette";
import { AiLimitedBanner } from "@/components/ai-limited-banner";
import { KBProvider } from "@/lib/kb-context";
import { ChatProvider } from "@/lib/chat-context";

// Each page is its own chunk; the graph pages pull in three.js.
const page = (load: () => Promise<{ default: React.ComponentType }>) => {
  const C = lazy(load);
  return (
    <Suspense fallback={null}>
      <C />
    </Suspense>
  );
};

const EXPLORER_WIDTH = 280;

/** Ribbon, file explorer (resizable, ⌘\\ hides it), then the page. */
function Shell() {
  const [explorerOpen, setExplorerOpen] = useStored("orb:explorer-open", true);
  const [explorerWidth, setExplorerWidth] = useStored("orb:explorer-width", EXPLORER_WIDTH);
  const toggleExplorer = () => setExplorerOpen(!explorerOpen);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "\\") {
        e.preventDefault();
        setExplorerOpen(!explorerOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [explorerOpen, setExplorerOpen]);

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <Ribbon explorerOpen={explorerOpen} onToggleExplorer={toggleExplorer} />
      {explorerOpen && (
        <aside
          className="relative shrink-0 border-r border-n-900 bg-bg-deep/40"
          style={{ width: explorerWidth }}
        >
          <FileExplorer />
          <ResizeHandle
            edge="right"
            width={explorerWidth}
            min={200}
            max={520}
            initial={EXPLORER_WIDTH}
            onWidth={setExplorerWidth}
          />
        </aside>
      )}
      <main className="relative flex min-w-0 flex-1">
        <Routes>
          <Route path="/" element={<Navigate to="/notes" replace />} />
          <Route path="/graph" element={<Navigate to="/graph-3d" replace />} />
          <Route path="/notes" element={page(() => import("./app/notes/page"))} />
          <Route path="/chat" element={page(() => import("./app/chat/page"))} />
          <Route path="/kb" element={page(() => import("./app/kb/page"))} />
          <Route path="/models" element={page(() => import("./app/models/page"))} />
          <Route path="/settings" element={page(() => import("./app/settings/page"))} />
          <Route path="/setup" element={page(() => import("./app/setup/page"))} />
          <Route path="/finance" element={page(() => import("./app/finance/page"))} />
          <Route path="/graph-3d" element={page(() => import("./app/graph-3d/page"))} />
          <Route path="/notes-graph" element={page(() => import("./app/notes-graph/page"))} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <KBProvider>
      <ChatProvider>
        <NotesProvider>
          <Shell />
          <NotesOverlays />
          <CommandPalette />
          <AiLimitedBanner />
        </NotesProvider>
      </ChatProvider>
    </KBProvider>
  );
}
