import {api, el, icon, notify, openDialog} from './ui.js';

class EmberLibrary extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <link rel="stylesheet" href="./library.css">
      <section class="library-section" aria-labelledby="library-heading">
        <div class="library-heading"><div><h2 id="library-heading">Document library</h2>
          <p>Import a text or Markdown file to use it in conversation.</p></div>
          <button type="button" class="button" data-import>${icon('plus', 16)}Import file</button>
          <input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" hidden>
        </div>
        <p class="form-error" role="alert" hidden></p>
        <div data-entries></div>
      </section>`;
    const fileInput = this.querySelector('input');
    this.querySelector('[data-import]').onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      const file = fileInput.files[0];
      if (!file) return;
      const button = this.querySelector('[data-import]');
      button.disabled = true;
      this.querySelector('.form-error').hidden = true;
      try {
        if (!/\.(txt|md|markdown)$/i.test(file.name) || file.size > 120000) {
          throw new Error('Choose a .txt or .md file up to 120 KB.');
        }
        let text;
        try { text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); }
        catch { throw new Error('Save the file as UTF-8 text and import it again.'); }
        await api('/api/documents', {method: 'POST', body: {name: file.name, text}});
        await this.refresh();
        notify('Document imported.');
      } catch (error) { this.showError(error); }
      finally { button.disabled = false; fileInput.value = ''; }
    };
    this.refresh();
  }
  showError(error) {
    const node = this.querySelector('.form-error');
    node.textContent = error.message;
    node.hidden = false;
  }
  async refresh() {
    try {
      const {documents} = await api('/api/documents');
      const list = this.querySelector('[data-entries]');
      list.replaceChildren();
      for (const item of documents) {
        const row = el('div', 'library-row');
        const open = el('button', 'library-name');
        open.type = 'button';
        open.innerHTML = icon('page', 17);
        open.append(el('span', '', item.name));
        open.onclick = () => this.read(item.id);
        const size = el('span', 'library-size', `${item.characters.toLocaleString()} characters`);
        const remove = el('button', 'icon-button');
        remove.type = 'button';
        remove.setAttribute('aria-label', `Remove ${item.name}`);
        remove.innerHTML = icon('trash', 16);
        remove.onclick = () => this.confirmRemoval(item);
        row.append(open, size, remove);
        list.append(row);
      }
    } catch (error) { this.showError(error); }
  }
  async read(id) {
    try {
      const item = await api(`/api/documents/${encodeURIComponent(id)}`);
      openDialog(item.name, dialog => dialog.append(el('pre', 'library-preview', item.text)));
    } catch (error) { this.showError(error); }
  }
  confirmRemoval(item) {
    openDialog('Remove document?', dialog => {
      dialog.append(el('p', 'library-prompt', `Remove “${item.name}” from future retrieval? Existing conversations and run snapshots keep their excerpts.`));
      const actions = el('div', 'form-actions');
      const cancel = el('button', 'button', 'Keep document');
      const remove = el('button', 'button primary', 'Remove document');
      cancel.onclick = () => dialog.close();
      remove.onclick = async () => {
        remove.disabled = true;
        try {
          await api(`/api/documents/${encodeURIComponent(item.id)}`, {method: 'DELETE'});
          dialog.close();
          await this.refresh();
          notify('Document removed.');
        } catch (error) {
          remove.disabled = false;
          dialog.insertBefore(el('p', 'form-error', error.message), actions);
        }
      };
      actions.append(cancel, remove);
      dialog.append(actions);
    });
  }
}
customElements.define('ember-library', EmberLibrary);
