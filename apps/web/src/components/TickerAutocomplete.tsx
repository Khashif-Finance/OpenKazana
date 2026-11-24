"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { AssetIndexItem, filterAssetIndex, getAssetIndex } from "@/lib/api";

interface TickerAutocompleteProps {
  value: string;
  onChange: (value: string) => void;
  onSelect?: (item: AssetIndexItem) => void;
  placeholder?: string;
  assetClasses?: string[];
  className?: string;
  inputClassName?: string;
  clearOnSelect?: boolean;
  showClear?: boolean;
  limit?: number;
}

export function TickerAutocomplete({
  value,
  onChange,
  onSelect,
  placeholder = "Search ticker or company (e.g. MEBL, Meezan, LUCK)...",
  assetClasses,
  className = "",
  inputClassName = "",
  clearOnSelect = false,
  showClear = true,
  limit = 10,
}: TickerAutocompleteProps) {
  const [index, setIndex] = useState<AssetIndexItem[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let mounted = true;
    getAssetIndex()
      .then((rows) => {
        if (mounted) setIndex(rows);
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const matches = useMemo(
    () => filterAssetIndex(index, value, { assetClasses, limit }),
    [index, value, assetClasses, limit],
  );

  useEffect(() => {
    setActiveIdx(0);
  }, [value]);

  const handlePick = (item: AssetIndexItem) => {
    onChange(clearOnSelect ? "" : item.symbol);
    onSelect?.(item);
    setOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((prev) => (matches.length > 0 ? (prev + 1) % matches.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((prev) => (matches.length > 0 ? (prev - 1 + matches.length) % matches.length : 0));
    } else if (e.key === "Enter" && open && matches.length > 0) {
      e.preventDefault();
      handlePick(matches[Math.min(activeIdx, matches.length - 1)]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className={`relative ${className}`}>
      <div className="relative flex items-center">
        <Search className="w-3.5 h-3.5 okz-muted absolute left-2.5 pointer-events-none" />
        <input
          type="text"
          value={value}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          autoComplete="off"
          className={`w-full pl-8 pr-7 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs focus:outline-none focus:border-emerald-500 ${inputClassName}`}
        />
        {showClear && value && (
          <button
            type="button"
            onClick={() => {
              onChange("");
              setOpen(false);
            }}
            aria-label="Clear search"
            className="absolute right-2 p-0.5 rounded okz-muted hover:text-[var(--text)]"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {open && matches.length > 0 && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 okz-card bg-[var(--card)] border border-[var(--border)] shadow-2xl max-h-64 overflow-y-auto divide-y divide-[var(--border)]">
          {matches.map((item, i) => {
            const isActive = i === activeIdx;
            return (
              <button
                key={`${item.asset_class}-${item.symbol}`}
                type="button"
                onMouseEnter={() => setActiveIdx(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  handlePick(item);
                }}
                className={`w-full px-3 py-2 text-left flex items-center justify-between gap-2 transition ${
                  isActive ? "bg-emerald-500/15" : "hover:bg-[var(--card-muted)]"
                }`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono font-bold text-xs text-[var(--text)]">{item.symbol}</span>
                    <span className="text-[9px] px-1 py-0.5 rounded bg-[var(--card-muted)] okz-muted">
                      {item.asset_class.replace("_EQUITY", "").replace("_", " ")}
                    </span>
                    {item.is_shariah_compliant && (
                      <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-500">Shariah</span>
                    )}
                  </div>
                  <div className="text-[11px] okz-muted truncate">{item.name}</div>
                </div>
                <div className="text-right shrink-0 font-mono">
                  <div className="text-[11px] font-semibold">
                    {item.last_price > 0 ? `${item.currency} ${item.last_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"}
                  </div>
                  <div className={`text-[10px] ${item.change_pct >= 0 ? "okz-pos" : "okz-neg"}`}>
                    {item.change_pct >= 0 ? "+" : ""}
                    {item.change_pct.toFixed(2)}%
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
