// Cartwheel HW4 review app: conversations, turn and step cards, margin comments.
'use strict';

const state = {
  index: [],          // conversation summaries
  batches: [],        // [{name, sessions: [session_id]}]
  order: [],          // flattened session ids in queue order
  cur: 0,             // position in order
  conv: null,         // loaded conversation
  cache: {},          // session_id -> conversation
  annotations: [],    // all annotations (comments + no_failure marks)
  activeId: null,     // active comment id
  draft: null,        // {anchor, trace_id} while composing a new comment
  editingId: null,
  spec: [],
};

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pretty = v => typeof v === 'string' ? v : JSON.stringify(v, null, 2);
const short = id => String(id).slice(0, 8);
const uid = () => 'c-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const now = () => new Date().toISOString();

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 1800);
}

// ---------------------------------------------------------------- annotations

const live = () => state.annotations.filter(a => !a.deleted_at);
const turnAnns = tid => live().filter(a => a.trace_id === tid);

function turnStatus(tid) {
  const anns = turnAnns(tid);
  if (anns.some(a => a.kind === 'comment' && a.first_failure)) return 'failure';
  if (anns.some(a => a.kind === 'no_failure')) return 'clean';
  if (anns.some(a => a.kind === 'comment')) return 'progress';
  return 'open';
}
const STATUS_TEXT = {failure: '⚑ first failure noted', clean: '✓ no failure observed', progress: '✎ comments, not concluded', open: '○ not reviewed'};

async function save() {
  try {
    await api('/api/annotations', {annotations: state.annotations});
  } catch (e) {
    toast('Save failed: ' + e.message);
  }
  renderQueue(); renderProgress();
}

// ---------------------------------------------------------------- loading

async function boot() {
  const [idx, anns, spec] = await Promise.all([api('/api/index'), api('/api/annotations'), api('/api/spec')]);
  state.index = idx.conversations;
  state.batches = idx.batches;
  state.order = idx.batches.flatMap(b => b.sessions);
  state.annotations = anns.annotations || [];
  state.spec = spec;
  $('#total').textContent = state.order.length;
  $('#pos').max = state.order.length;
  renderSpec('');
  const fromHash = decodeURIComponent(location.hash.slice(1));
  const start = state.order.indexOf(fromHash);
  await go(start >= 0 ? start : 0);
}

const summary = sid => state.index.find(c => c.session_id === sid);

async function go(i) {
  if (!state.order.length) { $('#conversation').innerHTML = '<p class="empty">No conversations.</p>'; return; }
  state.cur = Math.max(0, Math.min(state.order.length - 1, i));
  const sid = state.order[state.cur];
  state.draft = null; state.editingId = null; state.activeId = null;
  if (!state.cache[sid]) state.cache[sid] = await api('/api/conversation?id=' + encodeURIComponent(sid));
  state.conv = state.cache[sid];
  history.replaceState(null, '', '#' + encodeURIComponent(sid));
  $('#pos').value = state.cur + 1;
  $('#prev').disabled = state.cur === 0;
  $('#next').disabled = state.cur === state.order.length - 1;
  renderConversation(); renderQueue(); renderProgress();
  window.scrollTo({top: 0});
}

function nextOpen() {
  for (let k = 1; k <= state.order.length; k++) {
    const i = (state.cur + k) % state.order.length;
    const s = summary(state.order[i]);
    if (s && s.trace_ids.some(t => ['open', 'progress'].includes(turnStatus(t)))) return go(i);
  }
  toast('Every turn in the queue is reviewed');
}

// ---------------------------------------------------------------- queue + progress

