// Personal Finance Dashboard — frontend app.
// No build step: Supabase JS + Chart.js load from CDN as classic scripts
// (see index.html), this file is the only ES module.

// ---------------------------------------------------------------------
// Config: try the gitignored personal config first, fall back to the
// committed demo config. On GitHub Pages, config.local.js doesn't exist
// (it's gitignored) so the import 404s and the fallback fires naturally —
// this IS the runtime check the architecture calls for, just via
// dynamic import() instead of a manual fetch+eval.
// ---------------------------------------------------------------------
let config;
try {
  config = await import("./config.local.js");
} catch {
  config = await import("./config.js");
}
const { SUPABASE_URL, SUPABASE_ANON_KEY, LABEL } = config;

const badge = document.getElementById("sourceBadge");
badge.textContent = LABEL === "personal" ? "Personal data" : "Demo data";
badge.classList.add(LABEL === "personal" ? "personal" : "demo");

if (SUPABASE_URL.startsWith("REPLACE_WITH") || SUPABASE_ANON_KEY.startsWith("REPLACE_WITH")) {
  showFatalError(
    `${LABEL === "personal" ? "frontend/config.local.js" : "frontend/config.js"} still has placeholder ` +
    `Supabase credentials. Fill in the Project URL and anon (public) key from the Supabase console ` +
    `(Project Settings → API) and reload.`
  );
  throw new Error("Supabase not configured");
}

const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ---------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------
const ACCOUNT_LABELS = {
  "Assets:ANZ:AccessAdvantage": "ANZ Access Advantage",
  "Assets:ANZ:OnlineSaver": "ANZ Online Saver",
  "Liabilities:ANZ:FrequentFlyerBlack": "ANZ Frequent Flyer",
  "Liabilities:Amex:Card": "Amex",
};
const CATEGORY_COLORS = ["cat-1", "cat-2", "cat-3", "cat-4", "cat-5", "cat-6", "cat-7", "cat-8"];
const MONTH_FMT = new Intl.DateTimeFormat("en-AU", { month: "short", year: "2-digit" });
const DATE_FMT = new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short", year: "2-digit" });
const MONEY_FMT = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD", maximumFractionDigits: 0 });
const MONEY_FMT_PRECISE = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });

function formatAccountName(name) {
  return ACCOUNT_LABELS[name] || (name || "").split(":").pop();
}
function formatCategoryName(name) {
  if (!name) return "—";
  const parts = name.split(":");
  if (parts[0] === "Transfers") return "Transfer";
  return parts.slice(1).join(" › ") || name;
}
function topLevelCategoryLabel(name) {
  if (!name) return "Uncategorized";
  const parts = name.split(":");
  return parts[1] || parts[0];
}
function leafCategoryLabel(name) {
  if (!name) return "Uncategorized";
  const parts = name.split(":");
  return parts[parts.length - 1];
}
function hashColorVar(key) {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return cssVar(`--${CATEGORY_COLORS[h % CATEGORY_COLORS.length]}`);
}
function money(n) {
  return MONEY_FMT.format(n);
}
function moneyPrecise(n) {
  return MONEY_FMT_PRECISE.format(n);
}
function monthLabel(dateStr) {
  return MONTH_FMT.format(new Date(dateStr + "T00:00:00"));
}
function dateLabel(dateStr) {
  return DATE_FMT.format(new Date(dateStr + "T00:00:00"));
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function showFatalError(message) {
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.innerHTML = `<div class="error-state">${message}</div>`;
  });
}

