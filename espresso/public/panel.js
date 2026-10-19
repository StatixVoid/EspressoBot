"use strict";
const $ = selector => document.querySelector(selector);
const preview = location.pathname === "/preview";
const form = $("#settings-form");
// Handle validation here so errors can be shown beside Save and inside closed sections.
form.noValidate = true;
let fieldErrorSequence = 0;
const categories = {racism:"Racism", misogyny:"Misogyny", trans_hate:"Anti-trans hate"};
const levelDescriptions = {1:"Core explicit slurs",2:"Adds an expanded slur vocabulary",3:"Adds discriminatory and exclusionary phrases",4:"Adds spacing, punctuation, leetspeak, repetition, and Unicode evasions of slurs and phrases",5:"Adds broader identity-directed insults and stereotypes, including combined evasions; highest risk of false positives"};
const defaultHate = () => Object.fromEntries(Object.keys(categories).map(key => [key,{enabled:false,level:1,action:"warn",extra_terms:[],allowed_terms:[]}]));
let availableRoles = [], availableChannels = [], availableCategories = [], canManage = true, canModerate = true, screeningEventsEnabled = false, lastAttempt = null, serversLoading = false;
const defaultScreening = () => ({enabled:false,mode:"everyone",minimum_account_age_days:7,verified_role_id:null,entry_channel_id:null,require_agreement:true,rules_text:"I agree to follow this server's rules and respect other members."});
let csrf = "", currentServer = "", version = 0, baseline = null, dirty = false, busy = false, setupCompleted = false;
const defaults = {enabled:false,spam_enabled:true,spam_limit:6,spam_window:8,block_links:false,block_invites:true,bad_words:[],exempt_roles:[],log_channel:null,warn_threshold:3,warn_window_hours:24,timeout_minutes:10,hate_filters:defaultHate(),reaction_roles_enabled:false,reaction_roles:[],screening:defaultScreening()};
const demoRoles = [{id:"100", name:"Moderators"}, {id:"101", name:"Trusted members"}, {id:"102", name:"Community regulars"}];
const demoChannels = [{id:"200",name:"espresso-logs"},{id:"201",name:"staff-room"}];

