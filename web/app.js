"use strict";

const state = { zip: "", months: 36, status: "", region: null, charts: {}, map: null, layers: {} };

const PRICE_SERIES = ["median_listing_price", "average_listing_price", "median_sale_price", "zhvi"];
const ACTIVITY_SERIES = ["active_listing_count", "median_days_on_market", "homes_sold",
  "median_listing_price_per_square_foot", "median_sale_ppsf", "avg_sale_to_list", "months_of_supply",
  "price_reduced_share", "median_square_feet"];
const TILE_ORDER = ["average_listing_price", "median_listing_price", "active_listing_count",
  "median_days_on_market", "median_listing_price_per_square_foot", "median_square_feet",
  "median_sale_price", "homes_sold", "zhvi"];

// ---------------------------------------------------------------- helpers
const $ = (sel) => document.querySelector(sel);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = {
  money: (v) => v == null ? "—" : "$" + Math.round(v).toLocaleString(),
  moneyShort: (v) => v == null ? "—" : Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${Math.round(v / 1000)}k`,
  num: (v, d = 0) => v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }),
  pct: (v, d = 1) => v == null ? "—" : `${(v * 100).toFixed(d)}%`,
  signedPct: (v) => v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`,
  signedMoney: (v) => v == null ? "—" : `${v >= 0 ? "+" : "−"}$${Math.round(Math.abs(v)).toLocaleString()}`,
  month: (p) => p ? new Date(p + "T00:00:00").toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "",
};

function formatMetric(key, v) {
  if (v == null) return "—";
  if (key.includes("price") && !key.includes("share") && !key.includes("per_square") && !key.includes("ppsf")) return fmt.money(v);
  if (key === "zhvi") return fmt.money(v);
  if (key.includes("per_square") || key.includes("ppsf")) return fmt.money(v);
  if (key.includes("share") || key === "sold_above_list") return fmt.pct(v);
  if (key === "avg_sale_to_list") return fmt.pct(v, 1);
  if (key === "months_of_supply") return fmt.num(v, 1);
  return fmt.num(v);
}

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
    else if (v !== "" && v != null) url.searchParams.set(k, v);
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function deltaHtml(change, label, invert = false) {
  if (!change) return "";
  const good = invert ? change.pct < 0 : change.pct > 0;
  const cls = change.pct === 0 ? "" : good ? "up" : "down";
  const arrow = change.pct > 0 ? "▲" : change.pct < 0 ? "▼" : "";
  return `<span class="${cls}">${arrow} ${fmt.signedPct(change.pct)}</span> <span class="muted">vs ${label}</span>`;
}

function chartDefaults() {
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--grid");
}

// ---------------------------------------------------------------- snapshot tiles
function renderTiles(summary) {
  const tiles = $("#tiles");
  const kpis = summary.kpis;
  const html = [];
  const listing = summary.listings;
  if (listing && listing.count) {
    html.push(`<div class="tile"><div class="label">Average price · individual listings</div>
      <div class="value">${fmt.money(listing.average_price)}</div>
      <div class="delta muted">median ${fmt.money(listing.median_price)} · ${listing.count} for sale</div>
      <div class="foot">${esc(listing.sources.join(", "))}</div></div>`);
  }
  for (const key of TILE_ORDER) {
    const k = kpis[key];
    if (!k) continue;
    const yoy = k.changes["1y"];
    const invert = key === "median_days_on_market";
    html.push(`<div class="tile"><div class="label">${esc(k.label)}</div>
      <div class="value">${formatMetric(key, k.latest)}</div>
      <div class="delta">${yoy ? deltaHtml(yoy, "a year ago", invert) : '<span class="muted">no prior-year value</span>'}</div>
      <div class="foot">${esc(state.region.sources[k.source]?.label || k.source)} · ${fmt.month(k.period)}</div></div>`);
  }
  tiles.innerHTML = html.join("");
  $("#tiles-empty").hidden = html.length > 0;
  $("#snapshot-geo").textContent = "· " + summary.geo.name;
}

