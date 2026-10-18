"use strict";
window.espressoExtensions = (() => {
  const defaults = {
    observation_mode:false, allowed_domains:[], channel_rules:[], warning_expiry_days:0,
    raid:{enabled:false,join_limit:10,window_seconds:20,repeat_limit:5,action:"alert",slowmode_seconds:15,duration_minutes:10},
    welcome:{enabled:false,channel_id:null,message:"Welcome {member} to {server}! Grab a coffee and make yourself at home. ☕"},
    community:{coffee_enabled:false,polls_enabled:false,prompts_enabled:false,prompt_channel_id:null,prompt_hour_utc:16,notification_role_id:null}
  };
  const control = name => document.querySelector(`#settings-form [name="${name}"]`);
  const selectOptions = (name,options,value,empty) => {
    const select=control(name);select.replaceChildren(new Option(empty,""));
    options.forEach(item=>select.add(new Option(item.name,item.id)));
    if(value && !options.some(item=>item.id===value))select.add(new Option(`Unavailable (${value})`,value));
    select.value=value || "";
  };
  function row(value={channel_id:"",enabled:true,block_links:null,block_invites:null,spam_enabled:null}) {
    const item=document.createElement("div");item.className="channel-rule";item.dataset.channelRule="";
    for(const [key,title] of [["channel_id","Channel"],["enabled","Moderation"],["block_links","Links"],["block_invites","Invites"],["spam_enabled","Spam filter"]]) {
      const label=document.createElement("label"),select=document.createElement("select");label.textContent=title;select.dataset.ruleField=key;
      if(key==="channel_id") {select.add(new Option("Choose a channel…",""));availableChannels.forEach(channel=>select.add(new Option(channel.name,channel.id)));if(value[key] && !availableChannels.some(c=>c.id===value[key]))select.add(new Option(`Unavailable (${value[key]})`,value[key]));}
      else if(key==="enabled") {select.add(new Option("Enabled","true"));select.add(new Option("Skip this channel","false"));}
      else {select.add(new Option("Use server setting",""));select.add(new Option(key==="spam_enabled"?"On":"Block","true"));select.add(new Option(key==="spam_enabled"?"Off":"Allow","false"));}
      select.value=value[key]===null?"":String(value[key]);label.append(select);item.append(label);
    }
    const remove=document.createElement("button");remove.type="button";remove.className="text-button";remove.textContent="Remove channel rule";
    remove.addEventListener("click",()=>{item.remove();document.querySelector("#settings-form").dispatchEvent(new Event("change"));});
    item.append(remove);document.querySelector("#channel-rules").append(item);
  }
  function fill(settings) {
    control("observation_mode").checked=settings.observation_mode;
    control("allowed_domains").value=settings.allowed_domains.join("\n");
    control("warning_expiry_days").value=settings.warning_expiry_days;
    for(const name of ["raid","welcome","community"])for(const [key,value] of Object.entries(settings[name])) {
      const input=control(`${name}_${key}`);
      if(typeof value==="boolean")input.checked=value;else input.value=value??"";
    }
    selectOptions("welcome_channel_id",availableChannels,settings.welcome.channel_id,"Choose welcome channel…");
    selectOptions("community_prompt_channel_id",availableChannels,settings.community.prompt_channel_id,"Choose prompt channel…");
    selectOptions("community_notification_role_id",availableRoles,settings.community.notification_role_id,"No notification role");
    document.querySelector("#channel-rules").replaceChildren();settings.channel_rules.forEach(row);
  }
  function read() {
    const value={observation_mode:control("observation_mode").checked,allowed_domains:[...new Set(control("allowed_domains").value.split(/\r?\n/).map(item=>item.trim().toLowerCase()).filter(Boolean))],warning_expiry_days:Number(control("warning_expiry_days").value),channel_rules:[]};
    for(const name of ["raid","welcome","community"]) {
      value[name]={};for(const [key,initial] of Object.entries(defaults[name])) {
        const input=control(`${name}_${key}`);
        value[name][key]=typeof initial==="boolean"?input.checked:typeof initial==="number"?Number(input.value):input.value || null;
      }
    }
    value.channel_rules=Array.from(document.querySelectorAll("[data-channel-rule]"),item=>Object.fromEntries(Array.from(item.querySelectorAll("select"),input=>[input.dataset.ruleField,input.dataset.ruleField==="channel_id"?input.value:input.value===""?null:input.value==="true"])));
    return value;
  }
  document.querySelector("#add-channel-rule").addEventListener("click",()=>{row();document.querySelector("#settings-form").dispatchEvent(new Event("change"));});
  return {defaults,keys:Object.keys(defaults),fill,read};
})();
