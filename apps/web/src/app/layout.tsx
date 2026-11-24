import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/components/CurrencyContext";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "OpenKhazana — Open-Source Pakistan Market Hub & Portfolio Tracker",
  description:
    "Track PSX stocks, MUFAP mutual funds, gold, forex, and crypto with Pakistan FBR CGT/WHT accounting and transparent Shariah compliance.",
  manifest: "/manifest.json",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}
