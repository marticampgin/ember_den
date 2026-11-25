import {api, el, icon, notify, openDialog} from './ui.js';
import './library.js';
import {advancedConfig, profileDialog} from './configuration.js';
import {mountAppearance} from './appearance.js';
import {mountVoiceSettings} from './voice.js';

function formError(root, error) {
  const node = root.querySelector('.form-error');
  node.hidden = false;
  node.textContent = error.message || 'The request could not be completed.';
}

function memoryEditor(memory, refresh) {
  openDialog(memory ? 'Edit memory' : 'Add a memory', dialog => {
    const form = el('form', 'stack-form');
    form.innerHTML = `
      <label for="memory-text">What should Ember remember?</label>
      <textarea id="memory-text" rows="5" maxlength="2000" required autofocus placeholder="A preference, project detail, or fact about you…"></textarea>
      <p class="form-error" role="alert" hidden></p>
      <div class="form-actions"><button type="button" class="button" data-cancel>Cancel</button><button class="button primary">Save memory</button></div>`;
    const input = form.querySelector('textarea');
    input.value = memory?.text || '';
    form.querySelector('[data-cancel]').onclick = () => dialog.close();
    form.onsubmit = async event => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      const save = form.querySelector('.primary');
      save.disabled = true;
      try {
        const path = memory ? `/api/memories/${encodeURIComponent(memory.id)}` : '/api/memories';
        await api(path, {method: memory ? 'PATCH' : 'POST', body: {text}});
        dialog.close();
        notify('Memory saved.');
        await refresh();
      } catch (error) { formError(form, error); }
      finally { save.disabled = false; }
    };
    dialog.append(form);
  });
}

function confirmRemove(memory, refresh) {
  openDialog('Remove this memory?', dialog => {
    const excerpt = el('blockquote', 'memory-excerpt', memory.text);
    const actions = el('div', 'form-actions');
    actions.innerHTML = '<button type="button" class="button">Keep memory</button><button type="button" class="button primary">Remove memory</button>';
    actions.firstElementChild.autofocus = true;
    actions.firstElementChild.onclick = () => dialog.close();
    actions.lastElementChild.onclick = async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api(`/api/memories/${encodeURIComponent(memory.id)}`, {method: 'DELETE'});
        dialog.close();
        notify('Memory removed.');
        await refresh();
      } catch (error) { formError(dialog, error); }
      finally { button.disabled = false; }
    };
    const error = el('p', 'form-error');
    error.setAttribute('role', 'alert');
    error.hidden = true;
    const scope = el('p', 'delete-prompt', 'Ember will stop retrieving this memory. Earlier conversations and saved run snapshots keep their original text.');
    dialog.append(excerpt, scope, error, actions);
  });
}

