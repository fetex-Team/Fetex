"use strict";
// Gangnam Twin dashboard — no external libraries (works offline).

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
async function api(path, body) {
  const r = await fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `요청 실패 (${r.status})`);
  return data;
}
const S = { meta: null, params: [], exps: [], strategies: {}, selected: null, detail: null, runParams: {}, unityFrom: {}, metric: "service_rate" };
const ORDER = ["patrol", "forecast", "prepositioned", "rl_prepositioned"];          // 색은 전략을 따른다 (순위가 바뀌어도 그대로)
const COLOR = { patrol: "var(--s1)", prepositioned: "var(--s2)", forecast: "var(--s3)", rl_prepositioned: "var(--s4)" };
const M = {
  service_rate: { name: "서비스율", fmt: v => (v * 100).toFixed(1) + "%", axis: v => v.toFixed(0) + "%", k: 100, unit: "%" },
  avg_wait_sec: { name: "평균 대기", fmt: v => v.toFixed(0) + "초", axis: v => v.toFixed(0), k: 1, unit: "초" },
  n_timeout_removed: { name: "놓친 손님", fmt: v => v.toFixed(0) + "명", axis: v => v.toFixed(0), k: 1, unit: "명" },
  p90_wait_sec: { name: "90% 대기", fmt: v => v.toFixed(0) + "초", axis: v => v.toFixed(0), k: 1, unit: "초" },
};
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const md_ = d => { const t = new Date(d + "T00:00:00"); return { md: `${t.getMonth() + 1}/${t.getDate()}`, dow: DOW[t.getDay()] }; };
const dayLabel = d => { const x = md_(d); return `${x.md} ${x.dow}`; };
const sname = s => S.strategies[s] || s;
const sortS = arr => [...arr].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
const GRADES = { A: "실측", B: "공식 통계·조사", C: "규정·요금", D: "실데이터로 계산", E: "검증 구간으로 선택", F: "설계 선택", G: "가정" };
const gradeBadges = g => (g || "").split("+").filter(Boolean).map(x => `<span class="g ${esc(x.trim())}" title="${esc(GRADES[x.trim()] || "")}">${esc(x.trim())}</span>`).join("");

// ---------- 테마 ----------
(function theme() {
  const saved = (() => { try { return localStorage.getItem("twin-theme"); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  $("#themeBtn").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("twin-theme", next); } catch { }
  };
})();

// ---------- 툴팁 ----------
const tip = $("#tip");
document.addEventListener("mousemove", e => {
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.classList.remove("on"); return; }
  tip.innerHTML = el.dataset.tip;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(innerWidth - w - 12, e.clientX + 14) + "px";
  tip.style.top = Math.max(8, e.clientY - h - 12) + "px";
  tip.classList.add("on");
});
const tipHTML = (title, rows) => `<div class="t">${esc(title)}</div>` + rows.map(([c, l, v]) => `<div class="r"><span>${c ? `<i style="background:${c}"></i>` : ""}${esc(l)}</span><b>${esc(v)}</b></div>`).join("");

// ---------- 라우팅 ----------
const TITLES = { overview: ["분석", "개요"], compare: ["분석", "실험 비교"], sens: ["분석", "민감도"], run: ["실행", "새 시뮬레이션"], jobs: ["실행", "작업"], unity: ["실행", "Unity 재생"] };
$$("#nav a").forEach(a => a.onclick = () => show(a.dataset.tab));
function show(t) {
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.tab === t));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "tab-" + t));
  [$("#crumb").textContent, $("#pageTitle").textContent] = TITLES[t];
  $("#topRight").innerHTML = t === "overview" ? overviewChips() : "";
  if (t === "jobs") loadJobs();
  if (t === "unity") loadUnity();
  if (t === "sens") loadSens();
  if (t === "compare" && !S.selected && S.exps.length) selectExp((S.exps.find(e => e.name === "twin_v3") || S.exps[0]).name);
  history.replaceState(null, "", "#" + t);
}