function message(text, error = false) {
  const node = $("#panel-message"); node.textContent = text; node.hidden = !text; node.classList.toggle("notice-error", error);
  const feedback = $("#settings-feedback");
  feedback.textContent = form.hidden ? "" : text; feedback.hidden = form.hidden || !text;
  feedback.classList.toggle("save-feedback-error", error);
}
function clearSettingsErrors() {
  for (const control of form.querySelectorAll("[data-save-invalid]")) {
    const errorId = control.dataset.saveInvalid;
    control.removeAttribute("aria-invalid");
    const describedBy = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(id => id && id !== errorId);
    if (describedBy.length) control.setAttribute("aria-describedby", describedBy.join(" ")); else control.removeAttribute("aria-describedby");
    document.getElementById(errorId)?.remove(); delete control.dataset.saveInvalid;
  }
}
function settingsFieldError(control, text) {
  const error = document.createElement("small"); error.id = `settings-field-error-${++fieldErrorSequence}`;
  error.className = "settings-field-error"; error.textContent = text;
  control.insertAdjacentElement("afterend", error);
  control.dataset.saveInvalid = error.id; control.setAttribute("aria-invalid", "true");
  control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"),error.id].filter(Boolean).join(" "));
  for (let parent = control.parentElement; parent && parent !== form; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true;
  message(text, true);
  control.scrollIntoView({block:"center",behavior:"instant"}); control.focus({preventScroll:true});
}
function screeningRequirements() {
  const enabled = form.elements.screening_enabled.checked;
  for (const name of ["screening_verified_role_id","screening_entry_channel_id"]) form.elements[name].required = enabled;
}
async function request(url, options = {}) {
  const response = await fetch(url, {credentials:"same-origin", ...options, headers:{...options.headers}});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      form.hidden = true;
      $("#setup-guide").hidden = true;
      $("#preview-signin").hidden = false;
      $("#logout").hidden = true;
      document.dispatchEvent(new Event("espresso:unauthorized"));
    }
    throw new Error(data.error || "Something went wrong. Please try again.");
  }
  return data;
}
function snapshot() {
  const result = {setup_completed:setupCompleted};
  for (const name of ["enabled","spam_enabled","block_links","block_invites"]) result[name] = form.elements[name].checked;
  for (const name of ["spam_limit","spam_window","warn_threshold","warn_window_hours","timeout_minutes"]) result[name] = Number(form.elements[name].value);
  result.bad_words = [...new Set(form.elements.bad_words.value.split(/\r?\n/).map(word => word.trim()).filter(Boolean))];
  result.exempt_roles = Array.from(document.querySelectorAll("input[name=exempt-role]:checked"), input => input.value);
  result.log_channel = form.elements.log_channel.value || null;
  result.hate_filters = {};
  for (const category of Object.keys(categories)) {
    const card = document.querySelector(`[data-category="${category}"]`);
    const phrases = key => [...new Set(card.querySelector(`[data-key="${key}"]`).value.split(/\r?\n/).map(term => term.trim()).filter(Boolean))];
    result.hate_filters[category] = {enabled:card.querySelector('[data-key="enabled"]').checked, level:Number(card.querySelector('[data-key="level"]').value),action:card.querySelector('[data-key="action"]').value,extra_terms:phrases("extra_terms"),allowed_terms:phrases("allowed_terms")};
  }
  result.reaction_roles_enabled = form.elements.reaction_roles_enabled.checked;
  result.reaction_roles = Array.from(document.querySelectorAll("[data-reaction-row]"),row => ({channel_id:row.querySelector('[data-field="channel_id"]').value,message_id:row.querySelector('[data-field="message_id"]').value.trim(),emoji:row.querySelector('[data-field="emoji"]').value.trim(),role_id:row.querySelector('[data-field="role_id"]').value,remove_on_unreact:row.querySelector('[data-field="remove_on_unreact"]').checked}));
  result.screening = {enabled:form.elements.screening_enabled.checked,mode:form.elements.screening_mode.value,minimum_account_age_days:Number(form.elements.screening_minimum_account_age_days.value),verified_role_id:form.elements.screening_verified_role_id.value || null,entry_channel_id:form.elements.screening_entry_channel_id.value || null,require_agreement:form.elements.screening_require_agreement.checked,rules_text:form.elements.screening_rules_text.value.trim()};
  Object.assign(result,window.espressoExtensions.read());
  return result;
}
function fill(settings) {
  clearSettingsErrors();
  settings = {...defaults,...window.espressoExtensions.defaults,...settings};
  setupCompleted = Boolean(settings.setup_completed);
  renderHate(settings.hate_filters);
  renderReactions(settings.reaction_roles);
  renderScreening(settings.screening);
  window.espressoExtensions.fill(settings);
  for (const [name,value] of Object.entries(settings)) {
    if (name === "hate_filters" || name === "reaction_roles" || name === "setup_completed" || name === "screening" || window.espressoExtensions.keys.includes(name)) continue;
    if (name === "exempt_roles") {
      document.querySelectorAll("input[name=exempt-role]").forEach(input => input.checked = value.includes(input.value));
    } else if (name === "bad_words") form.elements[name].value = value.join("\n");
    else if (typeof value === "boolean") form.elements[name].checked = value;
    else form.elements[name].value = value ?? "";
  }
  const status = $("#moderation-status");
  status.hidden = false; status.textContent = settings.enabled ? "Moderation on" : "Moderation off";
  status.classList.toggle("status-on", settings.enabled);
  baseline = snapshot(); dirty = false; updateSave();
}
function updateSave() {
  $("#save-settings").disabled = preview || busy || !dirty;
  $("#reset-settings").disabled = busy || !dirty;
  $("#save-state").textContent = preview ? "Explore freely. This preview isn't saved." : busy ? "Saving your recipe…" : dirty ? "You have unsaved changes." : "Your recipe is up to date.";
}
function renderOptions(roles, channels, settings) {
  availableRoles = roles; availableChannels = channels;
  const rolesNode = $("#role-options"); rolesNode.replaceChildren();
  const validRoles = new Set(roles.map(role => role.id));
  for (const id of settings.exempt_roles) if (!validRoles.has(id)) roles.push({id, name:`Unavailable role (${id})`});
  for (const role of roles) {
    const label = document.createElement("label"), input = document.createElement("input"), name = document.createElement("span");
    input.type = "checkbox"; input.name = "exempt-role"; input.value = role.id; name.textContent = role.name;
    label.append(input, name); rolesNode.append(label);
  }
  if (!roles.length) rolesNode.textContent = "No additional roles in this server.";
  const select = form.elements.log_channel; select.replaceChildren(new Option("No channel logging", ""));
  for (const channel of channels) {
    const option = new Option(`# ${channel.name}${channel.can_log === false ? " — Espresso needs access" : ""}`, channel.id);
    option.disabled = channel.can_log === false; select.add(option);
  }
  if (settings.log_channel && !channels.some(c => c.id === settings.log_channel)) select.add(new Option("Unavailable channel — choose a new channel", settings.log_channel));
}
async function chooseServer(server, button, initial = null) {
  if (busy || (dirty && !confirm("Discard unsaved changes and switch servers?"))) return;
  lastAttempt = [server,button]; $("#retry-server").hidden = true; $("#panel-tabs").hidden = true;
  document.dispatchEvent(new Event("espresso:loading"));
  busy = true; form.hidden = true; $("#setup-guide").hidden = true; message("Loading this server's recipe…"); updateSave();
  $("#server-name").textContent = server.name; $("#moderation-status").hidden = true;
  try {
    if (initial?.error) throw new Error(initial.error);
    const data = initial?.data || (preview ? {settings:{...defaults, exempt_roles:["100"], log_channel:"200"}, version:0, roles:[...demoRoles], channels:demoChannels} : await request(`/api/servers/${encodeURIComponent(server.id)}/settings`));
    currentServer = server.id; version = data.version; screeningEventsEnabled = Boolean(data.screening_events_enabled);
    canManage = data.access?.manage ?? server.can_manage ?? true; canModerate = data.access?.moderate ?? server.can_moderate ?? true;
    availableCategories = data.categories || [];
    if(canManage) {renderOptions(data.roles, data.channels, data.settings); fill(data.settings);}
    else {availableRoles=[];availableChannels=[];baseline=null;dirty=false;form.hidden=true;$("#setup-guide").hidden=true;}
    document.querySelectorAll('#panel-tabs [data-manager]').forEach(tab=>tab.hidden=!canManage);
    $("#tab-desk").hidden=!canModerate;
    $("#server-description").textContent = preview ? "Example server · make yourself at home." : canManage ? "Your rules, your community. Changes apply when you save." : "Your moderation desk. Reports and cases are available with your current permissions.";
    document.querySelectorAll(".server-item").forEach(node => {node.classList.remove("active"); node.removeAttribute("aria-current");});
    button.classList.add("active"); button.setAttribute("aria-current", "true");
    if(canManage)showSetup(data.settings);else $("#run-setup").hidden=true; message("");
    $("#panel-tabs").hidden = false;
    $("#picker-name").textContent = server.name;
    if(matchMedia("(max-width:700px)").matches)$("#server-picker").open = false;
    document.dispatchEvent(new CustomEvent("espresso:server",{detail:{id:currentServer,manage:canManage,moderate:canModerate}}));
    const url = new URL(location.href); url.searchParams.set("server", currentServer); history.replaceState(null,"",url);
  } catch (error) { message(error.message, true); $("#retry-server").hidden = false; $("#server-description").textContent = "This server could not load. Retry here or choose another server."; }
  finally { busy = false; updateSave(); }
}
async function loadServers() {
  if (busy || serversLoading || (dirty && !confirm("Discard unsaved changes and refresh servers?"))) return;
  serversLoading = true; $("#refresh-servers").disabled = true; $("#panel-tabs").hidden = true; $("#retry-server").hidden = true;
  document.dispatchEvent(new Event("espresso:loading"));
  form.hidden = true; $("#setup-guide").hidden = true; $("#run-setup").hidden = true; dirty = false; message("");
  $("#moderation-status").hidden = true;
  const list = $("#server-list"); list.textContent = "Loading your servers…";
  try {
    const preferredId = new URLSearchParams(location.search).get("server");
    const params = new URLSearchParams({include_settings:"1"});
    if(preferredId)params.set("server",preferredId);
    const data = preview ? {bot_online:true, servers:[{id:"123",name:"The Espresso Lounge",installed:true}]} : await request(`/api/servers?${params}`);
    list.replaceChildren();
    if (!data.bot_online) message("Espresso is offline or reconnecting. Refresh when it's available to manage your servers.");
    if (!data.servers.length) {list.textContent = "No eligible servers found. You need Manage Server or Moderate Members permission."; return;}
    let first = null, preferred = null;
    for (const server of data.servers) {
      const installed = server.installed === true;
      const node = document.createElement(installed ? "button" : "a"); node.className = "server-item";
      if (installed) {node.type = "button"; node.addEventListener("click", () => chooseServer(server, node));}
      else if (data.bot_online) node.href = `/invite?guild_id=${encodeURIComponent(server.id)}`;
      const avatar = document.createElement("span"), text = document.createElement("span"), name = document.createElement("strong"), note = document.createElement("small");
      avatar.className = "server-avatar"; avatar.textContent = server.name.slice(0,1).toUpperCase();
      name.textContent = server.name; note.textContent = installed ? "Configure server →" : data.bot_online ? "Add Espresso ↗" : "Bot unavailable";
      text.append(name,note); node.append(avatar,text); list.append(node);
      if (installed && !first) first = [server,node];
      if (installed && server.id === preferredId) preferred = [server,node];
    }
    if (preferred || first) {
      const selected = preferred || first;
      await chooseServer(...selected,data.initial_server?.id === selected[0].id ? data.initial_server : null);
    }
    else if (data.bot_online) message("Add Espresso to one of your servers to configure its recipe.");
  } catch (error) { list.textContent = "Couldn't load your servers."; message(error.message, true); }
  finally {serversLoading = false; $("#refresh-servers").disabled = false;}
}
$("#retry-server").addEventListener("click",()=>{if(lastAttempt) chooseServer(...lastAttempt);});
$("#server-picker").addEventListener("click",event=>{if(event.target.closest("summary") && !matchMedia("(max-width:700px)").matches)event.preventDefault();});
matchMedia("(max-width:700px)").addEventListener("change",event=>{$("#server-picker").open=!event.matches || !currentServer;});
function settingsEdited() {
  if (form.querySelector("[data-save-invalid]")) {clearSettingsErrors();message("");}
  screeningRequirements();
  if (baseline) dirty = JSON.stringify(snapshot()) !== JSON.stringify(baseline);
  updateSave();
}
form.addEventListener("input", settingsEdited);
form.addEventListener("change", settingsEdited);
$("#reset-settings").addEventListener("click", () => {if (baseline) fill(baseline); message("");});
form.addEventListener("submit", async event => {
  event.preventDefault(); if (preview || busy || !dirty) return;
  clearSettingsErrors(); message(""); screeningRequirements();
  const proposed = {...snapshot(),setup_completed:true};
  if (proposed.screening.enabled && !proposed.screening.verified_role_id) {settingsFieldError(form.elements.screening_verified_role_id,"Choose the Verified access role members should receive after verification. Create a regular role in Discord first if needed, then refresh this server.");return;}
  if (proposed.screening.enabled && !proposed.screening.entry_channel_id) {settingsFieldError(form.elements.screening_entry_channel_id,"Choose the entry channel where members will read the rules and request verification.");return;}
  const invalid = Array.from(form.elements).find(control => control.willValidate && !control.validity.valid);
  if (invalid) {settingsFieldError(invalid,invalid.validationMessage || "Check this field before saving.");return;}
  if (proposed.bad_words.length > 100 || proposed.bad_words.some(word => word.length > 80)) {message("Use up to 100 phrases, each 80 characters or fewer.", true); return;}
  if (Object.values(proposed.hate_filters).some(config => [config.extra_terms,config.allowed_terms].some(list => list.length > 100 || list.some(term => term.length > 80)))) {message("Each category list supports up to 100 phrases of 80 characters or fewer.",true); return;}
  if (proposed.exempt_roles.length > 100) {message("Choose up to 100 exempt roles.", true); return;}
  if (proposed.screening.enabled && !screeningEventsEnabled) {message("Automatic join tracking is unavailable. Ask Espresso's operator to set ENABLE_MEMBER_SCREENING_EVENTS=true in the hosting computer's .env, enable Server Members Intent in Discord, and restart Espresso.",true);return;}
  if (!proposed.screening.rules_text) {message("Enter the rules members should agree to.",true);return;}
  const changes = Object.fromEntries(Object.entries(proposed).filter(([key,value])=>JSON.stringify(value)!==JSON.stringify(baseline[key])));
  busy = true; updateSave(); Array.from(form.elements).forEach(element => element.disabled = true);
  try {
    const data = await request(`/api/servers/${encodeURIComponent(currentServer)}/settings`, {method:"PATCH", headers:{"Content-Type":"application/json", "X-CSRF-Token":csrf}, body:JSON.stringify({version,changes})});
    version = data.version; fill(data.settings); showSetup(data.settings); document.dispatchEvent(new Event("espresso:saved")); message(data.setup_warnings?.length ? `Settings saved. ${data.setup_warnings.join(" ")}` : "Your recipe is saved. Espresso will use it immediately.", Boolean(data.setup_warnings?.length));
  } catch (error) { message(error.message, true); }
  finally {Array.from(form.elements).forEach(element => element.disabled = false); busy = false; updateSave();}
});
$("#refresh-servers").addEventListener("click", loadServers);
$("#logout").addEventListener("click", async () => {
  if (dirty && !confirm("Discard unsaved changes and sign out?")) return;
  try {await request("/auth/logout", {method:"POST",headers:{"X-CSRF-Token":csrf}}); dirty = false; location.assign("/");}
  catch (error) {message(error.message,true);}
});
window.addEventListener("beforeunload", event => {if (dirty && !preview) {event.preventDefault(); event.returnValue = "";}});

