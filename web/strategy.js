import {el, openDialog} from './ui.js';

const stylesheet = document.createElement('link');
stylesheet.rel = 'stylesheet';
stylesheet.href = new URL('./strategy.css', import.meta.url).href;
document.head.append(stylesheet);

const signed = value => (value > 0 ? '+' : '') + value;
const chips = value => signed(value) + (Math.abs(value) === 1 ? ' chip' : ' chips');
const count = (value, unit) => value + ' ' + unit + (value === 1 ? '' : 's');
const labels = {you:'You', opponent:'Opponent', check:'Check', bet:'Bet 1 chip', call:'Call 1 chip', fold:'Fold'};
const policyName = hand => hand.policy === 'model' ? hand.model : 'Rule-based baseline';

function card(title, rank, faceDown) {
  const seat = el('div', 'strategy-seat');
  const face = el('div', 'strategy-card' + (faceDown ? ' face-down' : ''), rank || '');
  face.setAttribute('aria-label', title + (rank ? ' ' + rank : ' hidden'));
  seat.append(face, el('span', '', title));
  return seat;
}

function decisions(hand) {
  const box = el('div', 'strategy-observations');
  for (const record of hand.decisions) {
    const action = el('div', 'strategy-decision');
    action.append(el('strong', '', 'Opponent chose ' + record.move),
      el('span', '', Math.round(record.elapsed_ms) + ' ms · legal moves were ' + record.legal.join(', ')));
    if (record.fallback) action.append(el('span', '', record.fallback));
    if (Number.isFinite(record.metrics?.output_tokens)) action.append(el('span', '',
      record.metrics.output_tokens + ' generated tokens · model ' + (record.model_digest || '').slice(0,12)));
    box.append(action);
  }
  return box;
}

function actionHistory(hand) {
  return hand.moves.map(m => labels[m.seat] + ' · ' + m.move).join('   /   ');
}

