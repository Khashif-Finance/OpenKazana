import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/components/CurrencyContext";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: {
    default: "OpenKhazana — Open-Source Pakistan Financial Market Terminal & Portfolio Hub",
    template: "%s | OpenKhazana",
  },
  description:
    "Institutional open-source Pakistan market terminal: live PSX equities, 5-brokerage research reports, SECP FMR mutual funds, Forex, PMEX commodities, NSS/Sukuk, PBS/SBP macro analytics, FBR tax ledger & Shariah screening.",
  manifest: "/manifest.json",
  openGraph: {
    title: "OpenKhazana — Open-Source Pakistan Financial Market Terminal",
    description:
      "Live PSX stocks, 5-brokerage research, SECP FMR mutual funds, Forex, PMEX commodities, NSS/Sukuk, and FBR tax-ready portfolio tracking.",
    type: "website",
    siteName: "OpenKhazana",
  },
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
