import type { Metadata } from "next";

import "./globals.css";
import ThemeProvider from "@/components/providers/ThemeProvider";

export const metadata: Metadata = {
  title: "AgentForge",
  description:
    "Turn an idea into a working, tested proof of concept without assembling an engineering team first.",
  keywords: ["AI", "code generation", "LangGraph", "multi-agent", "software development"],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // next-themes writes the theme class here on the client, which the server
    // cannot predict; suppressing the warning is the documented handling.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