// ---------------------------------------------------------------------
// Date range helpers
// ---------------------------------------------------------------------
function isoDate(d) {
  // Format LOCAL date components directly — never .toISOString(), which
  // converts through UTC and silently shifts month/day boundaries back a
  // day for any timezone ahead of UTC (e.g. Sydney, AEST/AEDT UTC+10/+11).
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function computeRangeStart(rangeKey) {
  const now = new Date();
  switch (rangeKey) {
    case "this-month":
      return isoDate(startOfMonth(now));
    case "last-3-months":
      return isoDate(new Date(now.getFullYear(), now.getMonth() - 2, 1));
    case "ytd":
      return isoDate(new Date(now.getFullYear(), 0, 1));
    case "last-12-months":
      return isoDate(new Date(now.getFullYear(), now.getMonth() - 11, 1));
    case "all-time":
    default:
      return null;
  }
}
function addMonths(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  return isoDate(new Date(d.getFullYear(), d.getMonth() + n, 1));
}
function daysBetween(startIso, endIso) {
  return Math.round((new Date(endIso) - new Date(startIso)) / 86400000);
}

// ---------------------------------------------------------------------
// Data fetchers
// ---------------------------------------------------------------------
// Supabase/PostgREST caps a single response at 1000 rows by default and
// truncates silently — no error, just a short result. Real history here
// spans 2010 (a mis-parsed statement year) through the present, so
// v_daily_spend alone has 1,300+ rows: any unranged query on a table or
// view that can plausibly exceed 1000 rows MUST page through with
// .range(), or it silently returns a stale, truncated slice instead of
// the real most-recent data. buildQuery is a factory (not a live query)
// since a fresh query object is needed for each page's .range() call.
const PAGE_SIZE = 1000;
async function fetchAllRows(buildQuery) {
  let all = [];
  let offset = 0;
  for (;;) {
    const { data, error } = await buildQuery().range(offset, offset + PAGE_SIZE - 1);
    if (error) throw error;
    all = all.concat(data);
    if (data.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return all;
}

async function fetchAccountBalances() {
  const { data, error } = await sb.from("v_account_balances").select("*");
  if (error) throw error;
  return data;
}

async function fetchMonthlyIncomeExpense() {
  const data = await fetchAllRows(() =>
    sb.from("v_monthly_income_expense").select("month,root_type,total").order("month")
  );
  const byMonth = new Map();
  for (const row of data) {
    if (!byMonth.has(row.month)) byMonth.set(row.month, { month: row.month, income: 0, expense: 0 });
    byMonth.get(row.month)[row.root_type] = Number(row.total);
  }
  return [...byMonth.values()].sort((a, b) => a.month.localeCompare(b.month));
}

async function fetchBiggestCategory(monthStartIso) {
  const { data, error } = await sb
    .from("v_monthly_category_totals")
    .select("category,total")
    .eq("month", monthStartIso)
    .order("total", { ascending: false })
    .limit(1);
  if (error) throw error;
  return data[0] || null;
}

async function fetchLargestTransaction(monthStartIso, nextMonthStartIso) {
  const { data, error } = await sb
    .from("postings")
    .select("amount, transactions!inner(date,description), accounts!inner(root_type)")
    .eq("accounts.root_type", "expense")
    .gte("transactions.date", monthStartIso)
    .lt("transactions.date", nextMonthStartIso)
    .order("amount", { ascending: false })
    .limit(1);
  if (error) throw error;
  return data[0] || null;
}

async function fetchDataFreshness() {
  const { data, error } = await sb
    .from("transactions")
    .select("created_at")
    .order("created_at", { ascending: false })
    .limit(1);
  if (error) throw error;
  return data[0]?.created_at || null;
}

function processTransactionRow(txn) {
  const postings = txn.postings || [];
  const accountLeg = postings.find((p) => p.accounts && ["asset", "liability"].includes(p.accounts.root_type));
  if (!accountLeg) return null;
  const categoryLeg = postings.find((p) => p !== accountLeg);
  return {
    id: txn.id,
    date: txn.date,
    description: txn.description,
    accountName: accountLeg.accounts.name,
    categoryName: categoryLeg ? categoryLeg.accounts.name : null,
    categoryRootType: categoryLeg ? categoryLeg.accounts.root_type : null,
    amount: Number(accountLeg.amount),
  };
}

async function fetchTransactions({ start = null, end = null, limit = null } = {}) {
  const build = () => {
    let query = sb
      .from("transactions")
      .select("id,date,description,postings(amount,accounts(name,root_type))")
      .order("date", { ascending: false })
      .order("id", { ascending: false });
    if (start) query = query.gte("date", start);
    if (end) query = query.lt("date", end);
    return query;
  };
  let data;
  if (limit) {
    // A small, explicitly-capped fetch (recent transactions, etc.) — no
    // need to page, .limit() is exactly the right tool here.
    const res = await build().limit(limit);
    if (res.error) throw res.error;
    data = res.data;
  } else {
    data = await fetchAllRows(build);
  }
  return data.map(processTransactionRow).filter(Boolean);
}

async function fetchMonthlyCategoryTotals() {
  return fetchAllRows(() => sb.from("v_monthly_category_totals").select("month,category,total").order("month"));
}

async function fetchDailySpend({ start = null, end = null } = {}) {
  return fetchAllRows(() => {
    let query = sb.from("v_daily_spend").select("date,total").order("date");
    if (start) query = query.gte("date", start);
    if (end) query = query.lt("date", end);
    return query;
  });
}

async function fetchCategoryMovers({ start = null } = {}) {
  return fetchAllRows(() => {
    let query = sb.from("v_category_movers").select("category,date,total").order("date");
    if (start) query = query.gte("date", start);
    return query;
  });
}

async function fetchAccountPostings(accountId) {
  const data = await fetchAllRows(() =>
    sb
      .from("transactions")
      .select("date, postings!inner(amount,account_id)")
      .eq("postings.account_id", accountId)
      .order("date")
  );
  return data.map((t) => ({ date: t.date, amount: t.postings.reduce((s, p) => s + Number(p.amount), 0) }));
}

// ---------------------------------------------------------------------
// Net position trend — derived from real monthly income/expense deltas,
// anchored to today's true balance (see ledger/schema.sql's views: every
// asset/liability-affecting posting is matched by an income/expense/
// transfer counter-posting, and transfers net to zero, so cumulative
// (income - expense) reconstructs the net-position trend exactly).
// ---------------------------------------------------------------------
function computeNetPositionToday(balances) {
  let net = 0;
  for (const b of balances) {
    if (b.root_type === "asset") net += Number(b.balance);
    else if (b.root_type === "liability") net -= Number(b.balance);
  }
  return net;
}

function buildNetPositionTrend(monthly, netToday) {
  let cum = 0;
  const points = monthly.map((m) => {
    cum += (m.income || 0) - (m.expense || 0);
    return { month: m.month, cum };
  });
  const offset = points.length ? netToday - points[points.length - 1].cum : 0;
  return points.map((p) => ({ month: p.month, net: p.cum + offset }));
}

// ---------------------------------------------------------------------
// Overview: stat boxes (always "this month vs last month", independent
// of the trend-chart range selector below them)
// ---------------------------------------------------------------------
async function renderOverviewStats() {
  const now = new Date();
  const thisMonthStart = isoDate(startOfMonth(now));
  const lastMonthStart = isoDate(new Date(now.getFullYear(), now.getMonth() - 1, 1));
  const nextMonthStart = isoDate(new Date(now.getFullYear(), now.getMonth() + 1, 1));

  const [balances, monthly, biggestCat, largestTxn, freshness] = await Promise.all([
    fetchAccountBalances(),
    fetchMonthlyIncomeExpense(),
    fetchBiggestCategory(thisMonthStart),
    fetchLargestTransaction(thisMonthStart, nextMonthStart),
    fetchDataFreshness(),
  ]);

  const netToday = computeNetPositionToday(balances);
  const trend = buildNetPositionTrend(monthly, netToday);
  const lastMonthPoint = trend.find((p) => p.month === lastMonthStart);
  const netDelta = lastMonthPoint ? netToday - lastMonthPoint.net : null;
  setStat("stat-net-position", money(netToday), deltaSub(netDelta, money, "mo"));

  const thisM = monthly.find((m) => m.month === thisMonthStart) || { income: 0, expense: 0 };
  const lastM = monthly.find((m) => m.month === lastMonthStart) || { income: 0, expense: 0 };
  setStat("stat-spent", money(thisM.expense), deltaSub(thisM.expense - lastM.expense, money, "vs last mo", true));

  const rateNow = thisM.income > 0 ? (thisM.income - thisM.expense) / thisM.income : null;
  const rateLast = lastM.income > 0 ? (lastM.income - lastM.expense) / lastM.income : null;
  setStat(
    "stat-savings-rate",
    rateNow == null ? "—" : `${(rateNow * 100).toFixed(0)}%`,
    rateNow != null && rateLast != null
      ? deltaSub((rateNow - rateLast) * 100, (v) => `${v.toFixed(0)}pp`, "vs last mo")
      : " "
  );

  setStat(
    "stat-biggest-category",
    biggestCat ? formatCategoryName(biggestCat.category) : "—",
    biggestCat ? money(biggestCat.total) : " "
  );

  setStat(
    "stat-largest-txn",
    largestTxn ? money(largestTxn.amount) : "—",
    largestTxn ? largestTxn.transactions.description : " "
  );

  document.getElementById("freshness").textContent = freshness
    ? `Data last ingested: ${new Date(freshness).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" })}`
    : "No data ingested yet.";

  return { balances, monthly, netToday, trend };
}

function setStat(id, value, sub) {
  const el = document.getElementById(id);
  el.querySelector(".stat-value").textContent = value;
  el.querySelector(".stat-sub").innerHTML = sub;
}
function deltaSub(delta, fmt, suffix, higherIsWorse = false) {
  if (delta == null || Number.isNaN(delta)) return " ";
  const dir = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const good = higherIsWorse ? dir === "down" : dir === "up";
  const cls = dir === "flat" ? "flat" : good ? "up" : "down";
  const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "–";
  return `<span class="stat-delta ${cls}">${arrow} ${fmt(Math.abs(delta))}</span> ${suffix}`;
}

// ---------------------------------------------------------------------
// Overview: trend charts (controlled by the range segmented control)
// ---------------------------------------------------------------------
const charts = {};

function chartTheme() {
  return {
    text: cssVar("--text-2"),
    grid: cssVar("--border"),
    accent: cssVar("--accent"),
    gain: cssVar("--gain"),
    loss: cssVar("--loss"),
    font: { family: "DM Sans", size: 11 },
  };
}

function destroyChart(key) {
  if (charts[key]) {
    charts[key].destroy();
    delete charts[key];
  }
}

function filterByRange(monthly, rangeKey) {
  const start = computeRangeStart(rangeKey);
  return start ? monthly.filter((m) => m.month >= start) : monthly;
}

function renderNetPositionChart(trend, rangeKey) {
  const rows = filterByRange(trend.map((p) => ({ month: p.month, net: p.net })), rangeKey);
  const theme = chartTheme();
  destroyChart("netPosition");
  charts.netPosition = new Chart(document.getElementById("chartNetPosition"), {
    type: "line",
    data: {
      labels: rows.map((r) => monthLabel(r.month)),
      datasets: [
        {
          data: rows.map((r) => r.net),
          borderColor: theme.accent,
          backgroundColor: theme.accent + "22",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
        },
      ],
    },
    options: baseLineOptions(theme, (v) => money(v)),
  });
}

function renderSavingsRateChart(monthly, rangeKey) {
  const rows = filterByRange(monthly, rangeKey);
  const theme = chartTheme();
  destroyChart("savingsRate");
  charts.savingsRate = new Chart(document.getElementById("chartSavingsRate"), {
    type: "line",
    data: {
      labels: rows.map((r) => monthLabel(r.month)),
      datasets: [
        {
          data: rows.map((r) => (r.income > 0 ? ((r.income - r.expense) / r.income) * 100 : null)),
          borderColor: theme.gain,
          backgroundColor: theme.gain + "22",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          spanGaps: true,
        },
      ],
    },
    options: baseLineOptions(theme, (v) => `${v.toFixed(0)}%`),
  });
}

function renderIncomeExpenseChart(monthly, rangeKey) {
  const rows = filterByRange(monthly, rangeKey);
  const theme = chartTheme();
  destroyChart("incomeExpense");
  charts.incomeExpense = new Chart(document.getElementById("chartIncomeExpense"), {
    type: "bar",
    data: {
      labels: rows.map((r) => monthLabel(r.month)),
      datasets: [
        { label: "Income", data: rows.map((r) => r.income), backgroundColor: theme.gain },
        { label: "Expenses", data: rows.map((r) => r.expense), backgroundColor: theme.loss },
      ],
    },
    options: {
      ...baseLineOptions(theme, (v) => money(v)),
      plugins: { legend: { display: true, labels: { color: theme.text, font: theme.font, boxWidth: 10 } } },
    },
  });
}

function baseLineOptions(theme, tickFmt) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: theme.grid }, ticks: { color: theme.text, font: theme.font, maxRotation: 0 } },
      y: {
        grid: { color: theme.grid },
        ticks: { color: theme.text, font: theme.font, callback: (v) => tickFmt(v) },
      },
    },
  };
}

