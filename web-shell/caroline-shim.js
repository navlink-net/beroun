// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
// The window.chrome.webview shim for Caroline's chat.html on the device (injected by shelld at serve time).
// Caroline's page talks to its native host through chrome.webview.postMessage and listens with addEventListener("message").
// Here the host is the Caroline layer page (system/caroline), which holds the tab strip; this page lives in an iframe of it.
// Messages that make no sense on the device are answered the way Caroline's own Linux shell answers them
// (linux-shell/caroline_shell.py, on_page_message): see docs/architecture.md
(function () {
  if (window.chrome && window.chrome.webview) return;
  const tab = new URLSearchParams(location.search).get('tab') || '1';
  const visualHost = tab === 'voice';
  const listeners = [];
  const dispatch = data => listeners.slice().forEach(fn => { try { fn({data}); } catch (e) { console.error(e); } });
  const hostLog = m => fetch('/api/log', {method: 'POST', body: JSON.stringify({msg: 'caroline tab ' + tab + ' ' + m})}).catch(() => {});
  function up(m) { if (/^visual_speech/.test(m.type || '')) hostLog('-> view ' + m.type + (m.requestId ? ' ' + m.requestId : '')); try { parent.postMessage({caroline: 'page', tab, msg: m}, '*'); } catch (e) {} }

  // what the page posts, and what the device does with it
  const handlers = {
    client_log: m => hostLog(m.message || ''),
    set_always_on_top: () => {},                 // no windows to keep above
    tab_state: up,                               // the layer shows the mode of the tab
    no_chat_source_error: up,
    no_chat_source_resolved: up,
    // Visual Mode (her talking head) lives in the voice view (system/caroline-voice, the tab "voice"): there the messages go up to the view, which
    // renders and plays the clip and answers visual_speech_done. Everywhere else (the text chat) "not played" makes the page fall back to plain
    // audio, as her Linux shell does when there is no animation.
    visual_speech_audio: m => visualHost ? up(m) : setTimeout(() => dispatch({type: 'visual_speech_done', requestId: m.requestId, played: false}), 0),
    visual_mode_config: () => {},
    visual_speech_prepare: m => { if (visualHost) up(m); }, visual_speech_start: m => { if (visualHost) up(m); },
    visual_speech_stop: m => { if (visualHost) up(m); }, visual_speech_cancel: m => { if (visualHost) up(m); },
    visual_speech_dismiss: m => { if (visualHost) up(m); },
    // viewers, editors, login and payment windows do not exist on the device yet
    open_editor: m => hostLog('unsupported ' + m.type), open_office_editor: m => hostLog('unsupported ' + m.type),
    close_editor: () => {}, open_login: m => hostLog('unsupported ' + m.type), open_payment: m => hostLog('unsupported ' + m.type),
  };

  // ---- voice mode (IntelliCat only): a hands-free dialogue on top of her own page, which records (getUserMedia, silence
  // ends an utterance), has the backend recognize it and plays the answer. This loop only presses her mic button whenever
  // nothing is going on, switches her continuous dialogue on (every answer is spoken) and reports the state to the layer.
  const backend = 'http://127.0.0.1:' + (new URLSearchParams(location.search).get('port') || String(48765 + (Number(location.port) - 48767)));
  const $ = id => document.getElementById(id);
  let voice = null;
  const voiceState = () => {
    const mic = $('micBtn'), stop = $('stopBtn');
    if (!mic) return 'idle';
    if (mic.classList.contains('recording')) return 'listening';
    if (mic.classList.contains('transcribing')) return 'thinking';
    if (document.querySelector('.speaking')) return 'speaking';
    if (stop && stop.offsetParent !== null) return 'thinking';
    return 'idle';
  };
  function setSwitch(el, on) { if (el && el.checked !== on) { el.checked = on; el.dispatchEvent(new Event('change', {bubbles: true})); } }
  function setDialogue(on) { setSwitch($('continuousDialogueEnabled'), on); }
  function voiceStart() {
    if (voice) return;
    voice = {state: '', idle: 0, before: null, dialogue: false};
    voice.timer = setInterval(() => {
      // her own switch in the settings panel: changing it tells the backend. The page may not have it yet when the view
      // has only just been opened, so it is tried on every round until it is there
      if (!voice.dialogue) {
        const cd = $('continuousDialogueEnabled');
        if (cd) { voice.before = cd.checked; setDialogue(true); voice.dialogue = true; }
      }
      // (her Visual Mode switch is put on by shelld in her workspace before the view opens: with one model on the card her own
      // switch, which wants both day-parity files, would start a download)
      const s = voiceState();
      if (s !== voice.state) { hostLog('voice state ' + s); voice.state = s; up({type: 'voice_state', state: s === 'idle' ? 'thinking' : s}); }
      voice.idle = s === 'idle' ? voice.idle + 1 : 0;
      if (voice.idle >= 3 && $('micBtn') && Date.now() >= (voice.hold || 0)) { voice.idle = 0; $('micBtn').click(); }  // nothing going on for 1.5 s: listen again
    }, 500);
  }
  function voiceStop() {
    if (!voice) return;
    clearInterval(voice.timer);
    const mic = $('micBtn');
    if (mic && mic.classList.contains('recording')) mic.click();  // ends the recording
    if (voice.before === false) setDialogue(false);
    voice = null;
  }
  function voiceInterrupt() {  // speaking over the answer: it stops, the loop listens again
    const stop = $('stopBtn');
    if (stop && stop.offsetParent !== null) stop.click();
  }

  window.chrome = window.chrome || {};
  window.chrome.webview = {
    postMessage(obj) {
      const m = typeof obj === 'string' ? JSON.parse(obj) : obj;
      if (visualHost && m && m.type !== 'client_log') hostLog('page posts ' + m.type);
      const h = handlers[m && m.type];
      if (h) h(m); else hostLog('unhandled message type=' + (m && m.type));
    },
    addEventListener(name, fn) { if (name === 'message') listeners.push(fn); },
    removeEventListener(name, fn) { const i = listeners.indexOf(fn); if (name === 'message' && i >= 0) listeners.splice(i, 1); },
  };
  addEventListener('message', e => {
    if (!e.data || e.data.caroline !== 'host') return;
    // the layer asks for her own settings panel (the Settings app's Caroline section): same as the gear button
    if (e.data.msg && e.data.msg.type === 'open_settings') { const b = document.getElementById('settingsBtn'); if (b) b.click(); return; }
    if (e.data.msg && e.data.msg.type === 'voice_mode') return e.data.msg.on ? voiceStart() : voiceStop();
    if (e.data.msg && e.data.msg.type === 'voice_interrupt') return voiceInterrupt();
    // a test of the voice view types a message into her page as the user would (in the continuous dialogue the answer is spoken)
    // (her page speaks an answer only when no recording is open, so the open one is ended first and the loop waits a while)
    if (e.data.msg && e.data.msg.type === 'test_say') {
      const i = $('input'), b = $('sendBtn'), mic = $('micBtn');
      if (voice) voice.hold = Date.now() + 40000;
      if (mic && mic.classList.contains('recording')) mic.click();
      setTimeout(() => { if (i && b) { i.value = e.data.msg.text; i.dispatchEvent(new Event('input', {bubbles: true})); b.click(); } }, 4000);
      return;
    }
    dispatch(e.data.msg);
  });
})();
