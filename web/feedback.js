import {api, el, notify, openDialog} from './ui.js';

export function feedbackButton(runId) {
  const button = el('button', 'turn-feedback', 'Feedback');
  button.type = 'button';
  button.onclick = () => {
    const path = '/api/runs/' + encodeURIComponent(runId) + '/feedback';
    openDialog('Reply feedback', dialog => {
      const panel = el('div', 'feedback-body', 'Loading feedback…');
      dialog.append(panel);
      loadForm(panel, dialog, path);
    });
  };
  return button;
}

async function loadForm(panel, dialog, path) {
  let saved;
  try { saved = await api(path); }
  catch (error) {
    panel.className = 'form-error';
    panel.textContent = error.message;
    const retry = el('button', 'button', 'Retry');
    retry.onclick = () => loadForm(panel, dialog, path);
    panel.append(retry);
    return;
  }
  if (!panel.isConnected) return;
  panel.className = 'feedback-body';
  const form = el('form', 'stack-form');
  form.innerHTML = '<fieldset class="feedback-rating"><legend>Was this reply helpful?</legend>' +
    '<label><input type="radio" name="rating" value="helpful" required> Helpful</label>' +
    '<label><input type="radio" name="rating" value="unhelpful"> Unhelpful</label></fieldset>' +
    '<label for="reply-note">Feedback note</label><textarea name="note" id="reply-note" rows="3" maxlength="2000" placeholder="Optional"></textarea>' +
    '<label class="feedback-remember"><input type="checkbox" name="remember"> Remember this preference</label>' +
    '<p class="feedback-hint"></p><p class="form-error" role="alert" hidden></p>' +
    '<div class="form-actions"><button type="button" class="button" data-cancel>Cancel</button><button class="button primary">Save feedback</button></div>';
  panel.replaceChildren(form);
  const {rating, note, remember} = form.elements;
  if (saved.rating) rating.value = saved.rating;
  note.value = saved.note || '';
  form.querySelector('[data-cancel]').onclick = () => dialog.close();
  const hint = form.querySelector('.feedback-hint');
  if (saved.memory_id) {
    remember.checked = true;
    remember.disabled = true;
    hint.textContent = 'The saved preference is independently editable in Memories.';
  } else {
    hint.textContent = 'Selecting this saves your note in Memories for future conversations.';
    remember.onchange = () => { note.required = remember.checked; note.placeholder = remember.checked ? 'What should Ember remember?' : 'Optional'; };
  }
  form.onsubmit = async event => {
    event.preventDefault();
    const message = note.value.trim();
    const savePreference = remember.checked && !remember.disabled;
    if (savePreference && !message) {
      note.focus();
      return;
    }
    const submit = form.querySelector('.primary');
    const error = form.querySelector('.form-error');
    submit.disabled = true;
    error.hidden = true;
    try {
      await api(path, {method:'POST', body:{rating:rating.value, note:message, remember:savePreference}});
      dialog.close();
      notify(savePreference ? 'Feedback and preference saved.' : 'Feedback saved.');
    } catch (reason) {
      error.hidden = false;
      error.textContent = reason.message;
    } finally { submit.disabled = false; }
  };
}
