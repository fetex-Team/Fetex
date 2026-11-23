"use strict";
// Gangnam Twin — 외부 라이브러리 없이 동작 (차트·지도는 직접 그린 SVG).

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
const ORDER = ["patrol", "forecast", "prepositioned", "rl_prepositioned"];   // 색은 전략을 따른다 (순위가 바뀌어도 그대로)
const COLOR = { patrol: "var(--s1)", forecast: "var(--s2)", prepositioned: "var(--s3)", rl_prepositioned: "var(--s4)" };
const M = {
  service_rate: { name: "서비스율", fmt: v => (v * 100).toFixed(1) + "%", k: 100, unit: "%", better: +1 },
  avg_wait_sec: { name: "평균 대기", fmt: v => v.toFixed(0) + "초", k: 1, unit: "초", better: -1 },
  n_timeout_removed: { name: "놓친 손님", fmt: v => v.toFixed(0) + "명", k: 1, unit: "명", better: -1 },
  p90_wait_sec: { name: "90% 대기", fmt: v => v.toFixed(0) + "초", k: 1, unit: "초", better: -1 },
};
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const dparts = d => { const t = new Date(d + "T00:00:00"); return { md: `${t.getMonth() + 1}/${t.getDate()}`, dow: DOW[t.getDay()], we: t.getDay() % 6 === 0 }; };
const dayLabel = d => { const x = dparts(d); return `${x.md} ${x.dow}`; };
const sname = s => S.strategies[s] || s;
const sortS = arr => [...arr].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
const GRADES = { A: "실측", B: "공식 통계·조사", C: "규정·요금", D: "실데이터로 계산", E: "검증 구간으로 선택", F: "설계 선택", G: "가정" };
const grades = g => (g || "").split("+").map(x => x.trim()).filter(Boolean).map(x => `<span class="grade ${esc(x)}" data-tip="${esc(tipHTML("근거 등급 " + x, [["", GRADES[x] || "", ""]]))}">${esc(x)}</span>`).join("");
const sum = a => a.reduce((x, y) => x + y, 0);

// ---------- 툴팁 ----------
const tip = $("#tip");
document.addEventListener("mousemove", e => {
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.classList.remove("on"); return; }
  if (tip._src !== el) { tip.innerHTML = el.dataset.tip; tip._src = el; }
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY - h - 12;
  if (x + w > innerWidth - 8) x = e.clientX - w - 14;
  if (y < 8) y = e.clientY + 16;
  tip.style.left = x + "px"; tip.style.top = y + "px";
  tip.classList.add("on");
});
function tipHTML(title, rows) {
  return `<div class="t">${esc(title)}</div>` + rows.map(([c, l, v]) => `<div class="r"><span>${c ? `<i style="background:${c}"></i>` : ""}${esc(l)}</span><b>${esc(v)}</b></div>`).join("");
}

// ---------- 라우팅 ----------
$$("#nav a").forEach(a => a.onclick = () => show(a.dataset.tab));
function show(t) {
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.tab === t));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "tab-" + t));
  scrollTo(0, 0);
  if (t === "overview" && OV) renderOverview();
  if (t === "jobs") loadJobs();
  if (t === "unity") loadUnity();
  if (t === "sens") loadSens();
  if (t === "compare") { if (!S.selected && S.exps.length) selectExp((S.exps.find(e => e.name === "twin_v3") || S.exps[0]).name); else renderCompare(); }
  history.replaceState(null, "", "#" + t);
}

// ---------- 차트 공통 ----------
function ticks(lo, hi, n = 4) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return out;
}
const W = el => Math.max(320, el.clientWidth || 800);

function sparkline(vals) {
  const w = 88, h = 28, lo = Math.min(...vals), hi = Math.max(...vals);
  const p = vals.map((v, i) => [i * w / (vals.length - 1 || 1), h - 3 - (h - 6) * ((v - lo) / ((hi - lo) || 1))]);
  return `<svg viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${p.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("")}" fill="none" stroke="#8f8f8f" stroke-width="1.5" stroke-linejoin="round"/><circle cx="${p.at(-1)[0]}" cy="${p.at(-1)[1]}" r="2.5" fill="#171717"/></svg>`;
}

