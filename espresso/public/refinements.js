"use strict";
window.espressoRefinements = (() => {
  const $r=selector=>document.querySelector(selector);
  const make=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const date=value=>new Date(value*1000).toLocaleString();
  let server="",generation=0,historyCursor=null,overviewLoaded=false,historyLoaded=false,calendarMonth=new Date(),menuRequest=crypto.randomUUID();
  const pending=new Set();
  const url=path=>`/api/servers/${encodeURIComponent(server)}/${path}`;
  const write=(path,body)=>request(url(path),{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(body)});
  function status(where,text,error=false){const item=$r(where);item.textContent=text;item.classList.toggle("error-text",error);}
  function card(title,text){const item=make("article",undefined,"activity-card");item.append(make("h3",title));if(text)item.append(make("p",text));return item;}
  async function run(key,where,fn,button,mutation=false){
    if(!server || pending.has(key) || mutation && (preview || !canManage))return;
    const token=generation;pending.add(key);if(button)button.disabled=true;
    try{await fn(token);}catch(error){if(token===generation)status(where,error.message,true);}
    finally{if(token===generation){pending.delete(key);if(button)button.disabled=mutation && preview;}}
  }
  function button(text,fn,mutation=true){const item=make("button",text,"button button-small");item.type="button";item.disabled=mutation && preview;item.addEventListener("click",()=>fn(item));return item;}
  function linkToTab(name,text){return button(text,()=>{$r(`#tab-${name}`).click();$r(`#${name}-panel`).scrollIntoView({block:"start"});},false);}
  function options(select,items,empty){const value=select.value;select.replaceChildren();if(empty!==null)select.add(new Option(empty,""));items.forEach(item=>select.add(new Option(item.name,item.id)));if(items.some(item=>item.id===value))select.value=value;}
  async function overview(){await run("overview","#overview-message",async token=>{
    const result=preview?{access:{manage:true,moderate:true},reports:{total:2,overdue:1,oldest:Date.now()/1000-172800},waiting:[],verification_waiting:1,post_issues:[],health_issues:[],protection:[],top_rules:[{reason:"Message spam",total:3}]}:await request(url("overview"));
    if(token!==generation)return;
    $r("#overview-metrics").replaceChildren();$r("#overview-actions").replaceChildren();
    const metrics=[];
    if(result.reports)metrics.push([result.reports.total,"Waiting reports & appeals"],[result.reports.overdue,"Waiting over 48 hours"]);
    if(result.access.manage)metrics.push([result.verification_waiting||0,"Verification requests"],[(result.post_issues||[]).length,"Delivery issues"],[(result.health_issues||[]).length,"Setup issues"]);
    metrics.forEach(([value,title])=>{const item=card(title,String(value));item.classList.add("metric-card");$r("#overview-metrics").append(item);});
    if(result.access.moderate){const item=card("Staff review",result.reports?.oldest?`Oldest waiting item: ${date(result.reports.oldest)}`:"No reports are waiting.");item.append(linkToTab("desk","Open staff desk"));$r("#overview-actions").append(item);}
    if(result.access.manage){
      for(const issue of result.post_issues||[]){const item=card(`Post #${issue.id} · ${issue.status}`,issue.outcome);item.append(linkToTab("tools","Review delivery"));$r("#overview-actions").append(item);}
      for(const issue of result.health_issues||[]){const item=card(issue.name,issue.detail);item.append(linkToTab("tools","Check server health"));$r("#overview-actions").append(item);}
      if(result.verification_waiting){const item=card("Member verification",`${result.verification_waiting} requests need attention.`);item.append(linkToTab("review","Open member review"));$r("#overview-actions").append(item);}
    }
    if(result.top_rules?.length)$r("#overview-actions").append(card("Filter activity · last 7 days",result.top_rules.map(item=>`${item.reason}: ${item.total} warnings`).join("\n")));
    const banner=$r("#active-protection-banner");banner.replaceChildren();banner.hidden=!(result.protection||[]).length;
    for(const item of result.protection||[]){const line=make("p",`${item.action} · channel ${item.channel_id} · `),timer=make("span");timer.dataset.restoreAt=item.restore_at;line.append(timer);banner.append(line);}if(!banner.hidden)banner.append(linkToTab("tools","Manage temporary protection"));
    updateCountdowns();overviewLoaded=true;status("#overview-message",preview?"Example overview. Connect your server to see current activity.":"Overview is up to date.");
  },$r("#refresh-overview"));}
  async function loadHistory(older=false){await run("history","#history-message",async token=>{
    const result=preview?{versions:[{version:1,actor_id:null,created_at:Date.now()/1000,changed_keys:["enabled","raid"]}],next_cursor:null}:await request(url(`settings-history${older && historyCursor!==null?`?before=${historyCursor}`:""}`));
    if(token!==generation)return;if(!older)$r("#history-list").replaceChildren();
    result.versions.forEach(row=>{const item=card(`Recipe version ${row.version}`,`${row.created_at?date(row.created_at):"Imported current configuration"} · ${row.actor_id?`changed by ${row.actor_id}`:"actor unavailable"}\n${row.changed_keys.join(", ") || "Initial snapshot"}`);item.append(button("Preview restore",b=>previewHistory(row.version,b),false));$r("#history-list").append(item);});
    historyCursor=result.next_cursor;$r("#more-history").hidden=historyCursor===null;historyLoaded=true;status("#history-message",preview?"Example history. Restoring is disabled in preview.":"Choose a version to inspect the exact changes before restoring.");
  },$r("#refresh-history"));}
  async function previewHistory(target,b){await run("history-preview","#history-message",async token=>{
    if(preview){status("#history-message","Connect your server to inspect real version differences.");return;}
    const result=await request(url(`settings-history/${target}`));if(token!==generation)return;
    const host=$r("#history-preview");host.replaceChildren(make("h3",`Restore preview · version ${target}`));
    result.diff.forEach(item=>{const block=card(item.field.replaceAll("_"," "),"");block.append(make("h4","Current"),make("pre",JSON.stringify(item.before,null,2)),make("h4","Earlier recipe"),make("pre",JSON.stringify(item.after,null,2)));host.append(block);});
    if(!result.diff.length){host.append(make("p","This version matches the current recipe."));return;}
    host.append(button("Restore this reviewed recipe",restore=>run("restore","#history-message",async restoreToken=>{
      if(dirty)throw new Error("Save or reset your unsaved form before restoring another recipe.");
      const saved=await request(url("settings"),{method:"PATCH",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify({version:result.version,changes:result.changes})});
      if(restoreToken!==generation)return;version=saved.version;fill(saved.settings);document.dispatchEvent(new Event("espresso:saved"));host.replaceChildren();await loadHistory();status("#history-message",`Version ${target} restored as new version ${saved.version}.`);
    },restore,true)));host.scrollIntoView({block:"start"});
  },b);}
  function simulationBody(form){
    const context={role_ids:Array.from(form.elements.role_ids.selectedOptions,item=>item.value),active_warnings:Number(form.elements.active_warnings.value),recent_messages:Number(form.elements.recent_messages.value),warning_cooldown:form.elements.warning_cooldown.checked,edited:form.elements.edited.checked};
    const result={text:form.elements.text.value,channel_id:form.elements.channel_id.value||null,member_id:form.elements.member_id.value.trim()||null,context};
    if(form.elements.recipe.value==="unsaved")result.settings=snapshot();return result;
  }
  function scheduleBody(form){
    return {local_start:form.elements.when.value,calendar:{timezone:form.elements.timezone.value.trim(),recurrence:form.elements.recurrence.value,
      weekdays:Array.from(form.querySelectorAll('[name=weekday]:checked'),item=>Number(item.value)),local_time:form.elements.when.value.slice(11,16)}};
  }
  function localInput(timestamp,zone){try{return new Intl.DateTimeFormat("sv-SE",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(timestamp*1000)).replace(" ","T");}catch{return "";}}
  function inputField(form,labelText,name,type,value){const label=make("label",labelText),input=make(type==="textarea"?"textarea":"input");input.name=name;if(type!=="textarea")input.type=type;input.value=value??"";label.append(input);form.append(label);return input;}
  function enhancePost(article,item,refresh){
    article.append(make("p",`${item.timezone||"UTC"} · ${item.recurrence||"interval"} · version ${item.version||1}`));
    const currentVersion=item.version||1,actions=make("div",undefined,"review-actions");
    async function change(kind,changes,acknowledge,b){await run(`post-change:${item.id}`,"#tools-message",async token=>{
      const result=await write(`posts/${item.id}/action`,{version:currentVersion,action:kind,...(changes?{changes}:{}),acknowledge});if(token!==generation)return;await refresh();status("#tools-message",`Post #${item.id} is ${result.status}.`);await loadCalendar();
    },b,true);}
    if(item.status==="pending")actions.append(button("Pause post",b=>change("pause",null,false,b)));
    if(item.status==="paused")actions.append(button("Cancel post",b=>change("cancel",null,false,b)));
    if(["pending","paused","failed","uncertain"].includes(item.status)){
      const details=make("details"),summary=make("summary",["failed","uncertain"].includes(item.status)?"Review and schedule another attempt":"Edit post or resume schedule"),edit=make("form");edit.className="post-editor";
      const channelLabel=make("label","Channel"),channel=make("select");channel.name="channel_id";options(channel,availableChannels,null);channel.value=item.channel_id;channelLabel.append(channel);edit.append(channelLabel);
      inputField(edit,"Message","content","textarea",item.content).maxLength=1800;
      inputField(edit,"Timezone","timezone","text",item.timezone||"UTC");
      inputField(edit,"Next local date/time","when","datetime-local",localInput(Math.max(item.due_at,Date.now()/1000+3600),item.timezone||"UTC"));
      const repeatLabel=make("label","Repeat"),repeat=make("select");repeat.name="recurrence";for(const [value,label]of [["once","Send once"],["daily","Daily"],["weekly","Selected weekdays"],["interval","Fixed interval"]])repeat.add(new Option(label,value));repeat.value=item.recurrence||"interval";repeatLabel.append(repeat);edit.append(repeatLabel);
      const days=typeof item.weekdays==="string"?JSON.parse(item.weekdays):item.weekdays||[];
      const weekdays=make("div",undefined,"weekday-picker");["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].forEach((day,index)=>{const label=make("label"),box=make("input");box.type="checkbox";box.name="weekday";box.value=index;box.checked=days.includes(index);label.append(box,document.createTextNode(day));weekdays.append(label);});edit.append(weekdays);
      const interval=inputField(edit,"Fixed interval days","repeat_days","number",item.repeat_days);interval.min=0;interval.max=365;
      const notifyLabel=make("label"),notify=make("input");notify.type="checkbox";notify.name="notify";notify.checked=Boolean(item.notify);notifyLabel.append(notify,document.createTextNode(" Notify configured role"));edit.append(notifyLabel);
      const ackLabel=make("label"),ack=make("input");ack.type="checkbox";ackLabel.append(ack,document.createTextNode(" I checked Discord and want a new delivery attempt"));if(item.status==="uncertain"){ack.required=true;edit.append(ackLabel);}
      const submit=make("button",item.status==="paused"?"Save edits to paused post":["failed","uncertain"].includes(item.status)?"Schedule another attempt":"Save post edits","button button-small");submit.type="submit";submit.disabled=preview;edit.append(submit);
      const fields=()=>({channel_id:channel.value,content:edit.elements.content.value,repeat_days:Number(interval.value),notify:notify.checked,due_at:new Date(edit.elements.when.value).getTime()/1000,...scheduleBody(edit)});
      edit.addEventListener("submit",event=>{event.preventDefault();change(["failed","uncertain"].includes(item.status)?"retry":"edit",fields(),ack.checked,submit);});
      if(item.status==="paused")edit.append(button("Resume post",b=>change("resume",fields(),false,b)));
      details.append(summary,edit);article.append(details);
    }
    article.append(actions);
  }
  function updateCountdowns(){document.querySelectorAll("[data-restore-at]").forEach(item=>{const seconds=Math.ceil(Number(item.dataset.restoreAt)-Date.now()/1000);item.textContent=seconds>0?`${Math.floor(seconds/60)}m ${seconds%60}s remaining`:"Awaiting restoration";});}
  function renderProtection(items,host,refresh){host.replaceChildren();items.forEach(item=>{
    const article=card(`Temporary ${item.action} · channel ${item.channel_id}`,item.error||"Prior settings will be restored when protection ends."),timer=make("p");timer.dataset.restoreAt=item.restore_at;article.append(timer);
    const actions=make("div",undefined,"review-actions");for(const [action,label]of [["extend","Extend 10 minutes"],["end","End this protection"]])actions.append(button(label,b=>run(`protection:${item.channel_id}`,"#tools-message",async token=>{const result=await write(`protection/${item.channel_id}`,{action,minutes:10});if(token!==generation)return;await refresh();overviewLoaded=false;status("#tools-message",result.restored===false?"Restoration needs a permission fix; recovery remains queued.":"Temporary protection updated.");},b,true)));article.append(actions);host.append(article);
  });updateCountdowns();}
  async function loadCalendar(){await run("calendar","#calendar-message",async token=>{
    const start=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth(),1),end=new Date(start.getFullYear(),start.getMonth()+1,1);
    const result=preview?{events:[]}:await request(url(`calendar?start=${start.getTime()/1000}&end=${end.getTime()/1000}`));if(token!==generation)return;
    $r("#calendar-month").textContent=start.toLocaleDateString(undefined,{month:"long",year:"numeric"});$r("#calendar-zone").textContent=`Calendar shown in ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Each post retains its own schedule timezone.`;
    const host=$r("#post-calendar");host.replaceChildren();["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].forEach(day=>host.append(make("strong",day,"calendar-weekday")));
    for(let index=0;index<(start.getDay()+6)%7;index++)host.append(make("div","","calendar-empty"));
    for(let day=1;day<=new Date(start.getFullYear(),start.getMonth()+1,0).getDate();day++){
      const cell=make("div",undefined,"calendar-day");cell.append(make("strong",day));
      result.events.filter(event=>{const when=new Date(event.due_at*1000);return when.getDate()===day&&when.getMonth()===start.getMonth()&&when.getFullYear()===start.getFullYear();}).forEach(event=>{const entry=make("p",`#${event.id} ${new Date(event.due_at*1000).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})} ${event.content}`);entry.title=`${event.status} · ${event.timezone}`;cell.append(entry);});host.append(cell);
    }
    status("#calendar-message",result.limit_reached?"Showing the first 1,000 occurrences. Narrow the schedule to inspect more.":preview?"Example calendar. Your scheduled posts appear here when connected.":`${result.events.length} scheduled occurrence(s) this month.`);
  });}
  function menuChoices(){const form=$r("#role-menu-form");return {title:form.elements.title.value.trim(),description:form.elements.description.value.trim(),style:form.elements.style.value,min_values:Number(form.elements.min_values.value),max_values:Number(form.elements.max_values.value),options:Array.from($r("#menu-options").children,row=>({role_id:row.querySelector("select").value,label:row.querySelector("[data-menu-label]").value.trim(),description:row.querySelector("[data-menu-description]").value.trim()}))};}
  function menuPreview(){const menu=menuChoices(),host=$r("#role-menu-preview");host.replaceChildren(make("h3",menu.title||"Role menu preview"),make("p",menu.description),make("p",`Choose ${menu.min_values}–${menu.max_values} roles using ${menu.style}.`));menu.options.forEach(option=>host.append(make("p",`${option.label||"Untitled option"}${option.description?` — ${option.description}`:""}`)));}
  function addMenuOption(){if($r("#menu-options").children.length>=25){status("#menu-message","Use up to 25 menu options.",true);return;}
    const row=make("div",undefined,"menu-option"),roleLabel=make("label","Role"),role=make("select");role.required=true;options(role,availableRoles,"Choose a role…");roleLabel.append(role);row.append(roleLabel);
    const label=inputField(row,"Button / option label","label","text","");label.dataset.menuLabel="";label.maxLength=80;label.required=true;
    const description=inputField(row,"Option description","description","text","");description.dataset.menuDescription="";description.maxLength=100;
    row.append(button("Remove option",()=>{row.remove();menuRequest=crypto.randomUUID();menuPreview();},false));$r("#menu-options").append(row);menuPreview();
  }
  async function loadMenus(){await run("menus","#menu-message",async token=>{
    const result=preview?{menus:[]}:await request(url("role-menus"));if(token!==generation)return;$r("#role-menu-list").replaceChildren();
    result.menus.forEach(row=>{const item=card(`${row.data.title} · ${row.status}`,`Menu #${row.id} · channel ${row.channel_id}`);if(row.status==="uncertain")item.append(make("p","Check Discord for a message before publishing a replacement. Disable this request to retire any uncertain controls."));if(row.status!=="disabled")item.append(button("Disable menu",b=>run(`menu:${row.id}`,"#menu-message",async token=>{const result=await write(`role-menus/${row.id}/disable`,{});if(token!==generation)return;await loadMenus();status("#menu-message",result.message);},b,true)));$r("#role-menu-list").append(item);});
  });}
  function coffeePreview(){if(!canManage)return;const values=snapshot().community,host=$r("#coffee-card-preview");const colors={classic:"#d8a7b1",pastel:"#c9b5eb",midnight:"#41314d",custom:/^[a-f0-9]{6}$/i.test(values.coffee_color)?"#"+values.coffee_color:"#d8a7b1"};host.style.setProperty("--coffee-color",colors[values.coffee_style]);host.replaceChildren(make("h3",values.coffee_title),make("p",values.coffee_message.replaceAll("{sender}","You").replaceAll("{member}","a friend").replaceAll("{drink}","a caramel latte")));}
  function reset(id=""){
    server=id;generation++;pending.clear();historyCursor=null;overviewLoaded=false;historyLoaded=false;menuRequest=crypto.randomUUID();calendarMonth=new Date();
    for(const selector of ["#overview-metrics","#overview-actions","#history-list","#history-preview","#post-calendar","#role-menu-list","#menu-options"])$r(selector).replaceChildren();
    for(const selector of ["#overview-message","#history-message","#calendar-message","#menu-message"])status(selector,"");$r("#active-protection-banner").hidden=true;$r("#more-history").hidden=true;
    if(!id)return;
    if(canManage){options($r('#filter-test [name=role_ids]'),availableRoles,null);options($r('#role-menu-form [name=channel_id]'),availableChannels,"Choose a channel…");addMenuOption();coffeePreview();}
    $r('#role-menu-form button[type=submit]').disabled=preview;
    $r('#schedule-post [name=timezone]').value=Intl.DateTimeFormat().resolvedOptions().timeZone;
    if(!canManage)overview();
  }
  $r("#refresh-overview").addEventListener("click",overview);$r("#refresh-history").addEventListener("click",()=>loadHistory());$r("#more-history").addEventListener("click",()=>loadHistory(true));
  $r("#calendar-prev").addEventListener("click",()=>{calendarMonth=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()-1,1);loadCalendar();});$r("#calendar-next").addEventListener("click",()=>{calendarMonth=new Date(calendarMonth.getFullYear(),calendarMonth.getMonth()+1,1);loadCalendar();});
  $r("#add-menu-option").addEventListener("click",addMenuOption);$r("#role-menu-form").addEventListener("input",()=>{menuRequest=crypto.randomUUID();menuPreview();});$r("#role-menu-form").addEventListener("change",()=>{menuRequest=crypto.randomUUID();menuPreview();});
  $r("#role-menu-form").addEventListener("submit",event=>{event.preventDefault();run("publish-menu","#menu-message",async token=>{const result=await write("role-menus",{channel_id:event.currentTarget.elements.channel_id.value,menu:menuChoices(),request_id:menuRequest});if(token!==generation)return;await loadMenus();status("#menu-message",result.status==="active"?`Role menu #${result.id} is ready in Discord.`:"Posting is uncertain. Check the channel and retire this request before publishing another copy.");},event.currentTarget.querySelector('button[type=submit]'),true);});
  document.querySelectorAll('#settings-form [name^="community_coffee_"]').forEach(item=>item.addEventListener("input",coffeePreview));
  document.addEventListener("espresso:server",event=>reset(event.detail.id));document.addEventListener("espresso:loading",()=>reset());document.addEventListener("espresso:unauthorized",()=>reset());
  document.addEventListener("espresso:saved",()=>{overviewLoaded=false;historyLoaded=false;coffeePreview();});
  document.addEventListener("espresso:tab",event=>{if(!server)return;const name=event.detail.name;if(name==="overview"&&!overviewLoaded)overview();if(name==="history"&&!historyLoaded)loadHistory();if(name==="tools"&&canManage){loadCalendar();loadMenus();}});
  setInterval(()=>{if(!document.hidden)updateCountdowns();},1000);
  if(currentServer)reset(currentServer);
  return {simulationBody,scheduleBody,enhancePost,renderProtection};
})();