// ---------------------------------------------------------------- trends
function cutoff(points) {
  if (!state.months || !points.length) return points;
  const last = new Date(points[points.length - 1].period + "T00:00:00");
  last.setMonth(last.getMonth() - state.months);
  return points.filter((p) => new Date(p.period + "T00:00:00") >= last);
}

function lineChart(canvas, datasets, yFormat) {
  return new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      parsing: false, normalized: true,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 5, hitRadius: 8 }, line: { borderWidth: 2, tension: 0.15 } },
      scales: {
        x: { type: "linear", grid: { display: false }, ticks: { callback: (v) => fmt.month(isoFromIndex(v)), maxTicksLimit: 8 } },
        y: { grid: { color: css("--grid") }, border: { display: false }, ticks: { callback: yFormat, maxTicksLimit: 6 } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items.length ? fmt.month(isoFromIndex(items[0].parsed.x)) : "",
            label: (item) => ` ${item.dataset.label}: ${yFormat(item.parsed.y, true)}`,
          },
        },
      },
    },
  });
}

// x values are month indexes (year*12+month) so series from different sources line up.
const monthIndex = (iso) => Number(iso.slice(0, 4)) * 12 + Number(iso.slice(5, 7)) - 1;
const isoFromIndex = (i) => `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`;
const toXY = (points) => points.map((p) => ({ x: monthIndex(p.period), y: p.value }));