export async function mountMemories(root) {
  root.innerHTML = `
    <header class="page-heading split-heading"><div><h1>Memories</h1><p class="page-subtitle">The details Ember can recall across conversations.</p></div>
      <button type="button" class="button primary" id="add-memory">${icon('plus', 16)}Add a memory</button></header>
    <section class="memory-list" aria-label="Saved memories" aria-busy="true"></section>
    <ember-library></ember-library>`;
  const list = root.querySelector('.memory-list');
  async function refresh() {
    list.setAttribute('aria-busy', 'true');
    try {
      const {memories} = await api('/api/memories');
      if (!list.isConnected) return;
      list.replaceChildren();
      if (!memories.length) {
        const empty = el('div', 'empty-state');
        empty.innerHTML = `<div class="empty-symbol">${icon('memories', 28)}</div><h2>No saved memories yet</h2><p>Add a detail you’d like Ember to bring into future chats.</p>`;
        list.append(empty);
      }
      for (const memory of memories) {
        const row = el('article', 'memory-row');
        const content = el('div');
        const note = el('p', 'memory-text', memory.text);
        content.append(note);
        if (memory.updated_at) {
          const updated = new Date(memory.updated_at);
          if (!Number.isNaN(updated.getTime())) content.append(el('span', 'memory-date', 'Updated ' + updated.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'})));
        }
        const actions = el('div', 'row-actions');
        actions.innerHTML = `<button class="icon-button" type="button" aria-label="Edit memory" title="Edit memory">${icon('edit', 17)}</button><button class="icon-button" type="button" aria-label="Remove memory" title="Remove memory">${icon('trash', 17)}</button>`;
        actions.firstElementChild.onclick = () => memoryEditor(memory, refresh);
        actions.lastElementChild.onclick = () => confirmRemove(memory, refresh);
        row.append(content, actions);
        list.append(row);
      }
    } catch (error) {
      list.replaceChildren(el('p', 'form-error', error.message));
    } finally { list.setAttribute('aria-busy', 'false'); }
  }
  root.querySelector('#add-memory').onclick = () => memoryEditor(null, refresh);
  await refresh();
}

export async function mountSettings(root, refreshStatus) {
  root.innerHTML = `
    <header class="page-heading"><h1>Settings</h1><p class="page-subtitle">Configure Ember’s everyday conversations.</p></header>
    <div class="settings-pane" aria-busy="true">Loading settings…</div>`;
  const pane = root.querySelector('.settings-pane');
  const status = await refreshStatus();
  if (!pane.isConnected) return;
  const settings = status.settings || await api('/api/settings');
  pane.setAttribute('aria-busy', 'false');
  pane.innerHTML = `
    <form class="settings-form">
      <section><h2>Local inference</h2>
        <div class="field-row"><div><label for="settings-model">Active model</label><p>Installed models provided by Ollama.</p></div><select name="model" id="settings-model"></select></div>
        <div class="field-row"><div><label for="temperature">Temperature</label><p>Lower values favor more consistent replies.</p></div><div class="range-field"><input type="range" id="temperature" name="temperature" min="0" max="2" step="0.05"><output for="temperature"></output></div></div>
        <div class="field-row"><div><label for="context-length">Context window</label><p>Maximum number of tokens per request.</p></div><input type="number" name="context_length" id="context-length" min="1024" max="16384" step="1" required></div>
      </section>
      <section><h2>Routing and retrieval</h2><div class="config-fields">${advancedConfig(settings, status)}</div></section>
      <section><h2>Companion</h2>
        <div class="field-row"><div><label for="use-memory">Use saved memories</label><p>Include relevant details when assembling a reply.</p></div><input type="checkbox" role="switch" class="switch" name="memory_enabled" id="use-memory"></div>
        <div class="prompt-field"><label for="system-prompt">System prompt</label><p>The instructions that shape Ember’s voice and behavior.</p><textarea id="system-prompt" rows="4" name="system_prompt" maxlength="4000" required></textarea></div>
      </section>
      <p class="form-error" role="alert" hidden></p>
      <div class="form-actions"><button type="button" class="button" data-profiles>Profiles</button><button type="submit" class="button primary">Save settings</button></div>
    </form>`;
  const form = pane.querySelector('form');
  const controls = form.elements;
  const choices = (status.models || []).map(model => model.name);
  if (settings.model && !choices.includes(settings.model)) choices.push(settings.model);
  for (const name of choices) controls.model.add(new Option(name, name));
  if (!choices.length) {
    controls.model.add(new Option(status.online ? 'No models installed' : 'Ollama offline', ''));
    controls.model.disabled = true;
  }
  if (settings.model) controls.model.value = settings.model;
  controls.temperature.value = settings.temperature;
  controls.context_length.value = settings.context_length;
  controls.memory_enabled.checked = Boolean(settings.memory_enabled);
  controls.system_prompt.value = settings.system_prompt;
  const output = pane.querySelector('output');
  const updateTemp = () => { output.value = Number(controls.temperature.value).toFixed(2); };
  controls.temperature.addEventListener('input', updateTemp);
  updateTemp();
  const readConfig = () => {
    const body = {
      temperature: Number(controls.temperature.value),
      context_length: Number(controls.context_length.value),
      memory_enabled: controls.memory_enabled.checked,
      system_prompt: controls.system_prompt.value.trim(),
    };
    if (controls.model.value) body.model = controls.model.value;
    for (const select of form.querySelectorAll('[data-setting]')) body[select.dataset.setting]=select.value;
    return {...settings,...body};
  };
  form.querySelector('[data-profiles]').onclick = () => profileDialog(readConfig(), async config=>{
    await api('/api/settings',{method:'PATCH',body:config});
    await mountSettings(root, refreshStatus);
    notify('Profile restored.');
  });
  mountAppearance(pane);
  if(root.isConnected) mountVoiceSettings(root);
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const body = readConfig();
    button.disabled = true;
    form.querySelector('.form-error').hidden = true;
    try {
      await api('/api/settings', {method: 'PATCH', body});
      await refreshStatus();
      notify('Settings saved.');
    } catch (error) { formError(form, error); }
    finally { button.disabled = false; }
  };
}
