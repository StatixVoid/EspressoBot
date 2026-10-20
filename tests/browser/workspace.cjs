// Run with Node and Playwright installed. No Discord credentials or local server.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "../..");
const site = "https://espresso.test";
const defaults = () => ({
  setup_completed:true,enabled:false,spam_enabled:true,spam_limit:6,spam_window:8,block_links:false,block_invites:true,
  bad_words:["private test phrase"],exempt_roles:[],log_channel:null,warn_threshold:3,warn_window_hours:24,timeout_minutes:10,
  hate_filters:Object.fromEntries(["racism","misogyny","trans_hate"].map(key=>[key,{enabled:false,level:1,action:"warn",extra_terms:[],allowed_terms:[]}])),
  reaction_roles_enabled:false,reaction_roles:[],screening:{enabled:false,mode:"everyone",minimum_account_age_days:7,
    verified_role_id:null,entry_channel_id:null,require_agreement:true,rules_text:"Respect the community."}
});
const hubDefaults = () => ({
  tickets_enabled:false,ticket_category_id:null,support_role_id:null,ticket_categories:["General"],ticket_transcripts:false,
  events_enabled:false,event_role_id:null,suggestions_enabled:false,suggestion_channel_id:null,voice_enabled:false,voice_lobby_id:null,
  voice_category_id:null,voice_user_limit:5,faq_enabled:false,birthdays_enabled:false,birthday_channel_id:null,birthday_role_id:null,
  timezone:"UTC",birthday_hour:9,highlights_enabled:false,highlight_channel_id:null,highlight_source_ids:[],highlight_threshold:3,profiles_enabled:false
});
async function main() {
  const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || (process.platform==="win32"?"msedge":undefined)});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors = [], dialogs = [], unexpected = [];
    page.on("pageerror",error=>{errors.push(error.message);console.error("Browser error: "+error.message);});
    let acceptDialogs=false, mode="manager", settings=defaults(), hub=hubDefaults(), version=0, hubVersion=0, writes=0, failure="", releaseSave=null, holdSave=false, releaseTest=null, holdTest=false, notifySave=null, notifyTest=null;
    page.on("dialog",async dialog=>{dialogs.push(dialog.message());await (acceptDialogs?dialog.accept():dialog.dismiss());});
    const roles=[{id:"31",name:"Support team"}], channels=[{id:"20",name:"general",kind:"text",can_log:true}];
    const access=()=>({manage:mode==="manager",moderate:true});
    const payload=()=>mode==="manager"?{settings,version,roles,channels,categories:[],screening_events_enabled:true,access:access()}:
      {settings:null,version:0,roles:[],channels:[],categories:[],access:access()};
    await page.route("**/*",async route=>{
      const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();
      if(url.origin!==site){await route.abort();return;}
      if(!p.startsWith("/api/")){
        const file=p==="/dashboard"?"dashboard.html":p.startsWith("/assets/")?p.slice(8):"";
        if(!/^[a-z0-9.-]+$/.test(file)){await route.fulfill({status:404,body:"Not found"});return;}
        const type={".html":"text/html",".js":"application/javascript",".css":"text/css",".png":"image/png",".svg":"image/svg+xml"}[path.extname(file)];
        await route.fulfill({path:path.join(root,"espresso/public",file),contentType:type});return;
      }
      const body=["PATCH","POST"].includes(method)?request.postDataJSON():null;
      let result;
      if(p==="/api/session")result={user:{name:"Test staff"},csrf:"test-csrf"};
      else if(p==="/api/servers")result={bot_online:true,servers:[{id:"1",name:"Test café",installed:true,can_manage:mode==="manager",can_moderate:true},
        {id:"2",name:"Second café",installed:true,can_manage:true,can_moderate:true}],initial_server:{id:"1",data:payload()}};
      else if(p.endsWith("/settings")&&method==="PATCH"){
        writes++;
        if(failure==="html"){await route.fulfill({status:502,contentType:"text/html",body:"<h1>Proxy error</h1>"});return;}
        if(failure==="network"){await route.abort("connectionfailed");return;}
        if(failure==="conflict"){await route.fulfill({status:409,json:{}});return;}
        settings={...settings,...body.changes};version++;result={settings,version,setup_warnings:[]};
      } else if(p.endsWith("/settings"))result=payload();
      else if(p.endsWith("/hub")&&method==="PATCH"){
        writes++;if(holdSave)await new Promise(resolve=>{releaseSave=resolve;notifySave?.();});
        hub=body.settings;hubVersion++;result={settings:hub,version:hubVersion};
      } else if(p.endsWith("/hub"))result={settings:hub,version:hubVersion,roles,channels,faq:[],issues:[]};
      else if(p.includes("/hub/items/"))result={items:[],next_cursor:null};
      else if(p.endsWith("/test-filter")){
        if(holdTest)await new Promise(resolve=>{releaseTest=resolve;notifyTest?.();});
        result={matched:true,rule:"Anti-trans hate filter · level 4 · Unicode evasion",action:"timeout",effective_action:"none",
          moderation_enabled:false,observation_mode:false,warnings_after:2,steps:["Automatic moderation is off for this server or channel."],note:"No Discord actions."};
      } else if(p.endsWith("/health"))result={checks:[],protection:[]};
      else if(p.endsWith("/calendar"))result={events:[],limit_reached:false};
      else if(p.endsWith("/role-menus"))result={menus:[]};
      else if(p.endsWith("/posts"))result={posts:[]};
      else if(p.endsWith("/overview"))result={access:access(),reports:{total:0,overdue:0},verification_waiting:0,post_issues:[],health_issues:[],protection:[]};
      else if(p.endsWith("/inbox"))result={items:[],next_cursor:null};
      else if(p.endsWith("/cases"))result={cases:[],next_cursor:null};
      else {unexpected.push(p);await route.fulfill({status:404,json:{error:"Unexpected fixture request"}});return;}
      await route.fulfill({json:result});
    });
    const visible=selector=>page.locator(selector).waitFor({state:"visible"});
    const waitFor=fn=>page.waitForFunction(fn);
    await page.goto(site+"/dashboard?server=1");await visible("#settings-form");
    await page.locator("[name=block_links]").check();
    await page.locator("#settings-search").fill("timeout minutes");assert.equal(await page.locator(".setting-search-hit").count(),1);
    await page.locator("#settings-search").press("Enter");assert.equal(writes,0);assert.equal(await page.locator("[name=timeout_minutes]").evaluate(node=>node===document.activeElement),true);
    await page.locator("#settings-search").fill("private test phrase");assert.equal(await page.locator(".setting-search-hit").count(),0);
    await page.locator("#settings-search").press("Escape");
    await visible("#workspace-drafts");assert.match(await page.locator("#settings-change-review").innerText(),/Review 1 change/);
    await page.keyboard.press("Control+s");await waitFor(()=>document.querySelector("#save-settings").disabled&&!document.querySelector("#workspace-drafts").offsetHeight);
    assert.equal(writes,1);assert.equal(settings.block_links,true);
    await page.locator("#tab-hub").click();
    try {await visible("#hub-config-fields [name=faq_enabled]");}
    catch(error){console.error("Hub status: "+await page.locator("#hub-message").innerText());console.error("Unexpected routes: "+unexpected.join(", "));throw error;}
    assert.equal(await page.locator("#hub-save").isDisabled(),true);
    await page.locator("[name=faq_enabled]").check();await visible("#workspace-drafts");
    assert.equal(await page.evaluate(()=>{const event=new Event("beforeunload",{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}),true);
    await page.locator("#tab-settings").click();await page.locator("[name=block_links]").uncheck();
    assert.match(await page.locator("#workspace-drafts").innerText(),/Settings and Community hub/);
    await page.locator(".server-item").nth(1).click();assert.match(dialogs.at(-1),/Settings and Community hub/);assert.match(await page.locator("#server-name").innerText(),/Test café/);
    await page.locator("#reset-settings").click();assert.match(await page.locator("#workspace-drafts").innerText(),/Community hub/);
    await page.locator("#tab-hub").click();await page.locator("[name=faq_enabled]").uncheck();assert.equal(await page.locator("#workspace-drafts").isHidden(),true);
    await page.locator("[name=faq_enabled]").check();holdSave=true;
    const saveStarted=new Promise(resolve=>notifySave=resolve);await page.keyboard.press("Control+s");await saveStarted;
    await page.waitForFunction(()=>document.querySelector("#hub-save").disabled);assert.ok(releaseSave);
    const prompts=dialogs.length;await page.locator(".server-item").nth(1).click();assert.equal(dialogs.length,prompts);
    assert.match(await page.locator("#workspace-drafts").innerText(),/save is still in progress/);
    await page.locator("[name=birthday_hour]").fill("10");releaseSave();holdSave=false;
    await waitFor(()=>!document.querySelector("#hub-save").disabled);
    assert.match(await page.locator("#hub-message").innerText(),/newer changes/);assert.equal(hub.birthday_hour,9);
    await page.locator("#hub-save").click();await waitFor(()=>document.querySelector("#workspace-drafts").hidden);assert.equal(hub.birthday_hour,10);
    await page.locator("#tab-settings").click();await page.locator("[name=block_links]").uncheck();failure="html";
    await page.locator("#save-settings").click();await page.locator("#settings-feedback").filter({hasText:"couldn't complete"}).waitFor();assert.equal(await page.locator("#save-settings").isDisabled(),false);
    assert.doesNotMatch(await page.locator("#settings-feedback").innerText(),/Unexpected token|Proxy error/);
    failure="network";await page.locator("#save-settings").click();await page.locator("#settings-feedback").filter({hasText:"Couldn't reach"}).waitFor();
    failure="conflict";await page.locator("#save-settings").click();await page.locator("#settings-feedback").filter({hasText:"updated elsewhere"}).waitFor();
    assert.equal(await page.locator("[name=block_links]").isChecked(),false);failure="";await page.locator("#save-settings").click();await waitFor(()=>document.querySelector("#workspace-drafts").hidden);
    await page.locator('[data-category=trans_hate] [data-key=enabled]').check();await page.locator('[aria-label="Try the Anti-trans hate filter"]').click();
    assert.equal(await page.locator("#filter-test [name=recipe]").inputValue(),"unsaved");
    await page.locator("#filter-test textarea").fill("A sample message");await page.locator("#filter-test [type=submit]").click();
    await page.locator("#filter-result h4").filter({hasText:"No moderation action"}).waitFor();
    assert.match(await page.locator("#filter-result").innerText(),/Matched rule/);assert.match(await page.locator("#filter-result").innerText(),/Moderation off/);
    await page.locator("#filter-test textarea").fill("Edited sample");assert.equal(await page.locator("#filter-result").getAttribute("data-stale"),"true");
    holdTest=true;const testStarted=new Promise(resolve=>notifyTest=resolve);
    await page.locator("#filter-test [type=submit]").click();await testStarted;await page.waitForFunction(()=>document.querySelector("#filter-test button").disabled);
    await page.locator("#filter-test textarea").fill("Changed during request");assert.ok(releaseTest);releaseTest();holdTest=false;
    await page.locator("#filter-result").filter({hasText:"Inputs changed while"}).waitFor();
    await page.locator("#tab-settings").click();await page.locator("#reset-settings").click();acceptDialogs=true;
    await page.locator("#tab-hub").click();await page.reload();await visible("#hub-config-fields [name=faq_enabled]");
    assert.equal(await page.locator("#tab-hub").getAttribute("aria-selected"),"true");
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.ok((await page.locator("#panel-tabs").boundingBox()).height<80);
    await page.locator("#tab-settings").click();await page.locator("#tab-settings").focus();await page.keyboard.press("End");
    assert.equal(await page.locator("#tab-hub").getAttribute("aria-selected"),"true");
    mode="moderator";await page.goto(site+"/dashboard?server=1&tab=hub");await visible("#overview-panel");
    assert.equal(await page.locator("#tab-hub").isVisible(),false);assert.equal(await page.locator("#workspace-drafts").isHidden(),true);
    assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
    console.log("Workspace browser checks passed: search, review, shortcuts, cross-tab drafts, in-flight saves, failed requests, simulation, deep links, permissions, and mobile navigation.");
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
