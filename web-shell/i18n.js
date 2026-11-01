// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* IntelliCat interface language.
 *
 * Same idea as the Ratatosk web client (squirrel_wisdom/portal/i18n.js): the pages keep their English strings, and this script
 * swaps in translations from /i18n/<lang>.json (a dictionary keyed by the English text) when the interface language is not
 * English. A missing key simply stays English.
 *   - Keys with {0}, {1}... are patterns matched against a whole text node / attribute ("Connecting to {0}…").
 *   - Text nodes and the placeholder/title/aria-label/alt attributes are translated, also when scripts insert them later
 *     (a MutationObserver); the page title too.
 *   - ar and fa pages get dir="rtl".
 *   - The language is the system setting "language" (shelld /api/settings), English when it was never chosen.
 * I18N.t(text, args) translates a string a script builds; I18N.lang is the current language.
 */
(function () {
  'use strict';
  var RTL = { he: 1 };
  var EXCLUDE = 'script,style,textarea,input,[translate="no"]';
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];

  function getJSON(url) {
    try {
      var x = new XMLHttpRequest();
      x.open('GET', url, false);
      x.send(null);
      if (x.status === 200) return JSON.parse(x.responseText);
    } catch (e) { console.warn('[i18n] cannot load', url, e); }
    return null;
  }

  var s = getJSON('/api/settings');
  var lang = (s && s.language) || 'en';
  var exact = Object.create(null), patterns = [], plural = null;

  function esc(x) { return x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function compile(dict) {
    Object.keys(dict).forEach(function (k) {
      var v = dict[k];
      if (/\{\d+\}/.test(k)) {
        var re = '^' + k.split(/(\{\d+\})/).map(function (p) { return /^\{\d+\}$/.test(p) ? '([\\s\\S]+?)' : esc(p); }).join('') + '$';
        patterns.push({ re: new RegExp(re), val: v, len: k.length });
      } else exact[k] = v;
    });
    patterns.sort(function (a, b) { return b.len - a.len; });  // most specific first
  }
  if (lang !== 'en') {
    var d = getJSON('/i18n/' + lang + '.json');
    if (d) { compile(d); try { plural = new Intl.PluralRules(lang); } catch (e) {} } else lang = 'en';
  }

  function fill(val, groups) {
    if (val && typeof val === 'object') {
      var n = parseFloat((groups && groups[0]) || ''), form = 'other';
      if (!isNaN(n) && plural) form = plural.select(n);
      val = val[form] || val.other || '';
    }
    return String(val).replace(/\{(\d+)\}/g, function (m, i) { return groups && groups[+i] !== undefined ? groups[+i] : m; });
  }
  function lookup(str) {
    if (lang === 'en' || !str) return null;
    if (Object.prototype.hasOwnProperty.call(exact, str)) return fill(exact[str], null);
    for (var i = 0; i < patterns.length; i++) {
      var m = patterns[i].re.exec(str);
      if (m) return fill(patterns[i].val, m.slice(1));  // captured parts (a network name) stay as they are
    }
    return null;
  }
  function t(str, args) {
    var r = lookup(str), out = r === null ? str : r;
    if (args) out = out.replace(/\{(\w+)\}/g, function (m, k) { return args[k] !== undefined ? args[k] : m; });
    return out;
  }
  function tr(str) {  // keeps the whitespace around the text
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(str), core = m[2].replace(/\s+/g, ' ');
    if (!core) return null;
    var r = lookup(core);
    return r === null ? null : m[1] + r + m[3];
  }

  function skip(node) { var el = node.nodeType === 1 ? node : node.parentElement; return !el || (el.closest && el.closest(EXCLUDE)); }
  function doText(n) { if (skip(n)) return; var r = tr(n.nodeValue); if (r !== null && r !== n.nodeValue) n.nodeValue = r; }
  function doAttrs(el) {
    if (el.closest && el.closest('script,style,[translate="no"]')) return;
    for (var i = 0; i < ATTRS.length; i++) {
      var v = el.getAttribute(ATTRS[i]);
      if (v) { var r = tr(v); if (r !== null && r !== v) el.setAttribute(ATTRS[i], r); }
    }
  }
  function walk(root) {
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1) return;
    doAttrs(root);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, null), n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }

  function start() {
    if (lang === 'en') return;
    var ti = document.title && tr(document.title);
    if (ti) document.title = ti;
    walk(document.documentElement);
    var pending = [], scheduled = false;
    function flush() {
      scheduled = false;
      var items = pending; pending = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.type === 'attributes') doAttrs(it.target);
        else if (it.type === 'characterData') doText(it.target);
        else it.addedNodes.forEach(walk);
      }
    }
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) pending.push(muts[i]);
      if (!scheduled) { scheduled = true; Promise.resolve().then(flush); }
    }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  document.documentElement.lang = lang;
  document.documentElement.dir = RTL[lang] ? 'rtl' : 'ltr';
  window.I18N = { t: t, lang: lang, dir: RTL[lang] ? 'rtl' : 'ltr', isRtl: !!RTL[lang], translateNode: walk };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
