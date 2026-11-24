"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  BarChart3,
  Bell,
  Briefcase,
  Calculator,
  CalendarDays,
  ChevronDown,
  Download,
  FileText,
  GitCompareArrows,
  Globe2,
  Landmark,
  Layers,
  LogOut,
  Menu,
  Moon,
  Newspaper,
  PieChart,
  Rocket,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Sun,
  TrendingUp,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useAppContext } from "./CurrencyContext";

interface NavSubItem {
  href: string;
  label: string;
  desc: string;
  icon: React.ComponentType<{ className?: string }>;
}

interface NavGroup {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  matchPrefixes: string[];
  items: NavSubItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    key: "markets",
    label: "Markets",
    icon: Globe2,
    matchPrefixes: ["/", "/markets", "/analytics"],
    items: [
      { href: "/", label: "Market Overview & Heatmap", desc: "KSE-100, sector treemap, FX, gold & headlines", icon: BarChart3 },
      { href: "/markets/sectors", label: "Sectors & Industries", desc: "All 37 PSX sectors, median P/E & stock drilldown", icon: Layers },
      { href: "/markets/ipos", label: "IPOs & Developments", desc: "Main & GEM Board IPOs, post-IPO return & PUCARS", icon: Rocket },
      { href: "/markets/news", label: "Financial News", desc: "Business Recorder, Profit, Dawn, Tribune & Investing", icon: Newspaper },
      { href: "/markets/calendar", label: "Corporate Calendar", desc: "Ex-dividends, bonus/rights, board meetings & results", icon: CalendarDays },
      { href: "/analytics", label: "Quant & Macro Lab", desc: "SBP/KIBOR yields, backtester & correlations", icon: TrendingUp },
    ],
  },
  {
    key: "stocks",
    label: "Stocks",
    icon: PieChart,
    matchPrefixes: ["/stocks", "/screener"],
    items: [
      { href: "/stocks", label: "PSX & Global Directory", desc: "Search 500+ PSX & international equities", icon: PieChart },
      { href: "/screener", label: "Technical Screener", desc: "RSI, MACD, Supertrend & Piotroski F-Score filters", icon: SlidersHorizontal },
      { href: "/stocks/compare", label: "Compare Stocks", desc: "Head-to-head 2–5 stock chart & valuation matrix", icon: GitCompareArrows },
      { href: "/stocks/downloads", label: "Filings & Downloads", desc: "1-click PUCARS PDFs, annual reports & CSV exports", icon: Download },
    ],
  },
  {
    key: "funds",
    label: "Mutual Funds",
    icon: Landmark,
    matchPrefixes: ["/funds"],
    items: [
      { href: "/funds", label: "MUFAP & VPS Directory", desc: "330+ funds, Sharpe ratio & risk-return scatter", icon: Landmark },
      { href: "/funds?daily_dividend=true", label: "Daily Dividend Funds", desc: "Cash & money market daily payout yield tracker", icon: Sparkles },
      { href: "/funds?fmr_only=true", label: "SECP FMR Explorer", desc: "Monthly asset allocation, WAM & top-10 holdings", icon: FileText },
    ],
  },
  {
    key: "portfolio",
    label: "Portfolio",
    icon: Briefcase,
    matchPrefixes: ["/portfolio", "/watchlists", "/calculators"],
    items: [
      { href: "/portfolio", label: "My Holdings & X-Ray", desc: "FBR CGT/WHT ledger, XIRR & daily dividend accrual", icon: Briefcase },
      { href: "/watchlists", label: "Watchlists & Alerts", desc: "Price alerts, PUCARS triggers & notifications", icon: Bell },
      { href: "/calculators", label: "Tax & SIP Calculators", desc: "FBR Sec 37A CGT, Zakat & wealth compounding", icon: Calculator },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    icon: Settings,
    matchPrefixes: ["/settings", "/status"],
    items: [
      { href: "/settings", label: "Workspace & Tax Settings", desc: "FBR ATL, Shariah default, broker bps & JSON backup", icon: Settings },
      { href: "/status", label: "Data Pipeline & Sync", desc: "Live scraper telemetry, coverage & manual refresh", icon: Activity },
    ],
  },
];

