import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "@/components/sidebar";
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

export default function App() {
  return (
    <KBProvider>
      <ChatProvider>
        <div className="flex h-screen w-full overflow-hidden">
          <Sidebar />
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
        <CommandPalette />
        <AiLimitedBanner />
      </ChatProvider>
    </KBProvider>
  );
}
