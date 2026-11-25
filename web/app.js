import {feedbackButton} from './feedback.js';
import './scene.js';
import {VoiceControls, readButton} from './voice.js';
import {api, el, icon, notify, openDialog, renderResponse} from './ui.js';
import {mountMemories, mountSettings} from './preferences.js';

const main = document.querySelector('#page-content');
const sidebar = document.querySelector('#sidebar');
const menu = document.querySelector('#menu-toggle');
const backdrop = document.querySelector('#nav-backdrop');
const navigation = [
  ['companion', 'Companion', 'message'],
  ['laboratory', 'Laboratory', 'laboratory'],
  ['memories', 'Memories', 'memories'],
  ['tools', 'Tools', 'tools'],
  ['settings', 'Settings', 'settings'],
];
const starters = [
  ['code', 'Help with code', 'Help me work through a coding problem. '],
  ['bulb', 'Brainstorm an idea', 'Help me brainstorm ideas for '],
  ['page', 'Plan a task', 'Help me break this task into manageable steps: '],
  ['memories', 'Recall something', 'What do you remember about '],
];
const cache = new Map();
let activeChat, activeJob, labCleanup, lastChatId;
let runtime = {online: false, models: [], settings: {}, checking: true};
let routeVersion = 0;
let historyVersion = 0;
let statusVersion = 0;
let pendingPoll, voice;
const newChat = () => ({id: null, draft: '', messages: []});
const chatPath = id => id ? '#chat/' + encodeURIComponent(id) : '#companion';

function showMenu(open) {
  sidebar.classList.toggle('is-open', open);
  main.inert = open;
  backdrop.hidden = !open;
  document.body.classList.toggle('nav-open', open);
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  menu.innerHTML = icon(open ? 'close' : 'menu', 21);
  if (open) sidebar.querySelector('a')?.focus();
}

function go(hash) {
  showMenu(false);
  if (location.hash === hash) return renderRoute();
  location.hash = hash;
}

function buildNavigation() {
  const nav = document.querySelector('.main-nav');
  for (const [route, label, glyph] of navigation) {
    const link = el('a', 'nav-link');
    link.href = '#' + route;
    link.dataset.route = route;
    link.innerHTML = icon(glyph, 20);
    link.append(el('span', '', label));
    link.onclick = () => showMenu(false);
    nav.append(link);
  }
  document.querySelector('#new-chat').innerHTML = icon('plus', 18) + 'New conversation';
  document.querySelector('#new-chat').onclick = () => {
    cache.set('new', newChat());
    lastChatId = null;
    go('#companion');
  };
  document.querySelector('.model-icon').innerHTML = icon('chip', 18);
  document.querySelector('.model-chevron').innerHTML = icon('chevron', 14);
  for (const id of ['#model-status', '#runtime-indicator']) {
    document.querySelector(id).onclick = () => go('#settings');
  }
  menu.onclick = () => showMenu(menu.getAttribute('aria-expanded') !== 'true');
  backdrop.onclick = () => { showMenu(false); menu.focus(); };
  window.matchMedia('(min-width: 761px)').addEventListener('change', event => {
    if (event.matches) showMenu(false);
  });
  document.addEventListener('keydown', event => {
    if (menu.getAttribute('aria-expanded') !== 'true') return;
    if (event.key === 'Escape') { showMenu(false); menu.focus(); }
    if (event.key === 'Tab') {
      const targets = [menu, ...sidebar.querySelectorAll('a[href],button:not(:disabled)')];
      if (event.shiftKey && document.activeElement === menu) {
        event.preventDefault();
        targets.at(-1).focus();
      } else if (!event.shiftKey && document.activeElement === targets.at(-1)) {
        event.preventDefault(); menu.focus();
      }
    }
  });
  document.querySelector('.skip-link').onclick = event => { event.preventDefault(); main.focus(); };
  showMenu(false);
}