function renderQueue() {
  const q = $('#queue');
  let html = '';
  let pos = 0;
  for (const b of state.batches) {
    html += `<h4>${esc(b.name)} · ${b.sessions.length}</h4>`;
    for (const sid of b.sessions) {
      const s = summary(sid);
      const dots = s.trace_ids.map(t => `<span class="dot ${turnStatus(t)}" title="${STATUS_TEXT[turnStatus(t)]}"></span>`).join('');
      html += `<div class="qrow ${pos === state.cur ? 'current' : ''}" data-pos="${pos}">
        <span>${esc(s.scenario_id || short(sid))}</span><span class="role">${esc(s.role || '')}</span>
        <span class="dots">${dots}</span></div>`;
      pos++;
    }
  }
  q.innerHTML = html;
  const cur = $('.qrow.current', q);
  if (cur) cur.scrollIntoView({block: 'nearest'});
}

function renderProgress() {
  const traces = state.order.flatMap(sid => summary(sid).trace_ids);
  const done = traces.filter(t => ['failure', 'clean'].includes(turnStatus(t))).length;
  const fails = traces.filter(t => turnStatus(t) === 'failure').length;
  $('#progress').innerHTML = `Turns reviewed <b>${done}</b> / ${traces.length} · with a failure <b>${fails}</b>`;
}

// ---------------------------------------------------------------- conversation

function selBlock(tid, stepId, field, text, cls = '') {
  return `<div class="sel ${cls}" data-anchor="${esc(tid)}|${esc(stepId)}|${esc(field)}">${esc(text)}</div>`;
}

function jsonBlock(tid, stepId, field, value, label) {
  if (value === undefined || value === null) return '';
  const text = pretty(value);
  const long = text.split('\n').length > 14;
  return `<div class="lbl">${label}${long ? ` <button class="linkish" data-expand>expand</button>` : ''}</div>
    <div class="box">${selBlock(tid, stepId, field, text, 'code')}</div>`;
}

function stepHtml(tid, st) {
  const d = `style="--d:${st.depth || 0}"`;
  const dur = st.duration_ms != null ? `<span class="dur">${(st.duration_ms / 1000).toFixed(2)}s</span>` : '';
  const cb = `<button class="cbtn" data-comment-step="${esc(st.id)}" title="Comment on this step">💬</button>`;
  const attrs = `data-step="${esc(st.id)}" data-trace="${esc(tid)}"`;
  if (st.kind === 'user') {
    return `<div class="step user" ${d} ${attrs}><div class="step-head"><span class="badge">USER</span><span class="name">User message</span>${cb}</div>
      <div class="step-body">${selBlock(tid, st.id, 'text', st.text, 'say')}</div></div>`;
  }
  if (st.kind === 'span') {
    return `<div class="step span" ${d} ${attrs}><div class="step-head"><span class="badge">${esc(st.type)}</span>
      <span>${esc(st.name)}</span>${st.level && st.level !== 'DEFAULT' ? `<span class="pill bad">${esc(st.level)}</span>` : ''}${dur}${cb}</div></div>`;
  }
  if (st.kind === 'generation') {
    const title = st.final ? 'Final reply' : `Model call ${st.number}`;
    const badge = st.final ? 'REPLY' : 'MODEL';
    const texts = st.texts.map((t, i) => selBlock(tid, st.id, 'text' + i, t, 'say')).join('');
    const reqs = st.tool_requests.map(r => `<div class="req">→ requests <b>${esc(r.name)}</b>(${esc(JSON.stringify(r.arguments))})</div>`).join('');
    return `<div class="step generation ${st.final ? 'final' : ''}" ${d} ${attrs}>
      <div class="step-head"><span class="badge">${badge}</span><span class="name">${title}</span><span class="dur">${esc(st.model || '')}</span>${dur}${cb}</div>
      <div class="step-body">${texts}${reqs}
        <button class="linkish" data-prompt>show model input</button><div class="prompt" hidden></div></div></div>`;
  }
  if (st.kind === 'tool') {
    const s = st.summary || {};
    const pills = Object.entries(s).map(([k, v]) => {
      const cls = (k === 'ok' && v === true) ? 'ok' : ((k === 'ok' && v === false) || k === 'error') ? 'bad' : '';
      return `<span class="pill ${cls}">${esc(k)}: ${esc(typeof v === 'string' ? v : JSON.stringify(v))}</span>`;
    }).join('');
    return `<div class="step tool ${st.retrieval ? 'retrieval' : ''}" ${d} ${attrs}>
      <div class="step-head"><span class="badge">${st.retrieval ? 'RETRIEVAL' : 'TOOL'}</span><span class="name">${esc(st.name)}</span>${dur}${cb}</div>
      <div class="step-body"><div class="pills">${pills}</div>
        ${jsonBlock(tid, st.id, 'input', st.input, 'Input')}${jsonBlock(tid, st.id, 'output', st.output, 'Result')}</div></div>`;
  }
  if (st.kind === 'missing_reply') {
    return `<div class="step missing_reply" ${d} ${attrs}><div class="step-head"><span class="badge">NO REPLY</span>
      <span class="name">No final reply was recorded for this turn</span>${cb}</div>
      ${st.text ? `<div class="step-body">${selBlock(tid, st.id, 'text', st.text, 'say')}</div>` : ''}</div>`;
  }
  return '';
}

