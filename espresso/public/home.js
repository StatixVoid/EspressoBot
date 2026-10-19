"use strict";
const notices = {
  unavailable: "Discord connection isn't available just yet. You can still explore the panel preview.",
  cancelled: "Sign-in cancelled. You're welcome to try again whenever you're ready.",
  "signin-failed": "Discord sign-in couldn't be completed. Please try again.",
};
const notice = notices[new URLSearchParams(location.search).get("notice")];
if (notice) { const node = document.querySelector("#notice"); node.textContent = notice; node.hidden = false; }
const examples = {
  invite: ["Come join my server! discord.gg/example", "Invite removed. A warning has been recorded.", "Your community, without the unwanted detour."],
  spam: ["hey! hey! hey! … six messages in eight seconds", "Spam detected. The latest message was removed.", "One automatic warning per 30 seconds keeps the response measured."],
  warning: ["Three warnings in the last 24 hours", "Time for a breather. A 10-minute timeout was applied.", "An example recipe. You choose your own threshold and duration."],
};
document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll("[data-example]").forEach(other => {
    const selected = other === button;
    other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected));
  });
  const [message, action, detail] = examples[button.dataset.example];
  document.querySelector("#example-message").textContent = message;
  document.querySelector("#example-action").textContent = action;
  document.querySelector("#example-detail").textContent = detail;
}));

const blends = {
  cozy: { name: "The cozy blend", description: "A gentler pace for a small circle of familiar faces.", limit: "4 messages / 10 sec", warnings: "3 in 24 hours", timeout: "5 minutes" },
  balanced: { name: "The balanced blend", description: "A little room to chat. A clear line when it gets noisy.", limit: "6 messages / 8 sec", warnings: "3 in 24 hours", timeout: "10 minutes" },
  busy: { name: "The lively blend", description: "More room for a fast-moving chat, with boundaries intact.", limit: "12 messages / 8 sec", warnings: "4 in 24 hours", timeout: "10 minutes" },
};
document.querySelectorAll("[data-blend]").forEach(button => button.addEventListener("click", () => {
  const blend = blends[button.dataset.blend];
  document.querySelectorAll("[data-blend]").forEach(other => {
    other.classList.toggle("selected", other === button);
    other.setAttribute("aria-pressed", String(other === button));
  });
  for (const key of ["name", "description", "limit", "warnings", "timeout"]) {
    document.querySelector(`#blend-${key}`).textContent = blend[key];
  }
}));

// Poll the bot's in-process cache; no Discord credentials or server details reach this page.
let statsTimer, statsController;
function showStats(state, count = null, latency = null) {
  const labels = {
    online: ["Espresso is on the clock", "Live · refreshes every 30 sec"],
    offline: ["Taking a coffee break", "Bot disconnected · checking again soon"],
    preview: ["A little taste of Espresso", "Website preview · bot not connected"],
    unavailable: ["Stats temporarily unavailable", "Trying again in 30 sec"],
  };
  document.querySelector("#live-dot").dataset.state = state;
  document.querySelector("#live-status").textContent = labels[state][0];
  document.querySelector("#stats-freshness").textContent = labels[state][1];
  document.querySelector("#server-count").textContent = count === null ? "—" : count.toLocaleString();
  document.querySelector("#gateway-latency").textContent = latency === null ? "—" : `${latency.toLocaleString()} ms`;
}
async function refreshStats() {
  clearTimeout(statsTimer);
  if (document.hidden || statsController) return;
  statsController = new AbortController();
  const timeout = setTimeout(() => statsController?.abort(), 8000);
  try {
    const response = await fetch("/api/stats", { cache: "no-store", credentials: "omit", signal: statsController.signal });
    if (!response.ok) throw new Error("Stats unavailable");
    const stats = await response.json();
    if (!["online", "offline", "preview"].includes(stats.status)) throw new Error("Unknown bot status");
    if (stats.status === "online" && (!Number.isSafeInteger(stats.server_count) || stats.server_count < 0)) throw new Error("Invalid count");
    const latency = Number.isSafeInteger(stats.latency_ms) && stats.latency_ms >= 0 ? stats.latency_ms : null;
    showStats(stats.status, stats.status === "online" ? stats.server_count : null, stats.status === "online" ? latency : null);
  } catch {
    if (!document.hidden) showStats("unavailable");
  } finally {
    clearTimeout(timeout);
    statsController = null;
    if (!document.hidden) statsTimer = setTimeout(refreshStats, 30000);
  }
}
document.addEventListener("visibilitychange", () => {
  clearTimeout(statsTimer);
  if (document.hidden) statsController?.abort();
  else refreshStats();
});
window.addEventListener("pagehide", () => { clearTimeout(statsTimer); statsController?.abort(); });
refreshStats();

// Cards are rendered by the server so the full menu also works without JavaScript.
(() => {
  const browser = document.querySelector("#feature-browser");
  if (!browser) return;
  const cards = Array.from(document.querySelectorAll("#feature-catalog .feature-menu-card"));
  const filters = Array.from(browser.querySelectorAll("[data-feature-filter]"));
  const search = document.querySelector("#feature-search");
  const count = document.querySelector("#feature-result-count");
  const empty = document.querySelector("#feature-empty");
  const searchable = new Map(cards.map(card => [card,card.textContent.toLocaleLowerCase()]));
  let group = "all";
  function update() {
    const words = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const card of cards) {
      const matches = (group === "all" || card.dataset.featureGroup === group) && words.every(word => searchable.get(card).includes(word));
      card.hidden = !matches;
      if (matches) visible++;
    }
    filters.forEach(button => button.setAttribute("aria-pressed",String(button.dataset.featureFilter === group)));
    count.textContent = `Showing ${visible} of ${cards.length} features`;
    empty.hidden = visible > 0;
  }
  function reset() {group="all";search.value="";update();}
  filters.forEach(button => button.addEventListener("click",()=>{group=button.dataset.featureFilter;update();}));
  search.addEventListener("input",update);
  document.querySelector("#feature-reset").addEventListener("click",()=>{reset();search.focus();});
  window.addEventListener("hashchange",()=>{
    const target=document.getElementById(location.hash.slice(1));
    if (target && cards.includes(target) && target.hidden) {reset();target.scrollIntoView({block:"start"});}
  });
  update();browser.hidden=false;
})();
