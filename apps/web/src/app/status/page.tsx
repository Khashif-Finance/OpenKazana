"use client";

import React, { useCallback, useEffect, useState } from "react";
import { KeyRound, RefreshCw, ShieldAlert } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";

import { formatNumber } from "@/lib/format";

interface ScraperRun {
  id: string;
  scraper_name: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  records_upserted: number;
  latency_ms: number;
  dlq_path?: string | null;
  error_detail: string | null;
}

interface DataCoverage {
  psx_total: number;
  psx_with_history: number;
  psx_deep_fundamentals: number;
  psx_financial_report_periods?: number;
  psx_multi_year_profiles?: number;
  kse100_total?: number;
  kse100_deep_fundamentals?: number;
  funds_total: number;
  funds_with_history: number;
  funds_with_fmr?: number;
  amc_count: number;
  amcs_with_history?: number;
  intl_total?: number;
  crypto_with_5y?: number;
  fx_gold_with_5y?: number;
  macro_indicators: number;
  total_price_bars: number;
  risk_free_rate_pkr_pct: number;
  risk_free_rate_usd_pct: number;
}

const SCRAPERS = [
  { name: "psx_intraday", label: "PSX DPS Intraday" },
  { name: "psx_eod_history", label: "PSX EOD History (5Y Batch)" },
  { name: "psx_fundamentals", label: "PSX Fundamentals & PUCARS" },
  { name: "psx_deep_fundamentals", label: "PUCARS PDF Balance Sheets" },
  { name: "psx_reports", label: "PUCARS Annual & Qtr PDFs" },
  { name: "mufap_nav", label: "Multi-AMC & MUFAP NAV" },
  { name: "mufap_history", label: "AMC Historical NAV Backfill" },
  { name: "fmr_reports", label: "SECP Monthly FMR PDFs" },
  { name: "sbp_macro", label: "SBP Policy, KIBOR & PKRV" },
  { name: "intl_stocks", label: "Yahoo Finance Intl + Deep" },
  { name: "fx_gold", label: "FX, Gold & Silver" },
  { name: "crypto_spot", label: "Crypto Spot" },
  { name: "pk_news", label: "Pakistan Financial News RSS" },
  { name: "psx_ipos", label: "PSX IPOs & Listings" },
];

