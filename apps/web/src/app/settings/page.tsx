"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  CheckCircle2,
  Database,
  Download,
  KeyRound,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Trash2,
  Upload,
} from "lucide-react";
import { useAppContext, UserSession } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";
import { formatNumber } from "@/lib/format";

interface CustomAssetRow {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  currency: string;
  sector_or_category: string;
  is_shariah_compliant: boolean;
  last_price: string;
  is_custom?: boolean;
}

export default function SettingsPage() {
  const { user, localMode, authMode, loadingUser, setCurrency, setUserSession, refreshUser } = useAppContext();

  const [tab, setTab] = useState<"workspace" | "tax" | "backup" | "assets">("workspace");

  // Profile & Preferences
  const [fullName, setFullName] = useState("");
  const [preferredCurrency, setPreferredCurrency] = useState<"PKR" | "USD">("PKR");
  const [telegramChatId, setTelegramChatId] = useState("");
  const [shariahDefault, setShariahDefault] = useState(false);
  const [defaultMarketView, setDefaultMarketView] = useState<"PSX" | "ALL" | "INTL">("PSX");
  const [compactTables, setCompactTables] = useState(false);
  const [showTicker, setShowTicker] = useState(true);

  // Tax profile fields
  const [filerStatus, setFilerStatus] = useState("ACTIVE_FILER");
  const [fiscalYear, setFiscalYear] = useState("FY2025-26");
  const [brokerRateBps, setBrokerRateBps] = useState("15");
  const [zakatExempt, setZakatExempt] = useState(false);
  const [usW8BenActive, setUsW8BenActive] = useState(true);
  const [foreignCgtRatePct, setForeignCgtRatePct] = useState("15");

  // Password change (multiuser mode)
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  // Custom private assets
  const [customAssets, setCustomAssets] = useState<CustomAssetRow[]>([]);
  const [priceEdits, setPriceEdits] = useState<Record<string, string>>({});

  const [saving, setSaving] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCustomAssets = useCallback(async () => {
    try {
      const list = await apiFetch<CustomAssetRow[]>("/api/v1/market/assets?limit=250");
      const customOnly = list.filter((a) => a.is_custom);
      setCustomAssets(customOnly);
      const nextEdits: Record<string, string> = {};
      for (const a of customOnly) {
        nextEdits[a.id] = String(Number(a.last_price));
      }
      setPriceEdits(nextEdits);
    } catch {
      // Non-critical
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    setFullName(user.full_name || "");
    setPreferredCurrency(user.preferred_currency === "USD" ? "USD" : "PKR");
    setTelegramChatId(user.telegram_chat_id || "");
    if (user.preferences) {
      setShariahDefault(Boolean(user.preferences.shariah_only_default));
      const mv = user.preferences.default_market_view;
      setDefaultMarketView(mv === "ALL" || mv === "INTL" ? mv : "PSX");
      setCompactTables(Boolean(user.preferences.compact_tables));
      setShowTicker(user.preferences.show_news_ticker !== false);
    }
    if (user.tax_profile) {
      setFilerStatus(user.tax_profile.filer_status || "ACTIVE_FILER");
      setFiscalYear(user.tax_profile.fiscal_year || "FY2025-26");
      setBrokerRateBps(String(user.tax_profile.default_broker_rate_bps ?? 15));
      setZakatExempt(Boolean(user.tax_profile.zakat_exempt));
      setUsW8BenActive(user.tax_profile.us_w8ben_active !== false);
      setForeignCgtRatePct(String(user.tax_profile.foreign_cgt_rate_pct ?? 15));
    }
    loadCustomAssets();
  }, [user, loadCustomAssets]);

  const handleSaveWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    setNotice(null);
    setError(null);
    setSaving("workspace");
    try {
      const updated = await apiFetch<UserSession>("/api/v1/auth/profile", {
        method: "PATCH",
        body: JSON.stringify({
          full_name: fullName.trim() || "Local Workspace",
          preferred_currency: preferredCurrency,
          telegram_chat_id: telegramChatId.trim() || null,
        }),
      });
      await apiFetch("/api/v1/auth/preferences", {
        method: "PATCH",
        body: JSON.stringify({
          shariah_only_default: shariahDefault,
          default_market_view: defaultMarketView,
          compact_tables: compactTables,
          show_news_ticker: showTicker,
        }),
      });
      setUserSession(updated);
      setCurrency(preferredCurrency);
      await refreshUser();
      setNotice("Workspace profile and display preferences saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save workspace preferences");
    } finally {
      setSaving(null);
    }
  };

  const handleSaveTax = async (e: React.FormEvent) => {
    e.preventDefault();
    setNotice(null);
    setError(null);
    setSaving("tax");
    try {
      await apiFetch("/api/v1/auth/tax-profile", {
        method: "PATCH",
        body: JSON.stringify({
          filer_status: filerStatus,
          fiscal_year: fiscalYear,
          default_broker_rate_bps: Math.max(0, Math.min(500, Number(brokerRateBps) || 0)),
          zakat_exempt: zakatExempt,
          us_w8ben_active: usW8BenActive,
          foreign_cgt_rate_pct: Math.max(0, Math.min(50, Number(foreignCgtRatePct) || 0)),
        }),
      });
      await refreshUser();
      setNotice("Pakistan FBR ATL, US W-8BEN & Zakat settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save tax profile");
    } finally {
      setSaving(null);
    }
  };

  const handleExportWorkspace = async () => {
    setNotice(null);
    setError(null);
    setSaving("export");
    try {
      const data = await apiFetch<Record<string, unknown>>("/api/v1/auth/workspace/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `openkhazana_workspace_backup_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setNotice("Workspace JSON backup downloaded (portfolios, transactions, watchlists, alerts & tax settings).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to export workspace backup");
    } finally {
      setSaving(null);
    }
  };

  const handleImportWorkspace = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setNotice(null);
    setError(null);
    setSaving("import");
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const res = await apiFetch<{
        ok: boolean;
        restored: { portfolios: number; transactions: number; watchlists: number; watchlist_items: number };
      }>("/api/v1/auth/workspace/import", {
        method: "POST",
        body: JSON.stringify(parsed),
      });
      await refreshUser();
      setNotice(
        `Workspace restored: ${res.restored.portfolios} portfolio(s), ${res.restored.transactions} transaction(s), ${res.restored.watchlists} watchlist(s), ${res.restored.watchlist_items} watched symbol(s).`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import JSON workspace backup");
    } finally {
      setSaving(null);
      e.target.value = "";
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setNotice(null);
    setError(null);
    setSaving("password");
    try {
      await apiFetch("/api/v1/auth/profile", {
        method: "PATCH",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      setCurrentPassword("");
      setNewPassword("");
      setNotice("Password updated successfully.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password change failed");
    } finally {
      setSaving(null);
    }
  };

  const handleUpdateCustomPrice = async (assetId: string) => {
    const val = Number(priceEdits[assetId]);
    if (!Number.isFinite(val) || val <= 0) {
      setError("Price must be positive.");
      return;
    }
    setError(null);
    try {
      await apiFetch(`/api/v1/market/assets/custom/${assetId}`, {
        method: "PATCH",
        body: JSON.stringify({ last_price: val }),
      });
      await loadCustomAssets();
      setNotice("Custom asset mark price updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update custom asset price");
    }
  };

  const handleDeleteCustomAsset = async (assetId: string, symbol: string) => {
    setError(null);
    try {
      await apiFetch(`/api/v1/market/assets/custom/${assetId}`, { method: "DELETE" });
      setNotice(`Custom asset ${symbol} deleted.`);
      await loadCustomAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cannot delete asset if transactions reference it.");
    }
  };

  if (loadingUser) {
    return <div className="okz-card p-8 text-center okz-muted">Loading workspace settings...</div>;
  }

  if (!user && !localMode) {
    return (
      <div className="okz-card p-8 max-w-xl mx-auto text-center space-y-4">
        <Settings className="w-8 h-8 text-emerald-500 mx-auto" />
        <h1 className="text-xl font-bold">Account, Tax &amp; Workspace Settings</h1>
        <p className="text-sm okz-muted">
          This OpenKhazana instance is running in multi-user mode. Sign in to manage your FBR tax profile, preferences, and portfolios.
        </p>
        <div className="flex justify-center gap-3">
          <Link
            href="/auth"
            className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold"
          >
            Sign In / Create Account
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="okz-card p-6 bg-gradient-to-br from-emerald-500/10 via-transparent to-sky-500/10 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Settings className="w-6 h-6 text-emerald-500" />
            Workspace &amp; Tax Settings
          </h1>
          <p className="text-sm okz-muted mt-0.5">
            {localMode
              ? "Running in Zero-Login Local Self-Hosted Mode (AUTH_MODE=local) — all portfolios, tax rules & preferences are stored locally in your database."
              : `Signed in as ${user?.email} (AUTH_MODE=${authMode}${user?.is_admin ? " · Admin" : ""})`}
          </p>
        </div>
        <span className="px-3 py-1 rounded-full text-xs font-bold bg-emerald-500/15 text-emerald-400 self-start">
          {localMode ? "Self-Hosted Local Mode" : "Multi-User Cloud Mode"}
        </span>
      </div>

      {notice && (
        <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-sm text-emerald-400 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-sm text-rose-400 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Clean 4-Tab Bar */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] pb-2 text-xs">
        {(
          [
            ["workspace", "Workspace & Preferences", Sliders],
            ["tax", "Pakistan FBR Tax & Zakat", ShieldCheck],
            ["backup", "Backup & Data Sync", Database],
            ["assets", localMode ? `Custom Assets (${customAssets.length})` : "Custom Assets & Security", KeyRound],
          ] as const
        ).map(([k, lbl, Icon]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className={`px-3.5 py-2 rounded-lg font-semibold inline-flex items-center gap-1.5 transition ${
              tab === k
                ? "bg-emerald-600 text-white"
                : "bg-[var(--card)] border border-[var(--border)] okz-muted hover:text-[var(--text)]"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {lbl}
          </button>
        ))}
      </div>

      {/* TAB 1: WORKSPACE & PREFERENCES */}
      {tab === "workspace" && (
        <form onSubmit={handleSaveWorkspace} className="okz-card p-6 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs okz-muted mb-1">Workspace / Investor Name</label>
              <input
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">Preferred Display Currency</label>
              <select
                value={preferredCurrency}
                onChange={(e) => setPreferredCurrency(e.target.value === "USD" ? "USD" : "PKR")}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              >
                <option value="PKR">PKR (Pakistani Rupee)</option>
                <option value="USD">USD (US Dollar Equivalent)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">Default Market View</label>
              <select
                value={defaultMarketView}
                onChange={(e) => setDefaultMarketView(e.target.value as "PSX" | "ALL" | "INTL")}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              >
                <option value="PSX">PSX Pakistan Only</option>
                <option value="ALL">All Markets (PSX + Global)</option>
                <option value="INTL">International / US Stocks</option>
              </select>
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">Telegram Chat ID (Optional Alerts)</label>
              <input
                type="text"
                value={telegramChatId}
                onChange={(e) => setTelegramChatId(e.target.value)}
                placeholder="e.g., 123456789"
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
          </div>

          <div className="pt-3 border-t border-[var(--border)] space-y-2.5 text-sm">
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={shariahDefault} onChange={(e) => setShariahDefault(e.target.checked)} />
              <span>
                <strong>Shariah-First Default Mode</strong> — automatically pre-filter Screeners &amp; Sector Explorer to KMI Shariah-compliant equities
              </span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={showTicker} onChange={(e) => setShowTicker(e.target.checked)} />
              <span>Show live scrolling PSX / FX / Gold market tape in top header</span>
            </label>
            <label className="flex items-center gap-2.5 cursor-pointer">
              <input type="checkbox" checked={compactTables} onChange={(e) => setCompactTables(e.target.checked)} />
              <span>Use compact financial table density</span>
            </label>
          </div>

          <button
            type="submit"
            disabled={saving === "workspace"}
            className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-60"
          >
            {saving === "workspace" ? "Saving..." : "Save Workspace Preferences"}
          </button>
        </form>
      )}

      {/* TAB 2: FBR TAX & ZAKAT */}
      {tab === "tax" && (
        <form onSubmit={handleSaveTax} className="okz-card p-6 space-y-4">
          <h2 className="font-semibold">Pakistan FBR Tax, Brokerage &amp; Zakat Rules</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs okz-muted mb-1">FBR Active Taxpayer List (ATL) Status</label>
              <select
                value={filerStatus}
                onChange={(e) => setFilerStatus(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              >
                <option value="ACTIVE_FILER">Active Filer (15% Sec 37A CGT / 15% Div WHT)</option>
                <option value="NON_FILER">Non-Filer (30% Div WHT / Doubled Tier)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">Default Fiscal Year</label>
              <select
                value={fiscalYear}
                onChange={(e) => setFiscalYear(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              >
                <option value="FY2025-26">FY2025-26 (Jul 2025 – Jun 2026)</option>
                <option value="FY2024-25">FY2024-25 (Jul 2024 – Jun 2025)</option>
                <option value="FY2023-24">FY2023-24 (Jul 2023 – Jun 2024)</option>
                <option value="FY2026-27">FY2026-27 (Jul 2026 – Jun 2027)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">
                Default PSX Brokerage Rate ({(Number(brokerRateBps || 0) / 100).toFixed(2)}%)
              </label>
              <input
                type="number"
                min="0"
                max="500"
                value={brokerRateBps}
                onChange={(e) => setBrokerRateBps(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
              <span className="text-[10px] okz-muted">Basis points (e.g., 15 bps = 0.15% PSX standard)</span>
            </div>
            <div>
              <label className="block text-xs okz-muted mb-1">Foreign Asset CGT Rate (%)</label>
              <input
                type="number"
                step="0.5"
                min="0"
                max="50"
                value={foreignCgtRatePct}
                onChange={(e) => setForeignCgtRatePct(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
              />
            </div>
          </div>

          <div className="pt-2 border-t border-[var(--border)] space-y-2.5 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={usW8BenActive} onChange={(e) => setUsW8BenActive(e.target.checked)} />
              <span>US W-8BEN Treaty Active (15% US Dividend WHT vs 30% statutory NRA)</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={zakatExempt} onChange={(e) => setZakatExempt(e.target.checked)} />
              <span>Zakat-Exempt Declaration Filed (CZ-50)</span>
            </label>
          </div>

          <button
            type="submit"
            disabled={saving === "tax"}
            className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-60"
          >
            {saving === "tax" ? "Saving..." : "Save Tax Profile"}
          </button>
        </form>
      )}

      {/* TAB 3: BACKUP & DATA SYNC */}
      {tab === "backup" && (
        <div className="space-y-4">
          <div className="okz-card p-6 space-y-4">
            <div>
              <h2 className="font-semibold">Portable JSON Workspace Backup &amp; Restore</h2>
              <p className="text-xs okz-muted mt-1">
                Export all your portfolios, buy/sell/dividend transactions, watchlists, FBR tax profile, and display preferences into one portable JSON file — ideal when migrating between local machines or Docker containers.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <button
                type="button"
                onClick={handleExportWorkspace}
                disabled={saving === "export"}
                className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold inline-flex items-center gap-1.5"
              >
                <Download className="w-4 h-4" />
                {saving === "export" ? "Exporting..." : "Download Workspace Backup (.json)"}
              </button>
              <label className="px-4 py-2 rounded-lg border border-[var(--border)] hover:border-sky-500 font-semibold inline-flex items-center gap-1.5 cursor-pointer">
                <Upload className="w-4 h-4 text-sky-400" />
                <span>{saving === "import" ? "Restoring..." : "Import / Restore Backup (.json)"}</span>
                <input type="file" accept=".json,application/json" onChange={handleImportWorkspace} className="hidden" />
              </label>
            </div>
          </div>

          <div className="okz-card p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold flex items-center gap-2">
                <Activity className="w-4 h-4 text-emerald-500" />
                Live Data Pipeline &amp; Scraper Control
              </h2>
              <p className="text-xs okz-muted mt-1">
                Monitor PSX, MUFAP, SECP FMR, SBP Macro, News RSS, and IPO scrapers or trigger manual refreshes.
              </p>
            </div>
            <Link
              href="/status"
              className="px-4 py-2 rounded-lg border border-[var(--border)] hover:border-emerald-500 text-xs font-semibold shrink-0"
            >
              Open Data Status &rarr;
            </Link>
          </div>
        </div>
      )}

      {/* TAB 4: CUSTOM ASSETS & SECURITY */}
      {tab === "assets" && (
        <div className="space-y-5">
          <div className="okz-card p-5 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold">Your Private Custom Assets ({customAssets.length})</h2>
                <p className="text-xs okz-muted">
                  Update mark-to-market valuations for plots, private sukuks, or jewellery created inside your workspace.
                </p>
              </div>
              <Link href="/portfolio" className="text-xs text-emerald-500 font-semibold hover:underline">
                Add via Portfolio &rarr;
              </Link>
            </div>
            {customAssets.length === 0 ? (
              <p className="text-xs okz-muted py-2">
                No private custom assets yet. Create one inside &ldquo;Portfolio &rarr; Add Transaction &rarr; Create Custom Asset&rdquo;.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs okz-muted border-b border-[var(--border)]">
                    <tr>
                      <th className="text-left py-2 pr-3">Symbol</th>
                      <th className="text-left py-2 pr-3">Name</th>
                      <th className="text-left py-2 pr-3">Class</th>
                      <th className="text-right py-2 pr-3">Current Valuation</th>
                      <th className="text-right py-2">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {customAssets.map((a) => (
                      <tr key={a.id}>
                        <td className="py-2 pr-3 font-semibold">{a.symbol}</td>
                        <td className="py-2 pr-3">{a.name}</td>
                        <td className="py-2 pr-3 text-xs okz-muted">
                          {a.asset_class} ({a.currency})
                        </td>
                        <td className="py-2 pr-3 text-right">
                          <input
                            type="number"
                            step="any"
                            min="0.0001"
                            value={priceEdits[a.id] ?? formatNumber(a.last_price, 2)}
                            onChange={(e) => setPriceEdits({ ...priceEdits, [a.id]: e.target.value })}
                            className="w-28 px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] text-right text-xs"
                          />
                        </td>
                        <td className="py-2 text-right space-x-2">
                          <button
                            type="button"
                            onClick={() => handleUpdateCustomPrice(a.id)}
                            className="px-2.5 py-1 rounded bg-emerald-600/15 text-emerald-500 text-xs font-semibold hover:bg-emerald-600/25"
                          >
                            Save Price
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteCustomAsset(a.id, a.symbol)}
                            className="p-1 text-[var(--text-muted)] hover:text-rose-500 align-middle"
                            title="Delete custom asset"
                          >
                            <Trash2 className="w-4 h-4 inline" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {!localMode && user && !user.is_demo && (
            <form onSubmit={handleChangePassword} className="okz-card p-5 space-y-3">
              <h2 className="font-semibold flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-emerald-500" />
                Change Password (Multi-User Security)
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs okz-muted mb-1">Current Password</label>
                  <input
                    type="password"
                    required
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs okz-muted mb-1">New Password (min 8 characters)</label>
                  <input
                    type="password"
                    required
                    minLength={8}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
                  />
                </div>
              </div>
              <button
                type="submit"
                disabled={saving === "password"}
                className="px-4 py-2 rounded-md border border-[var(--border)] hover:border-emerald-500 text-sm font-medium"
              >
                {saving === "password" ? "Updating..." : "Update Password"}
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