async function renderOverviewCharts(rangeKey, cache) {
  renderNetPositionChart(cache.trend, rangeKey);
  renderSavingsRateChart(cache.monthly, rangeKey);
  renderIncomeExpenseChart(cache.monthly, rangeKey);
}

// ---------------------------------------------------------------------
// Overview: recent transactions table
// ---------------------------------------------------------------------
async function renderRecentTransactions() {
  const rows = await fetchTransactions({ limit: 10 });
  const body = document.getElementById("recentTxnBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-state">No transactions yet.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(txnRowHtml).join("");
}

function txnRowHtml(r) {
  const amtCls = r.amount < 0 ? "expense" : "income";
  return `<tr>
    <td>${dateLabel(r.date)}</td>
    <td>${formatAccountName(r.accountName)}</td>
    <td>${escapeHtml(r.description)}</td>
    <td><span class="cat-pill">${formatCategoryName(r.categoryName)}</span></td>
    <td class="amount ${amtCls}">${moneyPrecise(r.amount)}</td>
  </tr>`;
}
function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// ---------------------------------------------------------------------
// Transactions tab
// ---------------------------------------------------------------------
const txnState = {
  range: "last-3-months",
  customStart: null,
  customEnd: null,
  accounts: [],
  category: "",
  search: "",
  minAmount: null,
  maxAmount: null,
  page: 1,
  pageSize: 50,
  allRows: [],
  loaded: false,
};

function populateAccountFilter() {
  const sel = document.getElementById("txnAccountFilter");
  sel.innerHTML = Object.entries(ACCOUNT_LABELS)
    .map(([value, label]) => `<option value="${value}">${label}</option>`)
    .join("");
}

function populateCategoryFilter(rows) {
  const sel = document.getElementById("txnCategoryFilter");
  const current = sel.value;
  const distinct = [...new Set(rows.map((r) => r.categoryName).filter(Boolean))].sort();
  sel.innerHTML =
    `<option value="">All categories</option>` +
    distinct.map((c) => `<option value="${c}">${formatCategoryName(c)}</option>`).join("");
  if (distinct.includes(current)) sel.value = current;
}

async function loadTransactionsTab() {
  const body = document.getElementById("txnTableBody");
  body.innerHTML = `<tr><td colspan="5" class="loading-state">Loading…</td></tr>`;
  try {
    const start = txnState.customStart || computeRangeStart(txnState.range);
    const end = txnState.customEnd || null;
    txnState.allRows = await fetchTransactions({ start, end });
    txnState.loaded = true;
    populateCategoryFilter(txnState.allRows);
    txnState.page = 1;
    renderTransactionsTable();
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="error-state">Failed to load transactions: ${escapeHtml(err.message || String(err))}</td></tr>`;
  }
}

function applyTxnFilters(rows) {
  return rows.filter((r) => {
    if (txnState.accounts.length && !txnState.accounts.includes(r.accountName)) return false;
    if (txnState.category && r.categoryName !== txnState.category) return false;
    if (txnState.search && !r.description.toLowerCase().includes(txnState.search.toLowerCase())) return false;
    const abs = Math.abs(r.amount);
    if (txnState.minAmount != null && abs < txnState.minAmount) return false;
    if (txnState.maxAmount != null && abs > txnState.maxAmount) return false;
    return true;
  });
}

function renderTransactionsTable() {
  const filtered = applyTxnFilters(txnState.allRows);
  const totalPages = Math.max(1, Math.ceil(filtered.length / txnState.pageSize));
  txnState.page = Math.min(txnState.page, totalPages);
  const start = (txnState.page - 1) * txnState.pageSize;
  const pageRows = filtered.slice(start, start + txnState.pageSize);

  const body = document.getElementById("txnTableBody");
  body.innerHTML = pageRows.length
    ? pageRows.map(txnRowHtml).join("")
    : `<tr><td colspan="5" class="empty-state">No transactions match these filters.</td></tr>`;

  const pagination = document.getElementById("txnPagination");
  pagination.innerHTML = `
    <button id="txnPrev" ${txnState.page <= 1 ? "disabled" : ""}>◂ prev</button>
    <span>page ${txnState.page} of ${totalPages} · ${filtered.length} transactions</span>
    <button id="txnNext" ${txnState.page >= totalPages ? "disabled" : ""}>next ▸</button>
  `;
  document.getElementById("txnPrev")?.addEventListener("click", () => {
    txnState.page -= 1;
    renderTransactionsTable();
  });
  document.getElementById("txnNext")?.addEventListener("click", () => {
    txnState.page += 1;
    renderTransactionsTable();
  });
}

function initTransactionsFilters() {
  populateAccountFilter();

  document.querySelectorAll("#txnRangeSeg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#txnRangeSeg button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      txnState.range = btn.dataset.range;
      txnState.customStart = null;
      txnState.customEnd = null;
      loadTransactionsTab();
    });
  });

  document.getElementById("txnAccountFilter").addEventListener("change", (e) => {
    txnState.accounts = [...e.target.selectedOptions].map((o) => o.value);
    txnState.page = 1;
    renderTransactionsTable();
  });
  document.getElementById("txnCategoryFilter").addEventListener("change", (e) => {
    txnState.category = e.target.value;
    txnState.page = 1;
    renderTransactionsTable();
  });
  document.getElementById("txnSearch").addEventListener("input", (e) => {
    txnState.search = e.target.value;
    txnState.page = 1;
    renderTransactionsTable();
  });
  document.getElementById("txnAmountMin").addEventListener("input", (e) => {
    txnState.minAmount = e.target.value === "" ? null : Number(e.target.value);
    txnState.page = 1;
    renderTransactionsTable();
  });
  document.getElementById("txnAmountMax").addEventListener("input", (e) => {
    txnState.maxAmount = e.target.value === "" ? null : Number(e.target.value);
    txnState.page = 1;
    renderTransactionsTable();
  });
  document.getElementById("txnResetFilters").addEventListener("click", () => {
    txnState.accounts = [];
    txnState.category = "";
    txnState.search = "";
    txnState.minAmount = null;
    txnState.maxAmount = null;
    document.getElementById("txnAccountFilter").selectedIndex = -1;
    document.getElementById("txnCategoryFilter").value = "";
    document.getElementById("txnSearch").value = "";
    document.getElementById("txnAmountMin").value = "";
    document.getElementById("txnAmountMax").value = "";
    txnState.page = 1;
    renderTransactionsTable();
  });
}

// ---------------------------------------------------------------------
// Shared: drill down from a chart into the Transactions tab
// ---------------------------------------------------------------------
function drillToTransactions({ category = null, start = null, end = null } = {}) {
  txnState.accounts = [];
  txnState.search = "";
  txnState.minAmount = null;
  txnState.maxAmount = null;
  txnState.customStart = start;
  txnState.customEnd = end;
  document.querySelectorAll("#txnRangeSeg button").forEach((b) => b.classList.remove("active"));
  document.getElementById("txnAccountFilter").selectedIndex = -1;
  document.getElementById("txnSearch").value = "";
  document.getElementById("txnAmountMin").value = "";
  document.getElementById("txnAmountMax").value = "";
  document.getElementById("txnCategoryFilter").value = category || "";
  txnState.category = category || "";
  switchTab("transactions");
  loadTransactionsTab();
}

// ---------------------------------------------------------------------
// Spending by Category tab
// ---------------------------------------------------------------------
const catState = { range: "last-3-months", loaded: false };

async function loadCategoryTab() {
  catState.loaded = true;
  const rangeStart = computeRangeStart(catState.range);

  const [monthlyCat, dailyAll, moversRows] = await Promise.all([
    fetchMonthlyCategoryTotals(),
    fetchDailySpend({}),
    fetchCategoryMovers({ start: rangeStart ? addMonths(rangeStart, -12) : null }),
  ]);

  const inRange = (m) => !rangeStart || m >= rangeStart;
  const rangeRows = monthlyCat.filter((r) => inRange(r.month));

  const totalsByCategory = new Map();
  for (const r of rangeRows) totalsByCategory.set(r.category, (totalsByCategory.get(r.category) || 0) + Number(r.total));
  const catTotals = [...totalsByCategory.entries()]
    .map(([category, total]) => ({ category, total }))
    .filter((c) => c.total > 0)
    .sort((a, b) => b.total - a.total);

  renderTreemap(catTotals);
  renderCategoryDonut(catTotals);
  renderCategoryTrendChart(rangeRows);

  const lastDay = dailyAll.length ? dailyAll[dailyAll.length - 1].date : null;
  const heatmapMonth = lastDay ? isoDate(startOfMonth(new Date(lastDay + "T00:00:00"))) : isoDate(startOfMonth(new Date()));
  const heatmapNextMonth = addMonths(heatmapMonth, 1);
  document.getElementById("heatmapMonthLabel").textContent = monthLabel(heatmapMonth);
  renderHeatmap(
    dailyAll.filter((d) => d.date >= heatmapMonth && d.date < heatmapNextMonth),
    heatmapMonth
  );
  renderWeekdayChart(dailyAll.filter((d) => inRange(d.date)));

  renderMovers(moversRows, catState.range, rangeStart);

  if (catTotals.length) {
    const biggest = catTotals[0].category;
    document.getElementById("topMerchantsHint").textContent = `— ${leafCategoryLabel(biggest)}`;
    await renderTopMerchants(biggest, rangeStart);
  } else {
    document.getElementById("topMerchants").innerHTML = `<div class="empty-state">No spending in this range.</div>`;
  }
}

function renderTreemap(items) {
  const container = document.getElementById("catTreemap");
  if (!items.length) {
    container.innerHTML = `<div class="treemap-empty">No spending in this range.</div>`;
    return;
  }
  const w = container.clientWidth || 400;
  const h = container.clientHeight || 300;
  const tiles = squarify(items.slice(0, 14), 0, 0, w, h);
  container.innerHTML = tiles
    .map((t) => {
      const color = hashColorVar(topLevelCategoryLabel(t.category));
      return `<div class="treemap-tile" data-category="${t.category}" style="left:${t.x}px; top:${t.y}px; width:${Math.max(0, t.w - 2)}px; height:${Math.max(0, t.h - 2)}px; background:${color};">
        <div class="t-name">${leafCategoryLabel(t.category)}</div>
        <div class="t-value">${money(t.value)}</div>
      </div>`;
    })
    .join("");
  container.querySelectorAll(".treemap-tile").forEach((el) => {
    el.addEventListener("click", () => {
      const rangeStart = computeRangeStart(catState.range);
      drillToTransactions({ category: el.dataset.category, start: rangeStart });
    });
  });
}

// Squarified treemap layout — classic algorithm, produces reasonably
// proportioned rectangles instead of one long sliced strip.
function squarify(items, x, y, w, h) {
  const total = items.reduce((s, i) => s + i.total, 0);
  if (!items.length || total <= 0) return [];
  const areaScale = (w * h) / total;
  let remaining = items.map((i) => ({ category: i.category, value: i.total, area: i.total * areaScale }));
  const result = [];
  let rx = x, ry = y, rw = w, rh = h;

  function worst(row, length) {
    const sum = row.reduce((s, i) => s + i.area, 0);
    const rMax = Math.max(...row.map((i) => i.area));
    const rMin = Math.min(...row.map((i) => i.area));
    return Math.max((length * length * rMax) / (sum * sum), (sum * sum) / (length * length * rMin));
  }

  while (remaining.length) {
    const length = Math.min(rw, rh);
    let row = [remaining[0]];
    let rest = remaining.slice(1);
    while (rest.length && worst([...row, rest[0]], length) <= worst(row, length)) {
      row = [...row, rest[0]];
      rest = rest.slice(1);
    }
    remaining = rest;

    const rowArea = row.reduce((s, i) => s + i.area, 0);
    if (rw >= rh) {
      const rowWidth = rowArea / rh;
      let cy = ry;
      for (const item of row) {
        const itemHeight = item.area / rowWidth;
        result.push({ category: item.category, value: item.value, x: rx, y: cy, w: rowWidth, h: itemHeight });
        cy += itemHeight;
      }
      rx += rowWidth;
      rw -= rowWidth;
    } else {
      const rowHeight = rowArea / rw;
      let cx = rx;
      for (const item of row) {
        const itemWidth = item.area / rowHeight;
        result.push({ category: item.category, value: item.value, x: cx, y: ry, w: itemWidth, h: rowHeight });
        cx += itemWidth;
      }
      ry += rowHeight;
      rh -= rowHeight;
    }
  }
  return result;
}

function renderCategoryDonut(items) {
  const theme = chartTheme();
  destroyChart("categoryDonut");
  const top = items.slice(0, 8);
  const otherTotal = items.slice(8).reduce((s, i) => s + i.total, 0);
  const labels = top.map((i) => leafCategoryLabel(i.category));
  const values = top.map((i) => i.total);
  const colors = top.map((i) => hashColorVar(topLevelCategoryLabel(i.category)));
  if (otherTotal > 0) {
    labels.push("Other");
    values.push(otherTotal);
    colors.push(theme.text);
  }
  if (!values.length) return;
  charts.categoryDonut = new Chart(document.getElementById("chartCategoryDonut"), {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: cssVar("--surface"), borderWidth: 2 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "right", labels: { color: theme.text, font: theme.font, boxWidth: 10, padding: 8 } } },
    },
  });
}

function renderCategoryTrendChart(rangeRows) {
  const theme = chartTheme();
  destroyChart("categoryTrend");
  const months = [...new Set(rangeRows.map((r) => r.month))].sort();
  const byTop = new Map();
  for (const r of rangeRows) {
    const top = topLevelCategoryLabel(r.category);
    if (!byTop.has(top)) byTop.set(top, new Map());
    const m = byTop.get(top);
    m.set(r.month, (m.get(r.month) || 0) + Number(r.total));
  }
  const totals = [...byTop.entries()].map(([top, m]) => [top, [...m.values()].reduce((s, v) => s + v, 0)]);
  totals.sort((a, b) => b[1] - a[1]);
  const topN = totals.slice(0, 6).map(([top]) => top);

  const datasets = topN.map((top) => ({
    label: top,
    data: months.map((m) => byTop.get(top)?.get(m) || 0),
    borderColor: hashColorVar(top),
    backgroundColor: hashColorVar(top) + "55",
    fill: true,
    stack: "cat",
    tension: 0.25,
    pointRadius: 0,
  }));

  if (!months.length) return;
  charts.categoryTrend = new Chart(document.getElementById("chartCategoryTrend"), {
    type: "line",
    data: { labels: months.map(monthLabel), datasets },
    options: {
      ...baseLineOptions(theme, (v) => money(v)),
      plugins: { legend: { display: true, position: "bottom", labels: { color: theme.text, font: theme.font, boxWidth: 10 } } },
      scales: {
        x: { stacked: true, grid: { color: theme.grid }, ticks: { color: theme.text, font: theme.font, maxRotation: 0 } },
        y: { stacked: true, grid: { color: theme.grid }, ticks: { color: theme.text, font: theme.font, callback: (v) => money(v) } },
      },
    },
  });
}

const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function dowIndex(dateStr) {
  const jsDay = new Date(dateStr + "T00:00:00").getDay(); // 0=Sun
  return (jsDay + 6) % 7; // 0=Mon
}

function renderHeatmap(dayRows, monthStartIso) {
  const container = document.getElementById("dailyHeatmap");
  const byDate = new Map(dayRows.map((r) => [r.date, Number(r.total)]));
  const max = Math.max(1, ...dayRows.map((r) => Number(r.total)));
  const monthStart = new Date(monthStartIso + "T00:00:00");
  const daysInMonth = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0).getDate();
  const leadingBlanks = dowIndex(monthStartIso);

  let html = DOW_LABELS.map((d) => `<div class="hm-dow">${d}</div>`).join("");
  for (let i = 0; i < leadingBlanks; i++) html += `<div class="hm-cell empty"></div>`;
  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${monthStartIso.slice(0, 8)}${String(day).padStart(2, "0")}`;
    const spend = byDate.get(dateStr) || 0;
    const intensity = spend > 0 ? Math.min(1, spend / max) : 0;
    const bg = spend > 0 ? withAlpha(cssVar("--loss"), 0.25 + intensity * 0.65) : null;
    html += `<div class="hm-cell${spend > 0 ? " has-spend" : ""}" ${bg ? `style="background:${bg};"` : ""} data-date="${dateStr}" title="${dateLabel(dateStr)}: ${money(spend)}">${day}</div>`;
  }
  container.innerHTML = html;
  container.querySelectorAll(".hm-cell.has-spend").forEach((el) => {
    el.addEventListener("click", () => {
      const d = el.dataset.date;
      drillToTransactions({ start: d, end: addDays(d, 1) });
    });
  });
}
function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  return isoDate(d);
}
function withAlpha(hexOrColor, alpha) {
  // Accepts a hex color (#RRGGBB); falls back to the raw value for anything else.
  if (/^#([0-9a-f]{6})$/i.test(hexOrColor)) {
    const r = parseInt(hexOrColor.slice(1, 3), 16);
    const g = parseInt(hexOrColor.slice(3, 5), 16);
    const b = parseInt(hexOrColor.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  return hexOrColor;
}

function renderWeekdayChart(dayRows) {
  const theme = chartTheme();
  destroyChart("weekday");
  const sums = new Array(7).fill(0);
  const counts = new Array(7).fill(0);
  for (const r of dayRows) {
    const idx = dowIndex(r.date);
    sums[idx] += Number(r.total);
    counts[idx] += 1;
  }
  const avgs = sums.map((s, i) => (counts[i] ? s / counts[i] : 0));
  charts.weekday = new Chart(document.getElementById("chartWeekday"), {
    type: "bar",
    data: { labels: DOW_LABELS, datasets: [{ data: avgs, backgroundColor: cssVar("--accent") }] },
    options: { ...baseLineOptions(theme, (v) => money(v)), plugins: { legend: { display: false } } },
  });
}

function renderMovers(moversRows, rangeKey, rangeStart) {
  const container = document.getElementById("catMovers");
  if (rangeKey === "all-time" || !rangeStart) {
    container.innerHTML = `<div class="empty-state">Pick a specific range to compare against the prior period.</div>`;
    return;
  }
  const today = isoDate(new Date());
  const rangeDays = daysBetween(rangeStart, today);
  const prevStart = addDays(rangeStart, -rangeDays);

  const currentByCat = new Map();
  const prevByCat = new Map();
  for (const r of moversRows) {
    const total = Number(r.total);
    if (r.date >= rangeStart) currentByCat.set(r.category, (currentByCat.get(r.category) || 0) + total);
    else if (r.date >= prevStart) prevByCat.set(r.category, (prevByCat.get(r.category) || 0) + total);
  }
  const categories = new Set([...currentByCat.keys(), ...prevByCat.keys()]);
  const rows = [...categories]
    .map((category) => {
      const current = currentByCat.get(category) || 0;
      const prev = prevByCat.get(category) || 0;
      return { category, delta: current - prev };
    })
    .filter((r) => Math.abs(r.delta) > 0.5)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 8);

  if (!rows.length) {
    container.innerHTML = `<div class="empty-state">No significant movers vs. the prior period.</div>`;
    return;
  }
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.delta)));
  container.innerHTML = rows
    .map((r) => {
      const dir = r.delta > 0 ? "up" : "down";
      const arrow = r.delta > 0 ? "▲" : "▼";
      const pct = Math.min(100, (Math.abs(r.delta) / maxAbs) * 100);
      return `<div class="bl-row">
        <div class="bl-name">${leafCategoryLabel(r.category)}</div>
        <div class="bl-value ${dir}">${arrow} ${money(Math.abs(r.delta))}</div>
        <div class="bl-track"><div class="bl-fill" style="width:${pct}%; background:${dir === "up" ? "var(--loss)" : "var(--gain)"};"></div></div>
      </div>`;
    })
    .join("");
}