function markNavigation(route) {
  for (const link of sidebar.querySelectorAll('.nav-link')) {
    if (link.dataset.route === route) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  sidebar.querySelector('[data-route="companion"]').href = chatPath(lastChatId);
  for (const item of sidebar.querySelectorAll('.history-link')) {
    item.classList.toggle('selected', route === 'companion' && item.dataset.id === activeChat?.id);
  }
}

async function refreshHistory() {
  const version = ++historyVersion;
  const list = document.querySelector('#history-list');
  try {
    const {conversations} = await api('/api/conversations');
    if (version !== historyVersion) return;
    list.replaceChildren();
    if (!conversations.length) list.append(el('p', 'history-empty', 'No conversations yet.'));
    for (const conversation of conversations) {
      const link = el('a', 'history-link');
      link.href = chatPath(conversation.id);
      link.dataset.id = conversation.id;
      link.classList.toggle('selected', conversation.id === activeChat?.id);
      link.title = conversation.title;
      link.innerHTML = icon('message', 15);
      link.append(el('span', '', conversation.title));
      link.onclick = () => showMenu(false);
      const row = el('div', 'history-row');
      const remove = el('button', 'icon-button history-delete');
      remove.type = 'button';
      remove.innerHTML = icon('trash', 14);
      remove.setAttribute('aria-label', 'Delete conversation: ' + conversation.title);
      remove.title = 'Delete conversation';
      remove.disabled = activeJob?.chat.id === conversation.id;
      remove.onclick = () => confirmDeleteConversation(conversation);
      row.append(link, remove);
      list.append(row);
    }
  } catch {
    if (version === historyVersion) list.replaceChildren(el('p', 'history-empty', 'History unavailable.'));
  }
}

function confirmDeleteConversation(conversation) {
  openDialog('Delete conversation?', dialog => {
    dialog.append(el('p', 'delete-prompt', 'Remove “' + conversation.title + '” and its chat turns and traces from this device?'));
    const error = el('p', 'form-error');
    error.setAttribute('role', 'alert');
    const actions = el('div', 'form-actions');
    const keep = el('button', 'button', 'Keep conversation');
    keep.autofocus = true;
    keep.onclick = () => dialog.close();
    const remove = el('button', 'button primary', 'Delete conversation');
    remove.onclick = async () => {
      remove.disabled = true;
      try {
        await api('/api/conversations/' + encodeURIComponent(conversation.id), {method: 'DELETE'});
        cache.delete(conversation.id);
        if (lastChatId === conversation.id) {
          lastChatId = null;
          sidebar.querySelector('[data-route="companion"]').href = '#companion';
        }
        dialog.close();
        if (activeChat?.id === conversation.id) go('#companion');
        await refreshHistory();
        notify('Conversation deleted.');
      } catch (reason) { error.textContent = reason.message; remove.disabled = false; }
    };
    actions.append(keep, remove);
    dialog.append(error, actions);
  });
}

async function refreshStatus() {
  const version = ++statusVersion;
  let response;
  try { response = await api('/api/status'); }
  catch (error) { response = {online: false, models: [], error: error.message}; }
  if (version < statusVersion) return response;
  runtime = response;
  const name = runtime.settings?.model;
  document.querySelector('#runtime-indicator').classList.toggle('online', runtime.online);
  document.querySelector('#runtime-label').textContent = runtime.online ? 'Local · On this device' : 'Local runtime offline';
  document.querySelector('#model-name').textContent = name || (runtime.online ? 'No model installed' : 'Unavailable');
  if (activeChat) updateCompanionControls();
  return runtime;
}

async function renderRoute() {
  const version = ++routeVersion;
  clearTimeout(pendingPoll);
  voice?.dispose();
  voice = null;
  if (labCleanup) { labCleanup(); labCleanup = null; }
  activeChat = null;
  const [slug = '', id, subId] = location.hash.slice(1).split('/');
  const route = navigation.find(([name]) => name === slug)?.[0] || 'companion';
  showMenu(false);
  const page = el('div', 'view view-' + route);
  main.replaceChildren(page);
  markNavigation(route);
  window.scrollTo(0, 0);
  document.title = (route === 'companion' ? 'The clearing' : navigation.find(([name]) => name === route)[1]) + ' · Ember Den';
  try {
    if (route === 'laboratory') {
      const {mountLaboratory} = await import('./laboratory.js');
      if (version !== routeVersion) return;
      const initialTraceId = id === 'trace' && subId ? decodeURIComponent(subId) : null;
      const cleanup = await mountLaboratory(page, {api, notify, onSettingsChange: refreshStatus, initialTraceId});
      if (version === routeVersion) labCleanup = cleanup;
      else cleanup?.();
    } else if (route === 'memories') await mountMemories(page);
    else if (route === 'settings') {
      await mountSettings(page, refreshStatus);
    } else if (route === 'tools') {
      const {mountTools} = await import('./tools.js');
      if (!page.isConnected) return;
      const cleanup = await mountTools(page, {api, notify});
      if (version === routeVersion) labCleanup = cleanup;
      else cleanup?.();
    }
    else {
      const key = slug === 'chat' && id ? decodeURIComponent(id) : 'new';
      let chat = cache.get(key);
      if (key !== 'new' && (!activeJob || activeJob.chat !== chat)) {
        page.append(el('p', 'page-subtitle', 'Opening conversation…'));
        const saved = await api('/api/conversations/' + encodeURIComponent(key));
        chat = {...newChat(), id: saved.id, draft: chat?.draft || '', messages: saved.messages};
      }
      if (version !== routeVersion) return;
      activeChat = chat || newChat();
      cache.set(key, activeChat);
      lastChatId = activeChat.id;
      renderCompanion(page);
      markNavigation('companion');
      followPendingTurn(activeChat);
    }
  } catch (error) {
    if (version !== routeVersion) return;
    page.replaceChildren(el('h1', '', 'Unable to open this view'), el('p', 'form-error', error.message));
    const back = el('a', 'button', 'Return to the clearing');
    back.href = '#companion';
    page.append(back);
  }
}

function followPendingTurn(chat) {
  if (!chat.id || chat.messages.at(-1)?.status !== 'running' || activeJob?.chat === chat) return;
  pendingPoll = setTimeout(async () => {
    if (activeChat !== chat) return;
    try {
      const saved = await api('/api/conversations/' + encodeURIComponent(chat.id));
      if (activeChat !== chat) return;
      chat.messages = saved.messages;
      renderTranscript(chat);
      updateCompanionControls();
    } catch {
      if (activeChat !== chat) return;
    }
    followPendingTurn(chat);
  }, 1500);
}

function renderCompanion(page) {
  page.innerHTML = `
    <header class="page-heading clearing-heading"><h1>The clearing</h1>
      <p class="page-subtitle companion-state" role="status"><span class="dot"></span><span id="companion-state"></span></p></header>
    <ember-scene state="idle"></ember-scene>
    <section id="transcript" class="transcript" aria-label="Conversation"></section>
    <div class="composer-area">
      <form class="composer" id="composer">
        <textarea id="prompt" rows="1" maxlength="12000" aria-label="Message Ember" placeholder="Message Ember…" spellcheck="true"></textarea>
        <a class="composer-tool" href="#tools" aria-label="Open tools" title="Open tools">${icon('tools', 17)}</a>
        <button type="button" class="icon-button voice-mic" aria-label="Dictate a message" title="Dictate a message" data-voice-mic aria-pressed="false"></button>
        <button type="submit" class="send-button" id="send"></button>
      </form>
      <div class="voice-feedback"><span role="status" data-voice-status></span><button type="button" data-voice-stop hidden>Stop voice</button></div>
      <div class="composer-actions"><div class="starter-list" aria-label="Conversation starters"></div>
        <button type="button" class="hands-free-toggle" data-hands-free aria-pressed="false"
          title="Automatically send each spoken turn after a pause and read replies aloud">Start hands-free</button></div>
    </div>`;
  const input = page.querySelector('#prompt');
  input.value = activeChat.draft;
  const chat = activeChat;
  voice = new VoiceControls(page, input, updateCompanionControls, {
    submitTurn(text) {
      if (activeChat !== chat || activeJob || chat.messages.at(-1)?.status === 'running') return false;
      sendMessage(chat, text);
      return true;
    },
    interruptTurn() {
      if (activeJob?.chat === chat) stopResponse(activeJob);
    },
  });
  input.oninput = () => {
    activeChat.draft = input.value;
    sizeInput(input);
    updateCompanionControls();
  };
  input.onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!activeJob && !page.querySelector('#send').disabled) page.querySelector('form').requestSubmit();
    }
  };
  page.querySelector('#composer').onsubmit = event => {
    event.preventDefault();
    if (activeJob?.chat === activeChat) {
      if (voice?.handsFree) voice.stop('Hands-free ended.');
      else stopResponse(activeJob);
      return;
    }
    if (!activeJob && !page.querySelector('#send').disabled && input.value.trim()) sendMessage(activeChat, input.value.trim());
  };
  for (const [glyph, label, prompt] of starters) {
    const button = el('button', 'starter');
    button.type = 'button';
    button.innerHTML = icon(glyph, 17);
    button.append(label);
    button.onclick = () => {
      input.value = prompt;
      input.dispatchEvent(new Event('input'));
      input.focus();
      input.setSelectionRange(prompt.length, prompt.length);
    };
    page.querySelector('.starter-list').append(button);
  }
  renderTranscript(activeChat);
  sizeInput(input);
  updateCompanionControls();
}