// 날짜별 점 그래프 (행 = 날짜, 점 = 전략, 회색 선 = 그날 전략 간 폭). 0 기준이 필요 없는 위치 인코딩.
function dotRows(el, dates, series, metric) {
  const f = M[metric], w = W(el), rowH = 32, L = 76, R = 96, T = 4, B = 26, H = dates.length * rowH + T + B;
  const all = series.flatMap(s => s.values).filter(v => v != null);
  if (!all.length) { el.innerHTML = '<div class="empty">데이터 없음</div>'; return; }
  let lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * .08 || 1;
  lo -= pad; hi += pad;
  if (metric === "service_rate") hi = Math.min(100, hi); else lo = Math.max(0, lo);
  const tk = ticks(lo, hi, 5).filter(t => t >= lo && t <= hi);
  const X = v => L + (w - L - R) * (v - lo) / (hi - lo);
  const unit = metric === "service_rate" ? "%" : "";
  let s = `<svg class="chart" viewBox="0 0 ${w} ${H}" height="${H}">`;
  tk.forEach(t => { s += `<line class="gridl" x1="${X(t)}" x2="${X(t)}" y1="${T}" y2="${H - B}"/><text x="${X(t)}" y="${H - 8}" text-anchor="middle">${t}${unit}</text>`; });
  dates.forEach((d, i) => {
    const y = T + i * rowH + rowH / 2, vs = series.map(se => se.values[i]).filter(v => v != null), x = dparts(d);
    const rows = series.map(se => [se.color, se.name, se.values[i] == null ? "-" : f.fmt(se.values[i] / f.k)]);
    s += `<g class="row" data-tip="${esc(tipHTML(`${x.md} (${x.dow}) · ${f.name}`, rows))}"><rect class="rowbg" x="0" y="${y - rowH / 2}" width="${w}" height="${rowH}"/>
      <text class="lab" x="0" y="${y + 4.5}">${x.md}</text><text x="40" y="${y + 4.5}" ${x.we ? 'style="fill:var(--ink-2)"' : ""}>${x.dow}</text>`;
    if (vs.length > 1) s += `<line class="rng" x1="${X(Math.min(...vs))}" x2="${X(Math.max(...vs))}" y1="${y}" y2="${y}"/>`;
    series.forEach(se => { const v = se.values[i]; if (v != null) s += `<circle class="dt" cx="${X(v)}" cy="${y}" r="5.5" fill="${se.color}"/>`; });
    const base = series[0].values[i];
    if (base != null) s += `<text class="val" x="${w}" y="${y + 4.5}" text-anchor="end">${f.fmt(base / f.k)}</text>`;
    s += `</g>`;
  });
  s += `<text x="${w}" y="${H - 8}" text-anchor="end">${esc(series[0].name)} 값</text>`;
  el.innerHTML = s + "</svg>";
}
const legendHTML = strats => strats.map(s => `<span><i style="background:${COLOR[s]}"></i>${esc(sname(s))}</span>`).join("");

// 시간대별 호출 (한 계열: 제목이 이름, 범례 없음. 막대는 0 기준)
function hourBars(el, rows) {
  if (!rows.length) { el.innerHTML = '<div class="empty">수요 데이터 없음</div>'; return; }
  const w = W(el), H = 180, L = 36, B = 24, T = 8, hi = Math.max(...rows.map(r => r.n)) * 1.12;
  const tk = ticks(0, hi, 3).filter(t => t <= hi), Y = v => T + (H - T - B) * (1 - v / hi), bw = (w - L) / rows.length;
  let s = `<svg class="chart" viewBox="0 0 ${w} ${H}" height="${H}">`;
  tk.forEach(t => { s += `<line class="${t ? "gridl" : "base"}" x1="${L}" x2="${w}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L - 8}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`; });
  const peak = Math.max(...rows.map(r => r.n));
  rows.forEach((r, i) => {
    const x = L + i * bw, y = Y(r.n), bx = x + 3, bwi = Math.max(2, bw - 6);
    s += `<g class="row" data-tip="${esc(tipHTML(`${r.h}:00–${r.h + 1}:00`, [["var(--q5)", "호출", r.n.toLocaleString() + "건"], ["", "하루 대비", (100 * r.n / sum(rows.map(q => q.n))).toFixed(1) + "%"]]))}">
      <rect class="rowbg" x="${x}" y="${T}" width="${bw}" height="${H - T - B}"/>
      <path d="M${bx} ${H - B}V${y + 3}q0 -3 3 -3h${bwi - 6}q3 0 3 3V${H - B}z" fill="${r.n === peak ? "var(--q6)" : "var(--q4)"}"/>
      <text x="${x + bw / 2}" y="${H - 7}" text-anchor="middle">${r.h}</text></g>`;
  });
  el.innerHTML = s + "</svg>";
}

// 신뢰구간 막대 (표 안). 유의성 색 (Statsig Pulse): 개선 초록 · 악화 빨강 · 0을 지나면 회색
function sigClass(w, better) {
  if (!w.diff_ci95) return "sig-none";
  const [a, b] = w.diff_ci95;
  if (a > 0 || b < 0) return (a > 0 ? 1 : -1) * better > 0 ? "sig-good" : "sig-bad";
  return "sig-none";
}
function ciSvg(w, ext, cls) {
  const sw = 140, X = v => sw / 2 + (sw / 2 - 6) * v / ext;
  const c = cls === "sig-good" ? "var(--good)" : cls === "sig-bad" ? "var(--bad)" : "#8f8f8f";
  return `<svg viewBox="0 0 ${sw} 18" aria-hidden="true"><line x1="0" x2="${sw}" y1="9" y2="9" stroke="var(--line)"/><line x1="${sw / 2}" x2="${sw / 2}" y1="1" y2="17" stroke="var(--line-3)"/>
    <line x1="${X(w.diff_ci95[0])}" x2="${X(w.diff_ci95[1])}" y1="9" y2="9" stroke="${c}" stroke-width="3" stroke-linecap="round"/><circle cx="${X(w.diff_mean)}" cy="9" r="4" fill="${c}" stroke="#fff" stroke-width="1.5"/></svg>`;
}