async function renderTrends() {
  const data = await api("/api/trends", { metric: [...PRICE_SERIES, ...ACTIVITY_SERIES], zip: state.zip });
  state.trends = data.series;
  const colors = [css("--series-1"), css("--series-2"), css("--series-3"), css("--series-4")];
  const datasets = PRICE_SERIES.map((key, i) => ({
    key, label: data.series[key].label, data: toXY(cutoff(data.series[key].points)),
    borderColor: colors[i], backgroundColor: colors[i],
  })).filter((d) => d.data.length);

  state.charts.price?.destroy();
  state.charts.price = lineChart($("#price-chart"), datasets, (v, full) => full ? fmt.money(v) : fmt.moneyShort(v));
  $("#price-legend").innerHTML = datasets.length ? datasets.map((d, i) => `
      <label><input type="checkbox" data-i="${i}" checked><span class="swatch" style="background:${d.borderColor}"></span>${esc(d.label)}</label>`).join("")
    : '<span>No price history for this area yet.</span>';
  $("#price-legend").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
    state.charts.price.setDatasetVisibility(Number(cb.dataset.i), cb.checked);
    state.charts.price.update();
  }));

  // change table
  const rows = [...PRICE_SERIES, "median_listing_price_per_square_foot", "median_sale_ppsf", "active_listing_count", "homes_sold"]
    .map((k) => [k, data.series[k]]).filter(([, s]) => s.summary.latest != null);
  const periods = ["3m", "1y", "3y", "5y"];
  $("#change-table").innerHTML = rows.length ? `<thead><tr><th>Metric</th><th class="num">Latest</th>${periods.map((p) => `<th class="num">${p}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(([k, s]) => `<tr><td>${esc(s.label)}<br><span class="muted">${fmt.month(s.summary.period)}</span></td>
      <td class="num">${formatMetric(k, s.summary.latest)}</td>
      ${periods.map((p) => {
        const c = s.summary.changes[p];
        return `<td class="num ${c ? (c.pct > 0 ? "up" : c.pct < 0 ? "down" : "") : ""}" title="${c ? "was " + formatMetric(k, c.previous) : ""}">${c ? fmt.signedPct(c.pct) : "—"}</td>`;
      }).join("")}</tr>`).join("")}</tbody>` : "<tbody><tr><td class='muted'>No data yet.</td></tr></tbody>";

  // activity metric select
  const sel = $("#activity-metric");
  const available = ACTIVITY_SERIES.filter((k) => data.series[k].points.length);
  const current = sel.value;
  sel.innerHTML = available.map((k) => `<option value="${k}">${esc(data.series[k].label)}</option>`).join("");
  if (available.includes(current)) sel.value = current;
  renderActivity();
}

function renderActivity() {
  const key = $("#activity-metric").value;
  state.charts.activity?.destroy();
  if (!key) return;
  const s = state.trends[key];
  $("#activity-title").textContent = s.label;
  state.charts.activity = lineChart($("#activity-chart"),
    [{ label: s.label, data: toXY(cutoff(s.points)), borderColor: css("--series-1"), backgroundColor: css("--series-1") }],
    (v) => formatMetric(key, v));
}

// ---------------------------------------------------------------- amenities
async function renderAmenities() {
  const a = await api("/api/amenities", { zip: state.zip, status: state.status });
  $("#amenity-empty").hidden = a.count > 0;
  $("#amenity-count").textContent = a.count ? `· ${a.count} homes (${a.sources.join(", ")})` : "";

  const stock = a.housing_stock || {};
  const stockVal = (k) => stock[k]?.value;
  const stockYear = Object.values(stock)[0]?.period?.slice(0, 4);
  const rows = [];
  const avg = a.averages;
  const line = (label, listingVal, stockText, known) => rows.push(
    `<tr><td>${label}</td><td class="num">${listingVal}</td><td class="num">${known ?? ""}</td><td class="num">${stockText ?? "—"}</td></tr>`);
  line("Bedrooms", avg.bedrooms ? fmt.num(avg.bedrooms.mean, 2) : "—", stockVal("avg_bedrooms") != null ? fmt.num(stockVal("avg_bedrooms"), 2) : null, avg.bedrooms?.known);
  line("Bathrooms", avg.bathrooms ? fmt.num(avg.bathrooms.mean, 2) : "—", null, avg.bathrooms?.known);
  line("Living area (sq ft)", avg.sqft ? fmt.num(avg.sqft.mean) : "—", null, avg.sqft?.known);
  line("Lot size (acres)", avg.lot_sqft ? fmt.num(avg.lot_sqft.mean / 43560, 2) : "—", null, avg.lot_sqft?.known);
  line("Year built (median)", avg.year_built ? avg.year_built.median.toFixed(0) : "—", stockVal("median_year_built")?.toFixed(0), avg.year_built?.known);
  line("Garage spaces", avg.garage_spaces ? fmt.num(avg.garage_spaces.mean, 1) : "—", null, avg.garage_spaces?.known);
  for (const [k, s] of Object.entries(a.shares)) line(esc(s.label), fmt.pct(s.share, 0), null, s.known);
  line("Price per sq ft (median)", a.price_per_sqft ? fmt.money(a.price_per_sqft.median) : "—", null, a.price_per_sqft?.known);
  line("Owner-occupied share", "—", stockVal("owner_occupied_share") != null ? fmt.pct(stockVal("owner_occupied_share"), 0) : null);
  line("Median home value", "—", stockVal("median_home_value") != null ? fmt.money(stockVal("median_home_value")) : null);
  $("#amenity-table").innerHTML = `<thead><tr><th></th><th class="num">Listings</th><th class="num">Known</th>
    <th class="num">Housing stock${stockYear ? ` <span class="muted">(ACS ${stockYear})</span>` : ""}</th></tr></thead><tbody>${rows.join("")}</tbody>`;

  // price by bedrooms
  state.charts.beds?.destroy();
  const groups = a.by_bedrooms.filter((g) => g.n >= 2);
  $("#bed-empty").hidden = groups.length > 0;
  if (groups.length) {
    state.charts.beds = new Chart($("#bed-chart"), {
      type: "bar",
      data: {
        labels: groups.map((g) => `${g.group} bd`),
        datasets: [{ label: "Median price", data: groups.map((g) => g.median_price), backgroundColor: css("--series-1"),
          borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 48 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        scales: { x: { grid: { display: false } }, y: { border: { display: false }, ticks: { callback: (v) => fmt.moneyShort(v), maxTicksLimit: 5 } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` Median ${fmt.money(it.parsed.y)} · ${groups[it.dataIndex].n} homes` } } },
      },
    });
  }

  // premiums
  const prem = Object.values(a.premiums);
  $("#premium-table").innerHTML = prem.length ? `<thead><tr><th>Feature</th><th class="num">With</th><th class="num">Without</th><th class="num">Difference</th></tr></thead>
    <tbody>${prem.map((p) => `<tr><td>${esc(p.label)}</td><td class="num">${fmt.money(p.with_median)} <span class="muted">n=${p.with_n}</span></td>
      <td class="num">${fmt.money(p.without_median)} <span class="muted">n=${p.without_n}</span></td><td class="num">${fmt.signedMoney(p.difference)}</td></tr>`).join("")}</tbody>`
    : "<tbody><tr><td class='muted'>Needs at least 3 homes with and 3 without a feature. Garage / basement data comes from CSV imports or RentCast enrichment.</td></tr></tbody>";

  // model
  const m = a.model;
  $("#model-table").innerHTML = m ? `<thead><tr><th>Term</th><th class="num">Estimate</th><th class="num">±</th></tr></thead>
    <tbody>${m.terms.map((t) => `<tr><td>${esc(t.label)}</td><td class="num">${fmt.signedMoney(t.estimate)}</td><td class="num muted">${fmt.money(t.std_error)}</td></tr>`).join("")}
    <tr><td class="muted" colspan="3">n = ${m.n} homes · R² = ${m.r2.toFixed(2)}</td></tr></tbody>`
    : "<tbody><tr><td class='muted'>Needs at least ~20 homes with amenity details.</td></tr></tbody>";
}

// ---------------------------------------------------------------- map + ZIP table
function mixColor(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

function initMap() {
  const r = state.region;
  state.map = L.map("map", { scrollWheelZoom: false }).setView(r.center, r.zoom);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.layers.zips = L.layerGroup().addTo(state.map);
  state.layers.listings = L.layerGroup().addTo(state.map);
}

const ZIP_COLUMNS = [
  ["zip", "ZIP", (r) => `${esc(r.zip)} <span class="muted">${esc(r.name)}</span>`],
  ["median_listing_price", "Median list", (r) => fmt.money(r.median_listing_price), true],
  ["median_listing_price_yoy", "YoY", (r) => r.median_listing_price_yoy == null ? "—" : `<span class="${r.median_listing_price_yoy >= 0 ? "up" : "down"}">${fmt.signedPct(r.median_listing_price_yoy)}</span>`, true],
  ["average_listing_price", "Avg list", (r) => fmt.money(r.average_listing_price), true],
  ["active_listing_count", "Active", (r) => fmt.num(r.active_listing_count), true],
  ["median_days_on_market", "Days on mkt", (r) => fmt.num(r.median_days_on_market), true],
  ["median_listing_price_per_square_foot", "$/sq ft", (r) => fmt.money(r.median_listing_price_per_square_foot), true],
  ["median_square_feet", "Median sq ft", (r) => fmt.num(r.median_square_feet), true],
  ["zhvi", "Typical value", (r) => fmt.money(r.zhvi), true],
  ["zhvi_yoy", "Value YoY", (r) => r.zhvi_yoy == null ? "—" : fmt.signedPct(r.zhvi_yoy), true],
  ["census_avg_bedrooms", "Avg beds (stock)", (r) => fmt.num(r.census_avg_bedrooms, 2), true],
];

async function renderZips() {
  const rows = await api("/api/zips");
  state.zipRows = rows;
  state.zipSort = state.zipSort || { key: "median_listing_price", dir: -1 };
  drawZipTable();

  // map circles shaded by median list price
  state.layers.zips.clearLayers();
  const vals = rows.map((r) => r.median_listing_price).filter((v) => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const [cLo, cHi] = [css("--seq-low"), css("--seq-high")];
  for (const r of rows) {
    if (r.lat == null) continue;
    const t = r.median_listing_price == null || hi === lo ? 0 : (r.median_listing_price - lo) / (hi - lo);
    const selected = state.zip === r.zip;
    L.circleMarker([r.lat, r.lon], {
      radius: 10 + Math.sqrt(r.active_listing_count || 0) * 2.2,
      color: selected ? css("--text") : css("--surface"), weight: selected ? 3 : 2,
      fillColor: r.median_listing_price == null ? css("--axis") : mixColor(cLo, cHi, t), fillOpacity: 0.85,
    }).bindTooltip(`<strong>${esc(r.zip)} ${esc(r.name)}</strong><br>Median list ${fmt.money(r.median_listing_price)}<br>
        ${fmt.num(r.active_listing_count)} active · ${fmt.num(r.median_days_on_market)} days on market`)
      .on("click", () => selectZip(r.zip))
      .addTo(state.layers.zips);
  }
  $("#map-legend").innerHTML = vals.length ? `Median list price by ZIP<div class="ramp"></div>
    <div class="ends"><span>${fmt.moneyShort(lo)}</span><span>${fmt.moneyShort(hi)}</span></div>
    <div>Circle size = active listings · click to filter</div>` : "No ZIP data yet";
}

function drawZipTable() {
  const { key, dir } = state.zipSort;
  const rows = [...state.zipRows].sort((a, b) => {
    const [x, y] = [a[key], b[key]];
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * dir;
  });
  const table = $("#zip-table");
  table.innerHTML = `<thead><tr>${ZIP_COLUMNS.map(([k, label, , num]) =>
      `<th data-key="${k}" class="${num ? "num" : ""} ${k === key ? "sorted" + (dir > 0 ? " asc" : "") : ""}">${label}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr class="clickable" data-zip="${esc(r.zip)}">${ZIP_COLUMNS.map(([, , f, num]) => `<td class="${num ? "num" : ""}">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  table.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.key;
    state.zipSort = { key: k, dir: state.zipSort.key === k ? -state.zipSort.dir : -1 };
    drawZipTable();
  }));
  table.querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => selectZip(tr.dataset.zip)));
}

