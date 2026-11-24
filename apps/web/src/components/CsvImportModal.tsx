"use client";

import React, { useState } from "react";
import { Upload, X } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface PreviewResponse {
  headers: string[];
  sample_rows: Array<Record<string, string>>;
  suggested_mapping: Record<string, string | null>;
  available_presets: string[];
  normalized_csv_text?: string;
}

interface CommitResponse {
  inserted: number;
  skipped_duplicates: number;
  errors: string[];
}

interface CsvImportModalProps {
  portfolioId: string;
  onClose: () => void;
  onImported: () => void;
}

const SAMPLE_CSV = `date,symbol,type,qty,price,fee,tax,notes
2025-02-10,MEBL,BUY,200,220.00,150.00,0,KTrade sample lot
2025-03-05,SYS,BUY,50,145.00,45.00,0,Core IT trade`;

const PRESET_LABELS: Record<string, string> = {
  generic: "Generic CSV / Excel",
  ktrade: "KTrade Export (PSX)",
  js_global: "JS Global / Zindigi (PSX)",
  arif_habib: "Arif Habib Limited — AHL (PSX)",
  cdc_investor: "CDC Investor Account (PSX)",
  ibkr: "Interactive Brokers — IBKR (US / Global)",
  elphinstone: "Elphinstone SmartRupee (US Stocks)",
  sarwa: "Sarwa Trade / Global ETFs",
};