// 전략 비교 표 (개요·실험 비교 공용)
function strategyTable(el, d) {
  const st = d.stats.strategies, strats = sortS(Object.keys(st)), base = d.stats.baseline;
  const ext = Math.max(5, ...strats.flatMap(s => (st[s].avg_wait_sec.diff_ci95 || []).map(Math.abs))) * 1.1;
  const best = m => { const v = strats.map(s => st[s][m].mean); return M[m].better > 0 ? Math.max(...v) : Math.min(...v); };
  const cell = (s, m) => { const v = st[s][m].mean; return v == null ? "-" : `<span ${v === best(m) ? 'style="font-weight:600"' : ""}>${M[m].fmt(v)}</span>`; };
  let h = `<table class="t"><thead><tr><th>전략</th><th class="num">서비스율</th><th class="num">평균 대기</th><th class="num">90% 대기</th><th class="num">놓친 손님/일</th>
    <th class="num">${esc(sname(base))} 대비 평균 대기 · 95% 신뢰구간</th><th class="num">나았던 날</th></tr></thead><tbody>`;
  strats.forEach(s => {
    const w = st[s].avg_wait_sec, cls = sigClass(w, -1);
    const ci = w.diff_mean != null
      ? `<span class="ci">${ciSvg(w, ext, cls)}<span class="v ${cls}"><b>${w.diff_mean > 0 ? "+" : ""}${w.diff_mean.toFixed(1)}초</b> <span class="muted">[${w.diff_ci95[0].toFixed(1)}, ${w.diff_ci95[1].toFixed(1)}]</span></span></span>`
      : `<span class="muted">기준</span>`;
    const days = w.days ? `${w.days_better}/${w.days}<span class="muted" style="margin-left:6px">p ${w.sign_test_p < .001 ? "<0.001" : w.sign_test_p.toFixed(3)}</span>` : "";
    h += `<tr><td class="name"><span class="sw" style="background:${COLOR[s]}"></span>${esc(sname(s))}</td><td class="num">${cell(s, "service_rate")}</td><td class="num">${cell(s, "avg_wait_sec")}</td>
      <td class="num">${cell(s, "p90_wait_sec")}</td><td class="num">${cell(s, "n_timeout_removed")}</td><td class="num">${ci}</td><td class="num">${days}</td></tr>`;
  });
  el.innerHTML = h + `</tbody></table><p class="muted" style="font-size:12.5px;margin-top:10px">굵은 값은 지표별 가장 좋은 전략. 나았던 날은 ${esc(sname(base))}보다 평균 대기가 짧았던 날 수, p는 부호 검정.</p>`;
}

// ---------- 개요 ----------
let OV = null, MAP = null;
async function loadOverview() {
  const main = S.exps.find(e => e.name === "twin_v3") || S.exps.find(e => e.complete);
  const [ov, map] = await Promise.all([main ? api(`/api/experiments/${main.name}`) : null, api("/api/map?date=2026-10-14").catch(e => ({ error: e.message }))]);
  OV = ov; MAP = map;
  renderOverview();
}
function renderOverview() {
  drawMap();
  if (!OV) { $("#kpis").innerHTML = '<div class="empty">저장된 실험이 없습니다.</div>'; return; }
  const st = OV.stats.strategies, base = OV.stats.baseline, cur = S.meta.current;
  const ds = [...OV.stats.dates].sort();
  $("#ovScope").innerHTML = `${esc(OV.label)} · ${dayLabel(ds[0])} ~ ${dayLabel(ds.at(-1))} 중 ${ds.length}일 · 9~23시 · 기준 전략 ${esc(sname(base))}`;
  const runs = OV.runs.filter(r => r.strategy === base).sort((a, b) => a.date.localeCompare(b.date)), k = st[base];
  const worst = runs.reduce((a, b) => (b.service_rate < a.service_rate ? b : a));
  const calls = sum(runs.map(r => r.n_total_passengers)) / runs.length;
  $("#kpis").innerHTML = [
    ["서비스율", (k.service_rate.mean * 100).toFixed(1), "%", runs.map(r => r.service_rate), `가장 낮은 날 ${dayLabel(worst.date)} ${(worst.service_rate * 100).toFixed(1)}%`],
    ["평균 대기", k.avg_wait_sec.mean.toFixed(0), "초", runs.map(r => r.avg_wait_sec), `손님 90%는 ${k.p90_wait_sec.mean.toFixed(0)}초 안에 배차`],
    ["놓친 손님", k.n_timeout_removed.mean.toFixed(0), "명/일", runs.map(r => r.n_timeout_removed), `${cur.passenger_wait_timeout}초 안에 못 잡으면 포기로 계산`],
    ["하루 호출", Math.round(calls).toLocaleString(), "건", runs.map(r => r.n_total_passengers), `카카오 몫 ${Math.round(cur.real_demand_scale * 100)}% · 생활인구 비례 위치`],
  ].map(([l, v, u, sp, c]) => `<div class="metric"><div class="lab">${l}</div><div class="val">${v}<small>${u}</small></div>${sparkline(sp)}<div class="cmp">${esc(c)}</div></div>`).join("");
  strategyTable($("#ovTable"), OV);
  const strats = sortS(Object.keys(st));
  $("#ovLegend").innerHTML = legendHTML(strats);
  dotRows($("#ovDates"), ds, strats.map(s => ({ name: sname(s), color: COLOR[s], values: ds.map(d => { const r = OV.runs.find(x => x.strategy === s && x.date === d); return r ? r.service_rate * 100 : null; }) })), "service_rate");
  $("#ovParams").innerHTML = `<table class="t"><thead><tr><th>조건</th><th>현재 값</th><th>등급</th><th>근거</th></tr></thead><tbody>${S.params.map(p => `
    <tr><td class="name">${esc(p.label)}<div class="sub">${esc(p.group)}</div></td><td style="white-space:nowrap">${esc(valueLabel(p, p.default))}</td>
    <td style="white-space:nowrap">${grades(p.grade)}</td><td><div class="ev clamp" onclick="this.classList.toggle('clamp')">${md(p.evidence)}</div></td></tr>`).join("")}</tbody></table>`;
  $("#gradeKey").innerHTML = "등급 " + Object.entries(GRADES).map(([g, l]) => `<span><span class="grade ${g}">${g}</span>${esc(l)}</span>`).join("");
}
function valueLabel(p, v) {
  if (p.type === "choice") return (p.choices.find(c => c.value === v) || {}).label || v;
  if (p.type === "bool") return v ? "켬" : "끔";
  return `${v}${p.unit ? " " + p.unit : ""}`;
}