// ---------------------------------------------------------------- listings
async function renderListings() {
  const rows = await api("/api/listings", { zip: state.zip, status: state.status, limit: 500 });
  $("#listing-count").textContent = rows.length ? `· ${rows.length}${rows.length === 500 ? "+" : ""}` : "";
  const yn = (v) => v == null ? "—" : v ? "Yes" : "No";
  $("#listing-table").innerHTML = rows.length ? `<thead><tr><th>Address</th><th>ZIP</th><th>Status</th><th class="num">Price</th>
      <th class="num">Beds</th><th class="num">Baths</th><th class="num">Sq ft</th><th class="num">$/sq ft</th><th class="num">Built</th>
      <th>Garage</th><th>Basement</th><th>Source</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.address)}</a>` : esc(r.address)}</td>
      <td>${esc(r.zip)}</td><td>${esc(r.status)}</td><td class="num">${fmt.money(r.price)}</td>
      <td class="num">${fmt.num(r.bedrooms)}</td><td class="num">${fmt.num(r.bathrooms, 1)}</td><td class="num">${fmt.num(r.sqft)}</td>
      <td class="num">${r.sqft ? fmt.money(r.price / r.sqft) : "—"}</td><td class="num">${r.year_built ?? "—"}</td>
      <td>${yn(r.garage)}${r.garage_spaces ? ` (${fmt.num(r.garage_spaces)})` : ""}</td>
      <td>${yn(r.basement)}${r.basement_finished ? " (finished)" : ""}</td><td>${esc(r.source)}</td></tr>`).join("")}</tbody>`
    : "<tbody><tr><td class='muted'>No listings for this selection.</td></tr></tbody>";

  state.layers.listings.clearLayers();
  for (const r of rows) {
    if (r.lat == null || r.lon == null) continue;
    L.circleMarker([r.lat, r.lon], { radius: 4, weight: 1, color: css("--surface"), fillColor: css("--series-2"), fillOpacity: 0.9 })
      .bindTooltip(`${esc(r.address)}<br>${fmt.money(r.price)} · ${fmt.num(r.bedrooms)} bd / ${fmt.num(r.bathrooms, 1)} ba · ${fmt.num(r.sqft)} sq ft`)
      .addTo(state.layers.listings);
  }
}

