/**
 * minima-events.js — run event strip + full code editor panel.
 */
(function(global){
  'use strict';

  function esc(s){
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function fileName(p){
    if(!p) return 'file';
    return p.split('\\').pop() || p.split('/').pop() || p;
  }

  function stripLineNumbers(text){
    if(!text) return '';
    return String(text).split('\n').map(function(line){
      return line.replace(/^\s*\d+\|/, '');
    }).join('\n');
  }

  var WS = {
    panelOpen: false,
    activePath: null,
    dirty: false,
    saving: false,
    saveTimer: null,
    agentWriting: false,
    streaming: false,
    onStop: null,
    files: {}
  };

  var panel, backdrop, titleEl, statusEl, filesEl, editor, lnEl, stopBtn, closeBtn;

  function init(opts){
    opts = opts || {};
    WS.onStop = opts.onStop || null;
    panel = document.getElementById('code-panel');
    backdrop = document.getElementById('code-panel-backdrop');
    titleEl = document.getElementById('code-panel-title');
    statusEl = document.getElementById('code-panel-status');
    filesEl = document.getElementById('code-panel-files');
    editor = document.getElementById('code-panel-editor');
    lnEl = document.getElementById('code-panel-ln');
    stopBtn = document.getElementById('code-panel-stop');
    closeBtn = document.getElementById('code-panel-close');

    if(closeBtn) closeBtn.addEventListener('click', closePanel);
    if(backdrop) backdrop.addEventListener('click', closePanel);
    if(stopBtn) stopBtn.addEventListener('click', function(){
      if(WS.onStop) WS.onStop('stopped');
    });
    if(editor){
      editor.addEventListener('input', function(){
        if(WS.agentWriting) return;
        WS.dirty = true;
        if(WS.activePath && WS.files[WS.activePath]) WS.files[WS.activePath].content = editor.value;
        syncLineNumbers();
        scheduleSave();
      });
      editor.addEventListener('scroll', function(){
        if(lnEl && editor) lnEl.scrollTop = editor.scrollTop;
      });
      editor.addEventListener('keydown', function(e){
        if((e.ctrlKey || e.metaKey) && e.key === 's'){ e.preventDefault(); flushSave(); }
      });
    }
  }

  function syncLineNumbers(){
    if(!lnEl || !editor) return;
    var text = editor.value || '';
    var n = Math.max(1, text.split('\n').length);
    var buf = [];
    for(var i = 1; i <= n; i++) buf.push(String(i));
    lnEl.textContent = buf.join('\n');
    if(lnEl && editor) lnEl.scrollTop = editor.scrollTop;
  }

  function setEditorValue(text){
    if(!editor) return;
    WS.agentWriting = true;
    editor.value = text || '';
    WS.agentWriting = false;
    syncLineNumbers();
  }

  function setStreaming(on){
    WS.streaming = !!on;
    if(stopBtn) stopBtn.disabled = !WS.streaming;
    setStatus(on ? 'agent working…' : (WS.dirty ? 'unsaved changes' : 'ready'), on);
  }

  function setStatus(text, live){
    if(!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.classList.toggle('is-live', !!live);
  }

  function registerFile(path, content, op, opts){
    opts = opts || {};
    if(!path) return;
    path = String(path);
    WS.files[path] = {
      content: typeof content === 'string' ? content : (WS.files[path] && WS.files[path].content) || '',
      op: op || 'read',
      ts: Date.now()
    };
    if(WS.panelOpen) renderFileList();
  }

  function listableFiles(){
    return Object.keys(WS.files).filter(function(p){
      return (WS.files[p].content || '').length > 0 || WS.files[p].op !== 'read';
    });
  }

  function renderFileList(){
    if(!filesEl) return;
    var paths = listableFiles().sort(function(a,b){ return WS.files[b].ts - WS.files[a].ts; });
    var html = '';
    for(var i = 0; i < paths.length; i++){
      var p = paths[i], f = WS.files[p];
      html += '<button type="button" class="code-panel-file'+(p===WS.activePath?' active':'')+'" data-path="'+esc(p)+'">'+
        esc(fileName(p))+'<span class="op">'+esc(f.op)+'</span></button>';
    }
    if(!html) html = '<div style="padding:8px;font-size:10px;color:var(--ink-muted);font-family:var(--mono)">no files yet</div>';
    filesEl.innerHTML = html;
    filesEl.querySelectorAll('.code-panel-file').forEach(function(btn){
      btn.addEventListener('click', function(){ selectFile(btn.getAttribute('data-path')); });
    });
  }

  function selectFile(path){
    if(!path || !WS.files[path]) return;
    WS.activePath = path;
    setEditorValue(WS.files[path].content || '');
    WS.dirty = false;
    if(titleEl) titleEl.textContent = path;
    renderFileList();
    setStatus('ready', false);
  }

  function openPanel(path){
    if(!panel) return;
    WS.panelOpen = true;
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    renderFileList();
    if(path && WS.files[path]) selectFile(path);
    else if(!WS.activePath){
      var keys = listableFiles();
      if(keys.length) selectFile(keys.sort(function(a,b){ return WS.files[b].ts - WS.files[a].ts; })[0]);
    }
  }

  function closePanel(){
    if(WS.dirty) flushSave();
    WS.panelOpen = false;
    if(panel){ panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true'); }
  }

  function scheduleSave(){
    if(!WS.activePath || !WS.dirty) return;
    if(WS.saveTimer) clearTimeout(WS.saveTimer);
    WS.saveTimer = setTimeout(flushSave, 700);
  }

  function flushSave(){
    if(WS.saveTimer){ clearTimeout(WS.saveTimer); WS.saveTimer = null; }
    if(!WS.activePath || !WS.dirty || WS.saving || !editor) return;
    WS.saving = true;
    setStatus('saving…', true);
    fetch('/api/fs/write', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ path:WS.activePath, content:editor.value, overwrite:true })
    }).then(function(r){
      if(r.ok){
        WS.dirty = false;
        if(WS.files[WS.activePath]) WS.files[WS.activePath].content = editor.value;
        setStatus(WS.streaming ? 'agent working…' : 'saved', WS.streaming);
      } else setStatus('save failed');
    }).catch(function(){ setStatus('save failed'); })
    .finally(function(){ WS.saving = false; });
  }

  async function loadFullFile(path){
    var chunks = [];
    var offset = 1;
    var limit = 160;
    while(true){
      var url = '/api/fs/read?path='+encodeURIComponent(path)+'&offset='+offset+'&limit='+limit;
      var r = await fetch(url);
      if(!r.ok) throw new Error('HTTP '+r.status);
      chunks.push(stripLineNumbers(await r.text()));
      var next = r.headers.get('X-Next-Offset');
      if(!next) break;
      offset = parseInt(next, 10) || (offset + limit);
    }
    return chunks.join('\n');
  }

  async function openFile(path){
    if(!path) return;
    try{
      setStatus('loading…', true);
      var text = await loadFullFile(path);
      registerFile(path, text, 'read');
      openPanel(path);
      setStatus('ready', false);
    }catch(e){
      setStatus('load failed', false);
      registerFile(path, 'Could not open: '+e.message, 'error');
      openPanel(path);
    }
  }

  async function refreshFromDisk(path){
    if(!path) return;
    try{
      var text = await loadFullFile(path);
      registerFile(path, text, WS.files[path] && WS.files[path].op || 'read');
      if(WS.panelOpen && path === WS.activePath && editor && !WS.dirty) setEditorValue(text);
    }catch(e){}
  }

  /* ── Run events (chat bubble) ── */

  function ensureEventHost(bubble){
    var host = bubble.querySelector('.run-events');
    if(!host){
      host = document.createElement('div');
      host.className = 'run-events';
      var prose = bubble.querySelector('.stream-prose');
      if(prose && prose.nextSibling) bubble.insertBefore(host, prose.nextSibling);
      else bubble.appendChild(host);
    }
    return host;
  }

  function renderEvents(bubble, events){
    if(!bubble || !events || !events.length) return;
    var host = ensureEventHost(bubble);
    var html = '';
    for(var i = 0; i < events.length; i++){
      var ev = events[i];
      if(ev.type === 'thinking') html += '<div class="run-event run-event-thinking">Working…</div>';
      else if(ev.type === 'tool_started'){
        var d = ev.detail || {};
        html += '<div class="run-event run-event-tool">'+esc(d.tool || 'tool')+'</div>';
      }
      else if(ev.type === 'tool_finished'){
        var f = ev.detail || {};
        var cls = f.ok ? 'run-event-ok' : 'run-event-fail';
        html += '<div class="run-event run-event-done '+cls+'">'+esc(f.summary || f.tool || 'done')+'</div>';
      }
      else if(ev.type === 'paused'){
        html += '<div class="run-event run-event-paused">Paused — send Continue or a new message</div>';
      }
      else if(ev.type === 'stopped'){
        html += '<div class="run-event run-event-stopped">Stopped</div>';
      }
      else if(ev.type === 'error'){
        html += '<div class="run-event run-event-fail">Error</div>';
      }
    }
    host.innerHTML = html;
  }

  function clearRunEvents(bubble){
    if(!bubble) return;
    var host = bubble.querySelector('.run-events');
    if(host) host.remove();
  }

  function restoreMessage(msgId, bubble, run){
    if(!bubble || !run) return;
    clearRunEvents(bubble);
    if(run.status !== 'interrupted' && run.status !== 'running' && run.status !== 'paused'){
      return;
    }
    var host = ensureEventHost(bubble);
    host.innerHTML = '';
    if(run.status === 'paused'){
      var reason = run.terminalReason || 'budget';
      var label = reason === 'token_budget' ? 'Paused (token budget)'
        : reason === 'timeout' ? 'Paused (time limit)'
        : reason === 'turn_safety' ? 'Paused (safety cap)'
        : 'Paused';
      var tokens = run.metrics && run.metrics.totalTokens;
      if(tokens) label += ' · ~' + Number(tokens).toLocaleString() + ' tokens';
      host.innerHTML = '<div class="run-event run-event-paused">'+esc(label)+' — send Continue or a new message</div>';
    } else if(run.status === 'running' || run.status === 'interrupted'){
      host.innerHTML = '<div class="run-event run-event-thinking">Working…</div>';
    }
    if(run.status === 'paused' || run.status === 'interrupted'){
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'run-continue-btn';
      btn.textContent = 'Continue run';
      btn.addEventListener('click', function(){
        if(global.Minima && global.Minima.app && global.Minima.app.continueRun){
          global.Minima.app.continueRun(msgId);
        }
      });
      host.appendChild(btn);
    }
  }

  function addFileLink(bubble, path){
    if(!bubble || !path) return;
    var host = ensureEventHost(bubble);
    if(!host) return;
    var fname = fileName(path);
    var existing = host.querySelectorAll('.run-file-link');
    for(var i=0;i<existing.length;i++){
      if(existing[i].textContent === fname) return;
    }
    var link = document.createElement('button');
    link.type = 'button';
    link.className = 'run-file-link';
    link.textContent = fileName(path);
    link.addEventListener('click', function(){ openFile(path); });
    ensureEventHost(bubble).appendChild(link);
  }

  function onToolStart(msgId, bubble, name, args){
    args = args || {};
    if(typeof global.beginToolActivity === 'function'){
      global.beginToolActivity(msgId, name, args);
      return;
    }
    if(typeof global.setActivity === 'function'){
      global.setActivity(msgId, name, args);
      return;
    }
    if(!bubble) return;
    var path = args.path || args.source || args.command;
    var host = ensureEventHost(bubble);
    var line = document.createElement('div');
    line.className = 'run-event run-event-tool';
    line.textContent = name + (path ? ' · '+fileName(String(path)) : '');
    host.appendChild(line);
  }

  function onToolFinish(msgId, bubble, name, args, raw, summary){
    args = args || {};
    if(typeof global.finishToolActivity === 'function'){
      global.finishToolActivity(msgId, name, args);
    } else if(typeof global.pushActivity === 'function'){
      global.pushActivity(msgId, name, args);
    }
    if((name === 'write_file' || name === 'edit_file') && args.path){
      var content = args.content;
      if(typeof content === 'string') registerFile(args.path, content, name === 'write_file' ? 'write' : 'edit');
      else refreshFromDisk(args.path);
    } else if(name === 'read_file' && args.path){
      refreshFromDisk(args.path);
    }
    if(bubble && (name === 'write_file' || name === 'edit_file' || name === 'append_file') && args.path){
      addFileLink(bubble, args.path);
    }
    if(bubble && (name === 'export_diagram' || name === 'view_image')){
      try{
        var parsed = JSON.parse(raw || '{}');
        if(global.showToolImageResult) global.showToolImageResult(bubble, name, parsed, args);
      }catch(e){}
    }
  }

  global.Minima = global.Minima || {};
  global.Minima.events = {
    init: init,
    setStreaming: setStreaming,
    setStatus: setStatus,
    closePanel: closePanel,
    openPanel: openPanel,
    openFile: openFile,
    registerFile: registerFile,
    renderEvents: renderEvents,
    restoreMessage: restoreMessage,
    clearRunEvents: clearRunEvents,
    onToolStart: onToolStart,
    onToolFinish: onToolFinish,
    get panelOpen(){ return WS.panelOpen; }
  };

  global.CodeStream = {
    init: init,
    setStreaming: setStreaming,
    setStatus: setStatus,
    closePanel: closePanel,
    openPanel: openPanel,
    openFile: openFile,
    registerFile: registerFile,
    restoreMessageCapsule: function(msgId, bubble){
      var msg = null;
      if(global.S && global.S.messages){
        for(var i=0;i<global.S.messages.length;i++){
          if(global.S.messages[i].id===msgId){ msg=global.S.messages[i]; break; }
        }
      }
      if(msg && msg.run) restoreMessage(msgId, bubble, msg.run);
    },
    resetCapsules: function(){},
    clearMsg: function(){},
    onToolComplete: function(){},
    isCodingTool: function(){ return false; },
    shouldHidePreview: function(){ return true; },
    settleCapsule: function(){}
  };
})(typeof window !== 'undefined' ? window : globalThis);