// 디지털 트윈 지도: 조용한 바탕(도로 회색) 위에 칸별 호출(단색 순차), 칸 번호 ↔ 오른쪽 표 연결
function drawMap() {
  const d = MAP;
  if (!d || d.error) { $("#map").innerHTML = `<div class="empty" style="padding:24px">${esc(d ? d.error : "지도 없음")}</div>`; return; }
  const max = Math.max(...d.hexes.map(h => h.calls), 1);
  const q = n => `var(--q${Math.min(6, Math.round(1 + 5 * Math.sqrt(n / max)))})`;    // 넓이 대신 제곱근으로 단계 → 적은 칸도 구분
  let s = `<svg viewBox="0 0 ${d.w} ${d.h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="강남역 도로망과 칸별 호출">`;
  d.hexes.forEach((h, i) => {
    s += `<polygon class="hex" data-hex="${i}" points="${h.b.map(p => p.join(",")).join(" ")}" fill="${q(h.calls)}"
      data-tip="${esc(tipHTML(`칸 ${i + 1} · H3 res 8`, [["", "하루 호출", h.calls.toLocaleString() + "건"], ["", "구역 대비", (100 * h.calls / d.total_calls).toFixed(1) + "%"], ["", "H3", h.id]]))}"/>`;
  });
  d.roads.forEach(r => { s += `<polyline class="road ${r.a ? "a" : ""}" points="${r.p.map(p => p.join(",")).join(" ")}" stroke-width="${r.a ? 2 + r.n * 1.5 : 2.5}"/>`; });
  d.hexes.forEach((h, i) => {
    const cx = sum(h.b.map(p => p[0])) / h.b.length, cy = sum(h.b.map(p => p[1])) / h.b.length;
    if (cx > 40 && cx < d.w - 40 && cy > 40 && cy < d.h - 40) s += `<text class="rank" x="${cx}" y="${cy}">${i + 1}</text>`;
  });
  d.signals.forEach(g => { s += `<g class="sig" data-tip="${esc(tipHTML("실제 신호 계획 (경찰청)", [["", "교차로", g.name], ["", "주기", "요일·시간대별 실제 값"]]))}"><circle cx="${g.x}" cy="${g.y}" r="15"/><text x="${g.x + 26}" y="${g.y + 12}">${esc(g.name)}</text></g>`; });
  $("#map").style.aspectRatio = `${d.w} / ${d.h}`;
  $("#map").innerHTML = s + "</svg>";
  $("#mapCap").innerHTML = `<div class="scale"><span>호출 적음</span><i></i><span>많음</span></div>
    <div class="scale"><i class="ln"></i><span>간선도로 (굵기 = 차로 수)</span><i class="pt" style="margin-left:10px"></i><span>실제 신호 계획</span></div>`;
  $("#mapSub").textContent = `${dayLabel(d.date)} 9~23시 호출 ${d.total_calls.toLocaleString()}건을 H3 칸(한 변 531m)별로 집계 — 시뮬레이션과 같은 수요 생성기·시드`;
  $("#mapFacts").innerHTML = [[d.n_edges, "도로 구간 (OSM)"], [d.n_junctions, "교차로"], [d.n_tls, "신호 교차로"], ["2", "실제 신호 계획"]]
    .map(([v, l]) => `<div><dt>${esc(l)}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#hexRank").innerHTML = `<tbody>${d.hexes.map((h, i) => `<tr data-hex="${i}"><td class="rk">${i + 1}</td><td><span class="sw" style="background:${q(h.calls)}"></span>${(100 * h.calls / d.total_calls).toFixed(1)}%</td>
    <td class="num">${h.calls.toLocaleString()}</td><td class="bar"><i style="width:${100 * h.calls / max}%"></i></td></tr>`).join("")}</tbody>`;
  const hl = (i, on) => { $$(`[data-hex="${i}"]`).forEach(e => e.classList.toggle("hl", on)); };
  $$("#hexRank tr, #map .hex").forEach(e => { e.onmouseenter = () => hl(e.dataset.hex, true); e.onmouseleave = () => hl(e.dataset.hex, false); });
  hourBars($("#ovHour"), d.hourly || []);
  if (d.hourly && d.hourly.length) { const pk = d.hourly.reduce((a, b) => b.n > a.n ? b : a); $("#ovHourSub").textContent = `${dayLabel(d.date)} · 가장 많은 때 ${pk.h}시 ${pk.n.toLocaleString()}건 · 서울 택시 시간대 통계(2025) 반영`; }
}

// ---------- 실험 비교 ----------
function renderExpList() {
  const qv = ($("#expSearch").value || "").toLowerCase();
  let html = "", g = null;
  S.exps.filter(e => !qv || (e.label + e.name).toLowerCase().includes(qv)).forEach(e => {
    if (e.group !== g) { g = e.group; html += `<div class="exp-grp">${esc(g)}</div>`; }
    html += `<div class="exp ${S.selected === e.name ? "on" : ""}" data-name="${esc(e.name)}"><div class="t1">${esc(e.label)}</div>
      <div class="t2">${esc(e.name)} · ${e.dates.length}일×${e.strategies.length}${e.complete ? "" : " · 진행 중"}</div></div>`;
  });
  $("#expList").innerHTML = html || '<div class="empty">결과 없음</div>';
  $$("#expList .exp").forEach(it => it.onclick = () => selectExp(it.dataset.name));
}
$("#expSearch").oninput = renderExpList;
async function selectExp(name) {
  S.selected = name; renderExpList();
  S.detail = await api(`/api/experiments/${name}`);
  renderCompare();
}
function renderCompare() {
  const d = S.detail; if (!d) return;
  const st = d.stats, ov = Object.entries(d.overrides || {});
  $("#cmpTitle").textContent = d.label;
  $("#cmpNote").textContent = d.note || "";
  $("#cmpMeta").innerHTML = `<span><b>${st.dates.length}</b>일</span><span><b>${Object.keys(st.strategies).length}</b>전략</span><span>기준 <b>${esc(sname(st.baseline))}</b></span>` +
    `<span>바꾼 조건 ${ov.length ? ov.map(([k, v]) => { const p = S.params.find(x => x.key === k); return `<b class="chg">${esc(p ? p.label : k)} ${esc(p ? valueLabel(p, v) : JSON.stringify(v))}</b>`; }).join(", ") : "<b>없음 (기본 조건)</b>"}</span>`;
  strategyTable($("#cmpTable"), d);
  renderCmpDates();
}
$$("#cmpMetric button").forEach(b => b.onclick = () => { $$("#cmpMetric button").forEach(x => x.classList.toggle("on", x === b)); S.metric = b.dataset.m; renderCmpDates(); });
function renderCmpDates() {
  const d = S.detail; if (!d) return;
  const m = S.metric, strats = sortS(Object.keys(d.stats.strategies)), ds = [...d.stats.dates].sort();
  $("#cmpLegend").innerHTML = legendHTML(strats);
  $("#cmpDatesSub").textContent = `${M[m].name} · ${M[m].better > 0 ? "오른쪽일수록 좋음" : "왼쪽일수록 좋음"} · 행에 마우스를 올리면 값`;
  dotRows($("#cmpDates"), ds, strats.map(s => ({ name: sname(s), color: COLOR[s],
    values: ds.map(dt => { const r = d.runs.find(x => x.strategy === s && x.date === dt); return r ? r[m] * M[m].k : null; }) })), m);
}

// ---------- 민감도 (토네이도: 하락 빨강·상승 초록, 0 기준) ----------
let SENS = null;
async function loadSens() { SENS = SENS || await api("/api/sensitivity"); renderSens(); }
function renderSens() {
  const rows = SENS, el = $("#sensChart"), w = W(el), rowH = 36, L = Math.min(320, w * .34), R = 72, B = 28, H = rows.length * rowH + B;
  const ext = Math.max(5, ...rows.map(r => Math.abs(r.diff_pp))) * 1.08, X = v => L + (w - L - R) * (v + ext) / (2 * ext);
  let s = `<svg class="chart" viewBox="0 0 ${w} ${H}" height="${H}">`;
  ticks(-ext, ext, 6).filter(t => Math.abs(t) <= ext).forEach(t => { s += `<line class="${t === 0 ? "base" : "gridl"}" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${H - B}"/><text x="${X(t)}" y="${H - 8}" text-anchor="middle">${t > 0 ? "+" : ""}${t}%p</text>`; });
  rows.forEach((r, i) => {
    const y = i * rowH, v = r.diff_pp, x1 = Math.min(X(0), X(v)), bw = Math.max(2, Math.abs(X(v) - X(0)));
    const c = v < 0 ? "var(--bad)" : "var(--good)";
    s += `<g class="row" data-tip="${esc(tipHTML(r.label, [["", "서비스율", `${(r.base_service_rate * 100).toFixed(1)}% → ${(r.service_rate * 100).toFixed(1)}%`], ["", "평균 대기", `${r.diff_wait_sec > 0 ? "+" : ""}${r.diff_wait_sec.toFixed(0)}초`], ["", "기준 실험", r.base], ["", "비교 날짜", r.days + "일"]]))}">
      <rect class="rowbg" x="0" y="${y + 2}" width="${w}" height="${rowH - 4}"/>
      <text class="lab" x="0" y="${y + rowH / 2 + 4.5}">${esc(r.label)}</text>
      <rect x="${x1}" y="${y + 11}" width="${bw}" height="${rowH - 22}" rx="2" fill="${c}"/>
      <text class="val" x="${v < 0 ? x1 - 8 : x1 + bw + 8}" y="${y + rowH / 2 + 4.5}" text-anchor="${v < 0 ? "end" : "start"}">${v > 0 ? "+" : ""}${v.toFixed(1)}%p</text></g>`;
  });
  el.innerHTML = s + "</svg>";
  $("#sensTable").innerHTML = `<table class="t"><thead><tr><th>바꾼 가정</th><th>기준 실험</th><th class="num">날짜</th><th class="num">기준 서비스율</th><th class="num">바꾼 뒤</th><th class="num">차이</th><th class="num">평균 대기 차이</th></tr></thead><tbody>${rows.map(r => `
    <tr><td class="name">${esc(r.label)}<div class="sub mono">${esc(r.name)}</div></td><td class="mono ink2">${esc(r.base)}</td><td class="num">${r.days}</td><td class="num">${(r.base_service_rate * 100).toFixed(1)}%</td>
    <td class="num">${(r.service_rate * 100).toFixed(1)}%</td><td class="num ${r.diff_pp < 0 ? "sig-bad" : "sig-good"}"><b>${r.diff_pp > 0 ? "+" : ""}${r.diff_pp.toFixed(1)}%p</b></td><td class="num">${r.diff_wait_sec > 0 ? "+" : ""}${r.diff_wait_sec.toFixed(0)}초</td></tr>`).join("")}</tbody></table>`;
}

// ---------- 새 시뮬레이션 (설정 화면 + 아래 고정 실행 막대) ----------
function checks(el, items, onChange) {
  el.innerHTML = items.map(it => `<span class="ck ${it.on ? "on" : ""}" data-v="${esc(it.v)}" role="checkbox" tabindex="0" aria-checked="${!!it.on}">${it.html}</span>`).join("");
  $$(".ck", el).forEach(t => { const tg = () => { t.classList.toggle("on"); t.setAttribute("aria-checked", t.classList.contains("on")); onChange && onChange(); };
    t.onclick = tg; t.onkeydown = e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); tg(); } }; });
}
const checked = el => $$(".ck.on", el).map(t => t.dataset.v);
const GROUP_NOTE = { "공급": "구역 안 택시 수와 구역 밖으로 나간 택시의 복귀", "수요": "손님 수·위치·시간대·하차 위치", "손님": "손님이 기다리다 포기하는 시간", "배차": "헝가리안 짝짓기에 쓰는 거리", "도로": "SUMO 도로망·신호·속도 설정" };
function renderRunForm() {
  S.runParams = {};
  $("#runForm").reset();
  checks($("#runStrategies"), sortS(Object.keys(S.strategies)).map(k => ({ v: k, on: k === "patrol", html: `<i style="background:${COLOR[k]}"></i>${esc(S.strategies[k])}` })), summary);
  checks($("#runDates"), [...S.meta.eval_dates].sort().map(d => { const x = dparts(d); return { v: d, on: ["2026-09-10", "2026-10-13", "2026-10-14"].includes(d), html: `${x.md} <small>${x.dow}</small>` }; }), summary);
  const n = Math.min(8, Math.max(1, S.meta.cpu || 1));
  $("#runJobs").innerHTML = [...Array(n).keys()].map(i => `<button type="button" data-j="${i + 1}" class="${i + 1 === Math.min(2, n) ? "on" : ""}">${i + 1}</button>`).join("");
  $$("#runJobs button").forEach(b => b.onclick = () => { $$("#runJobs button").forEach(x => x.classList.toggle("on", x === b)); summary(); });
  const groups = [...new Set(S.params.map(p => p.group))];
  $("#runParams").innerHTML = groups.map(g => `<section class="set"><div class="set-l"><h2>${esc(g)}</h2><p>${esc(GROUP_NOTE[g] || "")}</p></div>
    <div>${S.params.filter(p => p.group === g).map(paramRow).join("")}</div></section>`).join("");
  S.params.forEach(bindParam);
  summary();
}
function paramRow(p) {
  let ctl;
  if (p.type === "choice") ctl = `<div class="radios">${p.choices.map(c => `<label class="${c.value === p.default ? "on" : ""}"><input type="radio" name="p_${p.key}" value="${esc(c.value)}" ${c.value === p.default ? "checked" : ""}>${esc(c.label)}</label>`).join("")}</div>`;
  else if (p.type === "bool") ctl = `<div class="sw-row"><button type="button" class="switch ${p.default ? "on" : ""}" id="sw_${p.key}" role="switch" aria-checked="${!!p.default}" aria-label="${esc(p.label)}"></button><span id="swl_${p.key}">${p.default ? "켬" : "끔"}</span></div>`;
  else ctl = `<div class="rng"><input type="range" id="r_${p.key}" min="${p.min}" max="${p.max}" step="${p.type === "int" ? 1 : 0.01}" value="${p.default}" aria-label="${esc(p.label)}">
    <span class="nb"><input type="number" id="n_${p.key}" min="${p.min}" max="${p.max}" step="${p.type === "int" ? 1 : 0.01}" value="${p.default}">${p.unit ? `<span>${esc(p.unit)}</span>` : ""}</span></div>`;
  return `<div class="prm" id="p-${p.key}"><div><div class="nm">${esc(p.label)} ${grades(p.grade)}<span class="chgd hidden">변경됨</span></div><div class="ds">${esc(p.desc)}</div>
    <div class="ev clamp" onclick="this.classList.toggle('clamp')">${md(p.evidence)}</div></div>
    <div class="ctl">${ctl}<div class="def"><span>기본 ${esc(valueLabel(p, p.default))}${p.type === "int" || p.type === "float" ? ` · 범위 ${p.min}–${p.max}` : ""}</span><button type="button" data-reset="${p.key}" class="hidden">기본값으로</button></div></div></div>`;
}
function setParam(p, v) {
  if (v === p.default) delete S.runParams[p.key]; else S.runParams[p.key] = v;
  const c = p.key in S.runParams;
  $(`#p-${p.key} .chgd`).classList.toggle("hidden", !c);
  $(`[data-reset="${p.key}"]`).classList.toggle("hidden", !c);
  summary();
}
function bindParam(p) {
  const row = $("#p-" + p.key);
  const apply = v => {
    if (p.type === "choice") $$("label", row).forEach(l => { const i = $("input", l); i.checked = i.value === v; l.classList.toggle("on", i.checked); });
    else if (p.type === "bool") { $("#sw_" + p.key).classList.toggle("on", v); $("#sw_" + p.key).setAttribute("aria-checked", v); $("#swl_" + p.key).textContent = v ? "켬" : "끔"; }
    else { $("#r_" + p.key).value = v; $("#n_" + p.key).value = v; }
    setParam(p, v);
  };
  if (p.type === "choice") $$("input", row).forEach(i => i.onchange = () => apply(i.value));
  else if (p.type === "bool") $("#sw_" + p.key).onclick = () => apply(!$("#sw_" + p.key).classList.contains("on"));
  else {
    const parse = s => p.type === "int" ? parseInt(s, 10) : parseFloat(s);
    $("#r_" + p.key).oninput = e => apply(parse(e.target.value));
    $("#n_" + p.key).onchange = e => { let v = parse(e.target.value); if (isNaN(v)) v = p.default; apply(Math.min(p.max, Math.max(p.min, v))); };
  }
  $(`[data-reset="${p.key}"]`).onclick = () => apply(p.default);
}
const runDates = () => [...new Set([...checked($("#runDates")), ...$("#runDateExtra").value.split(/[,\s]+/).filter(Boolean)])];
const runJobs = () => parseInt(($("#runJobs .on") || { dataset: { j: "1" } }).dataset.j, 10);
function summary() {
  const nd = runDates().length, ns = checked($("#runStrategies")).length, n = nd * ns, j = runJobs(), min = Math.ceil(n * S.meta.min_per_run / j);
  const ch = Object.entries(S.runParams).map(([k, v]) => { const p = S.params.find(x => x.key === k); return `<span class="chg">${esc(p.label)} ${esc(valueLabel(p, v))}</span>`; });
  $("#runSummary").innerHTML = `<b>${n}회 · 약 ${min < 60 ? min + "분" : (min / 60).toFixed(1) + "시간"}</b>${nd}일 × ${ns}전략, 동시 ${j}개 · 바꾼 조건 ${ch.length ? ch.join(", ") : "없음"}`;
}
$("#runDateExtra").oninput = summary;
$("#runReset").onclick = renderRunForm;
$("#runForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target, btn = $("button[type=submit]", f);
  btn.disabled = true; $("#runMsg").innerHTML = "";
  try {
    const j = await api("/api/run", { name: f.name.value, label: f.label.value, dates: runDates(), strategies: checked($("#runStrategies")), overrides: S.runParams, jobs: runJobs() });
    $("#runMsg").innerHTML = `<div class="note ok" style="margin:0 0 12px"><b>${esc(j.name)}</b> 시작 — ${j.total}회, 약 ${j.eta_min}분. 끝난 실행부터 실험 비교에 나타납니다. <a href="#jobs" onclick="show('jobs');return false">작업 보기</a></div>`;
    pollJobs();
  } catch (e) { $("#runMsg").innerHTML = `<div class="note err" style="margin:0 0 12px">${esc(e.message)}</div>`; }
  btn.disabled = false;
};

