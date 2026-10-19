"use strict";
(() => {
  const host = $("#role-template-builder");
  let catalog = [], ready, generation = 0, reviewed = false, pending = null;
  const storageKey = () => `espresso-role-template:${currentServer}`;
  const status = (text, error=false) => {$("#template-status").textContent=text;$("#template-status").classList.toggle("error-text",error);};
  function remember(value) {
    pending=value;
    try {if(value)sessionStorage.setItem(storageKey(),JSON.stringify(value));else sessionStorage.removeItem(storageKey());} catch {}
    $("#retry-template").hidden=!value; $("#dismiss-template").hidden=!value;
    $("#post-template").disabled=preview || Boolean(value);
  }
  const load = () => ready ||= fetch("/assets/role-templates.json",{credentials:"same-origin"}).then(response=>{if(!response.ok)throw new Error("Templates could not load. Refresh this page.");return response.json();}).then(data=>catalog=data).catch(error=>{ready=null;throw error;});
  function invalidate() {reviewed=false;$("#template-review").hidden=true;$("#post-template").hidden=true;}
  function addOption(emoji="✨", name="", roleId=null) {
    const list=$("#template-options");
    if(list.children.length>=20){status("Use up to 20 options per message.",true);return;}
    const row=document.createElement("div");row.className="template-option";
    row.setAttribute("role","group");row.setAttribute("aria-label",name || "New role option");
    row.innerHTML='<label class="template-include"><input type="checkbox" data-option="include" checked> Include</label><div class="template-option-fields"><label>Reaction<input type="text" maxlength="64" data-option="emoji" aria-label="Reaction emoji"></label><label>Role label<input type="text" maxlength="60" data-option="name"></label><label>Role to give<select data-option="role"></select></label></div><button type="button" class="text-button">Remove option</button>';
    row.querySelector('[data-option="emoji"]').value=emoji;row.querySelector('[data-option="name"]').value=name;
    const select=row.querySelector("select");select.add(new Option("Create a basic role",""));
    availableRoles.forEach(role=>select.add(new Option(role.name,role.id)));
    select.value=roleId || "";
    row.querySelector("button").addEventListener("click",()=>{row.remove();invalidate();});list.append(row);
  }
  function renderTemplate() {
    const template=catalog.find(item=>item.id===$("#template-kind").value);if(!template)return;
    $("#template-title").value=template.title;$("#template-description").value=template.description;
    $("#template-options").replaceChildren();template.options.forEach(([emoji,name])=>addOption(emoji,name));invalidate();status("");
  }
  function choices() {
    return {version,template_id:$("#template-kind").value,channel_id:$("#template-channel").value,
      title:$("#template-title").value.trim(),description:$("#template-description").value.trim(),remove_on_unreact:$("#template-remove").checked,
      options:Array.from($("#template-options").children).filter(row=>row.querySelector('[data-option="include"]').checked).map(row=>({
        name:row.querySelector('[data-option="name"]').value.trim(),emoji:row.querySelector('[data-option="emoji"]').value.trim(),role_id:row.querySelector("select").value || null}))};
  }
  function review() {
    if(busy || pending)return;
    if(dirty){status("Save or reset your settings before reviewing a template.",true);return;}
    const data=choices(), remaining=25-(baseline?.reaction_roles.length || 0);
    if(!data.channel_id || !data.title || !data.description || !data.options.length || data.options.some(item=>!item.name || !item.emoji)){status("Choose a channel, enter a title and description, and include at least one complete option.",true);return;}
    if(data.options.length>remaining){status(`This server has room for ${remaining} more reaction roles. Uncheck options or remove old mappings.`,true);return;}
    if(new Set(data.options.map(item=>item.emoji.replaceAll("\uFE0F",""))).size!==data.options.length || new Set(data.options.map(item=>item.name.toLocaleLowerCase())).size!==data.options.length){status("Each option needs a different reaction and label.",true);return;}
    const panel=$("#template-review");panel.replaceChildren();
    const title=document.createElement("h4"), description=document.createElement("p"), list=document.createElement("ul");
    title.textContent=data.title;description.textContent=data.description;
    for(const item of data.options){const node=document.createElement("li"),role=availableRoles.find(role=>role.id===item.role_id);node.textContent=`${item.emoji}  ${item.name} → ${role ? `existing role: ${role.name}` : `create role: ${item.name}`}`;list.append(node);}
    const note=document.createElement("p");note.className="small";
    note.textContent=`Posting creates ${data.options.filter(item=>!item.role_id).length} basic roles and one embed, adds ${data.options.length} mappings, and turns reaction roles on. New roles have no extra permissions. ${data.remove_on_unreact ? "Removing a reaction removes a role granted by Espresso." : "Roles stay when reactions are removed."} Members can choose multiple roles. ${baseline?.screening.enabled ? "Members must finish verification first." : ""}`;
    panel.append(title,description,list,note);panel.hidden=false;reviewed=true;$("#post-template").hidden=false;$("#post-template").disabled=preview;status(preview ? "Preview only. Sign in to post a template in your own server." : "Review the message and roles, then add the template to your selected channel.");
  }
  async function publish(payload) {
    if(preview || busy || dirty){status("Save or reset your settings before posting.",true);return;}
    busy=true;updateSave();remember(payload);status("Posting the embed and preparing roles… Please wait.");
    Array.from(form.elements).forEach(control=>control.disabled=true);
    try {
      const data=await request(`/api/servers/${encodeURIComponent(currentServer)}/reaction-templates`,{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(payload)});
      if(data.state!=="complete"){
        status([data.error, ...(data.warnings || []), data.message_id ? `Recorded message ID: ${data.message_id}.` : "", data.created_role_ids?.length ? `Recorded new role IDs: ${data.created_role_ids.join(", ")}.` : ""].filter(Boolean).join(" "),true);
        return;
      }
      version=data.server.version;screeningEventsEnabled=Boolean(data.server.screening_events_enabled);
      renderOptions(data.server.roles,data.server.channels,data.server.settings);fill(data.server.settings);showSetup(data.server.settings);
      remember(null);invalidate();document.dispatchEvent(new Event("espresso:saved"));
      const link=$("#template-message-link");link.href=data.message_url;link.hidden=false;
      status(`Template posted. Roles and reactions are connected. ${(data.warnings || []).join(" ")}`,Boolean(data.warnings?.length));
    } catch(error){status(`${error.message} Use “Check / retry previous post” to safely check this same request before starting another.`,true);}
    finally{Array.from(form.elements).forEach(control=>control.disabled=false);busy=false;updateSave();$("#post-template").disabled=preview || Boolean(pending);}
  }
  host.addEventListener("input",invalidate);host.addEventListener("change",invalidate);
  $("#template-kind").addEventListener("change",renderTemplate);
  $("#template-add-option").addEventListener("click",()=>{addOption();invalidate();});
  $("#review-template").addEventListener("click",review);
  $("#post-template").addEventListener("click",()=>{
    if(!reviewed || busy || pending)return;
    const bytes=crypto.getRandomValues(new Uint8Array(16));
    publish({...choices(),request_id:Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join("")});
  });
  $("#retry-template").addEventListener("click",()=>{if(pending)publish(pending);});
  $("#dismiss-template").addEventListener("click",()=>{if(!busy && confirm("Check Discord for any menu or roles from this attempt first. Start a separate new template request?")){remember(null);invalidate();status("Previous request dismissed. Review your choices before posting again.");}});
  document.addEventListener("espresso:loading",()=>{generation++;host.hidden=true;invalidate();});
  document.addEventListener("espresso:unauthorized",()=>{generation++;host.hidden=true;});
  document.addEventListener("espresso:saved",()=>{
    invalidate();
    for(const select of host.querySelectorAll('[data-option="role"]')){
      const selected=select.value;select.replaceChildren(new Option("Create a basic role",""));
      availableRoles.forEach(role=>select.add(new Option(role.name,role.id)));select.value=selected;
    }
  });
  const initialize=async()=>{
    if(!canManage){host.hidden=true;return;}
    const current=++generation;host.hidden=false;status("Loading templates…");
    try{
      await load();if(current!==generation)return;
      const kinds=$("#template-kind"),channels=$("#template-channel");kinds.replaceChildren();channels.replaceChildren(new Option("Choose a channel…",""));
      catalog.forEach(item=>kinds.add(new Option(item.name,item.id)));availableChannels.forEach(channel=>channels.add(new Option(`# ${channel.name}`,channel.id)));
      renderTemplate();$("#template-message-link").hidden=true;$("#template-remove").checked=true;
      let saved=null;try{saved=JSON.parse(sessionStorage.getItem(storageKey()) || "null");}catch{}
      remember(saved);if(saved)status("A previous post has an unconfirmed result. Check / retry that request to avoid posting twice.",true);
    }catch(error){if(current===generation)status(error.message,true);}
  };
  document.addEventListener("espresso:server",initialize);
  // Preview bootstrap can finish synchronously before deferred scripts load.
  if(currentServer && !form.hidden)initialize();
})();