export function CsvImportModal({ portfolioId, onClose, onImported }: CsvImportModalProps) {
  const [preset, setPreset] = useState("generic");
  const [csvText, setCsvText] = useState(SAMPLE_CSV);
  const [fileName, setFileName] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [defaultFxRate, setDefaultFxRate] = useState("1");
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [autoCalculateCharges, setAutoCalculateCharges] = useState(false);
  const [result, setResult] = useState<CommitResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applySuggestedMapping = (res: PreviewResponse) => {
    setPreview(res);
    const initialMap: Record<string, string> = {};
    for (const key of ["tx_date", "symbol", "tx_type", "quantity", "unit_price", "fee_amount", "tax_withheld", "notes"]) {
      initialMap[key] = res.suggested_mapping[key] || "";
    }
    setMapping(initialMap);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setResult(null);
    setFileName(file.name);

    const lower = file.name.toLowerCase();
    if (lower.endsWith(".xlsx") || lower.endsWith(".xlsm")) {
      setLoading(true);
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("preset", preset);
        const res = await apiFetch<PreviewResponse>("/api/v1/portfolios/import/preview-file", {
          method: "POST",
          body: formData,
        });
        if (res.normalized_csv_text) {
          setCsvText(res.normalized_csv_text);
        }
        applySuggestedMapping(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to parse Excel workbook");
      } finally {
        setLoading(false);
      }
      return;
    }

    const text = await file.text();
    setCsvText(text);
    setPreview(null);
  };

  const runPreview = async () => {
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const res = await apiFetch<PreviewResponse>("/api/v1/portfolios/csv/preview", {
        method: "POST",
        body: JSON.stringify({ preset, csv_text: csvText }),
      });
      applySuggestedMapping(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to preview CSV");
    } finally {
      setLoading(false);
    }
  };

  const handleCommit = async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await apiFetch<CommitResponse>("/api/v1/portfolios/csv/commit", {
        method: "POST",
        body: JSON.stringify({
          portfolio_id: portfolioId,
          csv_text: csvText,
          default_fx_rate_to_pkr: Number(defaultFxRate) || 1,
          skip_duplicates: skipDuplicates,
          auto_calculate_charges: autoCalculateCharges,
          mapping: {
            tx_date: mapping.tx_date,
            symbol: mapping.symbol,
            tx_type: mapping.tx_type,
            quantity: mapping.quantity,
            unit_price: mapping.unit_price,
            fee_amount: mapping.fee_amount || null,
            tax_withheld: mapping.tax_withheld || null,
            notes: mapping.notes || null,
          },
        }),
      });
      setResult(res);
      if (res.inserted > 0) {
        onImported();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import transactions");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="okz-card w-full max-w-3xl p-6 shadow-2xl max-h-[90vh] overflow-y-auto space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">Broker CSV &amp; Excel Statement Importer</h2>
            <p className="text-xs okz-muted">
              Supports `.csv` and `.xlsx`/`.xlsm` statements with column mapping, duplicate detection, and auto-PSX fees.
            </p>
          </div>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-[var(--bg-elevated)]" aria-label="Close modal">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium okz-muted mb-1">Broker / Statement Preset</label>
            <select
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm"
            >
              {(preview?.available_presets || Object.keys(PRESET_LABELS)).map((p) => (
                <option key={p} value={p}>
                  {PRESET_LABELS[p] || p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium okz-muted mb-1">Upload File (.csv, .xlsx, .xlsm)</label>
            <label className="flex items-center justify-between gap-2 px-3 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-sm cursor-pointer hover:border-emerald-500">
              <span className="truncate">{fileName || "Choose broker statement..."}</span>
              <Upload className="w-4 h-4 okz-muted shrink-0" />
              <input
                type="file"
                accept=".csv,.xlsx,.xlsm"
                onChange={handleFileUpload}
                className="hidden"
              />
            </label>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium okz-muted mb-1">
            CSV Data {fileName?.toLowerCase().endsWith(".xlsx") || fileName?.toLowerCase().endsWith(".xlsm") ? "(Normalized from Excel workbook)" : "(or paste statement rows directly)"}
          </label>
          <textarea
            rows={5}
            value={csvText}
            onChange={(e) => setCsvText(e.target.value)}
            className="w-full p-3 rounded-md border border-[var(--border)] bg-[var(--bg)] font-mono text-xs"
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4 text-xs">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={skipDuplicates}
                onChange={(e) => setSkipDuplicates(e.target.checked)}
              />
              <span>Skip duplicate rows</span>
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={autoCalculateCharges}
                onChange={(e) => setAutoCalculateCharges(e.target.checked)}
              />
              <span>Auto-calculate PSX fees &amp; dividend WHT when 0</span>
            </label>
          </div>
          <button
            type="button"
            onClick={runPreview}
            disabled={loading}
            className="px-4 py-2 rounded-md bg-[var(--bg-elevated)] border border-[var(--border)] text-sm font-medium hover:border-emerald-500"
          >
            {loading ? "Parsing..." : "Preview & Detect Columns"}
          </button>
        </div>

        {preview && (
          <div className="space-y-3 border-t border-[var(--border)] pt-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Column Mapping ({preview.headers.length} columns detected)</h3>
              <div className="flex items-center gap-2 text-xs">
                <span className="okz-muted">Default USD FX Rate:</span>
                <input
                  type="number"
                  step="any"
                  min="1"
                  value={defaultFxRate}
                  onChange={(e) => setDefaultFxRate(e.target.value)}
                  className="w-20 px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] text-xs"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { key: "tx_date", label: "Date *" },
                { key: "symbol", label: "Symbol *" },
                { key: "tx_type", label: "Type *" },
                { key: "quantity", label: "Quantity *" },
                { key: "unit_price", label: "Price *" },
                { key: "fee_amount", label: "Fee (Optional)" },
                { key: "tax_withheld", label: "Tax WHT (Optional)" },
                { key: "notes", label: "Notes (Optional)" },
              ].map((field) => (
                <div key={field.key}>
                  <label className="block text-xs okz-muted mb-1">{field.label}</label>
                  <select
                    value={mapping[field.key] || ""}
                    onChange={(e) => setMapping({ ...mapping, [field.key]: e.target.value })}
                    className="w-full px-2 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
                  >
                    <option value="">-- Unmapped --</option>
                    {preview.headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>

            {preview.sample_rows.length > 0 && (
              <div className="overflow-x-auto border border-[var(--border)] rounded-md">
                <table className="w-full text-xs">
                  <thead className="bg-[var(--bg-elevated)] okz-muted">
                    <tr>
                      {preview.headers.slice(0, 8).map((h) => (
                        <th key={h} className="text-left py-1.5 px-2 font-medium whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {preview.sample_rows.slice(0, 4).map((row, idx) => (
                      <tr key={idx}>
                        {preview.headers.slice(0, 8).map((h) => (
                          <td key={h} className="py-1.5 px-2 whitespace-nowrap truncate max-w-32">
                            {row[h] ?? ""}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={handleCommit}
                disabled={loading}
                className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-60"
              >
                {loading ? "Importing..." : "Commit Import"}
              </button>
            </div>
          </div>
        )}

        {error && <p className="text-xs text-rose-500">{error}</p>}

        {result && (
          <div className="p-3 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-sm space-y-1">
            <div className="font-semibold text-emerald-500">
              Imported {result.inserted} transactions
              {result.skipped_duplicates > 0 ? ` (${result.skipped_duplicates} duplicate row(s) skipped)` : ""}.
            </div>
            {result.errors.length > 0 && (
              <div className="text-xs text-amber-500 max-h-28 overflow-y-auto">
                Skipped rows ({result.errors.length}): {result.errors.slice(0, 6).join(" | ")}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