interface SearchAssetItem {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  last_price: string;
  change_pct: string;
  is_shariah_compliant: boolean;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    currency,
    toggleCurrency,
    theme,
    toggleTheme,
    user,
    localMode,
    unreadNotifications,
    logout,
  } = useAppContext();

  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mobileExpanded, setMobileExpanded] = useState<string | null>("markets");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchAssetItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [tickerItems, setTickerItems] = useState<
    Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>
  >([]);
  const navRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    apiFetch<{
      indices: Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>;
      fx_rates: Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>;
      gold_rates: Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>;
      crypto_rates: Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>;
      top_gainers: Array<{ symbol: string; last_price: string; change_pct: string; asset_class: string }>;
    }>("/api/v1/market/overview")
      .then((res) => {
        const items = [
          ...(res.indices || []).slice(0, 3),
          ...(res.fx_rates || []).slice(0, 1),
          ...(res.gold_rates || []).slice(0, 1),
          ...(res.crypto_rates || []).slice(0, 1),
          ...(res.top_gainers || []).slice(0, 6),
        ];
        setTickerItems(items);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setOpenMenu(null);
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      } else if (e.key === "Escape") {
        setSearchOpen(false);
        setOpenMenu(null);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!searchOpen) return;
    let active = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch<SearchAssetItem[]>(
          `/api/v1/market/assets?q=${encodeURIComponent(searchQuery)}&limit=12`,
        );
        if (active) setSearchResults(res);
      } catch {
        if (active) setSearchResults([]);
      } finally {
        if (active) setSearching(false);
      }
    }, 180);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [searchQuery, searchOpen]);

  const navigateAsset = (item: SearchAssetItem) => {
    setSearchOpen(false);
    setSearchQuery("");
    if (
      item.asset_class === "PSX_EQUITY" ||
      item.asset_class === "PSX_ETF" ||
      item.asset_class === "PSX_INDEX" ||
      item.asset_class === "INTL_EQUITY" ||
      item.asset_class === "INTL_ETF" ||
      item.asset_class === "INTL_INDEX"
    ) {
      router.push(`/stocks/${item.symbol}`);
    } else if (item.asset_class === "MUTUAL_FUND" || item.asset_class === "VPS_PENSION") {
      router.push(`/funds/${encodeURIComponent(item.symbol)}`);
    } else {
      router.push("/portfolio");
    }
  };

  const isGroupActive = (group: NavGroup) => {
    if (group.key === "markets" && pathname === "/") return true;
    return group.matchPrefixes.some((p) => p !== "/" && pathname.startsWith(p));
  };

  const showTicker = user?.preferences?.show_news_ticker !== false;

  return (
    <div className="min-h-screen flex flex-col">
      {showTicker && tickerItems.length > 0 && (
        <div className="border-b border-[var(--border)] bg-[var(--bg-elevated)] overflow-hidden h-8 flex items-center text-[11px]">
          <div className="okz-ticker-track items-center">
            {[...tickerItems, ...tickerItems].map((tk, i) => {
              const chg = Number(tk.change_pct || 0);
              const px = Number(tk.last_price || 0);
              const isLinkable =
                tk.asset_class === "PSX_EQUITY" || tk.asset_class === "PSX_ETF" || tk.asset_class === "PSX_INDEX";
              const inner = (
                <span className="inline-flex items-center gap-1.5 px-3.5 border-r border-[var(--border)]/70 whitespace-nowrap font-mono">
                  <span className="font-bold text-[var(--text)]">{tk.symbol}</span>
                  <span className="okz-muted">
                    {px >= 1000 ? px.toLocaleString(undefined, { maximumFractionDigits: 0 }) : px.toFixed(2)}
                  </span>
                  <span className={chg >= 0 ? "text-emerald-400 font-semibold" : "text-rose-400 font-semibold"}>
                    {chg >= 0 ? "+" : ""}
                    {chg.toFixed(2)}%
                  </span>
                </span>
              );
              return isLinkable ? (
                <Link key={`${tk.symbol}-${i}`} href={`/stocks/${encodeURIComponent(tk.symbol)}`} className="hover:opacity-80">
                  {inner}
                </Link>
              ) : (
                <span key={`${tk.symbol}-${i}`}>{inner}</span>
              );
            })}
          </div>
        </div>
      )}

      <header className="sticky top-0 z-40 border-b border-[var(--border)] bg-[var(--card)]/95 backdrop-blur">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between gap-3">
          <div className="flex items-center gap-6" ref={navRef}>
            <Link href="/" className="flex items-center gap-2 font-bold text-lg tracking-tight shrink-0">
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white font-black">
                OK
              </span>
              <span>
                Open<span className="text-emerald-500">Khazana</span>
              </span>
            </Link>

            {/* 5 Main Top-Level Headings with Rich Dropdown Submenus */}
            <nav className="hidden lg:flex items-center gap-1" aria-label="Main Navigation">
              {NAV_GROUPS.map((group) => {
                const GroupIcon = group.icon;
                const active = isGroupActive(group);
                const isOpen = openMenu === group.key;
                return (
                  <div
                    key={group.key}
                    className="relative"
                    onMouseEnter={() => setOpenMenu(group.key)}
                    onMouseLeave={() => setOpenMenu((cur) => (cur === group.key ? null : cur))}
                  >
                    <button
                      type="button"
                      onClick={() => setOpenMenu((cur) => (cur === group.key ? null : group.key))}
                      aria-expanded={isOpen}
                      className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors ${
                        active || isOpen
                          ? "bg-emerald-500/15 text-emerald-500"
                          : "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--card-muted)]"
                      }`}
                    >
                      <GroupIcon className="w-4 h-4" />
                      <span>{group.label}</span>
                      <ChevronDown className={`w-3.5 h-3.5 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                    </button>

                    {isOpen && (
                      <div className="absolute left-0 top-full pt-1.5 z-50 w-72">
                        <div className="okz-card p-2 shadow-2xl border border-[var(--border)] bg-[var(--card)] space-y-0.5">
                          {group.items.map((sub) => {
                            const SubIcon = sub.icon;
                            const basePath = sub.href.split("?")[0];
                            const subActive = basePath === "/" ? pathname === "/" : pathname === basePath;
                            return (
                              <Link
                                key={sub.href}
                                href={sub.href}
                                onClick={() => setOpenMenu(null)}
                                className={`flex items-start gap-2.5 p-2.5 rounded-lg transition ${
                                  subActive
                                    ? "bg-emerald-500/15 text-emerald-400"
                                    : "hover:bg-[var(--card-muted)] text-[var(--text)]"
                                }`}
                              >
                                <SubIcon className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                                <div className="min-w-0">
                                  <div className="text-xs font-semibold leading-tight">{sub.label}</div>
                                  <div className="text-[11px] okz-muted leading-snug mt-0.5">{sub.desc}</div>
                                </div>
                              </Link>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </nav>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setSearchOpen(true)}
              title="Global asset search (Ctrl+K / Cmd+K)"
              className="hidden sm:inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-[var(--border)] text-xs okz-muted hover:text-[var(--text)] hover:bg-[var(--card-muted)] transition"
            >
              <Search className="w-3.5 h-3.5" />
              <span>Search</span>
              <kbd className="px-1 py-0.5 rounded bg-[var(--card-muted)] text-[10px] font-mono">⌘K</kbd>
            </button>

            <button
              onClick={toggleCurrency}
              title="Toggle PKR / USD display currency"
              className="px-2.5 py-1.5 rounded-md border border-[var(--border)] text-xs font-semibold hover:bg-[var(--card-muted)] transition"
            >
              {currency === "PKR" ? "PKR" : "USD"}
            </button>

            <button
              onClick={toggleTheme}
              aria-label="Toggle color theme"
              className="p-2 rounded-md border border-[var(--border)] hover:bg-[var(--card-muted)] transition"
            >
              {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            </button>

            {user && (
              <Link
                href="/watchlists"
                title="Alerts & Notifications"
                className="relative hidden sm:inline-flex p-1.5 rounded-md hover:bg-[var(--card-muted)] text-[var(--text-muted)] hover:text-[var(--text)]"
              >
                <Bell className="w-4 h-4" />
                {unreadNotifications > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-rose-500 text-white text-[10px] font-bold flex items-center justify-center">
                    {unreadNotifications > 9 ? "9+" : unreadNotifications}
                  </span>
                )}
              </Link>
            )}

            {localMode ? (
              <Link
                href="/settings"
                title="Self-hosted local workspace preferences"
                className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold hover:bg-emerald-500/20 transition"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                <span className="max-w-24 truncate">{user?.full_name || "Local"}</span>
              </Link>
            ) : user ? (
              <div className="hidden sm:flex items-center gap-1.5 pl-2 border-l border-[var(--border)]">
                <Link
                  href="/settings"
                  title="Account & Tax Settings"
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md hover:bg-[var(--card-muted)] text-xs font-medium"
                >
                  <Settings className="w-3.5 h-3.5 text-emerald-500" />
                  <span className="max-w-24 truncate">{user.full_name}</span>
                </Link>
                <button
                  onClick={logout}
                  title="Sign out"
                  className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-rose-500 hover:bg-[var(--card-muted)]"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <Link
                href="/auth"
                className="hidden sm:inline-flex px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition"
              >
                Sign In
              </Link>
            )}

            <button
              onClick={() => setMobileOpen(!mobileOpen)}
              className="lg:hidden p-2 rounded-md border border-[var(--border)]"
              aria-label="Toggle Menu"
            >
              {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>

        {/* Mobile Accordion Navigation Drawer */}
        {mobileOpen && (
          <div className="lg:hidden border-t border-[var(--border)] bg-[var(--card)] px-4 py-3 space-y-2 max-h-[80vh] overflow-y-auto">
            <button
              onClick={() => {
                setMobileOpen(false);
                setSearchOpen(true);
              }}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium bg-[var(--card-muted)]"
            >
              <Search className="w-4 h-4 text-emerald-500" />
              Search Stocks, Funds &amp; Assets...
            </button>

            {NAV_GROUPS.map((group) => {
              const GroupIcon = group.icon;
              const expanded = mobileExpanded === group.key;
              return (
                <div key={group.key} className="border border-[var(--border)] rounded-lg overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setMobileExpanded((cur) => (cur === group.key ? null : group.key))}
                    className="w-full flex items-center justify-between px-3 py-2.5 text-sm font-semibold bg-[var(--bg-elevated)]/60"
                  >
                    <span className="flex items-center gap-2">
                      <GroupIcon className="w-4 h-4 text-emerald-500" />
                      {group.label}
                    </span>
                    <ChevronDown className={`w-4 h-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
                  </button>
                  {expanded && (
                    <div className="p-1.5 space-y-0.5">
                      {group.items.map((sub) => {
                        const SubIcon = sub.icon;
                        return (
                          <Link
                            key={sub.href}
                            href={sub.href}
                            onClick={() => setMobileOpen(false)}
                            className="flex items-center gap-2.5 px-3 py-2 rounded-md text-xs font-medium hover:bg-[var(--card-muted)]"
                          >
                            <SubIcon className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                            <span>{sub.label}</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}

            {!localMode && (
              <div className="pt-2 border-t border-[var(--border)] flex items-center justify-between gap-2">
                {user ? (
                  <>
                    <Link
                      href="/settings"
                      onClick={() => setMobileOpen(false)}
                      className="flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium hover:bg-[var(--card-muted)]"
                    >
                      <Settings className="w-4 h-4 text-emerald-500" />
                      {user.full_name}
                    </Link>
                    <button
                      onClick={() => {
                        setMobileOpen(false);
                        logout();
                      }}
                      className="px-3 py-2 text-xs text-rose-500 font-semibold"
                    >
                      Sign Out
                    </button>
                  </>
                ) : (
                  <Link
                    href="/auth"
                    onClick={() => setMobileOpen(false)}
                    className="flex-1 py-2 text-center rounded-md bg-emerald-600 text-white text-xs font-semibold"
                  >
                    Sign In / Register
                  </Link>
                )}
              </div>
            )}
          </div>
        )}
      </header>

      {searchOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-start justify-center pt-20 p-4"
          onClick={() => setSearchOpen(false)}
        >
          <div className="okz-card w-full max-w-xl overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border)]">
              <Search className="w-4 h-4 okz-muted" />
              <input
                autoFocus
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search PSX symbols (MEBL, OGDC, LUCK), Mutual Funds, Gold, Crypto..."
                className="w-full bg-transparent text-sm focus:outline-none"
              />
              <button onClick={() => setSearchOpen(false)} className="text-xs okz-muted hover:text-[var(--text)]">
                ESC
              </button>
            </div>
            <div className="max-h-80 overflow-y-auto divide-y divide-[var(--border)]">
              {searching && <div className="p-4 text-xs okz-muted">Searching assets...</div>}
              {!searching && searchResults.length === 0 && (
                <div className="p-4 text-xs okz-muted">No matching assets found.</div>
              )}
              {searchResults.map((item) => {
                const chg = Number(item.change_pct);
                return (
                  <button
                    key={item.id}
                    onClick={() => navigateAsset(item)}
                    className="w-full px-4 py-2.5 text-left flex items-center justify-between gap-3 hover:bg-[var(--card-muted)] transition"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm">{item.symbol}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--card-muted)] okz-muted">
                          {item.asset_class}
                        </span>
                        {item.is_shariah_compliant && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-500">
                            Shariah
                          </span>
                        )}
                      </div>
                      <div className="text-xs okz-muted truncate">{item.name}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-sm font-semibold tabular-nums">
                        {item.currency} {Number(item.last_price).toLocaleString()}
                      </div>
                      <div className={`text-xs tabular-nums ${chg >= 0 ? "okz-pos" : "okz-neg"}`}>
                        {chg >= 0 ? "+" : ""}
                        {chg.toFixed(2)}%
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">{children}</main>

      <footer className="border-t border-[var(--border)] py-6 px-4 text-xs okz-muted">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between gap-2">
          <span>
            OpenKhazana — Open-source (Apache-2.0) Pakistan Market Hub &amp; Portfolio Tracker
            {localMode ? " · Local Self-Hosted Mode" : " · Multi-User Cloud Mode"}
          </span>
          <span>Informational &amp; tracking purposes only. Verify FBR CGT/Zakat &amp; Shariah screenings independently.</span>
        </div>
      </footer>
    </div>
  );
}