function sizeInput(input) {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 154) + 'px';
}

function appendMessage(message) {
  const row = el('article', 'chat-message ' + (message.role === 'user' ? 'from-user' : 'from-ember'));
  const author = el('h2', 'message-label', message.role === 'user' ? 'You' : 'Ember');
  const body = el('div', 'message-body');
  if (message.role === 'assistant') renderResponse(body, message.content || '');
  else body.textContent = message.content;
  row.append(author, body);
  document.querySelector('#transcript').append(row);
  return body;
}

function renderTranscript(chat) {
  main.querySelector('#transcript').replaceChildren();
  for (let index = 0; index < chat.messages.length; index++) {
    const message = chat.messages[index];
    const body = appendMessage(message);
    const failed = ['stopped', 'error'].includes(message.status);
    const noAnswer = !message.run_id || message.run_id !== chat.messages[index + 1]?.run_id;
    if (message.role !== 'assistant' && !(failed && noAnswer)) continue;
    if (message.status === 'running') continue;
    const footer = el('div', 'turn-detail');
    if (failed) {
      const reason = message.error || (message.status === 'stopped' ? 'Response stopped.' : 'The response could not be completed.');
      footer.append(el('span', 'turn-issue', reason));
    }
    if (message.run_id) {
      const inspect = el('a', 'trace-link');
      inspect.href = '#laboratory/trace/' + encodeURIComponent(message.run_id);
      inspect.textContent = 'Inspect run';
      inspect.insertAdjacentHTML('beforeend', icon('arrow', 13));
      footer.append(inspect);
      if (message.role === 'assistant') footer.append(feedbackButton(message.run_id));
    }
    if (message.role === 'assistant' && message.content) {
      if (message.content.includes('Review in Tools')) {
        const review = el('a', 'trace-link', 'Review in Tools');
        review.href = '#tools';
        footer.append(review);
      }
      footer.append(readButton(message.content, () => voice));
    }
    if (footer.childElementCount) body.after(footer);
  }
}

