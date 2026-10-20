"use strict";
(() => {
  const node=selector=>document.querySelector(selector);
  const make=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const date=value=>new Date(value*1000).toLocaleString();
  let server="",generation=0,inboxCursor=null,caseCursor=null,deskLoaded=false,toolsLoaded=false;
  let postRequestId=crypto.randomUUID(),caseUser="",testRevision=0;
  const busy=new Set();
  function status(where,text,error=false) {node(where).textContent=text;node(where).classList.toggle("error-text",error);}
  const endpoint=path=>`/api/servers/${encodeURIComponent(server)}/${path}`;
  const write=(path,body)=>request(endpoint(path),{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(body)});
  async function action(key,where,fn,element) {
    if(busy.has(key) || !server)return;
    const token=generation;busy.add(key);if(element)element.disabled=true;
    try {await fn(token);}catch(error) {if(token===generation)status(where,error.message,true);}
    finally {if(token===generation){busy.delete(key);if(element)element.disabled=preview && element.dataset.mutation==="true";}}
  }
  function button(label,fn,mutation=true) {
    const item=make("button",label,"button button-small");item.type="button";item.dataset.mutation=String(mutation);item.disabled=preview && mutation;item.addEventListener("click",()=>fn(item));return item;
  }
  function card(title,body) {const item=make("article",undefined,"activity-card");item.append(make("h3",title),make("p",body));return item;}
  function selectChannels(select,empty) {
    const previous=select.value;select.replaceChildren(new Option(empty,""));availableChannels.forEach(channel=>select.add(new Option(`# ${channel.name}`,channel.id)));select.value=previous;
  }
  function reset(id="") {
    server=id;generation++;busy.clear();inboxCursor=null;caseCursor=null;deskLoaded=false;toolsLoaded=false;caseUser="";postRequestId=crypto.randomUUID();
    for(const selector of ["#inbox-list","#case-list","#health-list","#post-list","#protection-list"])node(selector).replaceChildren();
    for(const selector of ["#desk-message","#tools-message","#filter-result","#schedule-result","#filter-test-context"])status(selector,"");
    node("#filter-result").removeAttribute("data-state");delete node("#filter-result").dataset.stale;testRevision++;
    node("#more-inbox").hidden=true;node("#more-cases").hidden=true;node("#end-protection").hidden=true;
    node("#case-search").reset();node("#filter-test").reset();node("#schedule-post").reset();
    selectChannels(node("#filter-test select"),"Server defaults");selectChannels(node("#schedule-post select"),"Choose a channel…");
    const local=new Date(Date.now()+3600000);local.setMinutes(local.getMinutes()-local.getTimezoneOffset());node("#schedule-post [name=when]").value=local.toISOString().slice(0,16);
    node("#schedule-zone").textContent=Intl.DateTimeFormat().resolvedOptions().timeZone;
    document.querySelectorAll("#tools-panel button[type=submit]").forEach(item=>item.disabled=preview && item.closest("#schedule-post")!==null);
    for(const selector of ["#refresh-desk","#refresh-tools","#check-health"])node(selector).disabled=false;
  }
  async function loadInbox(older=false) {
    const token=generation,statusFilter=node("#inbox-status").value;
    const params=new URLSearchParams({status:statusFilter});if(older && inboxCursor)params.set("before",inboxCursor);
    const result=preview?{items:[{id:"1",kind:"report",author_id:"123456789012345678",target_id:"234567890123456789",status:"open",version:1,body:"Example report: staff can review a private concern here.",created_at:Date.now()/1000,message_url:"",case_id:null,assigned_id:null,resolution:""}],next_cursor:null}:await request(endpoint(`inbox?${params}`));
    if(token!==generation || statusFilter!==node("#inbox-status").value)return;
    if(!older)node("#inbox-list").replaceChildren();
    result.items.forEach(renderInbox);inboxCursor=result.next_cursor;node("#more-inbox").hidden=!inboxCursor;
    if(!node("#inbox-list").children.length)node("#inbox-list").append(make("p","No reports or appeals in this view.","field-note"));
  }
  function renderInbox(item) {
    const article=card(`${item.kind==="appeal"?"Appeal":"Report"} #${item.id} · ${item.status.replaceAll("_"," ")}`,item.body);
    article.prepend(make("p",`${date(item.created_at)} · submitted by ${item.author_id}${item.target_id?` · about ${item.target_id}`:""}${item.case_id?` · case #${item.case_id}`:""}`,"member-number"));
    article.append(make("p",item.assigned_id?`Assigned to ${item.assigned_id}`:"Unassigned"));
    if(item.message_url && /^https:\/\/discord\.com\/channels\/\d+\/\d+\/\d+$/.test(item.message_url)) {const link=make("a","Open reported message ↗","text-link");link.href=item.message_url;link.target="_blank";link.rel="noopener noreferrer";article.append(link);}
    if(item.resolution)article.append(make("p",`Resolution: ${item.resolution}`));
    if(Date.now()/1000-item.created_at>48*3600 && ["open","in_review"].includes(item.status))article.append(make("p","Waiting more than 48 hours","error-text"));
    if(item.public_response)article.append(make("p",`Member-facing response: ${item.public_response}`));
    if(item.notice_status && item.notice_status!=="not_requested")article.append(make("p",`Private outcome notice: ${item.notice_status.replaceAll("_"," ")}. Members can also use /myreports.`));
    const label=make("label","Internal staff note (staff only)"),note=make("textarea");note.className="inbox-note";note.maxLength=1000;note.rows=3;label.append(note);article.append(label);
    const responseLabel=make("label","Member-facing outcome (optional)"),response=make("textarea");response.maxLength=1000;response.rows=2;responseLabel.append(response);
    const notifyLabel=make("label",""),notify=make("input");notify.type="checkbox";notifyLabel.append(notify,document.createTextNode(" Send this outcome privately to the submitter when closing the item"));article.append(responseLabel,notifyLabel);
    const actions=make("div",undefined,"review-actions"),closed=["resolved","rejected"].includes(item.status);
    for(const [kind,text] of closed?[["reopen","Reopen"]]:[["claim","Assign to me"],["resolve","Resolve"],["reject","Reject"]])actions.append(button(text,itemButton=>action(`inbox:${item.id}`,"#desk-message",async token=>{
      if(["resolve","reject"].includes(kind) && !note.value.trim())throw new Error("Add a resolution note first.");
      const closing=["resolve","reject"].includes(kind);
      const result=await write(`inbox/${encodeURIComponent(item.id)}`,{action:kind,version:item.version,note:note.value.trim(),public_response:closing?response.value.trim():"",notify:closing && notify.checked});
      if(token!==generation)return;await loadInbox();status("#desk-message",`Item #${item.id} is ${result.status.replaceAll("_"," ")}.`);
    },itemButton)));
    const history=make("div",undefined,"inbox-history");
    actions.append(button("Review history",itemButton=>action(`history:${item.id}`,"#desk-message",async token=>{
      const result=preview?{history:[]}:await request(endpoint(`inbox/${encodeURIComponent(item.id)}/history`));if(token!==generation)return;
      history.replaceChildren();result.history.forEach(row=>history.append(make("p",`${date(row.created_at)} · ${row.action} · staff ${row.actor_id}\n${row.note}`)));if(!result.history.length)history.textContent="No staff decisions recorded yet.";
    },itemButton),false));
    article.append(actions,history);node("#inbox-list").append(article);
  }
  async function loadCases(older=false) {
    const token=generation,user=caseUser,params=new URLSearchParams();if(user)params.set("user_id",user);if(older && caseCursor)params.set("before",caseCursor);
    const result=preview?{cases:[{id:"1",kind:"warning",user_id:"234567890123456789",actor_id:"345678901234567890",reason:"Example warning",outcome:"Warning recorded",warning_id:"1",created_at:Date.now()/1000,voided_at:null}],next_cursor:null}:await request(endpoint(`cases?${params}`));
    if(token!==generation || user!==caseUser)return;if(!older)node("#case-list").replaceChildren();
    result.cases.forEach(item=>{
      const article=card(`Case #${item.id} · ${item.kind.replaceAll("_"," ")}`,`${item.reason}\n${item.outcome}`);
      article.prepend(make("p",`${date(item.created_at)} · member ${item.user_id || "server"} · staff ${item.actor_id}`,"member-number"));
      if(item.expired && !item.voided_at)article.append(make("p",`Warning expired ${date(item.expires_at)} and no longer counts toward escalation.`));
      if(item.voided_at)article.append(make("p",`Warning removed ${date(item.voided_at)}. Audit history retained.`));
      else if(item.warning_id) {
        const details=make("details"),summary=make("summary",`Remove warning #${item.warning_id}`),form=make("form"),label=make("label","Reason for removing this warning"),input=make("textarea"),submit=make("button","Remove warning","button button-small");
        input.required=true;input.maxLength=1000;input.rows=2;submit.type="submit";submit.disabled=preview;label.append(input);form.append(label,submit);details.append(summary,form);article.append(details);
        form.addEventListener("submit",event=>{event.preventDefault();if(preview)return;action(`warning:${item.warning_id}`,"#desk-message",async token=>{await write(`warnings/${encodeURIComponent(item.warning_id)}/remove`,{reason:input.value.trim()});if(token!==generation)return;await loadCases();status("#desk-message","Warning removed from active history. Existing timeout is unchanged.");},submit);});
      }
      node("#case-list").append(article);
    });caseCursor=result.next_cursor;node("#more-cases").hidden=!caseCursor;
    if(!node("#case-list").children.length)node("#case-list").append(make("p","No matching cases.","field-note"));
  }
  async function loadDesk() {
    await action("desk","#desk-message",async token=>{status("#desk-message","Loading the staff desk…");await loadInbox();await loadCases();if(token===generation){deskLoaded=true;status("#desk-message",preview?"Example data. Sign in to review your server's reports and cases.":"The staff desk is up to date.");}},node("#refresh-desk"));
  }
  async function loadHealth() {
    const token=generation;
    const result=preview?{checks:[{name:"Example: message moderation",ok:true,detail:"Ready"},{name:"Example: verified role placement",ok:false,detail:"Move Espresso's role above the Verified role."}],protection:[]}:await request(endpoint("health"));
    if(token!==generation)return;node("#health-list").replaceChildren();
    result.checks.forEach(item=>{const article=card(`${item.ok?"✓":"!"} ${item.name}`,item.detail);article.classList.add("health-check");if(!item.ok)article.classList.add("needs-attention");node("#health-list").append(article);});
    node("#protection-list").replaceChildren();result.protection.forEach(item=>node("#protection-list").append(card(`Temporary ${item.action} · channel ${item.channel_id}`,`Restores ${date(item.restore_at)}.${item.error?` ${item.error}`:""}`)));
    window.espressoRefinements?.renderProtection(result.protection,node("#protection-list"),loadHealth);
    node("#end-protection").hidden=!result.protection.length;node("#end-protection").disabled=preview;
  }
  async function loadPosts() {
    const token=generation,result=preview?{posts:[]}:await request(endpoint("posts"));if(token!==generation)return;
    node("#post-list").replaceChildren();result.posts.forEach(item=>{
      const article=card(`Post #${item.id} · ${item.status}`,item.content);
      article.prepend(make("p",`${date(item.due_at)} · channel ${item.channel_id}${item.repeat_days?` · every ${item.repeat_days} day(s)`:""}${item.notify?" · notify role":""}`,"member-number"));if(item.outcome)article.append(make("p",item.outcome));
      if(item.status==="pending")article.append(button("Cancel post",itemButton=>action(`post:${item.id}`,"#tools-message",async token=>{await write(`posts/${encodeURIComponent(item.id)}/cancel`,{});if(token!==generation)return;await loadPosts();status("#tools-message",`Post #${item.id} cancelled.`);},itemButton)));
      window.espressoRefinements?.enhancePost(article,item,loadPosts);
      node("#post-list").append(article);
    });if(!result.posts.length)node("#post-list").append(make("p",preview?"Your server's scheduled announcements and reminders will appear here.":"No scheduled posts yet.","field-note"));
  }
  async function loadTools() {
    await action("tools","#tools-message",async token=>{await loadHealth();await loadPosts();if(token===generation){toolsLoaded=true;status("#tools-message",preview?"Example tools. Connect your server to run real checks and schedule posts.":"Tools are up to date. Save settings before testing new rules.");}},node("#refresh-tools"));
  }
  node("#filter-test").addEventListener("submit",event=>{event.preventDefault();action("filter","#filter-result",async token=>{
    if(preview){status("#filter-result","Connect your server to test its saved filters. The live test reports the matched rule and action without taking any moderation action.");return;}
    const form=event.currentTarget,recipe=form.elements.recipe.value,revision=testRevision;
    node("#filter-result").removeAttribute("data-state");delete node("#filter-result").dataset.stale;status("#filter-result","Testing this message…");
    const result=await write("test-filter",window.espressoRefinements.simulationBody(form));if(token!==generation)return;
    if(revision!==testRevision){status("#filter-result","Inputs changed while the test was running. Run the test again for the current values.");return;}
    window.espressoWorkspace.renderSimulation(result,recipe);
  },event.currentTarget.querySelector("button"));});
  function testEdited(){
    testRevision++;
    if(node("#filter-result").hasAttribute("data-state"))node("#filter-result").dataset.stale="true";
  }
  node("#filter-test").addEventListener("input",testEdited);node("#filter-test").addEventListener("change",testEdited);
  document.querySelector("#settings-form").addEventListener("input",event=>{
    if(!event.target.closest("#settings-navigator") && node("#filter-test [name=recipe]").value==="unsaved")testEdited();
  });
  document.addEventListener("espresso:saved",testEdited);
  node("#schedule-post").addEventListener("input",()=>{postRequestId=crypto.randomUUID();});
  node("#schedule-post").addEventListener("submit",event=>{event.preventDefault();if(preview)return;const form=event.currentTarget;action("schedule","#schedule-result",async token=>{
    const due=new Date(form.elements.when.value).getTime()/1000;if(!Number.isFinite(due))throw new Error("Choose a date and time.");
    const result=await write("posts",{channel_id:form.elements.channel_id.value,content:form.elements.content.value,due_at:due,repeat_days:Number(form.elements.repeat_days.value),notify:form.elements.notify.checked,request_id:postRequestId,...window.espressoRefinements.scheduleBody(form)});
    if(token!==generation)return;postRequestId=crypto.randomUUID();form.elements.content.value="";const scheduled=result.due_at??due;const display=result.timezone?new Intl.DateTimeFormat(undefined,{timeZone:result.timezone,dateStyle:"medium",timeStyle:"short"}).format(new Date(scheduled*1000))+` (${result.timezone})`:date(scheduled);status("#schedule-result",`Post #${result.id} scheduled for ${display}.`);await loadPosts();
  },form.querySelector("button"));});
  node("#case-search").addEventListener("submit",event=>{event.preventDefault();caseUser=event.currentTarget.elements.user_id.value.trim();caseCursor=null;action("cases","#desk-message",()=>loadCases());});
  node("#inbox-status").addEventListener("change",()=>{inboxCursor=null;action("inbox","#desk-message",()=>loadInbox());});
  node("#refresh-desk").addEventListener("click",loadDesk);node("#refresh-tools").addEventListener("click",loadTools);
  node("#more-inbox").addEventListener("click",()=>action("inbox","#desk-message",()=>loadInbox(true),node("#more-inbox")));
  node("#more-cases").addEventListener("click",()=>action("cases","#desk-message",()=>loadCases(true),node("#more-cases")));
  node("#check-health").addEventListener("click",()=>action("health","#tools-message",()=>loadHealth(),node("#check-health")));
  node("#end-protection").addEventListener("click",()=>{if(preview)return;action("protection","#tools-message",async token=>{const result=await write("protection/end",{});if(token!==generation)return;await loadHealth();status("#tools-message",`Restored ${result.restored} channel(s); ${result.remaining} still need attention.`);},node("#end-protection"));});
  document.addEventListener("espresso:server",event=>reset(event.detail.id));document.addEventListener("espresso:loading",()=>reset());document.addEventListener("espresso:unauthorized",()=>reset());
  document.addEventListener("espresso:saved",()=>{deskLoaded=false;toolsLoaded=false;});
  document.addEventListener("espresso:tab",event=>{if(!server)return;if(event.detail.name==="desk" && !deskLoaded)loadDesk();if(event.detail.name==="tools" && !toolsLoaded)loadTools();});
  if(currentServer)reset(currentServer);
})();