async function renderTopMerchants(categoryName, rangeStart) {
  const container = document.getElementById("topMerchants");
  let data;
  try {
    data = await fetchAllRows(() => {
      let query = sb
        .from("postings")
        .select("amount, transactions!inner(date,description), accounts!inner(name)")
        .eq("accounts.name", categoryName);
      if (rangeStart) query = query.gte("transactions.date", rangeStart);
      return query;
    });
  } catch (error) {
    container.innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    return;
  }
  const byMerchant = new Map();
  for (const row of data) {
    const desc = row.transactions.description;
    byMerchant.set(desc, (byMerchant.get(desc) || 0) + Math.abs(Number(row.amount)));
  }
  const rows = [...byMerchant.entries()]
    .map(([name, total]) => ({ name, total }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 6);
  if (!rows.length) {
    container.innerHTML = `<div class="empty-state">No transactions in this category yet.</div>`;
    return;
  }
  const max = rows[0].total;
  container.innerHTML = rows
    .map(
      (r) => `<div class="bl-row">
        <div class="bl-name">${escapeHtml(r.name)}</div>
        <div class="bl-value">${money(r.total)}</div>
        <div class="bl-track"><div class="bl-fill" style="width:${(r.total / max) * 100}%;"></div></div>
      </div>`
    )
    .join("");
}

function initCategoryFilters() {
  document.querySelectorAll("#catRangeSeg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#catRangeSeg button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      catState.range = btn.dataset.range;
      loadCategoryTab();
    });
  });
}