function updateCompanionControls() {
  if (!activeChat) return;
  const mine = activeJob?.chat === activeChat;
  const lastStatus = activeChat.messages.at(-1)?.status;
  const voiceState = {recording:'listening', transcribing:'thinking', synthesizing:'thinking', speaking:'responding'}[voice?.mode];
  const state = mine ? activeJob.state : voiceState || (lastStatus === 'running' ? 'thinking' : activeChat.draft.trim() ? 'listening' : lastStatus === 'error' ? 'error' : 'idle');
  const scene = main.querySelector('ember-scene');
  if (!scene) return;
  if (scene.getAttribute('state') !== state) scene.setAttribute('state', state);
  const statuses = {
    idle: 'Ember is resting in the clearing.',
    listening: 'Ember is listening.',
    thinking: 'Ember is thinking.',
    responding: 'Ember is responding.',
    error: 'Ember is waiting.',
  };
  const stateLabel = main.querySelector('#companion-state');
  if (stateLabel.textContent !== statuses[state]) stateLabel.textContent = statuses[state];
  main.querySelector('.companion-state').dataset.state = state;
  const send = main.querySelector('#send');
  send.classList.toggle('is-stop', mine);
  send.innerHTML = mine ? icon('stop', 15) + (activeJob.stopping ? 'Stopping…' : 'Stop') : icon('send', 21);
  send.title = mine ? 'Stop response' : 'Send message';
  send.setAttribute('aria-label', send.title);
  const recording = ['opening', 'recording', 'transcribing'].includes(voice?.mode);
  send.disabled = mine ? Boolean(activeJob.stopping) : recording || Boolean(activeJob) || lastStatus === 'running' || !activeChat.draft.trim();
  main.querySelector('.view-companion').classList.toggle('has-messages', activeChat.messages.length > 0);
  main.querySelector('#transcript').setAttribute('aria-busy', String(mine));
  main.querySelector('.starter-list').hidden = activeChat.messages.length > 0 || Boolean(voice?.handsFree);
  main.querySelector('[data-hands-free]').disabled = !voice?.handsFree && (Boolean(activeJob) || lastStatus === 'running');
}

