"use strict";
// Individual homes: address search, recent-sales table, and the home detail panel.
// Uses the helpers defined in app.js ($, esc, fmt, api, css, state).

const EVENT_LABELS = {
  listed: "Listed for sale", price_change: "Price changed", pending: "Under contract",
  sold: "Sold", off_market: "Taken off market", assessment: "Tax assessment",
};
const dayIndex = (iso) => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return y * 12 + (m - 1) + (d - 1) / 31;
};
const fmtDate = (iso) => iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";
const yn = (v) => v == null ? "—" : v ? "Yes" : "No";
const pctCell = (v, d) => v == null ? "—" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${fmt.signedPct(v)}</span>`;

// ---------------------------------------------------------------- search
function initSearch() {
  const input = $("#home-search");
  const list = $("#search-results");
  let timer = null, results = [], active = -1;

  const close = () => { list.hidden = true; active = -1; };
  const draw = () => {
    list.innerHTML = results.length ? results.map((r, i) => `<li data-id="${r.id}" class="${i === active ? "active" : ""}">
        ${esc(r.address)} <span class="muted">· ${esc(r.city || "")} ${esc(r.zip || "")}
        ${r.bedrooms != null ? ` · ${fmt.num(r.bedrooms)} bd` : ""}${r.sales ? ` · ${r.sales} sale${r.sales > 1 ? "s" : ""}` : ""}</span></li>`).join("")
      : `<li class="muted">No homes match. Only homes in loaded listings or sales can be found.</li>`;
    list.hidden = false;
    list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      openHome(Number(li.dataset.id));
      close();
      input.blur();
    }));
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      try { results = await api("/api/homes", { q }); active = -1; draw(); } catch (err) { console.error(err); }
    }, 200);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden || !results.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      draw();
    } else if (e.key === "Enter") {
      openHome(results[Math.max(active, 0)].id);
      close();
      input.blur();
    } else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
}

// ---------------------------------------------------------------- recent sales table
async function renderSales() {
  const rows = await api("/api/sales", { zip: state.zip, months: state.salesMonths ?? 24, limit: 500 });
  $("#sales-count").textContent = rows.length ? `· ${rows.length}${rows.length === 500 ? "+" : ""}` : "";
  $("#sales-table").innerHTML = rows.length ? `<thead><tr><th>Sold</th><th>Address</th><th>ZIP</th><th class="num">Price</th>
      <th class="num">Beds</th><th class="num">Baths</th><th class="num">Sq ft</th><th class="num">$/sq ft</th>
      <th class="num">Sale / list</th><th class="num">Previous sale</th><th class="num">Change</th><th>Source</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="clickable" data-home="${r.property_id}">
      <td>${fmtDate(r.sold_date)}</td><td>${esc(r.address)}</td><td>${esc(r.zip)}</td>
      <td class="num">${fmt.money(r.sold_price)}</td><td class="num">${fmt.num(r.bedrooms)}</td>
      <td class="num">${fmt.num(r.bathrooms, 1)}</td><td class="num">${fmt.num(r.sqft)}</td>
      <td class="num">${r.sqft ? fmt.money(r.sold_price / r.sqft) : "—"}</td>
      <td class="num">${r.sale_to_list ? fmt.pct(r.sale_to_list, 0) : "—"}</td>
      <td class="num">${r.previous_price ? `${fmt.money(r.previous_price)} <span class="muted">${r.previous_date.slice(0, 4)}</span>` : "—"}</td>
      <td class="num">${pctCell(r.change_pct)}</td><td>${esc(r.sources)}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="muted" style="white-space:normal">No individual sales recorded for this selection. Sales come from
        CSV imports of sold homes (<code>python -m realtymap import sold.csv</code>), RentCast property lookups, or listings
        that were followed until they sold.</td></tr></tbody>`;
  bindHomeRows($("#sales-table"));
}

function bindHomeRows(table) {
  table.querySelectorAll("tr[data-home]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    openHome(Number(tr.dataset.home));
  }));
}

// ---------------------------------------------------------------- detail panel
let lastFocus = null;

async function openHome(id, { pushHash = true } = {}) {
  if (!id) return;
  lastFocus = document.activeElement;
  $("#home-panel").hidden = false;
  $("#home-backdrop").hidden = false;
  document.body.style.overflow = "hidden";
  $("#home-title").textContent = "Loading…";
  $("#home-subtitle").textContent = "";
  $("#home-body").innerHTML = "";
  if (pushHash && location.hash !== `#home=${id}`) history.pushState(null, "", `#home=${id}`);
  try {
    renderHome(await api(`/api/homes/${id}`));
  } catch (err) {
    $("#home-title").textContent = "Home not found";
    $("#home-body").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
  $("#home-close").focus();
}