function showSetup(settings, force = false) {
  const guide = $("#setup-guide");
  guide.hidden = preview || (Boolean(settings.setup_completed) && !force);
  form.hidden = !guide.hidden;
  $("#run-setup").hidden = preview;
  if (guide.hidden) return;
  const channels = $("#setup-log-channel"); channels.replaceChildren(new Option("Choose a staff channel…", ""));
  for (const channel of availableChannels) {
    const option = new Option(`# ${channel.name}${channel.can_log === false ? " — Espresso needs access" : ""}`, channel.id);
    option.disabled = channel.can_log === false; channels.add(option);
  }
  channels.value = settings.log_channel || "";
  $("#setup-no-logs").checked = false;
  channels.disabled = false; channels.required = true;
  $("#setup-enable").checked = Boolean(settings.enabled);
  // Returning to the guide must preserve an existing custom recipe by default.
  $("#setup-preset").value = settings.setup_completed || version > 0 ? "custom" : "balanced";
  $("#setup-save").disabled = false;
  $("#setup-command").textContent = "/espresso logs channel:#your-staff-channel";
}
$("#setup-no-logs").addEventListener("change", () => {
  const select = $("#setup-log-channel"), off = $("#setup-no-logs").checked;
  select.disabled = off; select.required = !off;
});
$("#setup-guide").addEventListener("input", () => {dirty = true;});
$("#setup-guide").addEventListener("change", () => {dirty = true;});
$("#setup-advanced").addEventListener("click", () => {
  if (dirty && !confirm("Discard the guide's unsaved choices and open all settings?")) return;
  dirty = false; $("#setup-guide").hidden = true; form.hidden = false;
  message("Choose your settings below, then save. You can also reopen the setup guide from the server menu.");
});
$("#run-setup").addEventListener("click", () => {
  if (busy || !baseline || (dirty && !confirm("Discard unsaved changes and reopen setup?"))) return;
  fill(baseline); showSetup(baseline,true); message("");
});
$("#setup-guide").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || preview || !event.currentTarget.reportValidity()) return;
  const changes = {setup_completed:true, log_channel:$("#setup-no-logs").checked ? null : $("#setup-log-channel").value,
                   enabled:$("#setup-enable").checked};
  const preset = $("#setup-preset").value;
  if (preset !== "custom") {
    Object.assign(changes,{spam_enabled:true,spam_limit:6,spam_window:8,block_invites:true,block_links:false,
                           warn_threshold:3,warn_window_hours:24,timeout_minutes:10});
    changes.hate_filters = structuredClone(baseline.hate_filters);
    for (const config of Object.values(changes.hate_filters)) Object.assign(config,{enabled:preset === "balanced",level:2,action:"warn"});
  }
  busy = true; $("#setup-save").disabled = true;
  const controls = Array.from(event.currentTarget.elements).filter(control => !control.disabled);
  controls.forEach(control => control.disabled = true);
  message("Saving this server's setup…");
  try {
    const data = await request(`/api/servers/${encodeURIComponent(currentServer)}/settings`,{method:"PATCH",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify({version,changes})});
    version = data.version; fill(data.settings); showSetup(data.settings);
    document.dispatchEvent(new Event("espresso:saved"));
    message(data.settings.enabled ? "Setup saved. Automatic moderation is on. You can fine-tune every filter and add reaction roles below." : "Setup saved. Automatic moderation is off until you turn it on. You can fine-tune every filter and add reaction roles below.");
  } catch (error) {message(error.message,true);}
  finally {controls.forEach(control => control.disabled = false);busy = false;$("#setup-save").disabled = false;updateSave();}
});

