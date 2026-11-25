import {el} from './ui.js';
import {publicSourceUrl, sourceAttribution} from './public-source.js';

const css = document.createElement('link');
css.rel = 'stylesheet';
css.href = new URL('./tools.css', import.meta.url).href;
document.head.append(css);

function button(label, primary = false) {
  const node = el('button', 'button' + (primary ? ' primary' : ''), label);
  node.type = 'button';
  return node;
}
function field(label, control) {
  const wrapper = el('label','tools-field',label);
  wrapper.append(control);
  return wrapper;
}
function section(heading, description) {
  const panel = el('section','tools-panel');
  panel.append(el('h2','',heading));
  if (description) panel.append(el('p','tools-description',description));
  return panel;
}
function details(label, content) {
  const node = el('details','tools-details');
  node.append(el('summary','',label), content);
  return node;
}
function sourceLink(hit) {
  const url = publicSourceUrl(hit);
  const link = el(url ? 'a' : 'strong','tools-source',hit.name);
  if (url) {
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
  return link;
}

export function mountTools(root, {api, notify = () => {}}) {
  const controller = new AbortController();
  const call = (path, options={}) => api(path,{...options,signal:controller.signal});
  let approvedFolder = '';
  let searchProvider = 'Mwmbl';
  const heading = el('header','page-heading');
  const headingText = el('div');
  headingText.append(el('h1','','Tools'),el('p','page-subtitle','Look up a public source or prepare a local export.'));
  heading.append(headingText);
  const grid = el('div','tools-grid');
  const lookup = section('Public lookup','Only your search query is sent to English Wikipedia.');
  const lookupDescription=lookup.querySelector('.tools-description');
  const lookupForm = el('form','tools-form');
  const query = el('input');
  query.name = 'query'; query.maxLength = 240; query.required = true;
  query.placeholder = 'Search for a topic';
  const method = el('select'); method.name = 'method';
  for (const [value,label] of [['wiki_search','Search Wikipedia articles'],['wiki_research','Read the first Wikipedia article'],['news_search','Recent publisher headlines'],['web_search','General web search'],['weather','Current weather and three-day outlook']]) {
    const option = el('option','',label); option.value=value; method.append(option);
  }
  const look = button('Look up',true); look.type='submit';
  const queryField=field('Wikipedia query',query);
  function updateMethod() {
    const settings = {
      weather: ['Place name','City and optional country code, e.g. Berlin, DE','Open-Meteo receives the place name and coordinates to retrieve the forecast.'],
      news_search: ['News topic','Headlines or a topic','Search the past seven days of headlines from BBC News, The Guardian and NPR. Your topic stays on this computer.'],
      web_search: ['Web search query','Search across websites',searchProvider === 'SearXNG' ? 'Your local SearXNG server passes the query to its configured search engines.' : 'Mwmbl receives your query and returns results from its public web index.'],
    };
    const [label, hint, description] = settings[method.value] || ['Wikipedia query','Search for a topic','Only your search query is sent to English Wikipedia.'];
    queryField.firstChild.textContent=label;
    query.placeholder=hint;
    query.maxLength=method.value==='weather'?120:240;
    query.required=method.value!=='news_search';
    lookupDescription.textContent=description;
  }
  method.onchange=()=>{
    updateMethod();
    hits.replaceChildren();
  };
  lookupForm.append(field('Lookup method',method),queryField,look);
  const hits = el('div','tools-hits'); hits.setAttribute('aria-live','polite');
  lookup.append(lookupForm,hits);
  const exporter = section('Local export','Preview the file and approve it before Ember writes it.');
  const exportForm = el('form','tools-form');
  const type = el('select');
  for (const [value,label] of [['export_note','Text note (.txt)'],['export_event','All-day calendar event (.ics)']]) {
    const option = el('option','',label); option.value=value; type.append(option);
  }
  const title = el('input'); title.name='title';title.required=true;title.maxLength=120;title.placeholder='Name your export';
  const body = el('textarea');body.name='body';body.required=true;body.maxLength=8000;body.rows=3;
  const date = el('input');date.type='date';date.name='date';
  const noteField=field('Note text',body),dateField=field('Event date', date);
  dateField.hidden=true;
  type.onchange=()=>{
    const note=type.value==='export_note';
    noteField.hidden=!note;body.required=note;dateField.hidden=note;date.required=!note;
  };
  const prepare=button('Prepare export',true);prepare.type='submit';
  const exportMessage=el('p','tools-feedback');exportMessage.setAttribute('role','status');
  exportForm.append(field('File type',type),field('Title',title),noteField,dateField,prepare,exportMessage);
  exporter.append(exportForm);
  grid.append(lookup,exporter);
  const approvals=section('Approvals','Review the exact file contents and destination.');
  const approvalList=el('div','tools-list');
  approvals.append(approvalList);
  const activity=section('Tool activity','Recorded arguments, permissions, and outcomes.');
  const activityList=el('div','tools-list'); activity.append(activityList);
  const commands=el('div','tools-commands');
  const error=el('p','form-error');error.setAttribute('role','alert');
  const surface = el('div','tools-view');surface.append(heading,error,grid,approvals,commands,activity);
  root.replaceChildren(surface);

  async function refresh() {
    const state=await call('/api/tools');
    error.textContent='';
    approvedFolder=state.directory;
    searchProvider=state.search_provider || 'Mwmbl';
    updateMethod();
    approvalList.replaceChildren();
    if(!state.pending.length) approvalList.append(el('p','tools-empty','No exports awaiting approval.'));
    for(const item of state.pending) {
      const row=el('article','tools-proposal');
      row.append(el('h3','',item.title),el('p','tools-path',approvedFolder+(approvedFolder.includes('\\')?'\\':'/')+item.filename));
      const preview=el('pre','tools-preview',item.payload);
      const actions=el('div','tools-actions');
      const approve=button('Approve export',true),decline=button('Decline');
      actions.append(approve,decline);row.append(preview,actions);
      for(const [control,decision] of [[approve,'approve'],[decline,'decline']]) {
        control.onclick=async()=>{
          approve.disabled=decline.disabled=true;
          try {
            const outcome=await call('/api/tools/proposals/'+item.id+'/'+decision,{method:'POST',body:{}});
            exportMessage.textContent='';
            notify(outcome.status==='approved'?'File exported to '+approvedFolder:'Export declined.');
            await refresh();
          } catch(reason) {if(!controller.signal.aborted) error.textContent=reason.message;}
          finally {approve.disabled=decline.disabled=false;}
        };
      }
      approvalList.append(row);
    }
    const files=el('div','tools-files');
    for(const item of state.resolved) {
      const row=el('p','');
      row.append(el('strong','',item.status==='approved'?'Exported':'Declined'),document.createTextNode(' · '+item.filename));
      files.append(row);
    }
    if(state.resolved.length){
      files.prepend(el('p','tools-path',approvedFolder));
      approvalList.append(details('Recent export decisions',files));
    }
    activityList.replaceChildren();
    if(!state.activity.length)activityList.append(el('p','tools-empty','No tool calls yet.'));
    for(const entry of state.activity) {
      const item=el('div','tools-log');
      const definition=state.tools.find(t=>t.name===entry.tool);
      const dateText=new Date(entry.at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      const body=el('div','tools-log-detail');
      body.append(el('p','',entry.result),el('pre','tools-preview',JSON.stringify(entry.arguments,null,2)),el('small','',entry.permission+' · '+entry.elapsed_ms+' ms · '+entry.status));
      item.append(details((definition?.label || entry.tool)+' · '+dateText,body));activityList.append(item);
    }
    const examples=el('dl','tools-examples');
    for(const tool of state.tools) examples.append(el('dt','',tool.label),el('dd','',tool.usage));
    commands.replaceChildren(details('Use tools in a conversation',examples));
  }

  lookupForm.onsubmit=async event=>{
    event.preventDefault();look.disabled=method.disabled=query.disabled=true;
    const weather=method.value==='weather';
    const source = {weather:'Open-Meteo',news_search:'publisher feeds',web_search:searchProvider}[method.value] || 'Wikipedia';
    hits.replaceChildren(el('p','tools-feedback','Checking '+source+'…'));
    try {
      const term=method.value==='news_search' && !query.value.trim()?'headlines':query.value;
      const result=await call('/api/tools/run',{method:'POST',body:{tool:method.value,arguments:weather?{place:term}:{query:term}}});
      hits.replaceChildren();
      if(result.notice) hits.append(el('p','tools-feedback',result.notice));
      for(const hit of result.context || []) {
        const article=el('article','tools-hit');
        article.append(sourceLink(hit));
        const attribution=sourceAttribution(hit);
        if(attribution) {
          const byline=el('p');
          byline.append(el('small','',attribution));
          article.append(byline);
        }
        if(hit.text) article.append(el('p','',hit.text));
        hits.append(article);
      }
      if(!hits.children.length)hits.append(el('p','tools-feedback',result.response));
      await refresh();
    } catch(reason) {
      if(!controller.signal.aborted){hits.replaceChildren(el('p','form-error',reason.message));await refresh().catch(()=>{});}
    } finally {look.disabled=method.disabled=query.disabled=false;}
  };
  exportForm.onsubmit=async event=>{
    event.preventDefault();prepare.disabled=true;exportMessage.textContent='';
    try {
      const args=type.value==='export_note'?{title:title.value,body:body.value}:{title:title.value,date:date.value};
      await call('/api/tools/run',{method:'POST',body:{tool:type.value,arguments:args}});
      await refresh();
      exportMessage.textContent='Ready for review below.';
      approvals.scrollIntoView({block:'start'});
    } catch(reason){if(!controller.signal.aborted) exportMessage.textContent=reason.message;}
    finally {prepare.disabled=false;}
  };
  refresh().catch(reason=>{if(!controller.signal.aborted)error.textContent=reason.message;});
  return ()=>controller.abort();
}