// ---------------------------------------------------------------- sources
function renderSources() {
  const r = state.region;
  const status = Object.fromEntries(r.source_status.map((s) => [s.source, s]));
  const names = [...new Set([...Object.keys(r.sources).filter((k) => k !== "demo" || status.demo), ...Object.keys(status)])];
  $("#source-table").innerHTML = `<thead><tr><th>Source</th><th>Provides</th><th>Last run</th><th>Data through</th></tr></thead>
    <tbody>${names.map((name) => {
      const info = r.sources[name] || { label: name, description: "Imported listings" };
      const s = status[name];
      const run = s ? `${esc(s.status)} · ${new Date(s.finished || s.started).toLocaleDateString()}${s.message ? ` <span class="muted" title="${esc(s.message)}">(${esc(s.message.slice(0, 60))})</span>` : ""}` : `<span class="muted">not run${info.api_key_env ? ` · needs ${esc(info.api_key_env)}` : ""}</span>`;
      const through = s?.latest_period ? fmt.month(s.latest_period) : s?.listing_count != null ? `${s.listing_count} listings` : "—";
      return `<tr><td>${info.homepage ? `<a href="${esc(info.homepage)}" target="_blank" rel="noopener">${esc(info.label)}</a>` : esc(info.label)}</td>
        <td style="white-space:normal">${esc(info.description)}</td><td>${run}</td><td>${through}</td></tr>`;
    }).join("")}</tbody>`;
}