function closeHome({ pushHash = true } = {}) {
  if ($("#home-panel").hidden) return;
  $("#home-panel").hidden = true;
  $("#home-backdrop").hidden = true;
  document.body.style.overflow = "";
  state.charts.home?.destroy();
  if (pushHash && location.hash.startsWith("#home=")) history.pushState(null, "", location.pathname + location.search);
  lastFocus?.focus?.();
}

function homeFromHash() {
  const m = location.hash.match(/^#home=(\d+)$/);
  if (m) openHome(Number(m[1]), { pushHash: false });
  else closeHome({ pushHash: false });
}

function statusBadge(d) {
  const cur = d.current_listing;
  if (cur) return `<span class="badge active">${cur.status === "pending" ? "Under contract" : "For sale"}</span>`;
  const last = d.sales[d.sales.length - 1];
  if (last) return `<span class="badge">Sold ${fmtDate(last.date)}</span>`;
  return "";
}

function renderHome(d) {
  const p = d.property;
  const cur = d.current_listing;
  const sales = d.sales;
  const lastSale = sales[sales.length - 1];
  $("#home-title").innerHTML = `${esc(p.address)} ${statusBadge(d)}`;
  $("#home-subtitle").textContent = [p.city, p.zip].filter(Boolean).join(", ") + (p.property_type ? ` · ${p.property_type}` : "");

  const parts = [];

  // value tiles
  const tiles = [];
  if (cur) tiles.push(tile(cur.status === "pending" ? "Asking price (under contract)" : "Asking price", fmt.money(cur.list_price),
    `${esc(cur.source)} · listed ${fmtDate(cur.list_date || cur.first_seen)}`));
  if (lastSale) tiles.push(tile("Last sold", fmt.money(lastSale.price), fmtDate(lastSale.date)));
  const v = d.valuation;
  const compare = (value) => cur && cur.list_price ? `<br>asking is ${fmt.signedPct(cur.list_price / value - 1)} vs this` : "";
  if (v.index) tiles.push(tile("Value from area trend", fmt.money(v.index.value),
    `last sale ${fmt.money(v.index.from_price)} × area change ${fmt.signedPct(v.index.area_change_pct)} since then (${esc(v.index.index.label)})${compare(v.index.value)}`));
  if (v.comps) tiles.push(tile("Value from comparable sales", fmt.money(v.comps.value),
    `${v.comps.n} nearby sales at a median ${fmt.money(v.comps.price_per_sqft)}/sq ft${compare(v.comps.value)}`));
  if (v.model) tiles.push(tile("Value from price model", fmt.money(v.model.value),
    `regression on ${v.model.n} homes (R² ${v.model.r2.toFixed(2)})${compare(v.model.value)}`));
  if (tiles.length) parts.push(`<div class="tiles compact">${tiles.join("")}</div>
    <p class="note">Estimates are rough guides from public data, not appraisals. They can't see condition, updates or the lot.</p>`);

  // facts
  const facts = [
    ["Bedrooms", fmt.num(p.bedrooms)], ["Bathrooms", fmt.num(p.bathrooms, 1)], ["Living area", p.sqft ? `${fmt.num(p.sqft)} sq ft` : "—"],
    ["Lot", p.lot_sqft ? `${fmt.num(p.lot_sqft / 43560, 2)} acres` : "—"], ["Year built", p.year_built ?? "—"],
    ["Garage", p.garage ? `Yes${p.garage_spaces ? ` (${fmt.num(p.garage_spaces)})` : ""}` : yn(p.garage)],
    ["Basement", p.basement ? (p.basement_finished ? "Yes, finished" : p.basement_finished === 0 ? "Yes, unfinished" : "Yes") : yn(p.basement)],
    ["Fireplace", yn(p.fireplace)],
    ["Price / sq ft", p.sqft && (cur?.list_price || lastSale) ? fmt.money((cur?.list_price || lastSale.price) / p.sqft) : "—"],
  ];
  parts.push(`<div class="card"><h3>Home details</h3><div class="facts">${facts.map(([k, val]) => `<div>${k}<strong>${val}</strong></div>`).join("")}</div>
    ${cur?.url ? `<p class="note"><a href="${esc(cur.url)}" target="_blank" rel="noopener">View listing</a></p>` : ""}</div>`);

  // price history chart
  const hasChart = d.events.some((e) => e.event !== "assessment" && e.price);
  if (hasChart) parts.push(`<div class="card"><h3>Price history</h3><div class="legend" id="home-legend"></div>
    <div class="chart-box small"><canvas id="home-chart" aria-label="Price history for this home"></canvas></div>
    ${d.chart_index.length ? `<p class="note">The dashed line is the area trend (${esc(d.area_index.label)}) scaled to start at this
      home's first recorded sale, showing what the home would be worth had it tracked the area exactly.</p>` : ""}</div>`);

  // sales history
  if (sales.length) {
    parts.push(`<div class="card"><h3>Sale history</h3><div class="table-scroll"><table>
      <thead><tr><th>Date</th><th class="num">Price</th><th class="num">Change</th><th class="num">Per year</th>
        <th class="num">Area per year</th><th>Source</th></tr></thead>
      <tbody>${[...sales].reverse().map((s) => `<tr><td>${fmtDate(s.date)}</td><td class="num">${fmt.money(s.price)}</td>
        <td class="num">${s.change_pct != null ? `${pctCell(s.change_pct)} <span class="muted">${s.years_held.toFixed(1)} yrs</span>` : "—"}</td>
        <td class="num">${pctCell(s.annual_pct)}</td><td class="num">${pctCell(s.area_annual_pct)}</td>
        <td>${esc(s.sources.join(", "))}</td></tr>`).join("")}</tbody></table></div>
      ${d.area_index ? `<p class="note">"Area per year" is the ${esc(d.area_index.label)} over the same holding period.</p>` : ""}</div>`);
  }

  // lookup prompt
  const lookupNote = !p.looked_up
    ? (state.region.lookup_enabled
      ? `<button class="primary" id="home-lookup">Fetch sale history &amp; assessments</button>
         <span class="note">Uses 1 RentCast API request.</span><div id="lookup-msg"></div>`
      : `<p class="note">${sales.length ? "More" : "No"} sale history on file for this home. Add past sales by importing a CSV of
          sold homes, or set <code>RENTCAST_API_KEY</code> to fetch this home's record (past sales, assessments, garage /
          basement) with one click.</p>`)
    : `<p class="note">Property record fetched ${fmtDate(p.looked_up)}.</p>`;
  parts.push(`<div class="card">${lookupNote}</div>`);

  // timeline
  const timeline = d.events.filter((e) => e.event !== "assessment").reverse();
  if (timeline.length) {
    const color = { sold: css("--series-1"), listed: css("--series-2"), price_change: css("--series-2"), pending: css("--series-3") };
    parts.push(`<div class="card"><h3>Timeline</h3><ul class="timeline">${timeline.map((e) => `<li>
      <span class="ev-date">${fmtDate(e.event_date)}</span>
      <span><span class="dot" style="background:${color[e.event] || css("--axis")}"></span>${EVENT_LABELS[e.event] || esc(e.event)}
        <span class="muted">· ${esc(e.source)}${e.note && e.event !== "price_change" ? ` · ${esc(e.note)}` : ""}</span></span>
      <span class="ev-price">${e.price ? fmt.money(e.price) : ""}</span></li>`).join("")}</ul></div>`);
  }

  // assessments
  const assessments = d.events.filter((e) => e.event === "assessment");
  if (assessments.length) {
    parts.push(`<div class="card"><h3>Tax assessments</h3><div class="table-scroll"><table>
      <thead><tr><th>Year</th><th class="num">Assessed value</th><th class="num">Implied market value</th><th>Source</th></tr></thead>
      <tbody>${[...assessments].reverse().map((a) => `<tr><td>${a.event_date.slice(0, 4)}</td><td class="num">${fmt.money(a.price)}</td>
        <td class="num">${fmt.money(a.price * 2)}</td><td>${esc(a.source)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">In Michigan the assessed value (SEV) is set by law at half of the assessor's estimate of market value,
        so doubling it gives that estimate. (If a source reports taxable value instead, which is capped, doubling understates
        it.) Assessments are based on the prior year's sales, so they lag the market.</p></div>`);
  }

  // comparables
  parts.push(`<div class="card"><h3>Comparable recent sales</h3>${d.comparables.length ? `<div class="table-scroll"><table>
      <thead><tr><th>Address</th><th>Sold</th><th class="num">Price</th><th class="num">Beds</th><th class="num">Baths</th>
        <th class="num">Sq ft</th><th class="num">$/sq ft</th><th class="num">Distance</th></tr></thead>
      <tbody>${d.comparables.map((c) => `<tr class="clickable" data-home="${c.id}"><td>${esc(c.address)}</td><td>${fmtDate(c.sold_date)}</td>
        <td class="num">${fmt.money(c.sold_price)}</td><td class="num">${fmt.num(c.bedrooms)}</td><td class="num">${fmt.num(c.bathrooms, 1)}</td>
        <td class="num">${fmt.num(c.sqft)}</td><td class="num">${fmt.money(c.price_per_sqft)}</td>
        <td class="num">${c.distance_mi != null ? `${c.distance_mi.toFixed(1)} mi` : "same ZIP"}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">Sold in the last 2 years within 3 miles (or the same ZIP when locations are unknown), within one
        bedroom and 30% of the living area, ranked by closeness, recency and size.</p>`
    : `<p class="note">No similar homes sold nearby in the last 2 years among the sales loaded.</p>`}</div>`);

  $("#home-body").innerHTML = parts.join("");
  bindHomeRows($("#home-body"));
  $("#home-lookup")?.addEventListener("click", () => lookupHome(p.id));
  if (hasChart) drawHomeChart(d);
}

function tile(label, value, foot) {
  return `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div><div class="foot">${foot}</div></div>`;
}

async function lookupHome(id) {
  const btn = $("#home-lookup");
  btn.disabled = true;
  btn.textContent = "Fetching…";
  try {
    const res = await fetch(`/api/homes/${id}/lookup`, { method: "POST" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || res.statusText);
    renderHome(body);
    renderSales();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Fetch sale history & assessments";
    $("#lookup-msg").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

function drawHomeChart(d) {
  state.charts.home?.destroy();
  const sold = d.events.filter((e) => e.event === "sold" && e.price);
  const asks = d.events.filter((e) => ["listed", "price_change", "pending", "off_market"].includes(e.event) && e.price);
  const [c1, c2, muted] = [css("--series-1"), css("--series-2"), css("--muted")];
  const datasets = [];
  if (d.chart_index.length) datasets.push({
    label: "Area trend", data: d.chart_index.map((p) => ({ x: dayIndex(p.date), y: p.value })),
    borderColor: muted, backgroundColor: muted, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4, order: 3,
  });
  if (asks.length) datasets.push({
    label: "Asking price", data: asks.map((e) => ({ x: dayIndex(e.event_date), y: e.price, ev: e })),
    borderColor: c2, backgroundColor: c2, stepped: "before", borderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
    pointBorderColor: css("--surface"), pointBorderWidth: 2, order: 2,
  });
  datasets.push({
    label: "Sold", data: sold.map((e) => ({ x: dayIndex(e.event_date), y: e.price, ev: e })), showLine: false,
    borderColor: c1, backgroundColor: c1, pointRadius: 6, pointHoverRadius: 8, pointBorderColor: css("--surface"), pointBorderWidth: 2, order: 1,
  });
  const xs = datasets.flatMap((ds) => ds.data.map((p) => p.x));
  const [minX, maxX] = [Math.min(...xs), Math.max(...xs, dayIndex(new Date().toISOString()))];
  const pad = Math.max(3, (maxX - minX) * 0.04);
  $("#home-legend").innerHTML = datasets.map((ds) => `<label><span class="swatch" style="background:${ds.borderColor}${ds.borderDash ? ";opacity:.7" : ""}"></span>${ds.label}</label>`).join("");
  state.charts.home = new Chart($("#home-chart"), {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
      interaction: { mode: "nearest", intersect: false, axis: "x" },
      scales: {
        x: { type: "linear", min: minX - pad, max: maxX + pad, grid: { display: false },
          ticks: { maxTicksLimit: 7, callback: (v) => String(Math.floor(v / 12)) } },
        y: { grid: { color: css("--grid") }, border: { display: false }, ticks: { callback: (v) => fmt.moneyShort(v), maxTicksLimit: 6 } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => {
              const ev = items[0]?.raw?.ev;
              return ev ? fmtDate(ev.event_date) : fmt.month(isoFromIndex(Math.floor(items[0].parsed.x)));
            },
            label: (it) => {
              const ev = it.raw.ev;
              return ` ${ev ? EVENT_LABELS[ev.event] : it.dataset.label}: ${fmt.money(it.parsed.y)}`;
            },
          },
        },
      },
    },
  });
}

function initHomes() {
  initSearch();
  $("#home-close").addEventListener("click", () => closeHome());
  $("#home-backdrop").addEventListener("click", () => closeHome());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#home-panel").hidden) closeHome(); });
  window.addEventListener("popstate", homeFromHash);
  segmented($("#sales-range"), "months", (m) => { state.salesMonths = Number(m); renderSales(); });
  homeFromHash();
}