// ---------- 차트 공통 ----------
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0);
  const a = Math.floor(lo / step) * step, out = [];
  for (let v = a; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return out;
}
function sparkline(vals, color) {
  const W = 96, H = 32, lo = Math.min(...vals), hi = Math.max(...vals);
  const pts = vals.map((v, i) => [i * W / (vals.length - 1 || 1), H - 4 - (H - 8) * ((v - lo) / ((hi - lo) || 1))]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>
    <circle cx="${pts.at(-1)[0]}" cy="${pts.at(-1)[1]}" r="2.6" fill="${color}"/></svg>`;
}

// 가로 막대 (전략별 서비스율): 0부터 시작하지 않는 대신 축 눈금을 명시
function rankBars(el, items, { lo, hi, fmt }) {
  const W = Math.max(420, el.clientWidth || 700), rowH = 40, L = 120, R = 70, H = items.length * rowH + 22;
  const X = v => L + (W - L - R) * (v - lo) / (hi - lo);
  const ticks = niceTicks(lo, hi, 4).filter(t => t >= lo && t <= hi);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">`;
  ticks.forEach(t => { s += `<line class="gridl" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${H - 18}"/><text x="${X(t)}" y="${H - 4}" text-anchor="middle">${t}%</text>`; });
  items.forEach((it, i) => {
    const y = i * rowH + 4;
    s += `<g class="row" data-tip="${esc(tipHTML(it.label, it.tip))}"><rect class="hl" x="0" y="${y - 2}" width="${W}" height="${rowH - 4}" rx="6"/>
      <text class="lab" x="${L - 12}" y="${y + 18}" text-anchor="end">${esc(it.label)}</text>
      <rect x="${L}" y="${y + 8}" width="${Math.max(2, X(it.value) - L)}" height="14" rx="4" fill="${it.color}"/>
      <text class="val" x="${X(it.value) + 8}" y="${y + 19}">${esc(fmt(it.value))}</text></g>`;
  });
  el.innerHTML = s + "</svg>";
}

// 점 + 신뢰구간 (순찰 대비 차이)
function ciPlot(el, rows, unit = "초") {
  const W = Math.max(420, el.clientWidth || 700), rowH = 44, L = 120, R = 24, H = rows.length * rowH + 26;
  const ext = Math.max(10, ...rows.flatMap(r => r.ci ? r.ci.map(Math.abs) : [Math.abs(r.mean)])) * 1.1;
  const X = v => L + (W - L - R) * (v + ext) / (2 * ext);
  const ticks = niceTicks(-ext, ext, 4).filter(t => Math.abs(t) <= ext);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">`;
  ticks.forEach(t => { s += `<line class="${t === 0 ? "base" : "gridl"}" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${H - 20}"/><text x="${X(t)}" y="${H - 5}" text-anchor="middle">${t > 0 ? "+" : ""}${t}${t === 0 ? "" : unit}</text>`; });
  rows.forEach((r, i) => {
    const y = i * rowH + 18;
    const tipr = r.ci ? [["", "평균 차이", `${r.mean > 0 ? "+" : ""}${r.mean.toFixed(1)}${unit}`], ["", "95% 신뢰구간", `${r.ci[0].toFixed(1)} ~ ${r.ci[1].toFixed(1)}`], ["", "더 나았던 날", `${r.better}/${r.days}일`], ["", "부호 검정 p", r.p.toFixed(3)]] : [["", "", "기준"]];
    s += `<g class="row" data-tip="${esc(tipHTML(r.label, tipr))}"><rect class="hl" x="0" y="${y - 15}" width="${W}" height="${rowH - 6}" rx="6"/>
      <text class="lab" x="${L - 12}" y="${y + 4}" text-anchor="end">${esc(r.label)}</text>`;
    if (r.ci) s += `<line x1="${X(r.ci[0])}" x2="${X(r.ci[1])}" y1="${y}" y2="${y}" stroke="${r.color}" stroke-width="2" stroke-linecap="round"/>
      <line x1="${X(r.ci[0])}" x2="${X(r.ci[0])}" y1="${y - 5}" y2="${y + 5}" stroke="${r.color}" stroke-width="2"/><line x1="${X(r.ci[1])}" x2="${X(r.ci[1])}" y1="${y - 5}" y2="${y + 5}" stroke="${r.color}" stroke-width="2"/>`;
    s += `<circle cx="${X(r.mean)}" cy="${y}" r="5" fill="${r.color}" stroke="var(--surface)" stroke-width="2"/></g>`;
  });
  el.innerHTML = s + "</svg>";
}

// 날짜별 묶음 막대
function groupedBars(el, dates, series, metric) {
  const f = M[metric], W = Math.max(480, el.clientWidth || 1100), H = 250, L = 44, B = 40, T = 12;
  const all = series.flatMap(s => s.values).filter(v => v != null);
  if (!all.length) { el.innerHTML = '<div class="empty">데이터 없음</div>'; return; }
  let lo = metric === "service_rate" ? Math.min(...all) : 0, hi = Math.max(...all);
  if (metric === "service_rate") { lo = Math.max(0, Math.floor((lo - 3) / 5) * 5); hi = 100; } else hi *= 1.08;
  const ticks = niceTicks(lo, hi, 4).filter(t => t >= lo && t <= hi);
  const Y = v => T + (H - T - B) * (1 - (v - lo) / ((hi - lo) || 1));
  const gw = (W - L - 8) / dates.length, m = series.length, bw = Math.min(22, (gw - 18) / m);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">`;
  ticks.forEach(t => { s += `<line class="${t === lo ? "base" : "gridl"}" x1="${L}" x2="${W}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L - 8}" y="${Y(t) + 4}" text-anchor="end">${f.axis(t)}</text>`; });
  dates.forEach((d, i) => {
    const x0 = L + i * gw + (gw - bw * m) / 2, x = md_(d);
    const rows = series.map(se => [se.color, se.name, se.values[i] == null ? "-" : f.fmt(metric === "service_rate" ? se.values[i] / 100 : se.values[i])]);
    s += `<g class="grp" data-tip="${esc(tipHTML(`${x.md} (${x.dow}) · ${f.name}`, rows))}"><rect x="${L + i * gw}" y="0" width="${gw}" height="${H - B + 30}" fill="transparent"/>`;
    series.forEach((se, j) => {
      const v = se.values[i]; if (v == null) return;
      const yy = Y(v), hh = Math.max(1, H - B - yy);
      s += `<path class="mk" d="M${x0 + j * bw + 1} ${H - B}V${yy + 4}q0 -4 4 -4h${bw - 10}q4 0 4 4V${H - B}z" fill="${se.color}"/>`;
    });
    s += `<text x="${L + i * gw + gw / 2}" y="${H - B + 17}" text-anchor="middle" style="fill:var(--ink-2)">${x.md}</text><text x="${L + i * gw + gw / 2}" y="${H - B + 31}" text-anchor="middle">${x.dow}</text></g>`;
  });
  el.innerHTML = s + "</svg>";
}
const legendHTML = strats => strats.map(s => `<span><i style="background:${COLOR[s]}"></i>${esc(sname(s))}</span>`).join("");

// 토네이도 (민감도)
function tornado(el, items) {
  const W = Math.max(560, el.clientWidth || 1100), rowH = 36, L = Math.min(300, W * .32), R = 80, H = items.length * rowH + 30;
  const ext = Math.max(5, ...items.map(i => Math.abs(i.value))) * 1.3;
  const X = v => L + (W - L - R) * (v + ext) / (2 * ext);
  const ticks = niceTicks(-ext, ext, 6).filter(t => Math.abs(t) <= ext);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">`;
  ticks.forEach(t => { s += `<line class="${t === 0 ? "base" : "gridl"}" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${H - 22}"/><text x="${X(t)}" y="${H - 6}" text-anchor="middle">${t > 0 ? "+" : ""}${t}%p</text>`; });
  items.forEach((it, i) => {
    const y = i * rowH + 4;
    const c = it.value < 0 ? "var(--s2)" : "var(--s1)";
    const x1 = Math.min(X(0), X(it.value)), w = Math.abs(X(it.value) - X(0));
    s += `<g class="row" data-tip="${esc(tipHTML(it.label, it.tip))}"><rect class="hl" x="0" y="${y}" width="${W}" height="${rowH - 4}" rx="6"/>
      <text class="lab" x="${L - 14}" y="${y + 20}" text-anchor="end">${esc(it.label)}</text>
      <rect x="${x1}" y="${y + 8}" width="${Math.max(2, w)}" height="16" rx="4" fill="${c}"/>
      <text class="val" x="${it.value < 0 ? x1 - 8 : x1 + w + 8}" y="${y + 21}" text-anchor="${it.value < 0 ? "end" : "start"}">${it.value > 0 ? "+" : ""}${it.value.toFixed(1)}%p</text></g>`;
  });
  el.innerHTML = s + "</svg>";
}

// 시간대별 호출 (한 계열 — 범례 없이 제목이 이름)
function hourBars(el, rows) {
  if (!rows.length) { el.innerHTML = '<div class="empty">수요 데이터 없음</div>'; return; }
  const W = Math.max(420, el.clientWidth || 700), H = 150, L = 40, B = 24, T = 8, hi = Math.max(...rows.map(r => r.n)) * 1.1;
  const ticks = niceTicks(0, hi, 3).filter(t => t <= hi), Y = v => T + (H - T - B) * (1 - v / hi);
  const bw = (W - L) / rows.length;
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">`;
  ticks.forEach(t => { s += `<line class="${t ? "gridl" : "base"}" x1="${L}" x2="${W}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L - 8}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`; });
  rows.forEach((r, i) => {
    const x = L + i * bw + 2, y = Y(r.n);
    s += `<g class="grp" data-tip="${esc(tipHTML(`${r.h}:00 ~ ${r.h + 1}:00`, [["var(--s1)", "호출", r.n.toLocaleString() + "건"]]))}"><rect x="${L + i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>
      <path class="mk" d="M${x} ${H - B}V${y + 4}q0 -4 4 -4h${bw - 12}q4 0 4 4V${H - B}z" fill="var(--s1)"/>
      <text x="${L + i * bw + bw / 2}" y="${H - 7}" text-anchor="middle">${r.h}</text></g>`;
  });
  el.innerHTML = s + "</svg>";
}

// 디지털 트윈 지도
async function drawMap() {
  let d;
  try { d = await api("/api/map?date=2026-10-14"); } catch (e) { $("#map").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const max = Math.max(...d.hexes.map(h => h.calls), 1), bins = [0.08, 0.2, 0.4, 0.7];
  const cls = n => { const r = n / max; return r < bins[0] ? 0 : r < bins[1] ? 1 : r < bins[2] ? 2 : r < bins[3] ? 3 : 4; };
  let s = `<svg viewBox="0 0 ${d.w} ${d.h}" preserveAspectRatio="xMidYMid meet">`;
  d.hexes.forEach(h => {
    const pct = (100 * h.calls / d.total_calls).toFixed(1);
    s += `<polygon class="hex" points="${h.b.map(p => p.join(",")).join(" ")}" fill="var(--seq-${cls(h.calls)})" opacity=".82"
      data-tip="${esc(tipHTML("H3 칸 (res 8)", [["", "하루 호출 (9~23시)", h.calls.toLocaleString() + "건"], ["", "구역 전체 대비", pct + "%"], ["", "칸 번호", h.id.slice(0, 9) + "…"]]))}"/>`;
  });
  d.roads.forEach(r => { s += `<polyline class="road ${r.a ? "a" : ""}" points="${r.p.map(p => p.join(",")).join(" ")}" stroke-width="${r.a ? 3 + r.n * 1.6 : 3}"/>`; });
  d.signals.forEach(g => { s += `<g class="sig" data-tip="${esc(tipHTML("실제 신호 계획 (경찰청)", [["", "교차로", g.name], ["", "신호 주기", "요일·시간대별 실제 값"]]))}"><circle cx="${g.x}" cy="${g.y}" r="16"/><text x="${g.x + 26}" y="${g.y + 11}">${esc(g.name)}</text></g>`; });
  $("#map").innerHTML = s + "</svg>";
  $("#mapSub").textContent = `${dayLabel(d.date)} 9~23시 호출 ${d.total_calls.toLocaleString()}건 — 시뮬레이션과 같은 수요 생성기·시드`;
  hourBars($("#ovHour"), d.hourly || []);
  if (d.hourly && d.hourly.length) { const pk = d.hourly.reduce((a, b) => b.n > a.n ? b : a); $("#ovHourSub").textContent = `${dayLabel(d.date)} · 가장 많은 때 ${pk.h}시 ${pk.n.toLocaleString()}건 · 서울 택시 시간대 통계 반영`; }
  $("#mapLegend").innerHTML = `<span>적음</span><span class="ramp"></span><span>많음</span>`;
  $("#mapFacts").innerHTML = [[d.n_edges, "도로 구간 (OSM)"], [d.n_junctions, "교차로"], [d.n_tls, "신호 교차로"], [d.hexes.length, "H3 칸 (res 8)"]]
    .map(([v, l]) => `<div><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
}

// ---------- 개요 ----------
let OV = null;
const overviewChips = () => OV ? `<span class="chip accent">${esc(OV.label)}</span><span class="chip"><b>${OV.stats.dates.length}</b>일</span><span class="chip">택시 <b>${S.meta.current.num_taxis}</b>대</span><span class="chip">카카오 몫 <b>${Math.round(S.meta.current.real_demand_scale * 100)}%</b></span>` : "";
async function loadOverview() {
  const main = S.exps.find(e => e.name === "twin_v3") || S.exps.find(e => e.complete);
  drawMap();
  if (!main) { $("#kpis").innerHTML = '<div class="empty">저장된 실험이 없습니다.</div>'; return; }
  OV = await api(`/api/experiments/${main.name}`);
  $("#topRight").innerHTML = overviewChips();
  const st = OV.stats.strategies, base = st.patrol ? "patrol" : Object.keys(st)[0];
  const runs = OV.runs.filter(r => r.strategy === base).sort((a, b) => a.date.localeCompare(b.date));
  const k = st[base];
  const worst = runs.reduce((a, b) => (b.service_rate < a.service_rate ? b : a));
  $("#kpis").innerHTML = [
    { l: `서비스율 · ${sname(base)}`, v: (k.service_rate.mean * 100).toFixed(1), u: "%", sp: runs.map(r => r.service_rate), f: `가장 낮은 날 ${dayLabel(worst.date)} ${(worst.service_rate * 100).toFixed(1)}%` },
    { l: "평균 대기", v: k.avg_wait_sec.mean.toFixed(0), u: "초", sp: runs.map(r => r.avg_wait_sec), f: `90% 손님은 ${k.p90_wait_sec.mean.toFixed(0)}초 안에 배차` },
    { l: "놓친 손님", v: k.n_timeout_removed.mean.toFixed(0), u: "명/일", sp: runs.map(r => r.n_timeout_removed), f: `포기 기준 ${S.meta.current.passenger_wait_timeout}초` },
    { l: "택시 대수", v: S.meta.current.num_taxis, u: "대", sp: null, f: "국토부 통행량 × TOPIS 속도 × 공차 비율", g: "D" },
  ].map(x => `<div class="kpi"><div class="lab">${esc(x.l)}${x.g ? gradeBadges(x.g) : ""}</div><div class="val">${esc(x.v)}<small>${esc(x.u)}</small></div>
      ${x.sp ? sparkline(x.sp, "var(--s1)") : "<span></span>"}<div class="foot">${esc(x.f)}</div></div>`).join("");
  $("#ovN").textContent = `${OV.stats.dates.length}일 평균 · 차이 ${(100 * (Math.max(...Object.values(st).map(x => x.service_rate.mean)) - Math.min(...Object.values(st).map(x => x.service_rate.mean)))).toFixed(1)}%p`;
  const strats = sortS(Object.keys(st)), vals = strats.map(s => st[s].service_rate.mean * 100);
  rankBars($("#ovRate"), strats.map(s => ({ label: sname(s), value: st[s].service_rate.mean * 100, color: COLOR[s],
    tip: [["", "서비스율", M.service_rate.fmt(st[s].service_rate.mean)], ["", "평균 대기", M.avg_wait_sec.fmt(st[s].avg_wait_sec.mean)], ["", "놓친 손님/일", M.n_timeout_removed.fmt(st[s].n_timeout_removed.mean)]] })),
    { lo: Math.floor(Math.min(...vals) - 2), hi: 100, fmt: v => v.toFixed(1) + "%" });
  ciPlot($("#ovCI"), strats.map(s => { const w = st[s].avg_wait_sec; return { label: sname(s), color: COLOR[s], mean: w.diff_mean ?? 0, ci: w.diff_ci95, better: w.days_better, days: w.days, p: w.sign_test_p }; }));
  $("#ovLegend").innerHTML = legendHTML(strats);
  groupedBars($("#ovDates"), OV.stats.dates, strats.map(s => ({ name: sname(s), color: COLOR[s], values: OV.stats.dates.map(d => { const r = OV.runs.find(x => x.strategy === s && x.date === d); return r ? r.service_rate * 100 : null; }) })), "service_rate");
  $("#gradeKey").innerHTML = Object.entries(GRADES).map(([g, l]) => `<span>${gradeBadges(g)} ${esc(l)}</span>`).join("");
  $("#ovParams").innerHTML = `<table><thead><tr><th>조건</th><th>현재 값</th><th>등급</th><th>근거</th></tr></thead><tbody>${S.params.map(p => `
    <tr><td style="white-space:nowrap;font-weight:500">${esc(p.label)}<div class="sub">${esc(p.group)}</div></td><td style="white-space:nowrap">${esc(valueLabel(p, p.default))}</td>
    <td>${gradeBadges(p.grade)}</td><td><div class="ev clamp" onclick="this.classList.toggle('clamp')">${md(p.evidence)}</div></td></tr>`).join("")}</tbody></table>`;
}
function valueLabel(p, v) {
  if (p.type === "choice") return (p.choices.find(c => c.value === v) || {}).label || v;
  if (p.type === "bool") return v ? "켬" : "끔";
  return `${v}${p.unit ? " " + p.unit : ""}`;
}

// ---------- 실험 비교 ----------
function renderExpList() {
  const q = ($("#expSearch").value || "").toLowerCase();
  let html = "", g = null;
  S.exps.filter(e => !q || (e.label + e.name).toLowerCase().includes(q)).forEach(e => {
    if (e.group !== g) { g = e.group; html += `<div class="exp-grp">${esc(g)}</div>`; }
    html += `<div class="exp ${S.selected === e.name ? "on" : ""}" data-name="${esc(e.name)}"><div class="t1">${esc(e.label)}</div>
      <div class="t2">${esc(e.name)} · ${e.dates.length}일×${e.strategies.length}${e.complete ? "" : ' · <span class="live">진행 중</span>'}</div></div>`;
  });
  $("#expList").innerHTML = html || '<div class="empty">결과 없음</div>';
  $$("#expList .exp").forEach(it => it.onclick = () => selectExp(it.dataset.name));
}
$("#expSearch").oninput = renderExpList;
async function selectExp(name) {
  S.selected = name; renderExpList();
  const d = S.detail = await api(`/api/experiments/${name}`), st = d.stats;
  $("#cmpTitle").textContent = d.label;
  $("#cmpNote").textContent = d.note || "";
  const ov = Object.entries(d.overrides || {});
  $("#cmpChips").innerHTML = `<span class="chip"><b>${st.dates.length}</b>일</span><span class="chip"><b>${Object.keys(st.strategies).length}</b>전략</span>` +
    `<span class="chip">기준 <b>${esc(sname(st.baseline))}</b></span>` + (ov.length ? ov.map(([k, v]) => `<span class="chip accent">${esc(k)} = ${esc(JSON.stringify(v))}</span>`).join("") : `<span class="chip">기본 조건</span>`);
  const ms = ["service_rate", "avg_wait_sec", "n_timeout_removed", "p90_wait_sec"];
  let t = `<table><thead><tr><th>전략</th>${ms.map(m => `<th class="num">${M[m].name}</th>`).join("")}<th class="num">순찰 대비 평균 대기</th><th class="num">우세한 날</th></tr></thead><tbody>`;
  sortS(Object.keys(st.strategies)).forEach(s => {
    const x = st.strategies[s], w = x.avg_wait_sec;
    const diff = w.diff_mean != null ? `<b>${w.diff_mean > 0 ? "+" : ""}${w.diff_mean.toFixed(1)}초</b> <span class="sub">[${w.diff_ci95[0].toFixed(1)}, ${w.diff_ci95[1].toFixed(1)}]</span>` : '<span class="sub">기준</span>';
    t += `<tr><td><span class="sw" style="background:${COLOR[s]}"></span>${esc(sname(s))}</td>${ms.map(m => `<td class="num">${x[m].mean != null ? M[m].fmt(x[m].mean) : "-"}</td>`).join("")}
      <td class="num">${diff}</td><td class="num">${w.days ? `${w.days_better}/${w.days} <span class="sub">p=${w.sign_test_p.toFixed(3)}</span>` : "-"}</td></tr>`;
  });
  $("#cmpTable").innerHTML = t + "</tbody></table>";
  renderCmpDates();
}
$$("#cmpMetric button").forEach(b => b.onclick = () => { $$("#cmpMetric button").forEach(x => x.classList.toggle("on", x === b)); S.metric = b.dataset.m; renderCmpDates(); });
function renderCmpDates() {
  const d = S.detail; if (!d) return;
  const m = S.metric, strats = sortS(Object.keys(d.stats.strategies));
  $("#cmpLegend").innerHTML = legendHTML(strats);
  $("#cmpDatesSub").textContent = `${M[m].name} · 막대에 마우스를 올리면 값`;
  groupedBars($("#cmpDates"), d.stats.dates, strats.map(s => ({ name: sname(s), color: COLOR[s],
    values: d.stats.dates.map(dt => { const r = d.runs.find(x => x.strategy === s && x.date === dt); return r ? r[m] * M[m].k : null; }) })), m);
}

// ---------- 민감도 ----------
async function loadSens() {
  const rows = await api("/api/sensitivity");
  tornado($("#sensChart"), rows.map(r => ({ label: r.label, value: r.diff_pp, tip: [["", "서비스율", `${(r.base_service_rate * 100).toFixed(1)}% → ${(r.service_rate * 100).toFixed(1)}%`],
    ["", "평균 대기 차이", `${r.diff_wait_sec > 0 ? "+" : ""}${r.diff_wait_sec.toFixed(0)}초`], ["", "기준 실험", r.base], ["", "비교 날짜", r.days + "일"]] })));
  $("#sensTable").innerHTML = `<table><thead><tr><th>바꾼 것</th><th>기준</th><th class="num">날짜</th><th class="num">기준 서비스율</th><th class="num">바꾼 뒤</th><th class="num">차이</th><th class="num">평균 대기 차이</th></tr></thead><tbody>${rows.map(r => `
    <tr><td>${esc(r.label)}<div class="sub">${esc(r.name)}</div></td><td class="sub">${esc(r.base)}</td><td class="num">${r.days}</td><td class="num">${(r.base_service_rate * 100).toFixed(1)}%</td>
    <td class="num">${(r.service_rate * 100).toFixed(1)}%</td><td class="num"><b>${r.diff_pp > 0 ? "+" : ""}${r.diff_pp.toFixed(1)}%p</b></td><td class="num">${r.diff_wait_sec > 0 ? "+" : ""}${r.diff_wait_sec.toFixed(0)}초</td></tr>`).join("")}</tbody></table>`;
}

// ---------- 새 시뮬레이션 ----------
function toggles(el, items, onChange) {
  el.innerHTML = items.map(it => `<span class="tg ${it.on ? "on" : ""}" data-v="${esc(it.v)}">${it.html}</span>`).join("");
  $$(".tg", el).forEach(t => t.onclick = () => { t.classList.toggle("on"); onChange && onChange(); });
}
const tgValues = el => $$(".tg.on", el).map(t => t.dataset.v);
function renderRunForm() {
  S.runParams = {};
  toggles($("#runStrategies"), sortS(Object.keys(S.strategies)).map(k => ({ v: k, on: k === "patrol", html: `<i style="background:${COLOR[k]}"></i>${esc(S.strategies[k])}` })), summary);
  toggles($("#runDates"), [...S.meta.eval_dates].sort().map(d => { const x = md_(d); return { v: d, on: ["2026-09-10", "2026-10-13", "2026-10-14"].includes(d), html: `${x.md}<small>${x.dow}</small>` }; }), summary);
  const n = Math.max(1, S.meta.cpu || 1);
  $("#runJobs").innerHTML = [...Array(Math.min(n, 8)).keys()].map(i => `<button type="button" data-j="${i + 1}" class="${i + 1 === Math.min(2, n) ? "on" : ""}">${i + 1}</button>`).join("");
  $$("#runJobs button").forEach(b => b.onclick = () => { $$("#runJobs button").forEach(x => x.classList.toggle("on", x === b)); summary(); });
  const groups = [...new Set(S.params.map(p => p.group))];
  $("#runParams").innerHTML = groups.map(g => `<div class="card pcard"><div class="card-h"><div><h2>${esc(g)}</h2></div></div>${S.params.filter(p => p.group === g).map(paramRow).join("")}</div>`).join("");
  $$("#runParams [data-k]").forEach(bindParam);
  summary();
}
function paramRow(p) {
  let ctl;
  if (p.type === "choice") ctl = `<div class="seg" data-k="${p.key}">${p.choices.map(c => `<button type="button" data-v="${esc(c.value)}" class="${c.value === p.default ? "on" : ""}">${esc(c.label)}</button>`).join("")}</div>`;
  else if (p.type === "bool") ctl = `<button type="button" class="switch ${p.default ? "on" : ""}" data-k="${p.key}" aria-label="${esc(p.label)}"></button>`;
  else ctl = `<div class="rng"><input type="range" data-k="${p.key}" min="${p.min}" max="${p.max}" step="${p.type === "int" ? 1 : 0.01}" value="${p.default}"><output id="o-${p.key}">${p.default}<small> ${esc(p.unit || "")}</small></output></div>`;
  return `<div class="prm" id="p-${p.key}"><div><div class="nm">${esc(p.label)} ${gradeBadges(p.grade)}</div><div class="ds">${esc(p.desc)}</div><div class="ev clamp" onclick="this.classList.toggle('clamp')">${md(p.evidence)}</div></div>
    <div class="ctl">${ctl}<div class="def">기본 ${esc(valueLabel(p, p.default))}<button type="button" data-reset="${p.key}" class="hidden">되돌리기</button></div></div></div>`;
}
function setParam(p, v) {
  if (v === p.default) delete S.runParams[p.key]; else S.runParams[p.key] = v;
  const changed = p.key in S.runParams;
  $("#p-" + p.key).classList.toggle("changed", changed);
  $(`[data-reset="${p.key}"]`).classList.toggle("hidden", !changed);
  summary();
}
function bindParam(el) {
  const p = S.params.find(x => x.key === el.dataset.k);
  if (el.classList.contains("seg")) $$("button", el).forEach(b => b.onclick = () => { $$("button", el).forEach(x => x.classList.toggle("on", x === b)); setParam(p, b.dataset.v); });
  else if (el.classList.contains("switch")) el.onclick = () => { el.classList.toggle("on"); setParam(p, el.classList.contains("on")); };
  else el.oninput = () => { const v = p.type === "int" ? parseInt(el.value) : parseFloat(el.value); $("#o-" + p.key).innerHTML = `${v}<small> ${esc(p.unit || "")}</small>`; setParam(p, v); };
  $(`[data-reset="${p.key}"]`).onclick = () => {
    if (p.type === "choice") $$("button", el).forEach(x => x.classList.toggle("on", x.dataset.v === p.default));
    else if (p.type === "bool") el.classList.toggle("on", !!p.default);
    else { el.value = p.default; $("#o-" + p.key).innerHTML = `${p.default}<small> ${esc(p.unit || "")}</small>`; }
    setParam(p, p.default);
  };
}
const runDates = () => [...new Set([...tgValues($("#runDates")), ...$("#runDateExtra").value.split(/[,\s]+/).filter(Boolean)])];
const runJobs = () => parseInt(($("#runJobs .on") || {}).dataset?.j || "1");
function summary() {
  const n = runDates().length * tgValues($("#runStrategies")).length, j = runJobs(), min = Math.ceil(n * S.meta.min_per_run / j);
  const diff = Object.entries(S.runParams).map(([k, v]) => { const p = S.params.find(x => x.key === k); return `<span class="chip accent">${esc(p.label)}: <b>${esc(valueLabel(p, v))}</b></span>`; });
  $("#runSummary").innerHTML = `<div class="big">${n}회 · 약 ${min < 60 ? min + "분" : (min / 60).toFixed(1) + "시간"}</div>
    <div>${runDates().length}일 × ${tgValues($("#runStrategies")).length}전략, 동시 ${j}개</div>
    <div class="diff">${diff.length ? diff.join("") : '<span class="chip">현재 기본 조건</span>'}</div>`;
}
$("#runDateExtra").oninput = summary;
$("#runReset").onclick = renderRunForm;
$("#runForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target, btn = $("button[type=submit]", f);
  btn.disabled = true; $("#runMsg").innerHTML = "";
  try {
    const j = await api("/api/run", { name: f.name.value, label: f.label.value, dates: runDates(), strategies: tgValues($("#runStrategies")), overrides: S.runParams, jobs: runJobs() });
    $("#runMsg").innerHTML = `<div class="msg ok"><b>${esc(j.name)}</b> 시작 — ${j.total}회, 약 ${j.eta_min}분. 끝난 실행부터 '실험 비교'에 나타납니다.</div>`;
    pollJobs();
  } catch (e) { $("#runMsg").innerHTML = `<div class="msg err">${esc(e.message)}</div>`; }
  btn.disabled = false;
};

// ---------- 작업 ----------
const ST = { running: "실행 중", done: "완료", failed: "실패", cancelled: "멈춤", interrupted: "끊김", dry_run: "시험" };
let logTimer = null, jobTimer = null;
async function loadJobs() {
  const jobs = await api("/api/jobs");
  const running = jobs.filter(j => j.status === "running").length;
  $("#jobBadge").textContent = running; $("#jobBadge").classList.toggle("hidden", !running);
  $("#jobList").innerHTML = jobs.length ? `<table><thead><tr><th>작업</th><th>종류</th><th>진행</th><th>상태</th><th>시작</th><th></th></tr></thead><tbody>${jobs.map(j => `
    <tr><td><div style="font-weight:500">${esc(j.label || j.name)}</div><div class="sub" style="font-family:var(--mono)">${esc(j.name)}</div></td>
    <td>${j.kind === "unity" ? "Unity 재생" : "실험"}</td>
    <td>${j.kind === "experiment" ? `<div class="prog"><div class="bar"><i style="width:${100 * j.done / j.total}%"></i></div><span class="sub">${j.done}/${j.total}</span></div>` : '<span class="sub">-</span>'}</td>
    <td><span class="status ${j.status}">${ST[j.status] || j.status}</span></td><td class="sub">${esc(j.started.replace("T", " ").slice(5, 16))}</td>
    <td class="num"><button class="btn sm" data-log="${j.id}">로그</button> ${j.status === "running" ? `<button class="btn sm" data-cancel="${j.id}">멈춤</button>` : ""}
      ${j.kind === "experiment" && j.done ? `<button class="btn sm" data-open="${esc(j.name)}">결과</button>` : ""}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">아직 작업이 없습니다. \'새 시뮬레이션\'에서 시작하세요.</div>';
  $$("[data-log]").forEach(b => b.onclick = () => showLog(b.dataset.log));
  if (jobs.length && !logTimer && !$("#logBox").textContent) showLog((jobs.find(j => j.status === "running") || jobs[0]).id);
  $$("[data-cancel]").forEach(b => b.onclick = async () => { await api(`/api/jobs/${b.dataset.cancel}/cancel`, {}); loadJobs(); });
  $$("[data-open]").forEach(b => b.onclick = async () => { await refreshExps(); show("compare"); selectExp(b.dataset.open); });
  return running;
}
async function showLog(id) {
  clearInterval(logTimer);
  const tick = async () => {
    const j = await api(`/api/jobs/${id}`);
    $("#logTitle").textContent = `${j.label || j.name} · ${ST[j.status] || j.status}`;
    $("#logBox").textContent = j.log_tail || "(아직 출력 없음)";
    $("#logBox").scrollTop = 1e9;
    if (j.status !== "running") clearInterval(logTimer);
  };
  await tick(); logTimer = setInterval(tick, 3000);
}
function pollJobs() {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => { const n = await loadJobs(); refreshExps(); if (!n) clearInterval(jobTimer); }, 5000);
}

// ---------- Unity ----------
async function loadUnity() {
  $("#unityDate").innerHTML = [...S.meta.eval_dates].sort().map(d => `<option value="${d}" ${d === "2026-10-14" ? "selected" : ""}>${dayLabel(d)} (${d})</option>`).join("");
  if (!$("#unityStrategies").children.length) toggles($("#unityStrategies"), sortS(Object.keys(S.strategies)).map(k => ({ v: k, on: true, html: `<i style="background:${COLOR[k]}"></i>${esc(S.strategies[k])}` })));
  $("#unityFrom").innerHTML = `<option value="">현재 기본 조건 (최종 v3)</option>` + S.exps.filter(e => e.group === "대시보드에서 실행" || e.group.startsWith("민감도"))
    .map(e => `<option value="${esc(e.name)}">${esc(e.label)}</option>`).join("");
  const list = await api("/api/unity");
  $("#unityList").innerHTML = list.length ? list.map(u => `<div class="file"><div><div style="font-weight:500">${esc(u.name)}</div><div class="sub" style="font-family:var(--mono)">${esc(u.path)}</div></div>
    <div class="chips-row" style="margin:0;justify-content:flex-end">${u.files.map(f => `<span class="chip">${esc(f)}</span>`).join("")}</div></div>`).join("") : '<div class="empty">아직 없습니다.</div>';
  unityOverrides();
}
async function unityOverrides() {
  const n = $("#unityFrom").value;
  const ov = n ? ((await api(`/api/experiments/${n}`)).overrides || {}) : {};
  S.unityFrom = Object.fromEntries(Object.entries(ov).filter(([k]) => S.params.some(p => p.key === k)));
  $("#unityOverrides").innerHTML = Object.keys(S.unityFrom).length ? "적용 조건 " + Object.entries(S.unityFrom).map(([k, v]) => `<span class="chip accent">${esc(k)} = ${esc(JSON.stringify(v))}</span>`).join(" ") : "현재 기본 조건으로 만듭니다.";
}
$("#unityFrom").onchange = unityOverrides;
$("#unityForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target; $("#unityMsg").innerHTML = "";
  try {
    const j = await api("/api/unity", { date: f.date.value, name: f.name.value, unity_dir: f.unity_dir.value, strategies: tgValues($("#unityStrategies")), overrides: S.unityFrom });
    $("#unityMsg").innerHTML = `<div class="msg ok">시작 → <code>${esc(j.output)}</code></div>`;
    pollJobs();
  } catch (e) { $("#unityMsg").innerHTML = `<div class="msg err">${esc(e.message)}</div>`; }
};

// 창 크기가 바뀌면 그 화면의 차트만 다시 그림 (글자 크기 1:1 유지)
let rsz; addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => {
  const t = ($("#nav a.on") || {}).dataset?.tab;
  if (t === "overview" && OV) loadOverview(); if (t === "compare") renderCmpDates(); if (t === "sens") loadSens();
}, 200); });

// ---------- 시작 ----------
async function refreshExps() { S.exps = await api("/api/experiments"); renderExpList(); }
(async function init() {
  try {
    [S.meta, S.params] = await Promise.all([api("/api/meta"), api("/api/params")]);
    S.strategies = S.meta.strategies;
    await refreshExps();
    renderRunForm();
    await loadOverview();
    const start = (location.hash || "#overview").slice(1);
    show(TITLES[start] ? start : "overview");
    if (await loadJobs()) pollJobs();
  } catch (e) {
    $(".main").insertAdjacentHTML("afterbegin", `<div class="msg err">불러오지 못했습니다: ${esc(e.message)}</div>`);
  }
})();