// ---------------------------------------------------------------- wiring
async function refresh() {
  const tasks = [
    api("/api/summary", { zip: state.zip }).then(renderTiles),
    renderTrends(),
    renderAmenities(),
    renderListings(),
  ];
  const results = await Promise.allSettled(tasks);
  results.filter((r) => r.status === "rejected").forEach((r) => console.error(r.reason));
  if (state.zipRows) drawZipTable();
}

function selectZip(zip) {
  state.zip = state.zip === zip ? "" : zip;
  $("#zip-select").value = state.zip;
  refresh().then(renderZips);
}

function segmented(el, attr, onChange) {
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    onChange(b.dataset[attr]);
  }));
}

async function main() {
  chartDefaults();
  state.region = await api("/api/region");
  const r = state.region;
  $("#region-name").textContent = `${r.name} · local housing market`;
  document.title = `Realty Map · ${r.name}`;
  $("#zip-select").innerHTML = `<option value="">All of ${esc(r.name)}</option>` +
    r.zips.map((z) => `<option value="${esc(z.zip)}">${esc(z.zip)} · ${esc(z.name)}</option>`).join("");
  $("#demo-banner").hidden = !r.demo_listings;
  $("#zip-select").addEventListener("change", (e) => selectZip(e.target.value || state.zip));
  segmented($("#range"), "months", (m) => { state.months = Number(m); renderTrends(); });
  segmented($("#status"), "status", (s) => { state.status = s; renderAmenities(); renderListings(); });
  $("#activity-metric").addEventListener("change", renderActivity);
  renderSources();
  initMap();
  await Promise.all([refresh(), renderZips()]);
}

main().catch((err) => {
  console.error(err);
  $("#region-name").textContent = "Could not load data: " + err.message;
});
