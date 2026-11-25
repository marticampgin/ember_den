const glyphs = {
  tools: '<path d="M14 5a5 5 0 0 0-6 6l-5 5a2 2 0 0 0 3 3l5-5a5 5 0 0 0 6-6l-3 3-3-3z"/>',
  companion: '<path d="m3 4 6 3.5a12 12 0 0 1 6 0L21 4l-2 10-7 6-7-6z"/><path d="m6.5 13 3 2m8-2-3 2M11 17h2"/>',
  laboratory: '<path d="M9 3h6m-5 0v6l-6 9a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-9V3M8 15h8"/>',
  memories: '<path d="M3 5.5c3-1.3 6.6-.8 9 1.2 2.4-2 6-2.5 9-1.2v13c-3-1.2-6.6-.8-9 1.2-2.4-2-6-2.4-9-1.2zM12 7v12"/>',
  settings: '<path d="M4 6h7m4 0h5M4 12h2m4 0h10M4 18h10m4 0h2"/><circle cx="13" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
  message: '<path d="M20 14a3 3 0 0 1-3 3H9l-5 4v-5a3 3 0 0 1-1-2V7a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  send: '<path d="M12 19V5m-6.5 6.5L12 5l6.5 6.5"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor" stroke="none"/>',
  chip: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  close: '<path d="m6 6 12 12m0-12L6 18"/>',
  spark: '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z"/>',
  calc: '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M8 7h8m-8 5h1m5 0h1m-7 5h1m5 0h1"/>',
  leaf: '<path d="M19 3c-1 2-2 2-6 2S4 7 4 12a6 6 0 0 0 6 6c6 0 10-7 9-15ZM3 21l11-11"/>',
  book: '<path d="M6 4h13v15H7a2 2 0 0 0 0 4m-2-2V6a2 2 0 0 1 2-2zM9 4v7l2-1.5 2 1.5V4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  edit: '<path d="m5 15-1 5 5-1L20 8l-4-4zM14 6l4 4"/>',
  trash: '<path d="M4 6h16m-12 0 1-3h6l1 3M6 6l1 14h10l1-14M10 10v6m4-6v6"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  refresh: '<path d="M20 8a8.5 8.5 0 1 0 .5 7M20 3v5h-5"/>',
  monitor: '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M12 17v3m-4 0h8"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/>',
  code: '<path d="m7.5 6-5 6 5 6m9-12 5 6-5 6M14 4l-4 16"/>',
  bulb: '<path d="M8.5 16c0-2.5-3.5-3-3.5-7a7 7 0 0 1 14 0c0 4-3.5 4.5-3.5 7zM9 19h6m-5 3h4"/>',
  page: '<path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h5"/>',
};

export function icon(name, size = 18) {
  return `<svg class="icon" aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${glyphs[name] || ''}</svg>`;
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export async function api(path, {method = 'GET', body, signal, keepalive = false} = {}) {
  const response = await fetch(path, {
    method, signal, keepalive,
    headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return {};
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
  return data;
}

let toastTimeout;
export function notify(message) {
  const toast = document.querySelector('#toast');
  clearTimeout(toastTimeout);
  toast.textContent = message;
  toast.hidden = false;
  toastTimeout = setTimeout(() => { toast.hidden = true; }, 4500);
}

// Keep model output inert. Only a small set of text-formatting elements is created.
function inlineText(node, text) {
  const reference = text.trim();
  if (/^https:\/\/en\.wikipedia\.org\/\?curid=\d+$/.test(reference) ||
      /^https:\/\/api\.open-meteo\.com\/v1\/forecast\?[A-Za-z0-9%._=,&+-]+$/.test(reference)) {
    const source = el('a', 'reference-link', text.trim());
    source.href = text.trim();
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    node.append(source);
    return;
  }
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    node.append(text.slice(cursor, match.index));
    const code = match[0][0] === '`';
    node.append(el(code ? 'code' : 'strong', '', match[0].slice(code ? 1 : 2, code ? -1 : -2)));
    cursor = match.index + match[0].length;
  }
  node.append(text.slice(cursor));
}

export function renderResponse(node, text) {
  const output = document.createDocumentFragment();
  let paragraph, list, code;
  for (const line of text.split('\n')) {
    if (/^\s*```/.test(line)) {
      if (code) { code = null; }
      else {
        const pre = el('pre');
        code = el('code');
        pre.append(code);
        output.append(pre);
      }
      list = paragraph = null;
    } else if (code) {
      code.append(line + '\n');
    } else if (!line.trim()) {
      list = paragraph = null;
    } else if (/^#{1,6}\s/.test(line)) {
      const heading = el('h3');
      inlineText(heading, line.replace(/^#{1,6}\s+/, ''));
      output.append(heading);
      list = paragraph = null;
    } else if (/^\s*(?:[-*]|\d+\.)\s+/.test(line)) {
      const tag = /^\s*\d/.test(line) ? 'ol' : 'ul';
      if (list?.localName !== tag) {
        list = el(tag);
        output.append(list);
      }
      const item = el('li');
      inlineText(item, line.replace(/^\s*(?:[-*]|\d+\.)\s+/, ''));
      list.append(item);
      paragraph = null;
    } else {
      if (!paragraph) {
        paragraph = el('p');
        output.append(paragraph);
      } else {
        paragraph.append(el('br'));
      }
      inlineText(paragraph, line);
      list = null;
    }
  }
  node.replaceChildren(output);
}

export function openDialog(title, build) {
  const dialog = el('dialog', 'den-dialog');
  const heading = el('h2', '', title);
  heading.id = 'dialog-title';
  dialog.setAttribute('aria-labelledby', heading.id);
  const close = el('button', 'icon-button');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close dialog');
  close.innerHTML = icon('close');
  close.addEventListener('click', () => dialog.close());
  const header = el('div', 'dialog-header');
  header.append(heading, close);
  dialog.append(header);
  build(dialog);
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  dialog.showModal();
  return dialog;
}
