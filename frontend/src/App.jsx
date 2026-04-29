import { useState, useEffect, useCallback, useRef } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";

const fmt = (paise) => {
  if (paise == null) return "₹0.00";
  return "₹" + (paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const statusColors = {
  pending: { bg: "bg-amber-500/15", text: "text-amber-400", dot: "bg-amber-400" },
  processing: { bg: "bg-blue-500/15", text: "text-blue-400", dot: "bg-blue-400 animate-pulse" },
  completed: { bg: "bg-emerald-500/15", text: "text-emerald-400", dot: "bg-emerald-400" },
  failed: { bg: "bg-red-500/15", text: "text-red-400", dot: "bg-red-400" },
};

function StatusBadge({ status }) {
  const c = statusColors[status] || statusColors.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${c.bg} ${c.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {status}
    </span>
  );
}

function Spinner() {
  return (
    <div className="flex items-center justify-center p-8">
      <div className="w-8 h-8 border-2 border-[#00D4AA]/30 border-t-[#00D4AA] rounded-full animate-spin" />
    </div>
  );
}

function BalanceCard({ balance }) {
  if (!balance) return <Spinner />;
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      {[
        { label: "Available Balance", value: balance.available_paise, color: "from-[#00D4AA]/20 to-[#00D4AA]/5", accent: "#00D4AA", icon: "◈" },
        { label: "Held (In Transit)", value: balance.held_paise, color: "from-amber-500/20 to-amber-500/5", accent: "#F59E0B", icon: "◎" },
        { label: "Total Earned", value: balance.total_credits_paise, color: "from-violet-500/20 to-violet-500/5", accent: "#8B5CF6", icon: "◉" },
      ].map(({ label, value, color, accent, icon }) => (
        <div key={label} className={`relative overflow-hidden rounded-2xl border border-white/5 bg-gradient-to-br ${color} p-6`}>
          <div className="text-2xl mb-3" style={{ color: accent }}>{icon}</div>
          <div className="text-[11px] uppercase tracking-widest text-gray-400 mb-1">{label}</div>
          <div className="text-3xl font-bold text-white font-mono">{fmt(value)}</div>
          <div className="text-xs text-gray-500 mt-1">{value?.toLocaleString()} paise</div>
        </div>
      ))}
    </div>
  );
}

function PayoutForm({ merchant, bankAccounts, onSuccess }) {
  const [amount, setAmount] = useState("");
  const [bankId, setBankId] = useState(bankAccounts[0]?.id || "");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const submit = async () => {
    const paise = Math.round(parseFloat(amount) * 100);
    if (!paise || paise < 10000) {
      setResult({ type: "error", msg: "Minimum payout is ₹100" });
      return;
    }
    setLoading(true);
    setResult(null);
    const key = crypto.randomUUID();
    try {
      const res = await fetch(`${API}/merchants/${merchant.id}/payouts/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": key,
        },
        body: JSON.stringify({ amount_paise: paise, bank_account_id: bankId }),
      });
      const data = await res.json();
      if (res.ok) {
        setResult({ type: "success", msg: `Payout of ${fmt(paise)} submitted!`, payout: data });
        setAmount("");
        onSuccess();
      } else {
        setResult({ type: "error", msg: data.detail || data.error || "Request failed" });
      }
    } catch (e) {
      setResult({ type: "error", msg: "Network error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-2xl border border-white/5 bg-white/[0.03] p-6 mb-6">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-widest mb-5">Request Payout</h3>
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 font-semibold">₹</span>
          <input
            type="number"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="w-full pl-8 pr-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white placeholder-gray-600 focus:outline-none focus:border-[#00D4AA]/50 focus:bg-white/8 transition-all"
          />
        </div>
        <select
          value={bankId}
          onChange={(e) => setBankId(e.target.value)}
          className="flex-1 px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-gray-300 focus:outline-none focus:border-[#00D4AA]/50 transition-all appearance-none cursor-pointer"
        >
          {bankAccounts.map((b) => (
            <option key={b.id} value={b.id} className="bg-[#0D1117]">
              {b.account_holder_name} — {b.masked_account} ({b.ifsc_code})
            </option>
          ))}
        </select>
        <button
          onClick={submit}
          disabled={loading}
          className="px-7 py-3 rounded-xl font-semibold text-sm bg-[#00D4AA] text-[#0D1117] hover:bg-[#00E5B8] disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-95"
        >
          {loading ? "Sending…" : "Request Payout →"}
        </button>
      </div>
      {result && (
        <div className={`mt-4 px-4 py-3 rounded-xl text-sm font-medium ${result.type === "success" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-red-500/10 text-red-400 border border-red-500/20"}`}>
          {result.msg}
        </div>
      )}
    </div>
  );
}

function LedgerTable({ entries }) {
  if (!entries?.length) return (
    <div className="text-center py-8 text-gray-600 text-sm">No transactions yet</div>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/5">
            {["Type", "Amount", "Description", "Date"].map((h) => (
              <th key={h} className="text-left py-3 px-4 text-[10px] uppercase tracking-widest text-gray-500 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
              <td className="py-3 px-4">
                <span className={`text-xs font-bold uppercase tracking-wide ${e.entry_type === "credit" ? "text-emerald-400" : "text-red-400"}`}>
                  {e.entry_type === "credit" ? "▲" : "▼"} {e.entry_type}
                </span>
              </td>
              <td className={`py-3 px-4 font-mono font-semibold ${e.entry_type === "credit" ? "text-emerald-400" : "text-red-400"}`}>
                {e.entry_type === "credit" ? "+" : "-"}{fmt(e.amount_paise)}
              </td>
              <td className="py-3 px-4 text-gray-400 max-w-[240px] truncate">{e.description}</td>
              <td className="py-3 px-4 text-gray-500 text-xs whitespace-nowrap">
                {new Date(e.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PayoutTable({ payouts, onRefresh }) {
  if (!payouts?.length) return (
    <div className="text-center py-8 text-gray-600 text-sm">No payouts yet</div>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/5">
            {["ID", "Amount", "Bank Account", "Status", "Attempts", "Created"].map((h) => (
              <th key={h} className="text-left py-3 px-4 text-[10px] uppercase tracking-widest text-gray-500 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {payouts.map((p) => (
            <tr key={p.id} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
              <td className="py-3 px-4 font-mono text-xs text-gray-500">{p.id.slice(0, 8)}…</td>
              <td className="py-3 px-4 font-mono font-semibold text-white">{fmt(p.amount_paise)}</td>
              <td className="py-3 px-4 text-gray-400 text-xs">{p.bank_account?.masked_account || "—"}</td>
              <td className="py-3 px-4"><StatusBadge status={p.status} /></td>
              <td className="py-3 px-4 text-gray-500 text-center">{p.attempt_count}</td>
              <td className="py-3 px-4 text-gray-500 text-xs whitespace-nowrap">
                {new Date(p.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("payouts");
  const pollRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/merchants/`)
      .then((r) => r.json())
      .then((data) => {
        setMerchants(data);
        if (data.length) setSelectedId(data[0].id);
      })
      .catch(() => {});
  }, []);

  const loadDashboard = useCallback(async () => {
    if (!selectedId) return;
    try {
      const res = await fetch(`${API}/merchants/${selectedId}/`);
      const data = await res.json();
      setDashboard(data);
    } catch {}
  }, [selectedId]);

  useEffect(() => {
    setDashboard(null);
    setLoading(true);
    loadDashboard().finally(() => setLoading(false));

    // Live polling every 4 seconds
    clearInterval(pollRef.current);
    pollRef.current = setInterval(loadDashboard, 4000);
    return () => clearInterval(pollRef.current);
  }, [selectedId, loadDashboard]);

  const selected = merchants.find((m) => m.id === selectedId);

  return (
    <div className="min-h-screen bg-[#0D1117] text-white" style={{ fontFamily: "'DM Mono', 'Courier New', monospace" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=DM+Sans:wght@300;400;500;600;700&display=swap');
        body { font-family: 'DM Sans', sans-serif; }
        .mono { font-family: 'DM Mono', monospace; }
      `}</style>

      {/* Header */}
      <header className="border-b border-white/5 px-6 py-4 flex items-center justify-between sticky top-0 bg-[#0D1117]/95 backdrop-blur-md z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#00D4AA] flex items-center justify-center text-[#0D1117] font-bold text-sm">P</div>
          <div>
            <div className="font-semibold text-sm text-white">Playto Pay</div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest">Payout Engine</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs text-gray-500">Live</span>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-4 py-8">
        {/* Merchant selector */}
        <div className="mb-8">
          <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-3">Merchant</div>
          <div className="flex flex-wrap gap-2">
            {merchants.map((m) => (
              <button
                key={m.id}
                onClick={() => setSelectedId(m.id)}
                className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
                  selectedId === m.id
                    ? "bg-[#00D4AA] text-[#0D1117]"
                    : "bg-white/5 text-gray-400 hover:bg-white/10 border border-white/5"
                }`}
              >
                {m.business_name}
              </button>
            ))}
          </div>
        </div>

        {loading ? <Spinner /> : dashboard ? (
          <>
            {/* Balance cards */}
            <BalanceCard balance={dashboard.balance} />

            {/* Payout form */}
            {dashboard.bank_accounts?.length > 0 && (
              <PayoutForm
                merchant={selected}
                bankAccounts={dashboard.bank_accounts}
                onSuccess={loadDashboard}
              />
            )}

            {/* Tabs */}
            <div className="flex gap-1 mb-5 bg-white/[0.03] p-1 rounded-xl w-fit">
              {[["payouts", "Payout History"], ["ledger", "Ledger"]].map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setTab(key)}
                  className={`px-5 py-2 rounded-lg text-sm font-medium transition-all ${
                    tab === key ? "bg-white/10 text-white" : "text-gray-500 hover:text-gray-300"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Table */}
            <div className="rounded-2xl border border-white/5 bg-white/[0.02] overflow-hidden">
              {tab === "payouts" ? (
                <PayoutTable payouts={dashboard.recent_payouts} onRefresh={loadDashboard} />
              ) : (
                <LedgerTable entries={dashboard.recent_transactions} />
              )}
            </div>
          </>
        ) : (
          <div className="text-center py-20 text-gray-600">Select a merchant to view dashboard</div>
        )}
      </div>
    </div>
  );
}