// ---------------------------------------------------------------------
// Money Flow tab
// ---------------------------------------------------------------------
const flowState = { loaded: false, months: [] };

async function loadMoneyFlowTab() {
  flowState.loaded = true;
  const monthly = await fetchMonthlyIncomeExpense();
  flowState.months = [...new Set(monthly.map((m) => m.month))].sort().reverse();
  const select = document.getElementById("flowMonth");
  select.innerHTML = flowState.months.map((m) => `<option value="${m}">${monthLabel(m)}</option>`).join("");
  if (flowState.months.length) {
    select.value = flowState.months[0];
    await renderMoneyFlowForMonth(flowState.months[0]);
  } else {
    document.querySelector("#panel-money-flow .card").innerHTML = `<div class="empty-state">No data yet.</div>`;
  }
}

async function renderMoneyFlowForMonth(monthStartIso) {
  const nextMonthStartIso = addMonths(monthStartIso, 1);
  const rows = await fetchTransactions({ start: monthStartIso, end: nextMonthStartIso });
  renderSankey(buildSankeyFlows(rows));
  renderWaterfall(rows, monthStartIso, nextMonthStartIso);
}

function buildSankeyFlows(rows) {
  const links = new Map();
  const add = (from, to, value) => {
    if (!(value > 0)) return;
    const key = `${from}::${to}`;
    links.set(key, (links.get(key) || 0) + value);
  };
  const transferOut = new Map();
  const transferIn = new Map();

  for (const r of rows) {
    const acct = formatAccountName(r.accountName);
    if (r.categoryRootType === "income") {
      add("Income", acct, r.amount);
    } else if (r.categoryRootType === "expense") {
      add(acct, topLevelCategoryLabel(r.categoryName), Math.abs(r.amount));
    } else if (r.categoryRootType === "transfer") {
      if (r.amount < 0) transferOut.set(acct, (transferOut.get(acct) || 0) + Math.abs(r.amount));
      else transferIn.set(acct, (transferIn.get(acct) || 0) + r.amount);
    }
  }
  const totalIn = [...transferIn.values()].reduce((s, v) => s + v, 0);
  if (totalIn > 0) {
    for (const [outAcct, outAmt] of transferOut) {
      for (const [inAcct, inAmt] of transferIn) {
        add(outAcct, inAcct, outAmt * (inAmt / totalIn));
      }
    }
  }
  return [...links.entries()].map(([key, flow]) => {
    const [from, to] = key.split("::");
    return { from, to, flow };
  });
}