// ---------- 작업 ----------
const ST = { running: "실행 중", done: "완료", failed: "실패", cancelled: "멈춤", interrupted: "끊김", dry_run: "시험" };
const SDOT = { running: "run", done: "on" };
let logTimer = null, logShown = null, jobTimer = null;
async function loadJobs() {
  const jobs = await api("/api/jobs");
  const running = jobs.filter(j => j.status === "running");
  $("#jobBadge").textContent = running.length; $("#jobBadge").classList.toggle("hidden", !running.length);
  $("#jobState").innerHTML = running.length ? `<span class="dot run"></span>${esc(running[0].label || running[0].name)} 실행 중 ${running[0].done ?? ""}${running[0].total ? "/" + running[0].total : ""}` : `<span class="dot on"></span>대기 중`;
  $("#jobList").innerHTML = jobs.length ? `<table class="t"><thead><tr><th>작업</th><th>종류</th><th>진행</th><th>상태</th><th>시작</th><th></th></tr></thead><tbody>${jobs.map(j => `
    <tr><td class="name">${esc(j.label || j.name)}<div class="sub mono">${esc(j.name)}</div></td>
    <td>${j.kind === "unity" ? "Unity 재생" : "실험"}</td>
    <td>${j.kind === "experiment" ? `<div class="prog"><div class="bar"><i style="width:${100 * j.done / (j.total || 1)}%"></i></div><span class="muted">${j.done}/${j.total}</span></div>` : '<span class="muted">-</span>'}</td>
    <td class="status"><span class="dot ${SDOT[j.status] || ""}" ${["failed", "cancelled", "interrupted"].includes(j.status) ? 'style="background:var(--bad)"' : ""}></span>${ST[j.status] || esc(j.status)}</td>
    <td class="muted" style="white-space:nowrap">${esc(j.started.replace("T", " ").slice(5, 16))}</td>
    <td class="num"><button class="btn sm" data-log="${j.id}">로그</button> ${j.status === "running" ? `<button class="btn sm" data-cancel="${j.id}">멈춤</button>` : ""}
      ${j.kind === "experiment" && j.done ? `<button class="btn sm" data-open="${esc(j.name)}">결과</button>` : ""}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">아직 작업이 없습니다. <a href="#run" onclick="show('run');return false">새 시뮬레이션</a>에서 시작하세요.</div>`;
  $$("[data-log]").forEach(b => b.onclick = () => showLog(b.dataset.log));
  $$("[data-cancel]").forEach(b => b.onclick = async () => { await api(`/api/jobs/${b.dataset.cancel}/cancel`, {}); loadJobs(); });
  $$("[data-open]").forEach(b => b.onclick = async () => { await refreshExps(); S.selected = null; show("compare"); selectExp(b.dataset.open); });
  if (jobs.length && !logShown) showLog((running[0] || jobs[0]).id);
  return running.length;
}
async function showLog(id) {
  clearInterval(logTimer); logShown = id;
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
  if (!$("#unityStrategies").children.length) checks($("#unityStrategies"), sortS(Object.keys(S.strategies)).map(k => ({ v: k, on: true, html: `<i style="background:${COLOR[k]}"></i>${esc(S.strategies[k])}` })));
  const keep = $("#unityFrom").value;
  $("#unityFrom").innerHTML = `<option value="">기본 조건 (최종 v3)</option>` + S.exps.filter(e => e.group === "대시보드에서 실행" || e.group.startsWith("민감도"))
    .map(e => `<option value="${esc(e.name)}">${esc(e.label)}</option>`).join("");
  $("#unityFrom").value = keep;
  const list = await api("/api/unity");
  $("#unityList").innerHTML = list.length ? `<table class="t"><thead><tr><th>이름</th><th>위치</th><th>파일</th></tr></thead><tbody>${list.map(u => `
    <tr><td class="name">${esc(u.name)}</td><td class="mono ink2">${esc(u.path)}</td><td class="mono ink2">${u.files.map(esc).join(" · ")}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">아직 없습니다.</div>';
  unityOverrides();
}
async function unityOverrides() {
  const n = $("#unityFrom").value;
  const ov = n ? ((await api(`/api/experiments/${n}`)).overrides || {}) : {};
  S.unityFrom = Object.fromEntries(Object.entries(ov).filter(([k]) => S.params.some(p => p.key === k)));
  const e = Object.entries(S.unityFrom);
  $("#unityOverrides").innerHTML = e.length ? "적용: " + e.map(([k, v]) => { const p = S.params.find(x => x.key === k); return `<span class="chg">${esc(p.label)} ${esc(valueLabel(p, v))}</span>`; }).join(", ") : "바꾼 조건 없이 기본 조건으로 만듭니다.";
}
$("#unityFrom").onchange = unityOverrides;
$("#unityForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target; $("#unityMsg").innerHTML = "";
  try {
    const j = await api("/api/unity", { date: f.date.value, name: f.name.value, unity_dir: f.unity_dir.value, strategies: checked($("#unityStrategies")), overrides: S.unityFrom });
    $("#unityMsg").innerHTML = `<div class="note ok">시작했습니다 → <code>${esc(j.output)}</code></div>`;
    pollJobs();
  } catch (e) { $("#unityMsg").innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
};

// 창 크기가 바뀌면 보이는 화면의 차트만 다시 그림 (글자 크기 1:1 유지)
let rsz; addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => {
  const t = ($("#nav a.on") || {}).dataset?.tab;
  if (t === "overview" && OV) renderOverview(); if (t === "compare") renderCmpDates(); if (t === "sens" && SENS) renderSens();
}, 150); });

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
    show($("#tab-" + start) ? start : "overview");
    if (await loadJobs()) pollJobs();
  } catch (e) {
    $(".wrap").insertAdjacentHTML("afterbegin", `<div class="note err" style="margin-top:24px">불러오지 못했습니다: ${esc(e.message)}</div>`);
  }
})();
