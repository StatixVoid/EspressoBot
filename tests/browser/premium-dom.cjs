// Node + jsdom. No browser, Discord credentials, local server, or network needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require(process.env.JSDOM_MODULE || "jsdom");
const root = path.resolve(__dirname, "../../espresso/public");
const html = fs.readFileSync(path.join(root, "dashboard.html"), "utf8");
const script = fs.readFileSync(path.join(root, "premium.js"), "utf8");
const flush = () => new Promise(setImmediate);
const report = (guild, days) => ({guild_id:guild, days, generated_at:1795694400,
  totals:{cases:guild === "2" ? 99 : days, warnings:2, timeouts:1, members:3},
  daily:Array.from({length:days}, (_,i) => ({date:`2026-11-${i+1}`, cases:0}))});

async function main() {
  const dom = new JSDOM(html, {url:"https://espresso.test/dashboard", runScripts:"outside-only"});
  try {
    const w = dom.window, d = w.document, calls = [];
    let active = true, denied = false, malformed = false, hold = false, release = null, oldSignal = null;
    w.fetch = async (url, options) => {
      calls.push(url);
      if (url.includes("/insights")) {
        if (hold) {hold = false; oldSignal = options.signal; return new Promise(resolve => release = () => resolve({ok:true,json:async () => report("1",30)}));}
        if (malformed) return {ok:false,json:async () => {throw new Error("HTML from proxy");}};
        if (denied) return {ok:false,json:async () => ({error:"Staff access revoked."})};
        const u = new URL(url, w.location.href);
        return {ok:true,json:async () => report(url.split("/")[3],Number(u.searchParams.get("days")))};
      }
      return {ok:true,json:async () => ({active})};
    };
    w.eval(script);
    const server = id => d.dispatchEvent(new w.CustomEvent("espresso:server", {detail:{id}}));
    const text = selector => d.querySelector(selector).textContent;
    server("1"); await flush();
    assert.match(text("#premium-message"), /7 UTC dates/);
    assert.equal(d.querySelectorAll("#premium-daily tr").length, 7);
    d.querySelector("#premium-days").value = "30";
    d.querySelector("#premium-days").dispatchEvent(new w.Event("change")); await flush();
    assert.match(text("#premium-message"), /30 UTC dates/);
    assert.equal(d.querySelectorAll("#premium-daily tr").length, 30);
    active = false; d.querySelector("#premium-refresh").click(); await flush();
    assert.match(text("#premium-message"), /do not have access/);
    assert.equal(text("#premium-metrics"), "");
    assert.equal(d.querySelectorAll("#premium-daily tr").length, 0);
    active = true; denied = true; d.querySelector("#premium-refresh").click(); await flush();
    assert.match(text("#premium-message"), /revoked/);
    assert.equal(text("#premium-metrics"), "");
    denied = false; malformed = true; d.querySelector("#premium-refresh").click(); await flush();
    assert.match(text("#premium-message"), /could not load/);
    assert.doesNotMatch(text("#premium-message"), /HTML from proxy|Unexpected token/);
    malformed = false; hold = true; d.querySelector("#premium-refresh").click(); await flush();
    assert.ok(release);
    server("2"); await flush();
    assert.equal(oldSignal.aborted, true);
    assert.match(text("#premium-metrics"), /Recorded cases99/);
    release(); await flush();
    assert.match(text("#premium-metrics"), /Recorded cases99/);
    assert.doesNotMatch(text("#premium-metrics"), /Recorded cases30/);
    d.dispatchEvent(new w.Event("espresso:unauthorized"));
    assert.equal(text("#premium-metrics"), "");
    assert.equal(d.querySelector("#premium-insights").hidden, true);
    const count = calls.length;
    w.history.replaceState(null, "", "/preview"); server("3"); await flush();
    assert.equal(calls.length, count);
    assert.match(text("#premium-message"), /Connect an allowlisted/);
    console.log("Premium DOM checks passed: 7/30 dates, access loss, malformed responses, stale server replies, sign-out clearing, and offline preview.");
  } finally {dom.window.close();}
}
main().catch(error => {console.error(error); process.exitCode = 1;});