function renderConversation() {
  const c = state.conv;
  const n = c.turns.length;
  const run = c.run && c.run.status && c.run.status !== 'completed'
    ? `<div class="banner">⚠ This scenario run ended with an error: ${esc(c.run.error || c.run.status)}</div>` : '';
  let html = `<div class="conv-head"><h2>${esc(c.scenario_id || 'Conversation')}</h2>
    <div class="meta"><span>role: <b>${esc(c.role)}</b></span><span>user: ${esc(c.turns[0]?.user_id ?? '')}</span>
    <span>${n} turn${n > 1 ? 's' : ''}</span><span>model: ${esc(c.models.join(', '))}</span>
    <span>prompt ${esc(c.prompt_version || '')}</span><span>session ${esc(short(c.session_id))}</span></div>${run}</div>`;
  c.turns.forEach((t, i) => {
    const status = turnStatus(t.trace_id);
    const nofail = turnAnns(t.trace_id).some(a => a.kind === 'no_failure');
    const hasFF = status === 'failure';
    const turnComment = turnAnns(t.trace_id).some(a => a.kind === 'comment' && a.anchor.step_id === '__turn__');
    html += `<section class="turn" data-n="${(i % 4) + 1}" data-trace="${esc(t.trace_id)}">
      <div class="turn-head ${turnComment ? 'has-comment' : ''}" data-step="__turn__" data-trace="${esc(t.trace_id)}">
        <span class="tlabel">Turn ${i + 1} of ${n}</span>
        <span class="tid" data-copy="${esc(t.trace_id)}" title="Copy trace ID">trace ${short(t.trace_id)} ⧉</span>
        <span class="meta">${esc((t.timestamp || '').replace('T', ' ').slice(0, 19))}</span>
        <span class="spacer"></span>
        <span class="status ${status}">${STATUS_TEXT[status]}</span>
        <button class="nofail ${nofail ? 'on' : ''}" data-nofail="${esc(t.trace_id)}" ${hasFF ? 'disabled title="A first failure is noted for this turn"' : ''}>✓ No failure observed</button>
        <button class="cbtn" data-comment-step="__turn__" title="Comment on this whole turn">💬</button>
      </div>
      <div class="steps">${t.steps.map(st => stepHtml(t.trace_id, st)).join('')}</div></section>`;
  });
  $('#conversation').innerHTML = html;
  applyHighlights();
  layoutMargin();
}

// ---------------------------------------------------------------- highlights

function offsetWithin(el, node, offset) {
  const r = document.createRange();
  r.selectNodeContents(el);
  r.setEnd(node, offset);
  return r.toString().length;
}