function renderSankey(links) {
  destroyChart("sankey");
  const canvas = document.getElementById("chartSankey");
  if (!links.length) {
    canvas.parentElement.innerHTML = `<div class="empty-state">No money movement recorded this month.</div>`;
    return;
  }
  if (typeof Chart.registry.getController === "function") {
    try {
      Chart.registry.getController("sankey");
    } catch {
      console.warn("chartjs-chart-sankey plugin not registered; Money Flow diagram will not render.");
      canvas.parentElement.innerHTML = `<div class="error-state">Sankey chart plugin failed to load.</div>`;
      return;
    }
  }
  charts.sankey = new Chart(canvas, {
    type: "sankey",
    data: {
      datasets: [
        {
          data: links,
          colorFrom: (c) => hashColorVar(c.dataset.data[c.dataIndex].from),
          colorTo: (c) => hashColorVar(c.dataset.data[c.dataIndex].to),
          colorMode: "gradient",
          borderColor: cssVar("--surface"),
          font: { family: "DM Sans", size: 11, color: cssVar("--text") },
        },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
  });
}

function renderWaterfall(rows, monthStartIso, nextMonthStartIso) {
  const theme = chartTheme();
  destroyChart("waterfall");
  const income = rows.filter((r) => r.categoryRootType === "income").reduce((s, r) => s + r.amount, 0);
  const expenseByCat = new Map();
  for (const r of rows) {
    if (r.categoryRootType !== "expense") continue;
    const cat = topLevelCategoryLabel(r.categoryName);
    expenseByCat.set(cat, (expenseByCat.get(cat) || 0) + Math.abs(r.amount));
  }
  const sortedCats = [...expenseByCat.entries()].sort((a, b) => b[1] - a[1]);
  const topCats = sortedCats.slice(0, 4);
  const otherTotal = sortedCats.slice(4).reduce((s, [, v]) => s + v, 0);
  if (otherTotal > 0) topCats.push(["Other", otherTotal]);

  const openingPoint = overviewCache?.trend?.find((p) => p.month === monthStartIso);
  const closingPoint = overviewCache?.trend?.find((p) => p.month === nextMonthStartIso);
  const totalExpense = topCats.reduce((s, [, v]) => s + v, 0);
  const opening = openingPoint ? openingPoint.net - income + totalExpense : 0;

  const labels = ["Opening"];
  const bars = [[0, opening]];
  const colors = [cssVar("--text-3")];
  let running = opening;

  running += income;
  labels.push("+ Income");
  bars.push([opening, running]);
  colors.push(cssVar("--gain"));

  for (const [cat, amt] of topCats) {
    const start = running;
    running -= amt;
    labels.push(`− ${cat}`);
    bars.push([running, start]);
    colors.push(cssVar("--loss"));
  }

  labels.push("Closing");
  bars.push([0, running]);
  colors.push(cssVar("--accent"));

  const waterfallOptions = baseLineOptions(theme, (v) => money(v));
  waterfallOptions.scales.x.ticks.autoSkip = false;
  waterfallOptions.scales.x.ticks.maxRotation = 40;
  waterfallOptions.scales.x.ticks.minRotation = 40;
  charts.waterfall = new Chart(document.getElementById("chartWaterfall"), {
    type: "bar",
    data: { labels, datasets: [{ data: bars, backgroundColor: colors, borderRadius: 3 }] },
    options: { ...waterfallOptions, plugins: { legend: { display: false } } },
  });
}

function initMoneyFlowFilters() {
  document.getElementById("flowMonth").addEventListener("change", (e) => renderMoneyFlowForMonth(e.target.value));
}

// ---------------------------------------------------------------------
// Accounts tab
// ---------------------------------------------------------------------
const acctState = { selected: null, balances: [] };

async function loadAccountsTab() {
  // v_account_balances covers every row in `accounts` — real bank/card
  // accounts AND category "accounts" (Expenses:*, Income:*, Transfers:*)
  // alike, per the schema's double-entry design. This tab is only about
  // the real tracked accounts, so filter to asset/liability explicitly.
  const all = await fetchAccountBalances();
  const knownOrder = Object.keys(ACCOUNT_LABELS);
  acctState.balances = all
    .filter((b) => b.root_type === "asset" || b.root_type === "liability")
    .sort((a, b) => knownOrder.indexOf(a.name) - knownOrder.indexOf(b.name));
  if (!acctState.selected || !acctState.balances.some((b) => b.name === acctState.selected)) {
    acctState.selected = acctState.balances[0]?.name || null;
  }
  renderAccountSelector();
  if (acctState.selected) await renderAccountDetail(acctState.selected);
}

function renderAccountSelector() {
  const container = document.getElementById("acctSelector");
  container.innerHTML = acctState.balances
    .map((b) => `<button data-account="${b.name}" class="${b.name === acctState.selected ? "active" : ""}">${formatAccountName(b.name)}</button>`)
    .join("");
  container.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      acctState.selected = btn.dataset.account;
      container.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderAccountDetail(acctState.selected);
    });
  });
}