export function mountStrategy(container, {api}) {
  const controller = new AbortController();
  const req = (path, opts = {}) => api(path, {...opts, signal:controller.signal});
  let state, models = [], model = '', policy = 'baseline', busy = false, error = '', timer;
  const live = () => !controller.signal.aborted;

  async function refresh() {
    state = await req('/api/strategy/state');
    if (!live()) return;
    draw();
    if (state.hand?.turn === 'opponent') {
      clearTimeout(timer);
      timer = setTimeout(() => act('/api/strategy/hands/' + state.hand.id + '/continue', {}), 600);
    }
  }
  async function act(path, body) {
    if (!live() || busy) return;
    busy = true;
    error = '';
    draw();
    try {
      await req(path, {method:'POST', body});
    } catch (err) {
      if (live()) error = err.message;
    } finally {
      busy = false;
      if (live()) {
        try { await refresh(); } catch (err) { if (live()) {error = err.message; draw();} }
      }
    }
  }

  function controls(hand) {
    const section = el('div', 'strategy-controls');
    if (hand && !hand.winner) {
      const moves = el('div', 'strategy-actions');
      for (const move of hand.legal_moves) {
        const button = el('button', 'lab-btn', labels[move]);
        button.type = 'button';
        button.disabled = busy;
        button.onclick = () => act('/api/strategy/hands/' + hand.id + '/moves', {move,version:hand.version});
        moves.append(button);
      }
      section.append(el('p', 'strategy-turn', busy || hand.turn === 'opponent' ? 'Opponent is deciding…' : 'Your move'), moves);
      return section;
    }
    const policyLabel = el('label', '', 'Opponent policy');
    const select = el('select');
    select.add(new Option('Rule-based baseline','baseline'));
    select.add(new Option('Local model','model'));
    select.value = policy;
    select.disabled = busy;
    select.onchange = () => {policy = select.value; draw();};
    policyLabel.append(select);
    section.append(policyLabel);
    if (policy === 'model') {
      const modelLabel = el('label', '', 'Opponent model');
      const picker = el('select');
      for (const item of models) picker.add(new Option(item.name,item.name));
      if (!models.length) picker.add(new Option('No local models available',''));
      picker.value = model;
      picker.disabled = busy || !models.length;
      picker.onchange = () => {model = picker.value;};
      modelLabel.append(picker);
      section.append(modelLabel);
    }
    const deal = el('button', 'lab-btn lab-primary', busy ? 'Dealing…' : 'Deal hand');
    deal.type = 'button';
    deal.disabled = busy || (policy === 'model' && !model);
    deal.onclick = () => act('/api/strategy/hands', {policy, model});
    section.append(deal);
    return section;
  }

  async function review(id) {
    try {
      const h = await req('/api/strategy/hands/' + id);
      if (!live()) return;
      openDialog('Hand review', dialog => {
        const body = el('div', 'strategy-review');
        body.append(el('strong','',policyName(h)),el('p','','Your card ' + h.user_card + ' · Opponent ' + h.opponent_card),
          el('p','',actionHistory(h)),el('p','',chips(h.net_chips) + ' for you'),decisions(h));
        dialog.append(body);
      });
    } catch (err) {if (live()) {error = err.message; draw();}}
  }

  function draw() {
    if (!live()) return;
    const content = el('section', 'strategy-workbench');
    const hand = state?.hand;
    content.append(el('h2','','Kuhn poker'),
      el('p','lab-note','Play a three-card hand against a local opponent. Each player antes one simulated chip.'));
    const board = el('div', 'strategy-board');
    const center = el('div','strategy-pot');
    center.append(el('small','','Pot'),el('strong','',hand ? String(hand.pot) : '0'),el('span','','chips'));
    board.append(card('Your card',hand?.user_card,!hand),center,card('Opponent',hand?.opponent_card,!hand?.winner));
    content.append(board);
    if (hand) {
      content.append(el('p','strategy-history',actionHistory(hand) || (hand.first_seat === 'you' ? 'You act first.' : 'Opponent acts first.')));
      if (hand.winner) {
        const result = el('p','strategy-result', (hand.winner === 'you' ? 'You won' : 'Opponent won') + ' · ' + chips(hand.net_chips) + ' for you');
        result.setAttribute('role','status');
        content.append(result);
      }
    }
    if (state) content.append(controls(hand));
    if (error) {const p = el('p','lab-error',error); p.setAttribute('role','alert'); content.append(p);}
    if (!state) content.append(el('p','lab-note',error ? 'Unable to load the table.' : 'Opening table…'));
    const rules = el('details','strategy-rules');
    rules.append(el('summary','','Rules and opponent policy'),
      el('p','','Jack, queen and king are dealt without replacement. Each player sees one card. Check or bet one chip. Facing a bet, call or fold. At showdown the higher card wins. Seats alternate for each policy.'),
      el('p','','The baseline bets and calls with a king, checks or folds otherwise. A local opponent sees its own card and the public moves. If it supplies an illegal move, the baseline chooses instead.'));
    content.append(rules);
    if (state?.stats.length) {
      content.append(el('h3','','Policy results'));
      for (const s of state.stats) {
        const result = el('div','strategy-stat');
        result.append(el('strong','',policyName(s)),el('span','',count(s.hands,'hand') + ' · ' + s.wins + ' won by you · ' + chips(s.net_chips) + ' net'),
          el('small','', (s.mean_ms ?? 0) + ' ms per decision · ' + count(s.fallbacks || 0,'fallback')));
        content.append(result);
      }
      content.append(el('h3','','Recent hands'));
      for (const h of state.recent) {
        const row = el('button','strategy-log');
        row.type='button';
        row.append(el('span','',policyName(h)),el('strong','',chips(h.net_chips)),el('span','','Review ›'));
        row.onclick = () => review(h.id);
        content.append(row);
      }
    }
    container.replaceChildren(content);
  }

  draw();
  Promise.all([req('/api/status'), req('/api/strategy/state')]).then(([status, initial]) => {
    if (!live()) return;
    models = status.models || [];
    model = status.settings?.model || models[0]?.name || '';
    state = initial;
    if (state.hand) {policy = state.hand.policy; model = state.hand.model || model;}
    draw();
    if (state.hand?.turn === 'opponent') act('/api/strategy/hands/' + state.hand.id + '/continue', {});
  }).catch(err => {if (live()) {error = err.message; draw();}});
  return () => {clearTimeout(timer); controller.abort();};
}