function applyHighlights() {
  document.querySelectorAll('.sel').forEach(el => {
    const text = el.textContent;
    const key = el.dataset.anchor;
    const ranges = [];
    for (const a of live()) {
      if (a.kind !== 'comment' || !a.anchor || a.anchor.quote == null) continue;
      if (`${a.trace_id}|${a.anchor.step_id}|${a.anchor.field}` !== key) continue;
      let {start, end, quote} = a.anchor;
      if (text.slice(start, end) !== quote) {
        start = text.indexOf(quote); end = start + quote.length;
      }
      if (start >= 0 && end > start) ranges.push({start, end, id: a.id});
    }
    if (!ranges.length) { el.textContent = text; return; }
    const cuts = [...new Set([0, text.length, ...ranges.flatMap(r => [r.start, r.end])])].sort((x, y) => x - y);
    let html = '';
    for (let k = 0; k < cuts.length - 1; k++) {
      const [a, b] = [cuts[k], cuts[k + 1]];
      const ids = ranges.filter(r => r.start <= a && r.end >= b).map(r => r.id);
      const seg = esc(text.slice(a, b));
      html += ids.length ? `<mark data-ids="${ids.join(' ')}" class="${ids.includes(state.activeId) ? 'active' : ''}">${seg}</mark>` : seg;
    }
    el.innerHTML = html;
  });
  document.querySelectorAll('[data-step]').forEach(el => {
    const tid = el.dataset.trace, sid = el.dataset.step;
    const has = live().some(a => a.kind === 'comment' && a.trace_id === tid && a.anchor.step_id === sid);
    el.classList.toggle('has-comment', has);
  });
}

// ---------------------------------------------------------------- margin comments

function anchorElement(a) {
  if (!a || !a.anchor) return null;
  const mark = document.querySelector(`mark[data-ids~="${CSS.escape(a.id || '')}"]`);
  if (mark) return mark;
  if (a.anchor.field) {
    const sel = document.querySelector(`.sel[data-anchor="${CSS.escape(`${a.trace_id}|${a.anchor.step_id}|${a.anchor.field}`)}"]`);
    if (sel && a.anchor.quote == null) return sel;
  }
  return document.querySelector(`[data-trace="${CSS.escape(a.trace_id)}"][data-step="${CSS.escape(a.anchor.step_id)}"]`);
}

function whereLabel(a) {
  const conv = state.conv;
  const ti = conv.turns.findIndex(t => t.trace_id === a.trace_id);
  if (a.anchor.step_id === '__turn__') return `Turn ${ti + 1}`;
  const st = conv.turns[ti]?.steps.find(s => s.id === a.anchor.step_id);
  const name = !st ? 'step' : st.kind === 'user' ? 'user message' : st.kind === 'generation' ? (st.final ? 'final reply' : `model call ${st.number}`) : st.kind === 'missing_reply' ? 'missing reply' : st.name;
  return `Turn ${ti + 1} · ${name}`;
}

function commentHtml(a) {
  const active = a.id === state.activeId;
  const editing = a.id === state.editingId;
  const quote = a.anchor.quote ? `<div class="quote">“${esc(a.anchor.quote.slice(0, 160))}”</div>` : '';
  const edited = (a.history || []).length ? `<span class="edited" title="${esc(a.history.map(h => h.note).join('\n---\n'))}">(edited)</span>` : '';
  const body = editing
    ? `<textarea data-edit-text>${esc(a.note)}</textarea>
       <div class="acts"><button class="primary" data-save-edit="${a.id}">Save</button><button data-cancel-edit>Cancel</button></div>`
    : `<div class="ctext">${esc(a.note)}</div>
       <div class="acts">
         <label class="ff"><input type="checkbox" data-ff="${a.id}" ${a.first_failure ? 'checked' : ''}> first failure</label>
         <span class="spacer" style="flex:1"></span>
         <button data-edit="${a.id}">Edit</button><button data-del="${a.id}">Delete</button></div>`;
  return `<div class="comment ${active ? 'active' : ''}" data-cid="${a.id}">
    <div class="ctop"><span class="where">${esc(whereLabel(a))}</span>${a.first_failure ? '<span class="fftag">⚑</span>' : ''}${edited}
    <span>${esc(new Date(a.updated_at || a.created_at).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}))}</span></div>
    ${quote}${body}</div>`;
}