function renderHate(filters) {
  const container = $("#hate-filter-controls"); container.replaceChildren();
  for (const [category,title] of Object.entries(categories)) {
    const config = filters[category], card = document.createElement("div"); card.className = "filter-card"; card.dataset.category = category;
    // This markup contains only fixed category names; all configured terms use .value.
    card.innerHTML = `<label class="switch-row"><span><strong>${title}</strong><small>Filter abuse in this category.</small></span><input type="checkbox" role="switch" data-key="enabled"><span class="switch" aria-hidden="true"></span></label><label class="stack-label">Coverage level <output data-level-output></output><input type="range" data-key="level" min="1" max="5" step="1"><small data-level-description></small></label><label class="stack-label">Action<select data-key="action"><option value="delete">Remove message only</option><option value="warn">Remove + warning (normal escalation)</option><option value="timeout">Remove + warning + timeout</option></select></label><details class="advanced-filter"><summary>Fine-tune this category</summary><label class="stack-label">Extra blocked words or phrases<textarea data-key="extra_terms" rows="3" maxlength="8100" placeholder="One per line"></textarea><small>Additional literal matches at every level; levels 4–5 also check common evasions. Up to 100 entries.</small></label><label class="stack-label">Allowed matching terms<textarea data-key="allowed_terms" rows="3" maxlength="8100" placeholder="One exact matched term or phrase per line"></textarea><small>Skips that exact match in this category only. Does not whitelist the whole message or override other filters.</small></label></details>`;
    for (const key of ["enabled","level","action","extra_terms","allowed_terms"]) {
      const control = card.querySelector(`[data-key="${key}"]`);
      if (key === "enabled") control.checked = config[key];
      else control.value = Array.isArray(config[key]) ? config[key].join("\n") : config[key];
    }
    const slider = card.querySelector('[data-key="level"]');
    const describe = () => {card.querySelector("[data-level-output]").textContent = `${slider.value} / 5`;card.querySelector("[data-level-description]").textContent = levelDescriptions[slider.value];slider.setAttribute("aria-valuetext",`Level ${slider.value}: ${levelDescriptions[slider.value]}`);};
    slider.addEventListener("input",describe); describe(); container.append(card);
    window.espressoHateExamples.attach(card,category,title,slider);
  }
}
function renderScreening(config) {
  for (const [key,options] of [["verified_role_id",availableRoles],["entry_channel_id",availableChannels]]) {
    const select = form.elements[`screening_${key}`]; select.replaceChildren(new Option("Choose…",""));
    for (const option of options) select.add(new Option(option.name,option.id));
    if (config[key] && !options.some(option=>option.id === config[key])) select.add(new Option("Unavailable — choose another",config[key]));
  }
  for (const [key,value] of Object.entries(config)) {
    const control = form.elements[`screening_${key}`];
    if(typeof value === "boolean") control.checked = value; else control.value = value ?? "";
  }
  screeningRequirements();
  $("#screening-event-status").textContent = preview ? "Preview only. No members are screened here." : screeningEventsEnabled ? "Automatic join tracking is available. Save your policy before posting the verification button." : "Automatic join tracking is unavailable. Ask the bot operator to set ENABLE_MEMBER_SCREENING_EVENTS=true in the hosting computer's .env, enable Server Members Intent in Discord, and restart Espresso.";
}
function renderReactions(mappings) {$("#reaction-role-options").replaceChildren(); mappings.forEach(addReactionRow);}
function addReactionRow(mapping = {channel_id:availableChannels[0]?.id || "",message_id:"",emoji:"✅",role_id:"",remove_on_unreact:true}) {
  const container = $("#reaction-role-options"); if (container.children.length >= 25) {message("Use up to 25 reaction roles per server.",true); return;}
  const row = document.createElement("div"); row.className = "reaction-row"; row.dataset.reactionRow = "";
  row.innerHTML = '<div class="field-grid"><label class="stack-label">Message channel<select data-field="channel_id" required></select></label><label class="stack-label">Message ID<input data-field="message_id" type="text" inputmode="numeric" pattern="[0-9]+" maxlength="20" required></label><label class="stack-label">Reaction emoji<input data-field="emoji" type="text" maxlength="64" required></label><label class="stack-label">Role to grant<select data-field="role_id" required></select></label></div><label class="reaction-remove-label"><input type="checkbox" data-field="remove_on_unreact"> Remove the role when the reaction is removed</label><button type="button" class="text-button remove-reaction">Remove mapping</button>';
  for (const [field,options] of [["channel_id",availableChannels],["role_id",availableRoles]]) {
    const control = row.querySelector(`[data-field="${field}"]`); control.add(new Option("Choose…",""));
    options.forEach(option => control.add(new Option(option.name,option.id)));
    if (mapping[field] && !options.some(option => option.id === mapping[field])) control.add(new Option(`Unavailable (${mapping[field]})`,mapping[field]));
  }
  for (const [key,value] of Object.entries(mapping)) {const control = row.querySelector(`[data-field="${key}"]`); if (typeof value === "boolean") control.checked = value; else control.value = value;}
  row.querySelector(".remove-reaction").addEventListener("click",() => {row.remove();dirty = JSON.stringify(snapshot()) !== JSON.stringify(baseline);updateSave();});
  container.append(row);
}
$("#add-reaction-role").addEventListener("click",() => {addReactionRow();dirty = JSON.stringify(snapshot()) !== JSON.stringify(baseline);updateSave();});
(async () => {
  if (preview) {$("#preview-banner").hidden = false; $("#preview-signin").hidden = false; $("#refresh-servers").hidden = true; await loadServers();}
  else {
    try {const data = await request("/api/session"); csrf = data.csrf; $("#account-name").textContent = data.user.name; $("#logout").hidden = false; await loadServers();}
    catch (error) {message(error.message,true); $("#server-list").textContent = "Sign in again to see your servers.";}
  }
})();