async function renderAccountDetail(accountName) {
  const bal = acctState.balances.find((b) => b.name === accountName);
  renderAccountStats(bal);
  await Promise.all([renderAccountBalanceChart(bal), renderAccountTransactions(accountName)]);
}

function renderAccountStats(bal) {
  const container = document.getElementById("acctStats");
  const isLiability = bal.root_type === "liability";
  let html = `<div class="card stat-box acct-stat">
    <div class="stat-label">Balance</div>
    <div class="stat-value ${isLiability ? "owed" : ""}">${money(bal.balance)}</div>
    <div class="stat-sub">${isLiability ? "amount owed" : "available"}</div>
  </div>`;
  if (isLiability && bal.credit_limit) {
    const pct = ((bal.balance / bal.credit_limit) * 100).toFixed(1);
    html += `<div class="card stat-box acct-stat">
      <div class="stat-label">Credit utilization</div>
      <div class="stat-value">${pct}%</div>
      <div class="stat-sub">of ${money(bal.credit_limit)} limit</div>
    </div>`;
  }
  container.innerHTML = html;
}

async function renderAccountBalanceChart(bal) {
  const theme = chartTheme();
  destroyChart("accountBalance");
  const postings = await fetchAccountPostings(bal.account_id);
  let cum = 0;
  const byDate = new Map();
  for (const p of postings) {
    cum += p.amount;
    byDate.set(p.date, bal.root_type === "liability" ? -cum : cum);
  }
  const points = [...byDate.entries()];
  if (!points.length) {
    document.getElementById("chartAccountBalance").parentElement.innerHTML = `<div class="empty-state">No transactions on this account yet.</div>`;
    return;
  }
  charts.accountBalance = new Chart(document.getElementById("chartAccountBalance"), {
    type: "line",
    data: {
      labels: points.map(([d]) => dateLabel(d)),
      datasets: [
        {
          data: points.map(([, v]) => v),
          borderColor: theme.accent,
          backgroundColor: theme.accent + "22",
          fill: true,
          tension: 0.2,
          pointRadius: 0,
        },
      ],
    },
    options: baseLineOptions(theme, (v) => money(v)),
  });
}

