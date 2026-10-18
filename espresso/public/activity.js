"use strict";
(() => {
  const node = selector => document.querySelector(selector);
  const tabs = Array.from(document.querySelectorAll("#panel-tabs [role=tab]"));
  const header = document.querySelector(".site-header");
  if(header && typeof ResizeObserver !== "undefined")new ResizeObserver(()=>document.documentElement.style.setProperty("--espresso-header-height",`${header.getBoundingClientRect().height}px`)).observe(header);
  let server = "", generation = 0, active = "settings", logCursor = null, reviewCursor = null;
  let logLoaded = false, reviewLoaded = false, logBusy = false, reviewBusy = false;
  const element = (tag,text,className) => {const item=document.createElement(tag);if(text !== undefined)item.textContent=text;if(className)item.className=className;return item;};
  const date = value => Number.isFinite(value) ? new Date(value*1000).toLocaleString() : "Unknown time";
  function status(selector,text,error=false) {const item=node(selector);item.textContent=text;item.classList.toggle("error-text",error);}
  function selectTab(name,load=true) {
    active=name;
    for(const tab of tabs) {const selected=tab.dataset.panel===name;tab.setAttribute("aria-selected",String(selected));tab.tabIndex=selected?0:-1;node(`#${tab.dataset.panel}-panel`).hidden=!selected;}
    if(load && server && name === "logs" && !logLoaded) loadLogs();
    if(load && server && name === "review" && !reviewLoaded) loadReview();
    document.dispatchEvent(new CustomEvent("espresso:tab",{detail:{name}}));
  }
  tabs.forEach(tab=>{
    tab.addEventListener("click",()=>selectTab(tab.dataset.panel));
    tab.addEventListener("keydown",event=>{
      if(!["ArrowRight","ArrowLeft","Home","End"].includes(event.key))return;
      event.preventDefault();let index=tabs.indexOf(tab);
      index=event.key==="Home"?0:event.key==="End"?tabs.length-1:(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;
      tabs[index].focus();selectTab(tabs[index].dataset.panel);
    });
  });
  function reset(id="") {
    server=id;generation++;logCursor=null;reviewCursor=null;logLoaded=false;reviewLoaded=false;logBusy=false;reviewBusy=false;
    node("#activity-list").replaceChildren();node("#review-list").replaceChildren();
    node("#older-logs").hidden=true;node("#more-review").hidden=true;
    node("#refresh-logs").disabled=false;node("#refresh-review").disabled=false;
    node("#publish-verification").disabled=preview;
    status("#log-message","");status("#review-message","");selectTab("settings",false);
  }
  function appendLog(log) {
    const card=element("article",undefined,"activity-card"),header=element("header");
    const time=element("time",date(log.created_at));time.dateTime=new Date(log.created_at*1000).toISOString();
    header.append(element("span",log.kind,"activity-kind"),time);
    card.append(header,element("h3",log.title),element("p",log.details));node("#activity-list").append(card);
  }
  async function loadLogs(older=false) {
    if(logBusy || !server)return;logBusy=true;
    const token=generation,selectedKind=node("#log-kind").value;node("#refresh-logs").disabled=true;node("#older-logs").disabled=true;
    status("#log-message","Loading activity…");
    try {
      const params=new URLSearchParams({kind:node("#log-kind").value});if(older && logCursor)params.set("before",logCursor);
      const data=preview?{logs:[{kind:"moderation",title:"Example: automatic filter",details:"Example action metadata. Live entries appear after you connect your server.",created_at:Date.now()/1000}],next_cursor:null}:await request(`/api/servers/${encodeURIComponent(server)}/logs?${params}`);
      if(token!==generation || selectedKind!==node("#log-kind").value)return;
      if(!older)node("#activity-list").replaceChildren();data.logs.forEach(appendLog);logCursor=data.next_cursor;logLoaded=true;
      node("#older-logs").hidden=!logCursor;
      status("#log-message",node("#activity-list").children.length?"Activity is up to date. Refresh for the latest entries.":"No activity yet. New bot actions will appear here; earlier Discord messages are not imported.");
    } catch(error) {if(token===generation)status("#log-message",`${error.message} Use Refresh log to retry.`,true);}
    finally {if(token===generation){logBusy=false;node("#refresh-logs").disabled=false;node("#older-logs").disabled=false;if(selectedKind!==node("#log-kind").value)loadLogs();}}
  }
  function appendCase(item) {
    const card=element("article",undefined,"activity-card"),header=element("header");
    header.append(element("span",item.status.replaceAll("_"," "),"activity-kind"),element("time",`Joined ${date(item.joined_at)}`));
    const age=Math.max(0,Math.floor((item.joined_at-item.account_created_at)/86400));
    const agreementCurrent=item.agreement_current ?? Boolean(item.agreed_at);
    if(age < (baseline?.screening?.minimum_account_age_days || 7))header.append(element("span","New account","activity-kind"));
    const profile=element("a","Open Discord profile ↗","text-link");profile.href=`https://discord.com/users/${encodeURIComponent(item.user_id)}`;profile.target="_blank";profile.rel="noopener noreferrer";
    card.append(header,element("h3","Member access request"),element("p",`Member ID: ${item.user_id}`,"member-number"),profile,element("p",`Account age at join: ${age} day(s)\nRules agreement: ${item.agreed_at?date(item.agreed_at):"Not recorded"}${item.agreed_at && !agreementCurrent?" · rules changed; agreement needed again":""}\nStaff review: ${item.requires_review?"Required":"Account-age checks"}`));
    const actions=element("div",undefined,"review-actions");
    for(const [action,label] of item.status==="denied"?[["reopen","Reopen request"]]:[["approve","Approve access"],["deny","Decline access"]]) {
      const button=element("button",label,`button button-small${action==="approve"?" button-primary":""}`);button.type="button";
      button.disabled=preview || !baseline?.screening?.enabled || (action==="approve" && !agreementCurrent && baseline.screening.require_agreement);
      button.addEventListener("click",()=>review(item,action,card));actions.append(button);
    }
    card.append(actions);node("#review-list").append(card);
  }
  async function loadReview(more=false) {
    if(reviewBusy || !server)return;reviewBusy=true;
    const token=generation;node("#refresh-review").disabled=true;node("#more-review").disabled=true;
    status("#review-message","Loading verification requests…");
    try {
      const query=more && reviewCursor?`?after=${encodeURIComponent(reviewCursor)}`:"";
      const data=preview?{cases:[{user_id:"123456789012345678",status:"pending",joined_at:Date.now()/1000,account_created_at:Date.now()/1000-86400,agreed_at:Date.now()/1000,requires_review:true}],next_cursor:null}:await request(`/api/servers/${encodeURIComponent(server)}/screening${query}`);
      if(token!==generation)return;
      if(!more)node("#review-list").replaceChildren();data.cases.forEach(appendCase);reviewCursor=data.next_cursor;reviewLoaded=true;
      node("#more-review").hidden=!reviewCursor;
      status("#review-message",node("#review-list").children.length?"Each approval rechecks permissions and the current rules. Declining keeps access withheld.":"No requests are waiting. New joins and members using /verify appear here when screening is enabled.");
    } catch(error) {if(token===generation)status("#review-message",`${error.message} Use Refresh requests to retry.`,true);}
    finally {if(token===generation){reviewBusy=false;node("#refresh-review").disabled=false;node("#more-review").disabled=false;}}
  }
  async function review(item,action,card) {
    if(preview || !server)return;
    const token=generation,controls=Array.from(card.querySelectorAll("button")).filter(button=>!button.disabled);controls.forEach(button=>button.disabled=true);
    try {
      const data=await request(`/api/servers/${encodeURIComponent(server)}/screening/${encodeURIComponent(item.user_id)}`,{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify({action,version:item.version})});
      if(token!==generation)return;
      await loadReview();status("#review-message",`Request updated: ${data.status.replaceAll("_"," ")}.`);logLoaded=false;
    } catch(error) {if(token===generation){status("#review-message",error.message,true);controls.forEach(button=>button.disabled=false);}}
  }
  node("#publish-verification").addEventListener("click",async()=>{
    if(preview || !server)return;
    if(dirty){status("#review-message","Save your settings before posting the verification button.",true);return;}
    const token=generation;node("#publish-verification").disabled=true;
    try {
      await request(`/api/servers/${encodeURIComponent(server)}/screening/publish`,{method:"POST",headers:{"X-CSRF-Token":csrf}});
      if(token===generation){status("#review-message","Verification button posted in the saved entry channel.");logLoaded=false;}
    } catch(error){if(token===generation)status("#review-message",error.message,true);}
    finally{if(token===generation)node("#publish-verification").disabled=false;}
  });
  node("#refresh-logs").addEventListener("click",()=>loadLogs());node("#older-logs").addEventListener("click",()=>loadLogs(true));
  node("#log-kind").addEventListener("change",()=>{logLoaded=false;loadLogs();});
  node("#refresh-review").addEventListener("click",()=>loadReview());node("#more-review").addEventListener("click",()=>loadReview(true));
  document.addEventListener("espresso:server",event=>reset(event.detail.id));
  document.addEventListener("espresso:loading",()=>reset());
  document.addEventListener("espresso:unauthorized",()=>{reset();node("#panel-tabs").hidden=true;});
  document.addEventListener("espresso:saved",()=>{logLoaded=false;reviewLoaded=false;});
  if(currentServer)reset(currentServer);
})();
