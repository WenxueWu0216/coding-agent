// DOM helpers injected into pages by browser.py. Defines `A`; browser.py wraps this file in a
// function body, so nothing is left on the page's window object. Page scripts can still tamper
// with DOM prototypes; see README "Known limitations".
const A = (() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const INTERACTIVE = new Set(['button', 'link', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox',
    'listbox', 'spinbutton', 'slider', 'switch', 'tab', 'menuitem']);
  const SENSITIVE = /pass(word|wd|code|phrase)?|pwd|\botp\b|one.?time|\bpin\b|cvv|cvc|card.?num|cc-?num|\bssn\b|secret|token|api.?key|security.?code/i;

  function role(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.split(' ')[0];
    const t = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (t === 'a' && el.hasAttribute('href')) return 'link';
    if (t === 'button') return 'button';
    if (t === 'input') {
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';
      if (type === 'number') return 'spinbutton';
      if (type === 'range') return 'slider';
      if (['hidden', 'password', 'file', 'color', 'date', 'time'].includes(type)) return null;  // no ARIA role
      return 'textbox';
    }
    if (t === 'textarea') return 'textbox';
    if (t === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    if (/^h[1-6]$/.test(t)) return 'heading';
    return null;
  }

  function label(el) {
    const by = el.getAttribute('aria-labelledby');
    if (by) return norm(by.split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' '));
    if (el.labels && el.labels.length) return norm(Array.from(el.labels).map(l => l.innerText).join(' '));
    return '';
  }

  function name(el) {
    const t = el.tagName.toLowerCase();
    return norm(el.getAttribute('aria-label')) || label(el) ||
      (['button', 'a', 'summary'].includes(t) || /^h[1-6]$/.test(t) ? norm(el.innerText) : '') ||
      (t === 'input' && ['button', 'submit', 'reset'].includes(el.type) ? norm(el.value) : '') ||
      norm(el.getAttribute('alt')) || norm(el.getAttribute('title')) || norm(el.getAttribute('placeholder'));
  }

  const isPassword = el => el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'password';

  function sensitive(el) {
    if (isPassword(el)) return true;
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    if (/^(current-password|new-password|one-time-code|cc-)/.test(ac)) return true;
    const hints = [el.getAttribute('name'), el.id, label(el), el.getAttribute('placeholder'),
                   el.getAttribute('aria-label')].join(' ');
    return ['INPUT', 'TEXTAREA'].includes(el.tagName) && SENSITIVE.test(hints);
  }

  function unique(sel) {
    try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
  }

  function bestSelector(el) {
    if (el.id && unique('#' + CSS.escape(el.id))) return '#' + CSS.escape(el.id);
    for (const attr of ['data-testid', 'name']) {
      const v = el.getAttribute(attr);
      const s = v && `${el.tagName.toLowerCase()}[${attr}=${JSON.stringify(v)}]`;
      if (s && unique(s)) return s;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && cur !== document.documentElement) {
      if (cur !== el && cur.id && unique('#' + CSS.escape(cur.id))) { parts.unshift('#' + CSS.escape(cur.id)); return parts.join(' > '); }
      const tag = cur.tagName.toLowerCase();
      const sibs = Array.from(cur.parentElement ? cur.parentElement.children : []).filter(c => c.tagName === cur.tagName);
      parts.unshift(sibs.length > 1 ? `${tag}:nth-of-type(${sibs.indexOf(cur) + 1})` : tag);
      cur = cur.parentElement;
    }
    parts.unshift('body');
    return parts.join(' > ');
  }

  function describe(el) {
    const t = el.tagName.toLowerCase();
    const r = role(el);
    const text = ['button', 'a', 'summary', 'label'].includes(t) ? cut(norm(el.innerText), 80) : '';
    return {
      selector: bestSelector(el), role: r, name: cut(name(el), 80), text: text || null,
      label: label(el) || null, placeholder: el.getAttribute('placeholder') || null, tag: t,
      type: t === 'input' ? (el.type || 'text') : null, sensitive: sensitive(el),
    };
  }

  const visible = el => (el.checkVisibility ? el.checkVisibility({visibilityProperty: true}) : el.offsetParent !== null);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'HEAD', 'SVG']);

  function interactive(el) {
    const r = role(el);
    return (r && INTERACTIVE.has(r)) || isPassword(el) || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) &&
      el.type !== 'hidden';
  }

  function line(el, ref) {
    const r = role(el) || (isPassword(el) ? 'password' : el.tagName.toLowerCase());
    let s = `[${ref}] ${r} ${JSON.stringify(cut(name(el), 80))}`;
    if (el.tagName === 'SELECT') {
      const opts = Array.from(el.options).map(o => norm(o.label));
      s += ` selected=${JSON.stringify(norm(el.selectedOptions[0]?.label || ''))} options=${JSON.stringify(opts.slice(0, 20))}`;
    } else if (['checkbox', 'radio'].includes(el.type)) {
      s += el.checked ? ' checked' : ' unchecked';
    } else if (['INPUT', 'TEXTAREA'].includes(el.tagName) && !['button', 'submit', 'reset'].includes(el.type)) {
      s += sensitive(el) ? (el.value ? ' value=<hidden>' : ' value=""') : ` value=${JSON.stringify(cut(el.value || '', 60))}`;
    }
    if (el.disabled) s += ' disabled';
    return s;
  }

  // Compact text view of the page: interactive elements get [eN] refs (stored in a per-session
  // attribute), headings and table rows get one line each, other visible text follows in order.
  function snapshot(attr, maxChars) {
    document.querySelectorAll(`[${attr}]`).forEach(e => e.removeAttribute(attr));
    const out = [];
    let n = 0, size = 0, truncated = false;
    const push = s => {
      if (truncated) return;
      if (size + s.length > maxChars) { truncated = true; out.push('…(page view truncated)'); return; }
      out.push(s); size += s.length + 1;
    };
    const walk = node => {
      for (const child of node.childNodes) {
        if (truncated) return;
        if (child.nodeType === Node.TEXT_NODE) {
          const p = child.parentElement;
          const s = norm(child.textContent);
          if (s && p && !p.closest('tr, label, button, a, select, summary, h1, h2, h3, h4, h5, h6')) push(`text: ${cut(s, 300)}`);
          continue;
        }
        if (child.nodeType !== Node.ELEMENT_NODE || SKIP.has(child.tagName) || !visible(child)) continue;
        if (interactive(child)) {
          const ref = 'e' + (++n);
          child.setAttribute(attr, ref);
          push(line(child, ref));
          continue;
        }
        const t = child.tagName;
        if (/^H[1-6]$/.test(t)) push(`heading ${JSON.stringify(cut(norm(child.innerText), 120))}`);
        else if (t === 'TR') push(`row: ${Array.from(child.cells).map(c => cut(norm(c.innerText), 60)).join(' | ')}`);
        else if (t === 'LABEL' && !child.control) push(`text: ${cut(norm(child.innerText), 200)}`);
        walk(child);
      }
    };
    if (document.body) walk(document.body);
    return {title: document.title, view: out.join('\n'), refs: n};
  }

  return {describe, sensitive, snapshot, role, name};
})();
