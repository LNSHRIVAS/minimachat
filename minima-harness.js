/**
 * minima-harness.js — bounded linear model→tool loop with protocol-valid context.
 */
(function(global){
  'use strict';

  var MAX_TURN_SAFETY = 64;
  var DEFAULT_TOKEN_BUDGET = 100000;
  var MAX_MS = 10 * 60 * 1000;
  var LEDGER_MAX = 3000;
  var TURN_CTX_MAX = 2048;
  var RESULT_MAX = 8192;

  var SYSTEM_PROMPT = [
    'You are Minima, a helpful assistant with local file, web, memory, and book tools.',
    'Use tools for actions; trust tool results. Final reply with no tool calls ends the run.',
    'Documentation or simple file creation needs no tests unless the user asks.',
    'For code changes: grep_file to locate, head_file for imports/signatures, mtime before re-reading, read_file with offset/limit for sections, append_file to add lines at end, edit_file for patches.',
    'read_file always needs offset+limit (default 100 lines). head_file returns first 10 lines. Never read the same file twice without mtime check.',
    'find_in_workspace searches workspace only — never search_files on C:\\ or D:\\ drives.',
    'After locating a project file, remember its full path with remember.',
    'write_file creates new files; set overwrite:true to replace. edit_file replace must match once.',
    'Never use run_command for move/copy/delete — use native file tools.',
    'For deadlines/plans use remember with ttl_class slow (never ephemeral — it vanishes in 5 minutes). For preferences use permanent. For book passages use store_in_book (always appends; returns turn_id). Use edit_book_passage with that turn_id to revise.',
    'Temporal context and clock lines are authoritative for time/date questions — not web_search.',
    'Active memory JSON uses s=passed|upcoming and w=when. If s=passed, the plan/deadline is over — describe it in past tense only; never say tomorrow or upcoming.',
    'When recalling old plans, compare dates to the authoritative clock. Events before today already happened.',
    'Use full Windows paths. No emoji in replies.',
    'To show diagrams/illustrations/images in chat: output a complete ```svg ... ``` block in your reply. Do NOT use export_diagram for that.',
    'Use export_diagram only when the user explicitly asks to save a PNG file. Save under the workspace export directory from context — never C:\\Users\\default\\.',
    'Use view_image to display an existing PNG/JPG from disk or a URL in the viewport.',
    'If list_files returns path not found, try another path or ask — do not loop retries on the same path.'
  ].join('\n');

  function uid(){ return 'run_'+Date.now()+'_'+Math.random().toString(36).slice(2,8); }

  function capText(s, max){
    s = String(s || '');
    if(s.length <= max) return s;
    return s.slice(0, max - 20) + '\n...(truncated)';
  }

  function byteLen(obj){
    try{ return new Blob([JSON.stringify(obj)]).size; }catch(e){
      return JSON.stringify(obj).length;
    }
  }

  function summarizeToolResult(name, args, raw){
    var summary = name;
    var ok = true;
    var path = args && (args.path || args.source);
    if(path) summary += ' ' + path;
    try{
      var j = JSON.parse(raw);
      if(j.error){ ok = false; summary += ' error: ' + capText(j.error, 120); }
      else if(name === 'run_command'){
        summary += ' exit=' + (j.exit_code != null ? j.exit_code : '?');
        ok = !!j.ok;
        if(j.stderr) summary += ' ' + capText(String(j.stderr).split('\n')[0], 80);
      } else if(name === 'read_file'){
        var m = String(raw).match(/\[file has (\d+) lines/);
        if(m) summary += ' lines=' + m[1];
      } else if(j.ok === false) ok = false;
      else if(j.path) summary += ' ok';
    }catch(e){
      if(/error/i.test(String(raw).slice(0,200))) ok = false;
      summary += ' ' + capText(String(raw).split('\n')[0], 80);
    }
    return { tool:name, path:path || null, ok:ok, summary:capText(summary, 200), bytes:String(raw).length };
  }

  function ledgerText(run){
    if(!run.ledger.length) return '';
    var lines = run.ledger.map(function(r, i){
      return (i+1) + '. ' + r.summary + (r.ok ? '' : ' [fail]');
    });
    var t = 'Run ledger:\n' + lines.join('\n');
    return capText(t, LEDGER_MAX);
  }

  function recentFinalMessages(messages, msgId, limit){
    var finals = [];
    for(var i = messages.length - 1; i >= 0 && finals.length < limit; i--){
      var m = messages[i];
      if(m.id === msgId) continue;
      if(m.hide || m.role === 'system') continue;
      if(m.role === 'user'){
        finals.unshift({ role:'user', content:m.content });
        continue;
      }
      if(m.role === 'assistant' && m.content && !m.tool_calls){
        finals.unshift({ role:'assistant', content:m.content });
      }
    }
    return finals;
  }

  function buildMessages(run, deps){
    var sys = SYSTEM_PROMPT;
    sys += '\n\n' + deps.clientNowLine();
    sys += '\n\nAuthoritative clock: ' + deps.formatContextNowLine(new Date());
    if(run.turnContext){
      sys += '\n\n---\nTemporal context:\n' + capText(run.turnContext, TURN_CTX_MAX);
    }
    if(deps.isTimeQuestion && deps.isTimeQuestion(run.userText)){
      sys += '\n\nUser asks for current time — answer from clock above, not web_search.';
    }
    if(deps.getPassedFacts){
      var passed = deps.getPassedFacts();
      if(passed.length){
        sys += '\n\nPassed plans (historical — NOT upcoming):\n';
        for(var pi = 0; pi < Math.min(passed.length, 6); pi++){
          var pf = passed[pi];
          sys += '- ' + capText(pf.content || '', 120);
          if(pf.when) sys += ' (' + pf.when + ')';
          sys += '\n';
        }
      }
    }
    if(deps.workspaceLine){
      sys += '\n\n---\nWorkspace:\n' + capText(deps.workspaceLine(), 512);
    }
    var msgs = [{ role:'system', content:sys }];
    var finals = recentFinalMessages(deps.messages, run.msgId, 4);
    for(var i = 0; i < finals.length; i++){
      var fm = finals[i];
      if(fm.role === 'assistant' && deps.sanitizeAssistantContent){
        fm = { role:'assistant', content:deps.sanitizeAssistantContent(fm.content) };
      }
      msgs.push(fm);
    }
    var led = ledgerText(run);
    if(led) msgs.push({ role:'user', content:led, _harness:true });
    if(run.openCycle && run.openCycle.assistantMsg){
      msgs.push(run.openCycle.assistantMsg);
      if(run.openCycle.toolResults){
        for(var t = 0; t < run.openCycle.toolResults.length; t++){
          msgs.push(run.openCycle.toolResults[t]);
        }
      }
    } else if(run.turn === 0){
      msgs.push({ role:'user', content:run.userText });
    }
    run.metrics.lastRequestBytes = byteLen({ messages:msgs, tools:deps.tools });
    return { model:deps.model, messages:msgs, stream:true, tools:deps.tools, tool_choice:'auto' };
  }

  function validatePayload(payload){
    var msgs = payload.messages || [];
    var pending = null;
    for(var i = 0; i < msgs.length; i++){
      var m = msgs[i];
      if(m.role === 'assistant' && m.tool_calls && m.tool_calls.length){
        if(pending) return false;
        pending = {};
        for(var j = 0; j < m.tool_calls.length; j++) pending[m.tool_calls[j].id] = true;
        continue;
      }
      if(m.role === 'tool'){
        if(!pending || !pending[m.tool_call_id]) return false;
        delete pending[m.tool_call_id];
        if(!Object.keys(pending).length) pending = null;
      }
    }
    return !pending;
  }

  function compactOpenCycle(run){
    if(!run.openCycle || !run.openCycle.toolResults) return;
    var oc = run.openCycle;
    for(var i = 0; i < oc.toolResults.length; i++){
      var tr = oc.toolResults[i];
      var meta = oc.meta && oc.meta[i] ? oc.meta[i] : { tool:'?', summary:'done', ok:true, bytes:0 };
      run.ledger.push(meta);
      run.metrics.toolResultBytes += meta.bytes || 0;
    }
    run.openCycle = null;
  }

  function pushEvent(run, type, detail){
    run.events.push({ t:Date.now(), type:type, detail:detail || {} });
    if(run.onEvent) run.onEvent(type, detail);
  }

  function tokenBudget(deps){
    var n = deps && deps.maxTokenBudget;
    if(n == null || n === '') return DEFAULT_TOKEN_BUDGET;
    n = parseInt(n, 10);
    if(n === 0) return Infinity;
    if(isNaN(n) || n < 1000) return DEFAULT_TOKEN_BUDGET;
    return n;
  }

  function addUsage(run, result, payload){
    var added = 0;
    if(result && result.usage){
      var u = result.usage;
      added = u.total_tokens || ((u.prompt_tokens || 0) + (u.completion_tokens || 0));
    }
    if(!added){
      var req = (run.metrics && run.metrics.lastRequestBytes) || byteLen(payload || {});
      var resp = String((result && result.full) || '').length;
      added = Math.ceil((req + resp) / 4);
    }
    run.metrics.totalTokens = (run.metrics.totalTokens || 0) + added;
    run.metrics.lastTurnTokens = added;
    return added;
  }

  function overTokenBudget(run, deps){
    var budget = tokenBudget(deps);
    if(!isFinite(budget)) return false;
    return (run.metrics.totalTokens || 0) >= budget;
  }

  async function executeToolsSequentially(toolCalls, run, deps){
    var assistantMsg = {
      role:'assistant',
      content: run.pendingAssistantText || null,
      tool_calls: toolCalls
    };
    var toolResults = [];
    var meta = [];
    run.openCycle = { assistantMsg:assistantMsg, toolResults:toolResults, meta:meta };

    for(var i = 0; i < toolCalls.length; i++){
      if(deps.aborted()) break;
      var tc = toolCalls[i];
      var name = tc.function && tc.function.name || '';
      var args = {};
      try{ args = JSON.parse(tc.function.arguments || '{}'); }catch(e){}
      pushEvent(run, 'tool_started', { tool:name, args:args });
      if(deps.onToolStart) deps.onToolStart(name, args);
      var raw;
      try{
        raw = await deps.execTool(name, args);
      }catch(e){
        raw = JSON.stringify({ error:e.message });
      }
      raw = capText(raw, RESULT_MAX);
      toolResults.push({ role:'tool', tool_call_id:tc.id, content:raw });
      var sm = summarizeToolResult(name, args, raw);
      meta.push(sm);
      pushEvent(run, 'tool_finished', sm);
      if(deps.onToolFinish) deps.onToolFinish(name, args, raw, sm);
      if(deps.persist) deps.persist();
    }
  }

  async function runHarness(deps){
    var run;
    if(deps.resumeRun){
      var r = deps.resumeRun;
      run = {
        id: r.id,
        status:'running',
        terminalReason:null,
        turn: r.turn || 0,
        userText: deps.userText || r.userText,
        msgId: deps.msgId,
        turnContext: capText(r.turnContext || deps.turnContext || '', TURN_CTX_MAX),
        ledger: (r.ledger || []).slice(),
        openCycle: r.openCycle || null,
        events: (r.events || []).slice(),
        metrics: {
          modelCalls: (r.metrics && r.metrics.modelCalls) || 0,
          requestBytes: 0,
          toolResultBytes: (r.metrics && r.metrics.toolResultBytes) || 0,
          elapsedMs: (r.metrics && r.metrics.elapsedMs) || 0,
          cacheHit: (r.metrics && r.metrics.cacheHit) || 0,
          cacheMiss: (r.metrics && r.metrics.cacheMiss) || 0,
          totalTokens: (r.metrics && r.metrics.totalTokens) || 0,
          lastTurnTokens: (r.metrics && r.metrics.lastTurnTokens) || 0
        },
        onEvent: deps.onEvent,
        startedAt: Date.now() - ((r.metrics && r.metrics.elapsedMs) || 0),
        pendingAssistantText: r.pendingAssistantText || ''
      };
      pushEvent(run, 'thinking', { resumed: true });
    } else {
      run = {
        id: uid(),
        status:'running',
        terminalReason:null,
        turn:0,
        userText:deps.userText,
        msgId:deps.msgId,
        turnContext:capText(deps.turnContext || '', TURN_CTX_MAX),
        ledger:[],
        openCycle:null,
        events:[],
        metrics:{ modelCalls:0, requestBytes:0, toolResultBytes:0, elapsedMs:0, cacheHit:0, cacheMiss:0, totalTokens:0, lastTurnTokens:0 },
        onEvent:deps.onEvent,
        startedAt:Date.now(),
        pendingAssistantText:''
      };
      pushEvent(run, 'thinking', {});
    }

    var startTurn = run.turn || 0;
    for(run.turn = startTurn; run.turn < MAX_TURN_SAFETY; run.turn++){
      if(deps.aborted()){
        run.status = 'stopped';
        run.terminalReason = 'user';
        pushEvent(run, 'stopped', {});
        return run;
      }
      if(Date.now() - run.startedAt > MAX_MS){
        run.status = 'paused';
        run.terminalReason = 'timeout';
        pushEvent(run, 'paused', { reason:'timeout' });
        return run;
      }

      var payload = buildMessages(run, deps);
      if(!validatePayload(payload)){
        run.status = 'error';
        run.terminalReason = 'protocol';
        pushEvent(run, 'error', { message:'invalid tool protocol in context' });
        return run;
      }

      run.metrics.modelCalls++;
      var resp;
      try{
        resp = await deps.apiCall(payload);
      }catch(e){
        if(deps.aborted()){
          run.status = 'stopped';
          run.terminalReason = 'user';
          pushEvent(run, 'stopped', {});
          return run;
        }
        run.status = 'error';
        run.terminalReason = 'api_error';
        pushEvent(run, 'error', { message:e.message });
        return run;
      }

      if(!resp.ok){
        var errBody = '';
        try{ errBody = await resp.text(); }catch(e){}
        run.status = 'error';
        run.terminalReason = 'api_error';
        pushEvent(run, 'error', { message:'HTTP '+resp.status, body:capText(errBody, 300) });
        return run;
      }

      var result = await deps.streamResponse(resp, run.msgId);
      addUsage(run, result, payload);
      if(result.usage){
        run.metrics.cacheHit += result.usage.prompt_cache_hit_tokens || result.usage.cached_tokens || 0;
        run.metrics.cacheMiss += result.usage.prompt_cache_miss_tokens || 0;
      }
      if(overTokenBudget(run, deps)){
        run.status = 'paused';
        run.terminalReason = 'token_budget';
        run.pendingAssistantText = result.full || '';
        pushEvent(run, 'paused', { reason:'token_budget', tokens:run.metrics.totalTokens });
        compactOpenCycle(run);
        run.metrics.elapsedMs = Date.now() - run.startedAt;
        return run;
      }

      if(deps.aborted()){
        run.status = 'stopped';
        run.terminalReason = 'user';
        run.pendingAssistantText = result.full || '';
        pushEvent(run, 'stopped', {});
        return run;
      }

      if(!result.toolCalls || !result.toolCalls.length){
        run.status = 'final';
        run.terminalReason = 'complete';
        run.finalText = (result.full || '').trim() || '(no response)';
        pushEvent(run, 'final', { text:run.finalText });
        compactOpenCycle(run);
        run.metrics.elapsedMs = Date.now() - run.startedAt;
        return run;
      }

      run.pendingAssistantText = result.full || '';
      compactOpenCycle(run);
      await executeToolsSequentially(result.toolCalls, run, deps);
      if(deps.aborted()){
        run.status = 'stopped';
        run.terminalReason = 'user';
        pushEvent(run, 'stopped', {});
        return run;
      }
    }

    run.status = 'paused';
    run.terminalReason = 'turn_safety';
    pushEvent(run, 'paused', { reason:'turn_safety', tokens:run.metrics.totalTokens });
    compactOpenCycle(run);
    run.metrics.elapsedMs = Date.now() - run.startedAt;
    return run;
  }

  global.Minima = global.Minima || {};
  global.Minima.harness = {
    SYSTEM_PROMPT: SYSTEM_PROMPT,
    MAX_TURN_SAFETY: MAX_TURN_SAFETY,
    DEFAULT_TOKEN_BUDGET: DEFAULT_TOKEN_BUDGET,
    tokenBudget: tokenBudget,
    run: runHarness,
    buildMessages: buildMessages,
    validatePayload: validatePayload,
    capText: capText,
    summarizeToolResult: summarizeToolResult
  };
})(typeof window !== 'undefined' ? window : globalThis);
