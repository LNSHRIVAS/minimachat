/**
 * harness-tests.js — deterministic harness unit tests (Node or browser console).
 * Run: node harness-tests.js
 */
(function(global){
  'use strict';

  var passed = 0;
  var failed = 0;

  function assert(cond, msg){
    if(cond){ passed++; return; }
    failed++;
    console.error('FAIL:', msg);
  }

  function loadHarness(){
    if(global.Minima && global.Minima.harness) return global.Minima.harness;
    if(typeof require !== 'undefined'){
      require('./minima-harness.js');
      return global.Minima.harness;
    }
    throw new Error('Minima.harness not loaded');
  }

  var harness = loadHarness();

  // Protocol validation
  assert(harness.validatePayload({
    messages: [
      { role:'system', content:'x' },
      { role:'assistant', tool_calls:[{ id:'c1', function:{ name:'read_file', arguments:'{}' } }] },
      { role:'tool', tool_call_id:'c1', content:'ok' }
    ]
  }), 'valid single open cycle');

  assert(!harness.validatePayload({
    messages: [
      { role:'assistant', tool_calls:[{ id:'c1', function:{ name:'a', arguments:'{}' } }] }
    ]
  }), 'orphaned tool call invalid');

  assert(!harness.validatePayload({
    messages: [
      { role:'assistant', tool_calls:[{ id:'c1', function:{ name:'a', arguments:'{}' } }] },
      { role:'tool', tool_call_id:'c2', content:'x' }
    ]
  }), 'mismatched tool id invalid');

  // README mock: one write, two model calls max, no run_command
  (async function(){
    var modelCalls = 0;
    var toolsRun = [];
    var deps = {
      userText: 'create a README for this project',
      msgId: 'm1',
      turnContext: 'Now: test',
      messages: [],
      model: 'mock',
      tools: [{ type:'function', function:{ name:'write_file' } }],
      execTool: async function(name, args){
        toolsRun.push(name);
        return JSON.stringify({ ok:true, path:args.path });
      },
      apiCall: async function(){
        modelCalls++;
        if(modelCalls === 1){
          return {
            ok: true,
            body: { getReader: function(){
              var sent = false;
              return {
                read: async function(){
                  if(sent) return { done:true };
                  sent = true;
                  var chunk = 'data: '+JSON.stringify({
                    choices:[{ delta:{ tool_calls:[{ index:0, id:'w1', function:{ name:'write_file', arguments:JSON.stringify({ path:'D:\\\\readme.md', content:'# Hi' }) } }] }, finish_reason:'tool_calls' }]
                  })+'\n\n';
                  return { done:false, value: new TextEncoder().encode(chunk) };
                },
                cancel: function(){}
              };
            } }
          };
        }
        return {
          ok: true,
          body: { getReader: function(){
            var sent = false;
            return {
              read: async function(){
                if(sent) return { done:true };
                sent = true;
                var chunk = 'data: '+JSON.stringify({
                  choices:[{ delta:{ content:'README created at D:\\\\readme.md.' }, finish_reason:'stop' }]
                })+'\n\n';
                return { done:false, value: new TextEncoder().encode(chunk) };
              },
              cancel: function(){}
            };
          } }
        };
      },
      streamResponse: async function(resp){
        var reader = resp.body.getReader();
        var dec = new TextDecoder();
        var buf = '', full = '', tcs = [];
        while(true){
          var chunk = await reader.read();
          if(chunk.done) break;
          buf += dec.decode(chunk.value);
          var lines = buf.split('\n');
          buf = lines.pop() || '';
          for(var i=0;i<lines.length;i++){
            var l = lines[i].trim();
            if(!l.startsWith('data: ')) continue;
            var d = l.slice(6).trim();
            if(d === '[DONE]') break;
            var ck = JSON.parse(d);
            var dl = ck.choices && ck.choices[0] && ck.choices[0].delta || {};
            if(dl.tool_calls){
              for(var t=0;t<dl.tool_calls.length;t++){
                var tc = dl.tool_calls[t];
                tcs.push({ id:tc.id||'w1', type:'function', function:tc.function });
              }
            } else if(dl.content) full += dl.content;
          }
        }
        return { full:full, toolCalls:tcs };
      },
      aborted: function(){ return false; },
      clientNowLine: function(){ return 'now'; },
      formatContextNowLine: function(){ return 'Now: test'; },
      isTimeQuestion: function(){ return false; }
    };

    var run = await harness.run(deps);
    assert(run.status === 'final', 'README run final');
    assert(modelCalls <= 2, 'README at most 2 model calls (got '+modelCalls+')');
    assert(toolsRun.indexOf('write_file') >= 0, 'README uses write_file');
    assert(toolsRun.indexOf('run_command') < 0, 'README no run_command');
    assert(run.ledger.length >= 1, 'README ledger has tool row');

    // Byte budget on capText
    var big = 'x'.repeat(10000);
    assert(harness.capText(big, 8192).length <= 8192, 'capText respects limit');

    assert(!isFinite(harness.tokenBudget({ maxTokenBudget:0 })), 'zero budget means unlimited');
    assert(harness.tokenBudget({ maxTokenBudget:100000 }) === 100000, 'numeric budget preserved');

    console.log('harness-tests: '+passed+' passed, '+failed+' failed');
    if(failed) process.exit(1);
  })().catch(function(e){
    console.error(e);
    process.exit(1);
  });
})(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : global);