function draftHtml(d) {
  const quote = d.anchor.quote ? `<div class="quote">“${esc(d.anchor.quote.slice(0, 160))}”</div>` : '';
  const noFF = !turnAnns(d.trace_id).some(a => a.kind === 'comment' && a.first_failure);
  return `<div class="comment active" data-cid="__draft__">
    <div class="ctop"><span class="where">${esc(whereLabel({trace_id: d.trace_id, anchor: d.anchor}))}</span></div>${quote}
    <textarea data-draft-text placeholder="Describe what you see, in your own words…"></textarea>
    <div class="acts"><label class="ff"><input type="checkbox" data-draft-ff ${noFF ? '' : 'disabled'}> first failure</label>
      <span style="flex:1"></span><button class="primary" data-save-draft>Comment</button><button data-cancel-draft>Cancel</button></div>
    <div class="edited">Ctrl/⌘+Enter to save · Esc to cancel</div></div>`;
}

function layoutMargin() {
  const margin = $('#margin');
  const conv = state.conv;
  const traceIds = new Set(conv.turns.map(t => t.trace_id));
  const items = live().filter(a => a.kind === 'comment' && traceIds.has(a.trace_id));
  let html = items.map(commentHtml).join('');
  if (state.draft) html += draftHtml(state.draft);
  margin.innerHTML = html;

  const base = margin.getBoundingClientRect().top;
  const placed = [...margin.querySelectorAll('.comment')].map(el => {
    const cid = el.dataset.cid;
    const a = cid === '__draft__' ? {trace_id: state.draft.trace_id, anchor: state.draft.anchor, id: '__draft__'} : items.find(x => x.id === cid);
    const anchor = anchorElement(a);
    const want = anchor ? anchor.getBoundingClientRect().top - base : 0;
    return {el, want, active: cid === state.activeId || cid === '__draft__'};
  }).sort((x, y) => x.want - y.want);

  // Keep the active comment level with its anchor; push others around it.
  const activeIdx = placed.findIndex(p => p.active);
  const tops = new Array(placed.length);
  if (activeIdx >= 0) {
    tops[activeIdx] = placed[activeIdx].want;
    for (let i = activeIdx + 1; i < placed.length; i++) tops[i] = Math.max(placed[i].want, tops[i - 1] + placed[i - 1].el.offsetHeight + 8);
    for (let i = activeIdx - 1; i >= 0; i--) tops[i] = Math.min(placed[i].want, tops[i + 1] - placed[i].el.offsetHeight - 8);
  } else {
    placed.forEach((p, i) => { tops[i] = i ? Math.max(p.want, tops[i - 1] + placed[i - 1].el.offsetHeight + 8) : p.want; });
  }
  placed.forEach((p, i) => { p.el.style.top = tops[i] + 'px'; });
  const bottom = placed.length ? Math.max(...placed.map((p, i) => tops[i] + p.el.offsetHeight)) : 0;
  margin.style.minHeight = bottom + 'px';
  const ta = margin.querySelector('[data-draft-text], [data-edit-text]');
  if (ta && document.activeElement !== ta) ta.focus({preventScroll: true});
}

function setActive(id) {
  state.activeId = id;
  document.querySelectorAll('mark').forEach(m => m.classList.toggle('active', (m.dataset.ids || '').split(' ').includes(id)));
  document.querySelectorAll('.anchor-active').forEach(el => el.classList.remove('anchor-active'));
  const a = live().find(x => x.id === id);
  if (a && !a.anchor.quote) { const el = anchorElement(a); if (el) el.classList.add('anchor-active'); }
  layoutMargin();
}

function startDraft(trace_id, anchor) {
  state.draft = {trace_id, anchor};
  state.editingId = null; state.activeId = null;
  layoutMargin();
}