export default function StatusPage() {
  const { user } = useAppContext();
  const [runs, setRuns] = useState<ScraperRun[]>([]);
  const [coverage, setCoverage] = useState<DataCoverage | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [syncSummary, setSyncSummary] = useState<string | null>(null);
  const [adminKey, setAdminKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    const [data, cov] = await Promise.all([
      apiFetch<ScraperRun[]>("/api/v1/market/scraper-status"),
      apiFetch<DataCoverage>("/api/v1/market/coverage").catch(() => null),
    ]);
    setRuns(data);
    if (cov) setCoverage(cov);
  }, []);

  useEffect(() => {
    loadStatus().catch(() => {});
  }, [loadStatus]);

  const hasActiveJobs = runs.some((r) => r.status === "QUEUED" || r.status === "RUNNING");

  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(() => {
      loadStatus().catch(() => {});
    }, 3500);
    return () => clearInterval(timer);
  }, [hasActiveJobs, loadStatus]);

  const handleSyncAll = async () => {
    setBusy("sync_all");
    setError(null);
    setSyncSummary(null);
    try {
      const headers: Record<string, string> = {};
      if (adminKey.trim()) headers["X-Admin-Key"] = adminKey.trim();
      const res = await apiFetch<{ total_records_upserted: number; scrapers: Record<string, { status: string; count: number }> }>(
        "/api/v1/market/sync-all?include_eod_history=false&include_fundamentals=false",
        {
          method: "POST",
          headers,
        }
      );
      setSyncSummary(`Live sync completed: ${res.total_records_upserted} records updated across all feeds.`);
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Live market sync failed.");
    } finally {
      setBusy("");
    }
  };

  const handleTrigger = async (name: string) => {
    setBusy(name);
    setError(null);
    try {
      const headers: Record<string, string> = {};
      if (adminKey.trim()) headers["X-Admin-Key"] = adminKey.trim();
      await apiFetch(`/api/v1/market/scrapers/${name}/trigger`, {
        method: "POST",
        headers,
      });
      await loadStatus();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Trigger rejected. Admin account login or valid X-Admin-Key header required."
      );
    } finally {
      setBusy("");
    }
  };

  const badgeClass = (st: string) => {
    if (st === "SUCCESS") return "bg-emerald-500/15 text-emerald-500";
    if (st === "QUEUED" || st === "RUNNING") return "bg-amber-500/15 text-amber-500 animate-pulse";
    return "bg-rose-500/15 text-rose-500";
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Market Data Pipeline &amp; Scraper Health</h1>
          <p className="text-sm okz-muted">
            Live observability across PSX DPS, PUCARS PDF Balance Sheets, SBP Macro, Multi-AMC Mutual Funds, FX/Gold, and Crypto workers.
          </p>
        </div>
        <button
          type="button"
          onClick={() => loadStatus().catch(() => {})}
          className="self-start inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--border)] text-xs font-semibold hover:border-emerald-500"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${hasActiveJobs ? "animate-spin text-amber-500" : ""}`} />
          {hasActiveJobs ? "Polling Active Jobs..." : "Refresh Logs"}
        </button>
      </div>

      {coverage && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
          <div className="okz-card p-3.5 space-y-1.5">
            <div className="flex justify-between okz-muted">
              <span>PSX EOD History Coverage</span>
              <span className="font-mono font-bold text-[var(--text)]">
                {coverage.psx_with_history} / {coverage.psx_total}
              </span>
            </div>
            <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
              <div
                className="h-full bg-emerald-500"
                style={{
                  width: `${Math.min(100, coverage.psx_total > 0 ? (coverage.psx_with_history / coverage.psx_total) * 100 : 0)}%`,
                }}
              />
            </div>
            <div className="text-[11px] okz-muted">
              Total PriceBars: {formatNumber(coverage.total_price_bars, 0)}
            </div>
          </div>

          <div className="okz-card p-3.5 space-y-1.5">
            <div className="flex justify-between okz-muted">
              <span>KSE-100 / PSX Deep Balance Sheets</span>
              <span className="font-mono font-bold text-[var(--text)]">
                {coverage.kse100_deep_fundamentals ?? coverage.psx_deep_fundamentals} /{" "}
                {coverage.kse100_total || coverage.psx_total}
              </span>
            </div>
            <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
              <div
                className="h-full bg-sky-500"
                style={{
                  width: `${Math.min(
                    100,
                    (coverage.kse100_total || coverage.psx_total) > 0
                      ? ((coverage.kse100_deep_fundamentals ?? coverage.psx_deep_fundamentals) /
                          (coverage.kse100_total || coverage.psx_total)) *
                          100
                      : 0
                  )}%`,
                }}
              />
            </div>
            <div className="text-[11px] okz-muted">
              {coverage.psx_deep_fundamentals} deep BS/CF &bull;{" "}
              <span className="text-sky-400 font-semibold">{coverage.psx_financial_report_periods ?? 0}</span> multi-period reports
            </div>
          </div>

          <div className="okz-card p-3.5 space-y-1.5">
            <div className="flex justify-between okz-muted">
              <span>AMC Mutual Funds ({coverage.amc_count} AMCs)</span>
              <span className="font-mono font-bold text-[var(--text)]">
                {coverage.funds_with_history} / {coverage.funds_total}
              </span>
            </div>
            <div className="h-1.5 w-full rounded bg-[var(--border)] overflow-hidden">
              <div
                className="h-full bg-amber-500"
                style={{
                  width: `${Math.min(100, coverage.funds_total > 0 ? (coverage.funds_with_history / coverage.funds_total) * 100 : 0)}%`,
                }}
              />
            </div>
            <div className="text-[11px] okz-muted">
              {coverage.amcs_with_history ?? 0} AMCs w/ NAV history &bull;{" "}
              <span className="text-emerald-500 font-semibold">{coverage.funds_with_fmr ?? 0} SECP FMRs</span>
            </div>
          </div>

          <div className="okz-card p-3.5 space-y-1.5">
            <div className="flex justify-between okz-muted">
              <span>Macro &amp; 5Y Global / FX / Crypto</span>
              <span className="font-mono font-bold text-emerald-500">{coverage.macro_indicators} Macro</span>
            </div>
            <div className="flex justify-between text-xs font-mono pt-1">
              <span>PKR Rf: {coverage.risk_free_rate_pkr_pct.toFixed(2)}%</span>
              <span>USD Rf: {coverage.risk_free_rate_usd_pct.toFixed(2)}%</span>
            </div>
            <div className="text-[11px] okz-muted">
              {coverage.intl_total ?? 0} Global/UCITS &bull; {coverage.crypto_with_5y ?? 0} Crypto &bull;{" "}
              {coverage.fx_gold_with_5y ?? 0} FX/Gold (5Y)
            </div>
          </div>
        </div>
      )}

      <div className="okz-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs okz-muted">
            Manual triggers require an <span className="font-semibold text-[var(--text)]">Admin session</span>
            {user?.is_admin ? " (you are signed in as Admin)" : ""} or the <span className="font-mono">X-Admin-Key</span>.
          </div>
          <div className="flex items-center gap-2">
            <KeyRound className="w-3.5 h-3.5 okz-muted" />
            <input
              type="password"
              value={adminKey}
              onChange={(e) => setAdminKey(e.target.value)}
              placeholder="Optional X-Admin-Key"
              className="w-48 px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] text-xs"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleSyncAll}
            disabled={busy !== ""}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${busy === "sync_all" ? "animate-spin" : ""}`} />
            {busy === "sync_all" ? "Syncing Live Markets..." : "Sync All Live Market Feeds Now"}
          </button>
          {SCRAPERS.map((s) => (
            <button
              key={s.name}
              onClick={() => handleTrigger(s.name)}
              disabled={busy === s.name}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg border border-[var(--border)] hover:bg-[var(--card-muted)] hover:border-emerald-500 text-xs font-semibold disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${busy === s.name ? "animate-spin" : ""}`} />
              Queue {s.label}
            </button>
          ))}
        </div>

        {syncSummary && (
          <div className="p-2.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-500 font-medium">
            {syncSummary}
          </div>
        )}

        {error && (
          <div className="p-2.5 rounded-md bg-rose-500/10 border border-rose-500/30 text-xs text-rose-500 flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>

      <div className="okz-card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--border)] text-xs okz-muted text-left">
            <tr>
              <th className="py-3 px-4">Scraper</th>
              <th className="py-3 px-4">Status</th>
              <th className="py-3 px-4 text-right">Records</th>
              <th className="py-3 px-4 text-right">Latency</th>
              <th className="py-3 px-4">Started</th>
              <th className="py-3 px-4">Notes / DLQ</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)] text-xs">
            {runs.map((r) => {
              const isExpanded = expandedRunId === r.id;
              return (
                <React.Fragment key={r.id}>
                  <tr
                    onClick={() => setExpandedRunId(isExpanded ? null : r.id)}
                    className="hover:bg-[var(--card-muted)]/40 cursor-pointer"
                  >
                    <td className="py-3 px-4 font-bold">{r.scraper_name}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded font-semibold ${badgeClass(r.status)}`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">{r.records_upserted}</td>
                    <td className="py-3 px-4 text-right">{r.latency_ms} ms</td>
                    <td className="py-3 px-4 okz-muted">{new Date(r.started_at).toLocaleString()}</td>
                    <td className="py-3 px-4 okz-muted max-w-xs truncate">
                      {r.error_detail || (r.status === "SUCCESS" ? "OK" : "—")}
                    </td>
                  </tr>
                  {isExpanded && (r.error_detail || r.dlq_path) && (
                    <tr className="bg-[var(--bg-elevated)]">
                      <td colSpan={6} className="px-4 py-3 text-xs space-y-1 font-mono">
                        {r.error_detail && <div className="whitespace-pre-wrap break-all">{r.error_detail}</div>}
                        {r.dlq_path && <div className="text-amber-500">DLQ Artifact: {r.dlq_path}</div>}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
