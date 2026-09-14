import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/command-palette";
import { AiLimitedBanner } from "@/components/ai-limited-banner";
import { KBProvider } from "@/lib/kb-context";
import { ChatProvider } from "@/lib/chat-context";
import { SuppressThreeWarnings } from "@/components/suppress-three-warnings";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Orb",
  description: "Your knowledge, on your machine.",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/logo-icon.png", type: "image/png", sizes: "128x128" },
    ],
    apple: "/logo-icon.png",
    shortcut: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} font-sans`}>
        <KBProvider>
          <ChatProvider>
            <SuppressThreeWarnings />
            <div className="flex h-screen w-full overflow-hidden">
              <Sidebar />
              <main className="relative flex min-w-0 flex-1">{children}</main>
            </div>
            <CommandPalette />
            <AiLimitedBanner />
          </ChatProvider>
        </KBProvider>
      </body>
    </html>
  );
}
