"use client";

import React from "react";

export function KpiGridSkeleton({
  count = 6,
  cols = "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6",
}: {
  count?: number;
  cols?: string;
}) {
  return (
    <div className={`grid ${cols} gap-3`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="okz-card p-3.5 space-y-2 animate-pulse">
          <div className="h-3 w-2/3 rounded bg-[var(--card-muted)]" />
          <div className="h-6 w-1/2 rounded bg-[var(--card-muted)]" />
          <div className="h-2.5 w-3/4 rounded bg-[var(--card-muted)]" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 8, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="okz-card overflow-hidden animate-pulse">
      <div className="p-3 border-b border-[var(--border)] bg-[var(--card-muted)]/60 flex gap-3">
        {Array.from({ length: cols }).map((_, i) => (
          <div key={i} className="h-3 flex-1 rounded bg-[var(--border)]" />
        ))}
      </div>
      <div className="divide-y divide-[var(--border)]">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="p-3 flex items-center gap-3">
            {Array.from({ length: cols }).map((_, c) => (
              <div
                key={c}
                className={`h-3.5 rounded bg-[var(--card-muted)] ${c === 0 ? "w-24 shrink-0" : "flex-1"}`}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ChartSkeleton({ heightClass = "h-60" }: { heightClass?: string }) {
  return (
    <div className={`okz-card p-5 space-y-3 animate-pulse`}>
      <div className="flex justify-between">
        <div className="h-4 w-56 rounded bg-[var(--card-muted)]" />
        <div className="h-3 w-28 rounded bg-[var(--card-muted)]" />
      </div>
      <div className={`${heightClass} w-full rounded-lg bg-[var(--card-muted)]/70`} />
    </div>
  );
}

export function CardListSkeleton({
  count = 6,
  cols = "grid-cols-1 md:grid-cols-2 lg:grid-cols-3",
}: {
  count?: number;
  cols?: string;
}) {
  return (
    <div className={`grid ${cols} gap-4`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="okz-card p-4 space-y-2.5 animate-pulse">
          <div className="flex justify-between gap-2">
            <div className="h-4 w-1/2 rounded bg-[var(--card-muted)]" />
            <div className="h-4 w-16 rounded bg-[var(--card-muted)]" />
          </div>
          <div className="h-3 w-full rounded bg-[var(--card-muted)]" />
          <div className="h-3 w-4/5 rounded bg-[var(--card-muted)]" />
          <div className="flex gap-2 pt-1">
            <div className="h-5 w-14 rounded bg-[var(--card-muted)]" />
            <div className="h-5 w-14 rounded bg-[var(--card-muted)]" />
          </div>
        </div>
      ))}
    </div>
  );
}