async function renderAccountTransactions(accountName) {
  const body = document.getElementById("acctTxnBody");
  body.innerHTML = `<tr><td colspan="4" class="loading-state">Loading…</td></tr>`;
  const start = computeRangeStart("last-12-months");
  const rows = (await fetchTransactions({ start })).filter((r) => r.accountName === accountName);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty-state">No transactions in the last 12 months.</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .slice(0, 300)
    .map((r) => {
      const amtCls = r.amount < 0 ? "expense" : "income";
      return `<tr>
        <td>${dateLabel(r.date)}</td>
        <td>${escapeHtml(r.description)}</td>
        <td><span class="cat-pill">${formatCategoryName(r.categoryName)}</span></td>
        <td class="amount ${amtCls}">${moneyPrecise(r.amount)}</td>
      </tr>`;
    })
    .join("");
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
const TAB_NAMES = ["overview", "transactions", "category", "money-flow", "accounts"];

function initTabs() {
  document.querySelectorAll("#tabNav button[data-tab]").forEach((btn) => {
    if (btn.disabled) return;
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1);
    if (TAB_NAMES.includes(name)) switchTab(name, false);
  });
  const initial = location.hash.slice(1);
  if (TAB_NAMES.includes(initial)) switchTab(initial, false);
}
function switchTab(name, updateHash = true) {
  document.querySelectorAll("#tabNav button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
  if (updateHash) history.replaceState(null, "", `#${name}`);
  if (name === "transactions" && !txnState.loaded) loadTransactionsTab();
  if (name === "category" && !catState.loaded) loadCategoryTab();
  if (name === "money-flow" && !flowState.loaded) loadMoneyFlowTab();
  if (name === "accounts" && !acctState.balances.length) loadAccountsTab();
}

// ---------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------
let overviewCache = null;

async function init() {
  Chart.defaults.font.family = "DM Sans";

  initTabs();
  initTransactionsFilters();
  initCategoryFilters();
  initMoneyFlowFilters();

  try {
    const cache = await renderOverviewStats();
    overviewCache = cache;
    await renderOverviewCharts("last-12-months", cache);
    document.querySelectorAll("#overviewRangeSeg button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.range === "last-12-months");
      btn.addEventListener("click", () => {
        document.querySelectorAll("#overviewRangeSeg button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        renderOverviewCharts(btn.dataset.range, cache);
      });
    });
    await renderRecentTransactions();
  } catch (err) {
    console.error(err);
    showFatalError(`Failed to load dashboard data: ${escapeHtml(err.message || String(err))}. Check the Supabase URL/key in ${LABEL === "personal" ? "config.local.js" : "config.js"} and that the schema is applied.`);
  }
}

init();
