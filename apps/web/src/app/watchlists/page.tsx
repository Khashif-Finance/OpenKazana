"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bell, Check, CheckCheck, Pause, Play, Plus, Trash2, X } from "lucide-react";
import { useAppContext } from "@/components/CurrencyContext";
import { apiFetch } from "@/lib/api";
import { formatMoney, formatPct, pctColorClass } from "@/lib/format";

interface WatchlistData {
  id: string;
  name: string;
  items: Array<{
    symbol: string;
    name: string;
    asset_class: string;
    last_price: string;
    change_pct: string;
  }>;
}

interface AlertData {
  id: string;
  asset_symbol: string;
  rule_type: string;
  threshold_value: string | null;
  channels: string[];
  is_active: boolean;
}

interface NotificationData {
  id: string;
  title: string;
  body: string;
  is_read: boolean;
  created_at: string;
}

export default function WatchlistsPage() {
  const { user, currency, usdToPkr, refreshNotifications } = useAppContext();
  const [watchlists, setWatchlists] = useState<WatchlistData[]>([]);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [notifs, setNotifs] = useState<NotificationData[]>([]);
  const [newWatchlistName, setNewWatchlistName] = useState("");
  const [symbolInputs, setSymbolInputs] = useState<Record<string, string>>({});
  const [alertSymbol, setAlertSymbol] = useState("MEBL");
  const [ruleType, setRuleType] = useState("PRICE_ABOVE");
  const [threshold, setThreshold] = useState("560");
  const [editingAlertId, setEditingAlertId] = useState<string | null>(null);
  const [editThreshold, setEditThreshold] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    if (!user) return;
    const [w, a, n] = await Promise.all([
      apiFetch<WatchlistData[]>("/api/v1/watchlists"),
      apiFetch<AlertData[]>("/api/v1/alerts"),
      apiFetch<NotificationData[]>("/api/v1/notifications"),
    ]);
    setWatchlists(w);
    setAlerts(a);
    setNotifs(n);
    await refreshNotifications();
  }, [user, refreshNotifications]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleCreateWatchlist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWatchlistName.trim()) return;
    setError(null);
    try {
      await apiFetch("/api/v1/watchlists", {
        method: "POST",
        body: JSON.stringify({ name: newWatchlistName.trim() }),
      });
      setNewWatchlistName("");
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create watchlist");
    }
  };

  const handleDeleteWatchlist = async (wlId: string) => {
    setError(null);
    try {
      await apiFetch(`/api/v1/watchlists/${wlId}`, { method: "DELETE" });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete watchlist");
    }
  };

  const handleAddWatchItem = async (wlId: string) => {
    const sym = (symbolInputs[wlId] || "HUBC").trim().toUpperCase();
    if (!sym) return;
    setError(null);
    try {
      await apiFetch(`/api/v1/watchlists/${wlId}/items/${encodeURIComponent(sym)}`, { method: "POST" });
      setSymbolInputs((prev) => ({ ...prev, [wlId]: "" }));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Unable to add ${sym}`);
    }
  };

  const handleRemoveWatchItem = async (wlId: string, symbol: string) => {
    setError(null);
    await apiFetch(`/api/v1/watchlists/${wlId}/items/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    await loadAll();
  };

  const handleCreateAlert = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await apiFetch("/api/v1/alerts", {
        method: "POST",
        body: JSON.stringify({
          asset_symbol: alertSymbol.trim().toUpperCase(),
          rule_type: ruleType,
          threshold_value: ruleType === "ANNOUNCEMENT" ? null : threshold,
          channels: ["in_app", "email"],
        }),
      });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create alert rule");
    }
  };

  const handleToggleAlert = async (alert: AlertData) => {
    setError(null);
    await apiFetch(`/api/v1/alerts/${alert.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !alert.is_active }),
    });
    await loadAll();
  };

  const handleSaveThreshold = async (alertId: string) => {
    setError(null);
    await apiFetch(`/api/v1/alerts/${alertId}`, {
      method: "PATCH",
      body: JSON.stringify({ threshold_value: editThreshold }),
    });
    setEditingAlertId(null);
    await loadAll();
  };

  const handleDeleteAlert = async (id: string) => {
    setError(null);
    await apiFetch(`/api/v1/alerts/${id}`, { method: "DELETE" });
    await loadAll();
  };

  const handleMarkRead = async (notifId: string) => {
    await apiFetch(`/api/v1/notifications/${notifId}/read`, { method: "POST" });
    await loadAll();
  };

  const handleMarkAllRead = async () => {
    await apiFetch("/api/v1/notifications/read-all", { method: "POST" });
    await loadAll();
  };

  if (!user) {
    return (
      <div className="okz-card p-8 max-w-lg mx-auto text-center space-y-4">
        <h1 className="text-xl font-bold">Watchlists &amp; Real-Time Alerts</h1>
        <p className="text-sm okz-muted">Sign in or create an account to manage watchlists and real-time alerts.</p>
        <div className="flex justify-center gap-3">
          <Link href="/auth" className="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold">
            Sign In / Create Account
          </Link>
        </div>
      </div>
    );
  }

  const unreadCount = notifs.filter((n) => !n.is_read).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Watchlists, Alerts &amp; Notifications</h1>
        <form onSubmit={handleCreateWatchlist} className="flex items-center gap-2">
          <input
            value={newWatchlistName}
            onChange={(e) => setNewWatchlistName(e.target.value)}
            placeholder="New watchlist name..."
            className="px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--bg)] text-xs"
          />
          <button
            type="submit"
            className="px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold inline-flex items-center gap-1"
          >
            <Plus className="w-3.5 h-3.5" /> New Watchlist
          </button>
        </form>
      </div>

      {error && (
        <div className="okz-card p-3 border-rose-500/40 bg-rose-500/10 text-xs text-rose-500 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="hover:underline">Dismiss</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-4">
          {watchlists.length === 0 ? (
            <div className="okz-card p-6 text-sm okz-muted text-center">
              No watchlists yet. Create your first watchlist above.
            </div>
          ) : (
            watchlists.map((wl) => (
              <div key={wl.id} className="okz-card p-5 space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-sm">{wl.name}</span>
                    <span className="text-[11px] okz-muted">({wl.items.length})</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      value={symbolInputs[wl.id] ?? ""}
                      onChange={(e) => setSymbolInputs((prev) => ({ ...prev, [wl.id]: e.target.value }))}
                      placeholder="Symbol (e.g. HUBC)"
                      className="w-28 px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] text-xs uppercase"
                    />
                    <button
                      onClick={() => handleAddWatchItem(wl.id)}
                      className="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold inline-flex items-center gap-1"
                    >
                      <Plus className="w-3.5 h-3.5" /> Add
                    </button>
                    <button
                      onClick={() => handleDeleteWatchlist(wl.id)}
                      title="Delete watchlist"
                      className="p-1 text-[var(--text-muted)] hover:text-rose-500"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                {wl.items.length === 0 ? (
                  <p className="text-xs okz-muted py-2">Empty watchlist. Add any PSX, MUFAP, FX, or Crypto symbol above.</p>
                ) : (
                  <div className="divide-y divide-[var(--border)]">
                    {wl.items.map((item) => (
                      <div key={item.symbol} className="py-2 flex items-center justify-between text-xs gap-2">
                        <Link
                          href={item.asset_class.startsWith("PSX") ? `/stocks/${item.symbol}` : `/funds?q=${encodeURIComponent(item.symbol)}`}
                          className="font-bold text-emerald-500 hover:underline truncate"
                        >
                          {item.symbol} <span className="okz-muted font-normal">({item.name})</span>
                        </Link>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="font-semibold">{formatMoney(item.last_price, currency, usdToPkr)}</span>
                          <span className={pctColorClass(item.change_pct)}>{formatPct(item.change_pct)}</span>
                          <button
                            onClick={() => handleRemoveWatchItem(wl.id, item.symbol)}
                            title={`Remove ${item.symbol}`}
                            className="p-0.5 text-[var(--text-muted)] hover:text-rose-500"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="space-y-6">
          <div className="okz-card p-5 space-y-4">
            <h2 className="font-semibold text-sm flex items-center gap-2">
              <Bell className="w-4 h-4 text-emerald-500" /> Price &amp; PUCARS Announcement Alerts
            </h2>
            <form onSubmit={handleCreateAlert} className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <input
                value={alertSymbol}
                onChange={(e) => setAlertSymbol(e.target.value)}
                placeholder="Symbol"
                className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] text-xs uppercase"
              />
              <select
                value={ruleType}
                onChange={(e) => setRuleType(e.target.value)}
                className="px-2 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] text-xs"
              >
                <option value="PRICE_ABOVE">Price Above</option>
                <option value="PRICE_BELOW">Price Below</option>
                <option value="ANNOUNCEMENT">PSX Notice</option>
              </select>
              <input
                type="number"
                step="any"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                disabled={ruleType === "ANNOUNCEMENT"}
                className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-[var(--bg)] text-xs disabled:opacity-40"
              />
              <button type="submit" className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold">
                Create Rule
              </button>
            </form>

            <div className="divide-y divide-[var(--border)] text-xs">
              {alerts.map((a) => (
                <div key={a.id} className="py-2.5 flex items-center justify-between gap-2">
                  <div className={!a.is_active ? "opacity-55" : ""}>
                    <span className="font-bold">{a.asset_symbol}</span> &bull; {a.rule_type}{" "}
                    {editingAlertId === a.id && a.rule_type !== "ANNOUNCEMENT" ? (
                      <span className="inline-flex items-center gap-1 ml-1">
                        <input
                          type="number"
                          step="any"
                          value={editThreshold}
                          onChange={(e) => setEditThreshold(e.target.value)}
                          className="w-20 px-1.5 py-0.5 rounded border border-[var(--border)] bg-[var(--bg)]"
                        />
                        <button
                          type="button"
                          onClick={() => handleSaveThreshold(a.id)}
                          className="text-emerald-500 font-semibold"
                        >
                          Save
                        </button>
                      </span>
                    ) : a.threshold_value ? (
                      <button
                        type="button"
                        onClick={() => {
                          setEditingAlertId(a.id);
                          setEditThreshold(a.threshold_value || "");
                        }}
                        title="Click to edit threshold"
                        className="underline decoration-dotted hover:text-emerald-500"
                      >
                        @ {a.threshold_value}
                      </button>
                    ) : null}{" "}
                    <span className="okz-muted">({a.channels.join(", ")})</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleToggleAlert(a)}
                      title={a.is_active ? "Pause alert" : "Resume alert"}
                      className={`px-2 py-0.5 rounded border text-[11px] inline-flex items-center gap-1 ${
                        a.is_active
                          ? "border-emerald-500/30 text-emerald-500"
                          : "border-amber-500/30 text-amber-500"
                      }`}
                    >
                      {a.is_active ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                      {a.is_active ? "Active" : "Paused"}
                    </button>
                    <button onClick={() => handleDeleteAlert(a.id)} className="text-[var(--text-muted)] hover:text-rose-500">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="okz-card p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-sm">
                Notification Inbox {unreadCount > 0 ? `(${unreadCount} unread)` : ""}
              </h2>
              {unreadCount > 0 && (
                <button
                  type="button"
                  onClick={handleMarkAllRead}
                  className="text-xs text-emerald-500 hover:underline inline-flex items-center gap-1 font-semibold"
                >
                  <CheckCheck className="w-3.5 h-3.5" /> Mark All Read
                </button>
              )}
            </div>
            <div className="divide-y divide-[var(--border)] text-xs">
              {notifs.length === 0 ? (
                <p className="py-3 okz-muted text-center">No notifications yet.</p>
              ) : (
                notifs.map((n) => (
                  <div
                    key={n.id}
                    className={`py-2.5 flex items-start justify-between gap-2 ${
                      !n.is_read ? "bg-emerald-500/5 -mx-2 px-2 rounded" : "opacity-80"
                    }`}
                  >
                    <div className="space-y-0.5">
                      <div className="font-semibold flex items-center gap-1.5">
                        {!n.is_read && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />}
                        <span>{n.title}</span>
                      </div>
                      <p className="okz-muted">{n.body}</p>
                    </div>
                    {!n.is_read && (
                      <button
                        type="button"
                        onClick={() => handleMarkRead(n.id)}
                        title="Mark as read"
                        className="px-2 py-1 rounded border border-[var(--border)] hover:border-emerald-500 text-[11px] shrink-0 inline-flex items-center gap-1"
                      >
                        <Check className="w-3 h-3" /> Read
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
