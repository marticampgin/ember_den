// Shared between everyday settings and the two independent laboratory arms.
import {api, el, openDialog, notify} from './ui.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function advancedConfig(cfg, status, arm) {
  const models = (status.models || []).map(m => [m.name,m.name]);
  const embeddings = (status.embedding_models || []).map(m => [m.name,m.name]);
  const fields = [
    ['memory_strategy','Memory sources',[['notes','Saved notes'],['episodes','Earlier conversations'],['both','Notes and earlier conversations']]],
    ['history_mode','Long conversations',[['window','Recent messages'],['recap','Recent messages and earlier excerpts']]],
    ['retrieval_mode','Document search',[['keyword','Keyword'],['semantic','Semantic'],['hybrid','Hybrid'],['off','Off']]],
    ['embedding_model','Embedding model',[['','Automatic'],...embeddings]],
    ['routing_mode','Model routing',[['fixed','Fixed model'],['adaptive','Adaptive rules'],['model','Local classifier']]],
    ['tool_mode','Tool selection',[['natural','Requests and slash commands'],['commands','Slash commands only'],['off','Off']]],
    ['router_model','Routing model',[['','Primary model'],...models]],
    ['specialist_model','Specialist model',[['','Primary model'],...models]],
    ['workflow','Response workflow',[['direct','Direct answer'],['revise','Draft and revise']]],
    ['reviewer_model','Revision model',[['','Primary model'],...models]],
    ['device','Inference device',[['auto','Automatic'],['cpu','CPU']]],
  ];
  return fields.map(([key,label,options]) => {
    const value = cfg[key] ?? options[0][0];
    if (value && !options.some(([v])=>v===value)) options.push([value,value+' (unavailable)']);
    return `<label>${label}<select name="${key}${arm ?? ''}" data-setting="${key}" ${arm===undefined ? '' : `data-config="${arm}" data-key="${key}"`}>
      ${options.map(([v,text])=>`<option value="${escape(v)}" ${v===value?'selected':''}>${escape(text)}</option>`).join('')}</select></label>`;
  }).join('');
}

export function profileDialog(config, apply) {
  openDialog('Configuration profiles', dialog => {
    const form = el('form','stack-form');
    form.innerHTML = '<label>New profile name<input name="name" maxlength="100" required></label><button class="button">Save current configuration</button>';
    const list = el('div','stack-form');
    const error = el('p','form-error');
    dialog.append(form, list, error);
    const refresh = async () => {
      const {profiles} = await api('/api/lab/profiles');
      list.replaceChildren();
      for (const profile of profiles) {
        const row = el('div','profile-row');
        const name = el('span','',profile.name);
        const applyBtn = el('button','button','Load');
        applyBtn.type='button';
        applyBtn.onclick=async()=>{
          try { await apply(profile.config); dialog.close(); }
          catch (e) { error.textContent=e.message; }
        };
        const remove = el('button','button','Delete');
        remove.type='button';
        remove.onclick=async()=>{
          if (!confirm(`Delete “${profile.name}”?`)) return;
          try {await api('/api/lab/profiles/'+profile.id,{method:'DELETE'}); await refresh();}
          catch(e){error.textContent=e.message;}
        };
        row.append(name, applyBtn, remove);
        list.append(row);
      }
    };
    form.onsubmit=async event=>{
      event.preventDefault();
      const submit = form.querySelector('button');
      submit.disabled=true;
      try {
        await api('/api/lab/profiles',{method:'POST',body:{name:form.elements.name.value,config}});
        form.reset(); await refresh(); notify('Profile saved.');
      } catch(e){error.textContent=e.message;}
      finally {submit.disabled=false;}
    };
    refresh().catch(e=>{error.textContent=e.message;});
  });
}
