// The sysop page. Every URL is relative: the app is served under a path
// prefix on one address and at a subdomain's root on another.
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const when = (t) => (t ? new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + "Z" : "-");
let tab = "overview", msgType = "", catalog = {};

// The page's words come from static/i18n/<lang>.json (I18N.md: web
// catalogs); the English in the HTML is only a fallback the catalog holds.
function t(key, vars) {
  let v = key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), catalog);
  if (typeof v !== "string") v = key;
  return v.replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
}
async function loadCatalog() {
  for (const lang of [...new Set([(navigator.language || "en").split("-")[0], "en"])]) {
    try { const r = await fetch("i18n/" + lang + ".json"); if (r.ok) { catalog = await r.json(); break; } } catch (e) { /* next */ }
  }
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => el.dataset.i18nAttr.split(";").forEach((pair) => {
    const [attr, key] = pair.split(":"); el.setAttribute(attr.trim(), t(key.trim()));
  }));
}

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch("api/" + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
  return data;
}
function say(text) { $("said").textContent = text; }
async function act(path, body) { try { await api(path, body ?? {}); say(""); } catch (e) { say(e.message); } refresh(); }

async function overview() {
  const s = await api("status");
  $("bbs").textContent = s.bbs || "";
  const tx = s.transmit, r = s.radio;
  const badge = (cls, text) => `<span class="badge ${cls}">${esc(text)}</span>`;
  $("badges").innerHTML =
    badge(s.state === "running" ? "ok" : "", s.state) +
    (r.reserved ? badge("", t("badge.reserved", { kind: r.kind })) : "") +
    (tx.armed ? badge("armed", t("badge.armed", { who: tx.armedBy })) : badge("", t("badge.rx"))) +
    (tx.onAir ? badge("onair", t("badge.onair")) : "");
  const c = s.bbsState && s.bbsState.counts;
  const rows = [[t("fact.bbs"), s.bbs], [t("fact.room"), s.room || t("fact.roomByC")], [t("fact.radio"), `${r.kind} ${r.device || ""}`],
    [t("fact.freq"), (r.frequencyHz / 1e6).toFixed(4) + " MHz"], [t("fact.claim"), r.claim],
    [t("fact.allowed"), tx.allowed ? t("fact.yes") : t("fact.noSetting")],
    [t("fact.expires"), tx.armed ? (tx.expires ? when(tx.expires) : t("fact.untilDisarmed")) : "-"], [t("fact.ids"), s.idSent],
    [t("fact.messages"), c ? t("fact.counts", { p: c.personal, b: c.bulletins, u: c.users }) : "-"]];
  if (s.error) rows.push([t("fact.problem"), s.error]);
  for (const p of s.problems) rows.push([t("fact.setting"), p]);
  $("facts").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  const b = s.bbsState || { sessions: [], partners: [] };
  $("sessions").innerHTML = b.sessions.map((x) => `<tr><td>${esc(x.call)}</td><td>${esc(x.state)}</td></tr>`).join("") || `<tr><td colspan="2">${esc(t("empty.nobody"))}</td></tr>`;
  $("partners").innerHTML = b.partners.map((p) => `<tr><td>${esc(p.call)}</td><td>${esc(p.via.join(" "))}</td><td>${esc(p.routes.join(" "))}</td><td>${esc(p.state)}</td></tr>`).join("") || `<tr><td colspan="4">${esc(t("empty.none"))}</td></tr>`;
}
async function messages() {
  const d = await api("messages" + (msgType ? "?type=" + msgType : ""));
  $("msgs").innerHTML = d.messages.map((m) => `<tr><td><a href="#" data-read="${m.number}">${m.number}</a></td><td>${esc(m.type)}</td><td>${esc(m.to)}</td><td>${esc(m.at)}</td><td>${esc(m.from)}</td><td>${esc(m.subject)}</td><td>${esc(m.state)}</td><td><button data-kill="${m.number}">${esc(t("btn.kill"))}</button></td></tr>`).join("");
}
async function users() {
  const d = await api("users");
  $("userrows").innerHTML = d.users.map((u) => `<tr><td>${esc(u.call)}</td><td>${esc(u.name)}</td><td>${esc(u.home)}</td><td>${esc(u.qth)}</td><td>${u.connects}</td><td>${when(u.lastSeen)}</td><td><button data-bar="${esc(u.call)}" data-barred="${u.barred ? 0 : 1}">${esc(u.barred ? t("btn.unbar") : t("btn.bar"))}</button></td></tr>`).join("");
}
async function room() {
  const d = await api("room");
  $("topic").textContent = d.topic || t("empty.noTopic");
  $("members").innerHTML = d.members.map((c) => `${esc(c)} <button data-remove="${esc(c)}">${esc(t("btn.remove"))}</button>`).join(" ") || esc(t("empty.nobodyHere"));
  $("roomlog").textContent = d.lines.map((l) => `${when(l.t).slice(11)} ${l.text}`).join("\n");
}
async function logs() {
  const d = await api("log");
  $("logtext").textContent = d.log.map((l) => `${when(l.t)} ${l.kind} ${l.call} ${l.text}`).reverse().join("\n");
}
const views = { overview, messages, users, room, log: logs };
async function refresh() { try { await views[tab](); } catch (e) { say(e.message); } }

document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  tab = b.dataset.tab;
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll("main section").forEach((s) => (s.hidden = s.id !== tab));
  refresh();
}));
document.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => { msgType = b.dataset.type; refresh(); }));
$("arm").onclick = () => { if (confirm(t("ask.arm"))) act("tx/arm"); };
$("disarm").onclick = () => act("tx/disarm");
$("forward").onclick = () => act("forward");
$("release").onclick = () => { if (confirm(t("ask.release"))) act("radio/release"); };
$("claim").onclick = () => act("radio/claim");
document.body.addEventListener("click", async (e) => {
  const el = e.target;
  if (el.dataset.read) { e.preventDefault(); try { const d = await api("messages/" + el.dataset.read); const m = d.message; $("reading").textContent = [t("msg.header", { n: m.number, type: m.type, from: m.from, to: m.to, at: m.at }), t("msg.bid", { bid: m.bid, when: when(m.created) }), m.subject, "", m.body].join("\n"); } catch (x) { $("reading").textContent = x.message; } }
  if (el.dataset.kill && confirm(t("ask.kill", { n: el.dataset.kill }))) act("messages/" + el.dataset.kill + "/kill");
  if (el.dataset.bar) act("users/" + el.dataset.bar, { barred: el.dataset.barred === "1" });
  if (el.dataset.remove) act("room/remove", { call: el.dataset.remove });
});
$("sayform").onsubmit = (e) => { e.preventDefault(); const v = $("say").value; $("say").value = ""; act("room/say", { text: v }); };
$("topicform").onsubmit = (e) => { e.preventDefault(); act("room/topic", { topic: $("newtopic").value }); };
loadCatalog().then(refresh);
setInterval(refresh, 3000);