async function stopResponse(job) {
  if (job.stopping) return;
  job.stopping = true;
  updateCompanionControls();
  try {
    if (job.runId) {
      await api('/api/runs/' + encodeURIComponent(job.runId) + '/stop', {method: 'POST', body: {}, keepalive: true});
    }
  } catch {
    // Closing the stream also releases inference when the stop endpoint is unavailable.
  } finally { job.controller.abort(); }
}

async function consumeStream(response, handle) {
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `Request failed (HTTP ${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const {done, value} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) if (line.trim()) handle(JSON.parse(line));
      if (done) {
        if (buffer.trim()) handle(JSON.parse(buffer));
        break;
      }
    }
  } finally { reader.releaseLock(); }
}

async function sendMessage(chat, prompt) {
  const job = {chat, controller: new AbortController(), state: 'thinking'};
  const spokenTurn = voice?.sending();
  const replyVoice = voice;
  activeJob = job;
  chat.draft = '';
  const input = main.querySelector('#prompt');
  input.value = '';
  sizeInput(input);
  const user = {role: 'user', content: prompt, status: 'running'};
  chat.messages.push(user);
  appendMessage(user);
  updateCompanionControls();
  main.querySelector('.composer-area').scrollIntoView({block: 'nearest'});
  let assistant, completed = false;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt, ...(chat.id ? {conversation_id: chat.id} : {})}),
      signal: job.controller.signal,
    });
    await consumeStream(response, event => {
      if (event.type === 'start') {
        job.runId = user.run_id = event.run_id;
        chat.id = event.conversation_id;
        cache.set(chat.id, chat);
        if (cache.get('new') === chat) cache.delete('new');
        if (activeChat === chat) {
          lastChatId = chat.id;
          history.replaceState({}, '', chatPath(chat.id));
          markNavigation('companion');
        }
        refreshHistory();
      } else if (event.type === 'token') {
        if (!assistant) {
          assistant = {role: 'assistant', content: '', status: 'running', run_id: job.runId};
          chat.messages.push(assistant);
          job.state = 'responding';
          if (activeChat === chat) { appendMessage(assistant); updateCompanionControls(); }
        }
        assistant.content += event.text;
        if (activeChat === chat) {
          const closeToEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120;
          main.querySelector('#transcript .chat-message:last-child .message-body').append(event.text);
          if (closeToEnd) window.scrollTo(0, document.documentElement.scrollHeight);
        }
      } else if (event.type === 'error') throw new Error(event.error || 'The response could not be completed.');
      else if (event.type === 'done') completed = true;
    });
    if (!completed) throw new Error('The local connection ended before the response finished.');
    user.status = 'completed';
  } catch (error) {
    const stopped = job.stopping || error.name === 'AbortError';
    user.status = stopped ? 'stopped' : 'error';
    user.error = stopped ? 'Response stopped.' : error.message;
    if (replyVoice?.handsFree && voice === replyVoice) replyVoice.stop('Hands-free ended. ' + user.error, false);
  } finally {
    activeJob = null;
    if (assistant) Object.assign(assistant, {status: user.status, error: user.error});
    if (activeChat === chat) {
      renderTranscript(chat);
      if (completed && spokenTurn && voice === replyVoice && voice.serial === spokenTurn.serial) voice.speak(assistant?.content, spokenTurn);
    }
    updateCompanionControls();
    refreshHistory();
  }
}

buildNavigation();
window.addEventListener('hashchange', renderRoute);
renderRoute();
refreshStatus();
refreshHistory();
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { refreshStatus(); refreshHistory(); }
});
