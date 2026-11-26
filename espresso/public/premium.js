"use strict";
(() => {
  const section = document.querySelector("#premium-insights");
  const status = document.querySelector("#premium-message");
  const metrics = document.querySelector("#premium-metrics");
  const daily = document.querySelector("#premium-daily");
  const refresh = document.querySelector("#premium-refresh");
  const days = document.querySelector("#premium-days");
  let server = "", generation = 0, controller = null;

  function clear() {
    generation++;
    controller?.abort(); controller = null;
    metrics.replaceChildren(); daily.replaceChildren();
    status.textContent = ""; refresh.disabled = false;
  }
  async function get(path, signal) {
    const response = await fetch(path, {credentials:"same-origin", signal});
    let value;
    try {value = await response.json();}
    catch {throw new Error("Espresso could not load Premium Insights. Try again shortly.");}
    if (!response.ok) throw new Error(value.error || "Premium Insights could not load.");
    return value;
  }
  async function load() {
    clear();
    if (!server) return;
    if (location.pathname === "/preview") {
      status.textContent = "Premium Insights is a pilot feature. Connect an allowlisted server or staff account to view recorded moderation trends.";
      refresh.disabled = true;
      return;
    }
    const token = generation;
    const pending = new AbortController(); controller = pending;
    refresh.disabled = true; status.textContent = "Checking Premium access…";
    const base = `/api/servers/${encodeURIComponent(server)}/premium`;
    try {
      const access = await get(base, pending.signal);
      if (token !== generation) return;
      if (!access.active) {
        status.textContent = "Premium Insights is in an allowlisted pilot. This server and account do not have access yet.";
        return;
      }
      const report = await get(`${base}/insights?days=${days.value}`, pending.signal);
      if (token !== generation) return;
      for (const [key, label] of [["cases","Recorded cases"],["warnings","Warnings recorded"],["timeouts","Timeout cases"],["members","Members in recorded cases"]]) {
        const item = document.createElement("article"); item.className = "activity-item metric-card";
        const title = document.createElement("h3"), count = document.createElement("p");
        title.textContent = label; count.textContent = String(report.totals[key]);
        item.append(title, count); metrics.append(item);
      }
      for (const row of report.daily) {
        const tr = document.createElement("tr");
        for (const value of [row.date, String(row.cases)]) {
          const td = document.createElement("td"); td.textContent = value; tr.append(td);
        }
        daily.append(tr);
      }
      status.textContent = `Pilot access active · ${report.days} UTC dates · updated ${new Date(report.generated_at*1000).toLocaleString()}`;
    } catch (error) {
      if (token === generation && error.name !== "AbortError") status.textContent = error.message;
    } finally {
      if (token === generation) refresh.disabled = false;
    }
  }
  document.addEventListener("espresso:server", event => {
    server = event.detail.id; section.hidden = false; load();
  });
  for (const name of ["espresso:loading", "espresso:unauthorized"]) {
    document.addEventListener(name, () => {server = ""; clear(); section.hidden = true;});
  }
  refresh.addEventListener("click", load);
  days.addEventListener("change", load);
})();
