"use strict";
window.espressoHateExamples = (() => {
  let library;
  function examples() {
    if(!library)library=fetch('/assets/filter-examples.json',{credentials:'same-origin'}).then(response=>{
      if(!response.ok)throw new Error('Examples could not be loaded. Close and reopen to retry.');
      return response.json();
    }).catch(error=>{library=null;throw error;});
    return library;
  }
  const make=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  function attach(card,category,title,slider) {
    const details=make('details',undefined,'hate-examples'),summary=make('summary','Click for examples'),content=make('div',undefined,'hate-example-content');
    summary.id=`${category}-example-toggle`;summary.setAttribute('aria-label',`${title}: click for uncensored examples`);
    content.id=`${category}-examples`;content.setAttribute('aria-labelledby',summary.id);
    details.append(summary,content);card.querySelector('[data-level-description]').closest('label').insertAdjacentElement('afterend',details);
    const actionNote=make('p','Unicode-disguised matches use an immediate timeout instead of this action. Ordinary Unicode alone is allowed.','field-note');
    card.querySelector('[data-key=action]').closest('label').insertAdjacentElement('afterend',actionNote);
    let request=0;
    const hide=()=>{request++;details.open=false;content.replaceChildren();};
    slider.addEventListener('input',hide);
    details.addEventListener('toggle',async()=>{
      if(!details.open){request++;content.replaceChildren();return;}
      const current=++request,level=Number(slider.value);
      content.replaceChildren(make('p','Loading examples…','field-note'));
      try {
        const data=await examples();
        if(!details.isConnected||!details.open||request!==current||Number(slider.value)!==level)return;
        content.replaceChildren(make('p',data.notice,'example-notice'));
        content.append(make('p',`${title} · level ${level} / 5. These examples assume this category is enabled and no matching exception is configured.`,'field-note'));
        for(let minimum=level;minimum>=1;minimum--){
          const group=data.categories[category][String(minimum)],section=make('section',undefined,'hate-example-group');
          section.append(make('h4',`Level ${minimum} · ${group.title}`));
          const list=make('ul');
          for(const example of group.examples){
            const row=make('li');row.append(make('span',example.label,'example-label'),make('code',example.text,'example-text'));
            if(example.matches){const readable=make('p','Recognized phrase: ','example-readable');readable.append(make('code',example.matches));row.append(readable);}
            row.append(make('span',example.unicode_evasion?'Unicode disguise in matched abuse → immediate timeout.':'Matched abuse → the category action you selected.',example.unicode_evasion?'example-outcome example-timeout':'example-outcome'));
            list.append(row);
          }
          section.append(list);content.append(section);
        }
        content.append(make('p','Examples run locally as coverage illustrations. Opening this panel never posts a message, issues a warning, or times anyone out. No text filter can guarantee catching every bypass.','field-note'));
      }catch(error){if(details.isConnected&&details.open&&request===current)content.replaceChildren(make('p',error.message,'error-text'));}
    });
    return {hide};
  }
  document.addEventListener('espresso:loading',()=>document.querySelectorAll('.hate-examples').forEach(details=>{details.open=false;details.querySelector('.hate-example-content').replaceChildren();}));
  document.addEventListener('espresso:unauthorized',()=>document.querySelectorAll('.hate-examples').forEach(details=>{details.open=false;details.querySelector('.hate-example-content').replaceChildren();}));
  return {attach};
})();
