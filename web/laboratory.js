const stylesheet = document.createElement('link');
stylesheet.rel = 'stylesheet';
stylesheet.href = new URL('./laboratory.css', import.meta.url).href;
document.head.append(stylesheet);

const paths = {
  sliders: '<path d="M4 7h8m4 0h4M4 17h2m4 0h10"/><circle cx="14" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  trace: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 5v6h6m3-4v5l3 2"/>',
  bookmark: '<path d="M6 4h12v17l-6-4-6 4z"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  back: '<path d="m11 5-7 7 7 7M4 12h16"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-9v.3"/>',
  flask: '<path d="M9 3h6m-5 0v7L4 19q-.5 2 2 2h12q2.5 0 2-2l-6-9V3M7 15h10"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5m10-2a7 7 0 0 0-12-5m-2 9a7 7 0 0 0 12 5"/>',
};
const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const statusLabel = status => ({completed:'Complete', pending:'Queued', running:'Running', failed:'Failed', error:'Failed', partial:'Partial', interrupted:'Interrupted'}[status] || status || 'Unknown');
const statusTag = status => `<span class="lab-status" data-state="${escape(status)}">${escape(statusLabel(status))}</span>`;
const button = (action, text, glyph='', primary=false, disabled=false) => `<button type="button" class="lab-btn${primary ? ' lab-primary' : ''}" data-action="${action}" ${disabled ? 'disabled' : ''}>${glyph ? icon(glyph) : ''}${escape(text)}</button>`;
const empty = (glyph, title, message) => `<div class="lab-empty">${icon(glyph)}<h3>${escape(title)}</h3><p>${escape(message)}</p></div>`;
const when = iso => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
};
const duration = ms => Number.isFinite(ms) ? (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`) : '—';
const number = value => Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:1}) : '—';
function metrics(values = {}) {
  const pairs = [
    ['First token', duration(values.ttft_ms)], ['Total time', duration(values.total_ms)],
    ['Tokens / sec', number(values.tokens_per_second)], ['Output tokens', number(values.output_tokens)],
  ];
  if (Number.isFinite(values.resident_vram_bytes)) {
    pairs.push(['Resident VRAM', `${(values.resident_vram_bytes / 2 ** 30).toFixed(2)} GiB`], ['Load time',duration(values.load_ms)]);
  }
  const more = [['Input tokens', number(values.input_tokens)],['Runtime context window',number(values.effective_context_length)]];
  return `<dl class="lab-metrics">${pairs.map(([key,value]) => `<div><dt>${key}</dt><dd>${value}</dd></div>`).join('')}</dl>
    ${Number.isFinite(values.output_tokens) ? `<details><summary>Measurement details</summary><p class="lab-note">Ollama reports generation speed and token counts, including internal generation. Resident VRAM is sampled after the response.</p><dl class="lab-extra-metrics">${more.map(([k,v])=>`<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl></details>` : ''}`;
}
function traceEvents(events = []) {
  if (!events.length) return '<p class="lab-note">No trace events recorded.</p>';
  return `<p class="lab-event-clock">Elapsed since request start</p><ol class="lab-events">${events.map(event => `<li><div><strong>${escape(event.stage)}</strong><span>${duration(event.elapsed_ms)}</span></div><p>${escape(typeof event.detail === 'string' ? event.detail : JSON.stringify(event.detail))}</p></li>`).join('')}</ol>`;
}
function capturedReferences(refs = []) {
  if (!refs.length) return '<p class="lab-note">No references retrieved.</p>';
  return `<ul class="lab-references">${refs.map(item => {
    const lines = item.line_start == null ? '' : ` · lines ${item.line_start}–${item.line_end ?? item.line_start}`;
    const name = item.kind==='document' ? (item.name || 'Document') + lines : 'Saved memory';
    return `<li><strong>${escape(name)}</strong><p>${escape(item.text)}</p></li>`;
  }).join('')}</ul>`;
}

export function mountLaboratory(root, {api, notify = () => {}, initialTraceId = null}) {
  let alive = true;
  let request = 0;
  let timer;
  let tab = 'workbench';
  let view = 'form';
  let installed = [];
  let online = false;
  let settings = {};
  let draft;
  let record;
  let selectedCase = '';
  let rows = [];
  let loading = true;
  let submitting = false;
  let error = '';
  const handle = new AbortController();
  const get = (path, opts={}) => api(path, {...opts, signal: handle.signal});
  const tabs = [
    ['workbench','Workbench','sliders'], ['traces','Run traces','trace'],
    ['history','History','history'], ['cases','Test cases','bookmark'],
  ];

  function initialDraft() {
    const a = {
      model: installed.some(m => m.name === settings.model) ? settings.model : installed[0]?.name || '',
      temperature: settings.temperature ?? 0.7, context_length: settings.context_length ?? 4096,
      seed: settings.seed ?? 42, system_prompt: settings.system_prompt ?? '',
    };
    const alternative = installed.find(m => m.name !== a.model && m.name.toLowerCase().includes('lfm2.5-2.6b'));
    const b = alternative ? {...a,model:alternative.name} : {...a,temperature:a.temperature === 0.2 ? 0.8 : 0.2};
    return {prompt:'', expected:'', configs:[a,b], history:[], context:[], source_run_id:null};
  }
  function formCard(index) {
    const cfg = draft.configs[index];
    const label = index ? 'B' : 'A';
    const missing = cfg.model && !installed.some(m => m.name === cfg.model);
    const selected = installed.find(m => m.name === cfg.model);
    const opts = installed.map(m => `<option value="${escape(m.name)}" ${m.name===cfg.model ? 'selected' : ''}>${escape(m.name)}</option>`).join('');
    const scalar = (key,title,min,max,step,optional=false) => `<label>${title}<input name="${key}${index}" data-config="${index}" data-key="${key}" type="number" ${optional ? 'placeholder="Runtime default"' : 'required'} min="${min}" max="${max}" step="${step}" value="${escape(cfg[key])}"></label>`;
    return `<section class="lab-config" aria-label="Configuration ${label}">
      <header><span class="lab-letter">${label}</span><h3>Configuration ${label}</h3></header>
      <label>Model<select required name="model${index}" data-config="${index}" data-key="model" ${installed.length ? '' : 'disabled'}>${missing ? `<option selected value="${escape(cfg.model)}">${escape(cfg.model)} (unavailable)</option>` : ''}${opts || '<option value="">No installed models</option>'}</select></label>
      <p class="lab-model-spec" data-spec="${index}">${escape([selected?.family, selected?.parameters, selected?.quantization].filter(Boolean).join(' · '))}</p>
      <div class="lab-fields">${scalar('temperature','Temperature',0,2,0.01)}${scalar('context_length','Context window',1024,16384,1)}</div>
      <details class="lab-advanced"><summary>Advanced settings</summary><div>${scalar('seed','Random seed',0,2147483647,1,true)}<label>System prompt<textarea rows="3" maxlength="4000" data-config="${index}" data-key="system_prompt">${escape(cfg.system_prompt)}</textarea></label></div></details>
    </section>`;
  }
  function form() {
    const pinned = draft.source_run_id || draft.context.length || draft.history.length;
    return `<form class="lab-form" data-form="comparison"><div class="lab-section-intro"><div><h2>New comparison</h2><p>Run one prompt through two local configurations, one after the other.</p></div></div>
      ${online && installed.length ? '' : `<div class="lab-banner">${icon('info')}<span>${online ? 'Ollama has no local models installed.' : 'Ollama is offline. Start it to run a comparison.'}</span>${button('refresh','Refresh','refresh')}</div>`}
      <label class="lab-prompt">Test prompt<textarea name="prompt" required maxlength="12000" rows="3" placeholder="What would you like to test?">${escape(draft.prompt)}</textarea></label>
      ${pinned ? `<div class="lab-toolbar"><p class="lab-note">Captured input · ${number(draft.history.length)} prior message${draft.history.length===1?'':'s'} · ${number(draft.context.length)} reference${draft.context.length===1?'':'s'}</p>${button('clear-context','Remove captured context')}</div>` : ''}
      ${draft.source_redacted || draft.redacted ? '<p class="lab-note">The saved input masks recognized credentials. Replay uses the masked text.</p>' : ''}
      <div class="lab-cols">${formCard(0)}${formCard(1)}</div>
      <details class="lab-check" ${draft.expected ? 'open' : ''}><summary>Expected text check <span>Optional</span></summary><label>Response must contain<input name="expected" maxlength="2000" value="${escape(draft.expected)}" placeholder="A word or phrase to check in each answer"></label><p class="lab-note">Ignores capitalization. A match checks only the phrase you enter.</p></details>
      <div class="lab-actions">${button('save-case','Save test case','bookmark',false,!installed.length)}<button class="lab-btn lab-primary" type="submit" ${!online || !installed.length || submitting ? 'disabled' : ''}>${icon('play')}${submitting ? 'Starting…' : 'Run comparison'}</button></div>
    </form>`;
  }
  function resultCard(item, index) {
    const model = record.snapshot.models?.[index] || {};
    const cfg = record.snapshot.configs[index];
    const run = item.run;
    const message = item.status==='pending' ? 'Waiting for the first configuration to finish.' : 'Generating a response locally…';
    return `<section class="lab-config lab-result"><header><span class="lab-letter">${item.label}</span><h3>Configuration ${item.label}</h3>${statusTag(item.status)}</header>
      <b class="lab-model-name">${escape(cfg.model)}</b><p class="lab-note">Temperature ${escape(cfg.temperature)} · Context ${number(cfg.context_length)}</p>
      ${run ? metrics(run.metrics) : ''}${item.error ? `<p class="lab-error" role="status">${escape(item.error)}</p>` : ''}
      ${run?.response ? `<div class="lab-response">${escape(run.response)}</div>` : !run && !item.error ? `<p class="lab-note">${message}</p>` : ''}
      ${typeof item.check === 'boolean' ? `<p class="lab-verdict" data-pass="${item.check}">Expected text ${item.check ? 'matched' : 'missing'}</p>` : ''}
      ${run ? `<details><summary>Execution trace</summary>${traceEvents(run.events)}${run.context?.length ? capturedReferences(run.context) : ''}</details>` : ''}
      <details><summary>Recorded configuration</summary><pre>${escape(JSON.stringify({model:model.name || cfg.model,digest:model.digest || null,...cfg},null,2))}</pre></details></section>`;
  }
  function result() {
    return `<div class="lab-toolbar">${button('back','Back','back')}<span>${escape(when(record.created_at))}</span></div>
      <section class="lab-record-heading"><div><h2>Comparison results</h2>${statusTag(record.status)}</div><p>${escape(record.snapshot.prompt)}</p>
      ${record.snapshot.expected ? `<small>Expected text · ${escape(record.snapshot.expected)}</small>` : ''}<small>Shared input · ${record.snapshot.history.length} prior messages · ${record.snapshot.context.length} references</small>
      ${record.snapshot.redacted || record.snapshot.source_redacted ? '<small>Recognized credentials were redacted in the saved input.</small>' : ''}
      ${record.snapshot.changed_models?.length ? `<small>Model revision changed since the original comparison for ${escape(record.snapshot.changed_models.join(', '))}.</small>` : ''}</section>
      <div class="lab-cols">${record.results.map(resultCard).join('')}</div>
      <div class="lab-actions">${button('edit-comparison','Edit configurations','sliders')}${button('replay','Replay comparison','play',true,record.status==='running' || submitting)}</div>`;
  }
  function traceDetail() {
    return `<div class="lab-toolbar">${button('back','All traces','back')}<span>${escape(when(record.created_at))}</span></div><section class="lab-record-heading"><div><h2>Execution trace</h2>${statusTag(record.status)}</div><p>${escape(record.prompt)}</p><small>${escape(record.model)} · ${escape(record.source)}</small></section>
      ${metrics(record.metrics)}${record.error ? `<p class="lab-error">${escape(record.error)}</p>` : ''}
      <section class="lab-panel"><h3>Recorded events</h3>${traceEvents(record.events)}<details><summary>Response</summary><div class="lab-response">${escape(record.response || 'No response recorded.')}</div></details>
      <details><summary>Retrieved references (${record.context?.length || 0})</summary>${capturedReferences(record.context)}</details>
      <details><summary>Configuration and conversation history</summary><pre>${escape(JSON.stringify({configuration:record.config,history:record.history || []},null,2))}</pre></details></section>
      <div class="lab-actions">${button('use-trace','Use in comparison','sliders',true)}</div>`;
  }
  function listings() {
    const info = {
      history:['Comparison history','Saved runs keep both answers, their settings, and their measurements.','No comparisons yet','Run a comparison in the workbench to see it here.','history'],
      traces:['Run traces','Inspect the model, inputs, and recorded stages behind a response.','No runs yet','Send Ember a message or run a comparison to create a trace.','trace'],
      cases:['Saved test cases','Reusable prompts, settings, and expected text for regression checks.','No test cases yet','Save a test case from the workbench to run it again later.','bookmark'],
    }[tab];
    return `<div class="lab-section-intro"><div><h2>${info[0]}</h2><p>${info[1]}</p></div>${button('refresh','Refresh','refresh')}</div>
    ${!rows.length ? empty(info[4],info[2],info[3]) : `<div class="lab-list">${rows.map(item => `<button type="button" class="lab-row" data-item="${escape(item.id)}"><span><strong>${escape(item.name || item.prompt)}</strong><small>${escape([when(item.created_at),item.model,item.source].filter(Boolean).join(' · '))}</small></span>${item.status ? statusTag(item.status) : ''}${icon('arrow')}</button>`).join('')}</div>`}`;
  }
  function render() {
    if (!alive) return;
    const title = '<header class="page-heading lab-heading"><div><h1>Developer laboratory</h1><p class="page-subtitle">Experiment with the models and settings behind Ember.</p></div></header>';
    root.innerHTML = `<div class="lab">${title}<nav class="lab-tabs" aria-label="Laboratory views">${tabs.map(([id,title,glyph])=>`<button type="button" data-tab="${id}" aria-current="${tab===id ? 'page' : 'false'}">${icon(glyph)}${title}</button>`).join('')}</nav>
      ${error ? `<div class="lab-error lab-banner" role="alert">${icon('info')}<span>${escape(error)}</span></div>` : ''}
      <div class="lab-body">${loading ? '<p class="lab-note" role="status">Loading laboratory…</p>' : view==='result' ? result() : view==='trace' ? traceDetail() : tab==='workbench' ? form() : listings()}</div></div>`;
  }
  async function load(nextTab = tab) {
    clearTimeout(timer);
    const current = ++request;
    tab = nextTab; view = nextTab==='workbench' ? 'form' : 'list'; loading = true; error = ''; rows = []; render();
    try {
      if (tab==='workbench') {
        const status = await get('/api/status');
        if (!alive || current !== request) return;
        installed = status.models || []; online = !!status.online; settings = status.settings || {};
        if (!draft || (!draft.configs[0].model && installed.length)) {
          const defaults = initialDraft();
          draft = draft ? {...draft, configs:defaults.configs} : defaults;
        }
      } else {
        const [path,field] = {cases:['/api/lab/cases','cases'],history:['/api/lab/experiments','experiments'],traces:['/api/runs','runs']}[tab];
        const data = await get(path);
        if (!alive || current !== request) return;
        rows = data[field] || [];
      }
    } catch (err) {
      if (current !== request || !alive) return;
      error = err.message;
      draft ||= initialDraft();
    }
    if (current === request) {loading=false; render();}
  }
  async function openRecord(id, kind) {
    clearTimeout(timer);
    const current = ++request;
    const path = kind==='trace' ? '/api/runs/' : kind==='case' ? '/api/lab/cases/' : '/api/lab/experiments/';
    try {
      const data = await get(path + encodeURIComponent(id));
      if (!alive || current !== request) return;
      error = '';
      if (kind==='case') {
        draft = structuredClone(data.snapshot); selectedCase=data.name; await load('workbench'); return;
      }
      if (kind==='result' && view===kind && record?.id===data.id && record.updated_at===data.updated_at && record.status===data.status) {
        if(data.status==='running') timer=setTimeout(()=>openRecord(id,kind),1250);
        return;
      }
      record=data; view=kind; loading=false;
      const openDetails = Array.from(root.querySelectorAll('.lab-result details')).map(el=>el.open);
      render();
      root.querySelectorAll('.lab-result details').forEach((el,i)=>{el.open=!!openDetails[i];});
      if(kind==='result' && data.status==='running') timer=setTimeout(()=>openRecord(id,kind),1250);
    } catch (err) {
      if (!alive || current !== request) return;
      error=err.message; loading=false; render();
    }
  }
  function syncInput(event) {
    const input=event.target;
    if (!input.closest('[data-form="comparison"]')) return;
    if (input.dataset.config !== undefined) {
      const idx=Number(input.dataset.config), key=input.dataset.key;
      draft.configs[idx][key]=input.type==='number' ? (input.value==='' ? null : Number(input.value)) : input.value;
      if (key==='model') {
        const model=installed.find(m=>m.name===input.value);
        root.querySelector(`[data-spec="${idx}"]`).textContent=[model?.family,model?.parameters,model?.quantization].filter(Boolean).join(' · ');
      }
    } else if (input.name==='prompt' || input.name==='expected') draft[input.name]=input.value;
  }
  async function runComparison(replay=false) {
    if (submitting) return;
    const current = request;
    submitting=true; error=''; render();
    try {
      const path = replay ? `/api/lab/experiments/${record.id}/replay` : '/api/lab/experiments';
      const response = await get(path,{method:'POST',body:replay ? {} : draft});
      if (!alive || current !== request) return;
      submitting=false; tab='history'; await openRecord(response.id,'result');
    } catch(err) {
      if (!alive || current !== request) return;
      error=err.message; render();
    } finally {
      if (alive) {submitting=false; if (current===request) render();}
    }
  }
  function openSaveDialog() {
    const dialog = document.createElement('dialog');
    dialog.className='lab-dialog';
    dialog.setAttribute('aria-label','Save a test case');
    dialog.innerHTML=`<form><h2>Save test case</h2><label>Case name<input name="name" required maxlength="100" value="${escape(selectedCase || draft.prompt.trim().slice(0,60))}"></label><div class="lab-actions">${button('cancel','Cancel')}<button type="submit" class="lab-btn lab-primary">Save case</button></div></form>`;
    root.querySelector('.lab').append(dialog); dialog.showModal();
    dialog.querySelector('input').select();
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    dialog.querySelector('[data-action="cancel"]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=async e=>{
      e.preventDefault();
      const name=dialog.querySelector('input').value.trim();
      const submit=dialog.querySelector('[type="submit"]'); submit.disabled=true;
      try {await get('/api/lab/cases',{method:'POST',body:{...draft,name}}); if(alive){selectedCase=name;dialog.close();notify('Test case saved.');}}
      catch(err){if(alive){dialog.close();error=err.message;render();}}
    };
  }
  async function click(event) {
    const el=event.target.closest('button');
    if (!el || el.disabled) return;
    if (el.dataset.tab) {await load(el.dataset.tab);return;}
    if (el.dataset.item) {await openRecord(el.dataset.item,tab==='traces'?'trace':tab==='cases'?'case':'result');return;}
    switch (el.dataset.action) {
      case 'refresh': case 'back': await load();break;
      case 'clear-context': draft.context=[]; draft.history=[]; draft.source_run_id=null; render();break;
      case 'replay': await runComparison(true);break;
      case 'edit-comparison': draft=structuredClone(record.snapshot);selectedCase='';await load('workbench');break;
      case 'save-case': if(root.querySelector('.lab-form').reportValidity()) openSaveDialog();break;
      case 'use-trace': {
        const captured=record; draft=null; selectedCase=''; await load('workbench');
        if(!alive || tab!=='workbench' || !draft) return;
        draft.prompt=captured.prompt; draft.source_run_id=captured.id;
        draft.source_redacted=!!captured.redacted;
        draft.context=captured.context || []; draft.history=captured.history || [];
        const selectedModel = captured.route==='calculator' ? draft.configs[0].model : captured.model;
        draft.configs[0]={...draft.configs[0],...captured.config,model:selectedModel,seed:captured.config?.seed ?? null};
        render();break;
      }
    }
  }
  root.addEventListener('input',syncInput,{signal:handle.signal});
  root.addEventListener('click',click,{signal:handle.signal});
  root.addEventListener('submit',e=>{if(e.target.matches('.lab-form')){e.preventDefault();runComparison();}},{signal:handle.signal});
  if (initialTraceId) {
    tab='traces'; view='list'; render(); openRecord(initialTraceId,'trace');
  } else {
    load();
  }
  return ()=>{alive=false;++request;clearTimeout(timer);handle.abort();};
}