function commitDraft() {
  const d = state.draft;
  const text = $('[data-draft-text]').value.trim();
  if (!text) { toast('Write a comment first'); return; }
  const ff = $('[data-draft-ff]');
  const conv = state.conv;
  const a = {
    id: uid(), kind: 'comment', author: 'human',
    trace_id: d.trace_id, session_id: conv.session_id, scenario_id: conv.scenario_id,
    anchor: d.anchor, note: text, first_failure: !!(ff && ff.checked),
    created_at: now(), updated_at: now(), history: [],
  };
  if (a.first_failure) {
    for (const other of turnAnns(a.trace_id)) if (other.kind === 'no_failure') other.deleted_at = now();
  }
  state.annotations.push(a);
  state.draft = null; state.activeId = a.id;
  applyHighlights(); renderTurnStatuses(); layoutMargin(); save();
}

function renderTurnStatuses() {
  // Cheap full re-render keeps turn headers and highlights in sync.
  const y = window.scrollY;
  renderConversation();
  window.scrollTo({top: y});
}

// ---------------------------------------------------------------- selection comments

function currentSelectionAnchor() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const el = (range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement).closest('.sel');
  if (!el || !el.contains(range.endContainer)) return null;
  const start = offsetWithin(el, range.startContainer, range.startOffset);
  const end = offsetWithin(el, range.endContainer, range.endOffset);
  const quote = el.textContent.slice(start, end);
  if (!quote.trim()) return null;
  const [trace_id, step_id, field] = el.dataset.anchor.split('|');
  return {trace_id, anchor: {step_id, field, start, end, quote}, rect: range.getBoundingClientRect()};
}

document.addEventListener('mouseup', e => {
  if (e.target.closest('#sel-comment') || e.target.closest('.comment')) return;
  setTimeout(() => {
    const btn = $('#sel-comment');
    const hit = currentSelectionAnchor();
    if (!hit) { btn.hidden = true; return; }
    btn.hidden = false;
    btn.style.top = (hit.rect.bottom + window.scrollY + 6) + 'px';
    btn.style.left = (hit.rect.left + window.scrollX) + 'px';
    btn._hit = hit;
  }, 0);
});

$('#sel-comment').addEventListener('mousedown', e => e.preventDefault());
$('#sel-comment').addEventListener('click', () => {
  const btn = $('#sel-comment');
  const hit = btn._hit;
  btn.hidden = true;
  if (!hit) return;
  window.getSelection().removeAllRanges();
  startDraft(hit.trace_id, hit.anchor);
});

// ---------------------------------------------------------------- events

document.addEventListener('click', e => {
  const t = e.target;
  const q = t.closest('.qrow'); if (q) return go(+q.dataset.pos);
  const cstep = t.closest('[data-comment-step]');
  if (cstep) {
    const host = cstep.closest('[data-trace]');
    return startDraft(host.dataset.trace, {step_id: cstep.dataset.commentStep, field: null, quote: null});
  }
  const nf = t.closest('[data-nofail]');
  if (nf) {
    const tid = nf.dataset.nofail;
    const existing = turnAnns(tid).find(a => a.kind === 'no_failure');
    if (existing) existing.deleted_at = now();
    else state.annotations.push({id: uid(), kind: 'no_failure', author: 'human', trace_id: tid,
      session_id: state.conv.session_id, scenario_id: state.conv.scenario_id, note: 'no failure observed', created_at: now()});
    renderTurnStatuses(); save(); return;
  }
  const copy = t.closest('[data-copy]');
  if (copy) { navigator.clipboard?.writeText(copy.dataset.copy); return toast('Trace ID copied'); }
  if (t.closest('[data-expand]')) { const box = t.closest('.step-body').querySelectorAll('.box'); box.forEach(b => b.classList.toggle('open')); layoutMargin(); return; }
  if (t.closest('[data-prompt]')) {
    const body = t.closest('.step-body'); const p = $('.prompt', body);
    const step = state.conv.turns.flatMap(x => x.steps).find(s => s.id === t.closest('.step').dataset.step);
    if (p.hidden) { p.innerHTML = `<div class="box"><pre>${esc(pretty(step.prompt))}</pre></div>`; }
    p.hidden = !p.hidden; t.textContent = p.hidden ? 'show model input' : 'hide model input';
    layoutMargin(); return;
  }
  const mark = t.closest('mark'); if (mark) return setActive(mark.dataset.ids.split(' ')[0]);
  if (t.closest('[data-save-draft]')) return commitDraft();
  if (t.closest('[data-cancel-draft]')) { state.draft = null; return layoutMargin(); }
  const ed = t.closest('[data-edit]'); if (ed) { state.editingId = ed.dataset.edit; state.activeId = ed.dataset.edit; return layoutMargin(); }
  if (t.closest('[data-cancel-edit]')) { state.editingId = null; return layoutMargin(); }
  const se = t.closest('[data-save-edit]');
  if (se) {
    const a = live().find(x => x.id === se.dataset.saveEdit);
    const text = $('[data-edit-text]').value.trim();
    if (a && text && text !== a.note) {
      a.history = [...(a.history || []), {note: a.note, at: a.updated_at || a.created_at}];
      a.note = text; a.updated_at = now();
    }
    state.editingId = null; layoutMargin(); save(); return;
  }
  const del = t.closest('[data-del]');
  if (del) {
    if (!confirm('Delete this comment? It stays in the saved history as deleted.')) return;
    const a = live().find(x => x.id === del.dataset.del);
    if (a) a.deleted_at = now();
    state.activeId = null; renderTurnStatuses(); save(); return;
  }
  const rid = t.closest('.rid');
  if (rid) { navigator.clipboard?.writeText(rid.textContent); return toast(rid.textContent + ' copied'); }
  const c = t.closest('.comment');
  if (c && c.dataset.cid !== '__draft__' && !t.closest('input, textarea, button, label')) return setActive(c.dataset.cid);
});

