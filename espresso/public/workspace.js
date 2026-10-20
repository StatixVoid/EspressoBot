"use strict";
window.espressoWorkspace = (() => {
  const find = selector => document.querySelector(selector);
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const settings = find("#settings-form"), tabs = find("#panel-tabs"), drafts = new Map();
  const motionControl = find(".background-toggle");
  if (motionControl) find(".server-picker-content").append(motionControl);
  const sectionNames = ["General", "Spam & links", "Abuse filters", "Warnings & timeouts", "Roles & logging",
    "Reaction roles", "Member screening", "Channel rules & exceptions", "Raid protection", "Welcomes & café"];
  const sections = Array.from(settings.children).filter(node => node.matches("section.settings-section"));
  sections.forEach((section, index) => {
    if (!section.id) section.id = "settings-section-" + (index + 1);
    section.dataset.settingsName = sectionNames[index] || section.querySelector("h2").textContent;
    section.querySelector("h2")?.setAttribute("tabindex", "-1");
  });
  const navigator = make("div", undefined, "settings-navigator");
  navigator.id = "settings-navigator";
  const searchLabel = make("label", "Find a setting", "stack-label");
  const search = make("input"); search.type = "search"; search.id = "settings-search";
  search.placeholder = "Search filters, roles, welcomes…"; search.autocomplete = "off";
  search.setAttribute("aria-controls", "settings-search-results");
  searchLabel.append(search);
  const jumpLabel = make("label", "Jump to section", "stack-label"), select = make("select");
  select.id = "settings-jump"; select.add(new Option("Choose a section…", ""));
  sections.forEach(section => select.add(new Option(section.dataset.settingsName, section.id)));
  jumpLabel.append(select); navigator.append(searchLabel, jumpLabel);
  const results = make("div", undefined, "settings-search-results"); results.id = "settings-search-results"; results.hidden = true;
  const searchStatus = make("p", "", "field-note"); searchStatus.id = "settings-search-status"; searchStatus.setAttribute("role", "status");
  navigator.append(results, searchStatus); settings.prepend(navigator);
  const summary = make("details", undefined, "settings-change-review"), summaryTitle = make("summary");
  summary.id = "settings-change-review"; summary.hidden = true;
  const changeList = make("ul"); summary.append(summaryTitle, changeList);
  find(".save-bar > div").append(summary);
  const banner = make("aside", undefined, "workspace-drafts"); banner.id = "workspace-drafts"; banner.hidden = true;
  banner.setAttribute("aria-label", "Unsaved changes");
  const draftStatus = make("p"); draftStatus.setAttribute("role", "status");
  const draftLinks = make("div", undefined, "workspace-draft-links"); banner.append(draftStatus, draftLinks); tabs.after(banner);
  let activeTab = "settings";
  const orderedDrafts = () => Array.from(drafts).sort(([a],[b]) => a === "settings" ? -1 : b === "settings" ? 1 : a.localeCompare(b));
  function focusSection(section, control) {
    find("#tab-settings").click();
    for (let parent = control?.parentElement; parent && parent !== settings; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    const target = control || section.querySelector("h2") || section;
    target.scrollIntoView({block:"center", behavior:"instant"}); target.focus({preventScroll:true});
  }
  function searchSettings() {
    const query = search.value.trim().toLocaleLowerCase(), words = query.split(/\s+/);
    results.replaceChildren(); results.hidden = !query; searchStatus.textContent = "";
    if (!query) return;
    const hits = [];
    for (const section of sections) {
      for (const label of section.querySelectorAll("label")) {
        const control = label.querySelector("input,select,textarea");
        if (!control || control.closest(".template-options")) continue;
        // Search labels and help, never private field values or offensive examples.
        const clone = label.cloneNode(true);
        clone.querySelectorAll("input,select,textarea,output").forEach(node => node.remove());
        const title = clone.textContent.replace(/\s+/g, " ").trim();
        const text = (section.dataset.settingsName + " " + title).toLocaleLowerCase();
        if (words.every(word => text.includes(word))) hits.push({section, control, title:title.slice(0,110)});
      }
    }
    for (const hit of hits.slice(0,8)) {
      const button = make("button", undefined, "setting-search-hit"); button.type = "button";
      button.append(make("strong", hit.title), make("small", hit.section.dataset.settingsName));
      button.addEventListener("click", () => focusSection(hit.section, hit.control)); results.append(button);
    }
    searchStatus.textContent = hits.length ? (hits.length > 8 ? "Showing the first 8 matches. Refine your search to narrow the list." :
      hits.length + (hits.length === 1 ? " setting found." : " settings found.")) : "No settings found. Try “timeout”, “spam”, or “welcome”.";
  }
  search.addEventListener("input", searchSettings);
  search.addEventListener("keydown", event => {
    if (event.key === "Escape") {search.value = ""; searchSettings();}
    if (event.key === "Enter") {event.preventDefault(); results.firstElementChild?.click();}
    if (event.key === "ArrowDown" && results.firstElementChild) {event.preventDefault(); results.firstElementChild.focus();}
  });
  select.addEventListener("change", () => {const section = document.getElementById(select.value); if (section) focusSection(section); select.value = "";});
  function reviewDraft(key) {
    find("#tab-" + key)?.click();
    const target = key === "settings" ? (!find("#setup-guide").hidden ? find("#setup-guide") : summary.hidden ? navigator : summary) : find("#hub-settings-form");
    if (target === summary) summary.open = true;
    target?.scrollIntoView({block:"center", behavior:"instant"});
    (target?.querySelector("summary,input,button,select") || target)?.focus({preventScroll:true});
  }
  function renderDrafts() {
    const entries = orderedDrafts().filter(([,value]) => value.dirty || value.saving);
    banner.hidden = !entries.length; draftLinks.replaceChildren();
    const saving = entries.some(([,value]) => value.saving);
    draftStatus.textContent = saving ? "Saving your changes. Please wait before leaving this server." :
      (location.pathname === "/preview" ? "Preview changes in " : "Unsaved changes in ") + entries.map(([,value]) => value.label).join(" and ") + ".";
    for (const [key,value] of entries) {
      const button = make("button", "Review " + value.label, "text-button"); button.type = "button";
      button.addEventListener("click", () => reviewDraft(key)); draftLinks.append(button);
    }
    for (const key of ["settings","hub"]) {
      const tab = find("#tab-" + key), changed = Boolean(drafts.get(key)?.dirty);
      tab.toggleAttribute("data-unsaved", changed); tab.title = changed ? "Unsaved changes" : "";
    }
  }
  function setDraft(key, dirty, saving = false) {
    drafts.set(key, {dirty:Boolean(dirty), saving:Boolean(saving), label:key === "hub" ? "Community hub" : "Settings"}); renderDrafts();
  }
  function canLeave(action) {
    if (Array.from(drafts.values()).some(value => value.saving)) {draftStatus.textContent = "A save is still in progress. Wait for it to finish before " + action + "."; return false;}
    if (location.pathname === "/preview" || !Array.from(drafts.values()).some(value => value.dirty)) return true;
    const names = orderedDrafts().filter(([,value]) => value.dirty).map(([,value]) => value.label).join(" and ");
    return confirm("Discard unsaved " + names + " changes before " + action + "?");
  }
  const labels = {enabled:"Automatic moderation", spam_enabled:"Spam protection", spam_limit:"Message limit", spam_window:"Spam time window",
    block_links:"Website links", block_invites:"Discord invites", bad_words:"Blocked phrases", warn_threshold:"Warning threshold",
    warn_window_hours:"Warning window", timeout_minutes:"Timeout duration", exempt_roles:"Exempt roles", log_channel:"Staff log channel",
    reaction_roles_enabled:"Reaction roles", reaction_roles:"Reaction mappings", screening:"Member screening", observation_mode:"Observation mode",
    allowed_domains:"Trusted domains", channel_rules:"Channel rules", warning_expiry_days:"Warning expiry", raid:"Raid protection", welcome:"Welcome cards", community:"Café features"};
  const sectionFor = key => key === "enabled" ? 0 : ["spam_enabled","spam_limit","spam_window","block_links","block_invites","bad_words"].includes(key) ? 1 :
    key === "hate_filters" ? 2 : ["warn_threshold","warn_window_hours","timeout_minutes"].includes(key) ? 3 :
    ["exempt_roles","log_channel"].includes(key) ? 4 : key.startsWith("reaction_") ? 5 : key === "screening" ? 6 :
    ["observation_mode","allowed_domains","channel_rules","warning_expiry_days"].includes(key) ? 7 : key === "raid" ? 8 : 9;
  const describe = value => typeof value === "boolean" ? value ? "On" : "Off" : typeof value === "number" ? String(value) :
    Array.isArray(value) ? value.length + " entries" : value === null ? "Not set" : typeof value === "string" ? "Selected" : "Updated";
  function updateSettings(current, baseline, dirty, saving) {
    setDraft("settings", dirty, saving);
    changeList.replaceChildren(); summary.hidden = !dirty || settings.hidden; if (!baseline || !current) return;
    const changes = [];
    for (const [key,value] of Object.entries(current)) {
      if (key === "setup_completed" || JSON.stringify(value) === JSON.stringify(baseline[key])) continue;
      if (key === "hate_filters") {
        for (const [category,config] of Object.entries(value)) {
          const title = {racism:"Racism",misogyny:"Misogyny",trans_hate:"Anti-trans hate"}[category];
          for (const [field,after] of Object.entries(config)) if (JSON.stringify(after) !== JSON.stringify(baseline[key]?.[category]?.[field])) {
            const fieldName = {enabled:"filter",level:"coverage",action:"action",extra_terms:"blocked terms",allowed_terms:"exceptions"}[field];
            const actionName = {delete:"Remove only",warn:"Remove + warning",timeout:"Remove + warning + timeout"};
            const readable = val => field === "action" ? actionName[val] : describe(val);
            changes.push({key,text:title + " " + fieldName + ": " + readable(baseline[key]?.[category]?.[field]) + " → " + readable(after)});
          }
        }
      } else changes.push({key,text:(labels[key] || key.replaceAll("_"," ")) + ": " +
        (typeof value === "object" && !Array.isArray(value) && value !== null ? "Updated" : describe(baseline[key]) + " → " + describe(value))});
    }
    summaryTitle.textContent = "Review " + changes.length + (changes.length === 1 ? " change" : " changes");
    changes.forEach(change => {
      const li = make("li"), button = make("button", change.text, "text-button"); button.type = "button";
      button.addEventListener("click", () => focusSection(sections[sectionFor(change.key)])); li.append(button); changeList.append(li);
    });
  }
  function openTester(category) {
    find("#tab-tools").click(); const form = find("#filter-test");
    form.elements.recipe.value = "unsaved";
    find("#filter-test-context").textContent = "Testing your current form, including unsaved " +
      ({racism:"Racism",misogyny:"Misogyny",trans_hate:"Anti-trans hate"}[category] || "filter") +
      " settings. The moderation switch, exemptions, and selected channel still apply.";
    form.elements.text.scrollIntoView({block:"center",behavior:"instant"}); form.elements.text.focus({preventScroll:true});
  }
  function renderSimulation(result, recipe) {
    const host = find("#filter-result"); host.replaceChildren(); host.classList.remove("error-text"); delete host.dataset.stale; host.dataset.state = result.effective_action || "none";
    const names = {none:"No moderation action",observe:"Log only",delete:"Remove message",warn:"Remove + warning",timeout:"Remove + timeout"};
    host.append(make("span", recipe === "unsaved" ? "Current unsaved form" : "Saved settings", "simulation-recipe"),
      make("h4", names[result.effective_action] || "Simulation complete"),
      make("p", result.matched ? "Matched rule: " + result.rule : "No text rule matched.", "simulation-rule"));
    const facts = make("div", undefined, "simulation-facts");
    facts.append(make("span","Moderation " + (result.moderation_enabled ? "on" : "off")),
      make("span","Observation " + (result.observation_mode ? "on" : "off")));
    if (Number.isFinite(result.warnings_after)) facts.append(make("span", "Warnings after: " + result.warnings_after));
    if (result.action) facts.append(make("span", "Rule action: " + ({delete:"Remove only",warn:"Warning",timeout:"Timeout"}[result.action] || result.action)));
    host.append(facts);
    if (result.steps?.length) {const steps = make("ol"); result.steps.forEach(step => steps.append(make("li",step))); host.append(steps);}
    host.append(make("p", result.steps?.length ? "This test performs no Discord actions. Counts are hypothetical; live timing, deduplication, and Discord failures can change the outcome." :
      result.note || "This test performs no Discord actions.", "simulation-note"));
  }
  document.addEventListener("espresso:tab", event => activeTab = event.detail.name);
  document.addEventListener("espresso:loading", () => {search.value = ""; searchSettings();});
  document.addEventListener("keydown", event => {
    if (event.defaultPrevented) return;
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "s") {
      if (tabs.hidden || !["settings","hub"].includes(activeTab)) return;
      event.preventDefault();
      const save = find(activeTab === "hub" ? "#hub-save" : !find("#setup-guide").hidden ? "#setup-save" : "#save-settings");
      if (!save.disabled) save.click();
    } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && activeTab === "settings" &&
      !settings.hidden && !event.target.closest("input,textarea,select,[contenteditable]")) {event.preventDefault(); search.scrollIntoView({block:"center"}); search.focus();}
  });
  return {setDraft,canLeave,updateSettings,openTester,renderSimulation,
    hasChanges:() => Array.from(drafts.values()).some(value => value.dirty || value.saving)};
})();