document.addEventListener('change', e => {
  const ff = e.target.closest('[data-ff]');
  if (!ff) return;
  const a = live().find(x => x.id === ff.dataset.ff);
  if (!a) return;
  if (ff.checked) {
    // One first failure per turn, and it replaces a "no failure observed" mark.
    for (const other of turnAnns(a.trace_id)) {
      if (other.kind === 'comment' && other.id !== a.id && other.first_failure) other.first_failure = false;
      if (other.kind === 'no_failure') other.deleted_at = now();
    }
  }
  a.first_failure = ff.checked; a.updated_at = now();
  renderTurnStatuses(); save();
});

document.addEventListener('keydown', e => {
  const typing = e.target.closest('input, textarea');
  if (e.target.closest('[data-cid="__draft__"]')) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); commitDraft(); }
    if (e.key === 'Escape') { state.draft = null; layoutMargin(); }
    return;
  }
  if (e.target.matches('[data-edit-text]') && e.key === 'Escape') { state.editingId = null; layoutMargin(); return; }
  if (typing) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); go(state.cur - 1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); go(state.cur + 1); }
  if (e.key === 'Escape') { $('#spec').hidden = true; setActive(null); }
});

$('#prev').onclick = () => go(state.cur - 1);
$('#next').onclick = () => go(state.cur + 1);
$('#next-open').onclick = nextOpen;
$('#pos').addEventListener('change', e => go((+e.target.value || 1) - 1));
$('#spec-btn').onclick = () => { $('#spec').hidden = !$('#spec').hidden; if (!$('#spec').hidden) $('#spec-q').focus(); };
$('#spec-close').onclick = () => { $('#spec').hidden = true; };
$('#spec-q').addEventListener('input', e => renderSpec(e.target.value));
window.addEventListener('resize', () => layoutMargin());

function renderSpec(q) {
  const needle = q.trim().toLowerCase();
  const items = state.spec.filter(s => !needle || (s.id + ' ' + s.section + ' ' + s.text).toLowerCase().includes(needle));
  $('#spec-list').innerHTML = items.map(s => `<div class="req-item"><button class="rid">${esc(s.id)}</button><span class="sec">${esc(s.section)}</span>
    <pre>${esc(s.text)}</pre></div>`).join('') || '<p class="hint">No match.</p>';
}

boot().catch(e => { $('#conversation').innerHTML = `<p class="empty">Could not load: ${esc(e.message)}</p>`; });
