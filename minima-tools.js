/**
 * minima-tools.js — tool schemas and bounded API adapters.
 */
(function(global){
  'use strict';

  var TOOLS = [
    {type:'function',function:{name:'list_files',description:'List files in a folder (max 100). Response includes workspace hint when folder is empty or at drive root.',parameters:{type:'object',properties:{path:{type:'string'},limit:{type:'integer'}},required:['path']}}},
    {type:'function',function:{name:'read_file',description:'Read numbered lines from a local file. Always use offset+limit (default limit 100, max 160). Prefer head_file for imports/signatures.',parameters:{type:'object',properties:{path:{type:'string'},offset:{type:'integer',description:'1-based line'},limit:{type:'integer',description:'max 160'}},required:['path']}}},
    {type:'function',function:{name:'head_file',description:'First N lines only (default 10, max 40). Use before read_file to inspect imports, classes, signatures.',parameters:{type:'object',properties:{path:{type:'string'},lines:{type:'integer',description:'default 10'}},required:['path']}}},
    {type:'function',function:{name:'mtime',description:'File mtime, size, and hash. Check before re-reading to avoid stale/duplicate reads.',parameters:{type:'object',properties:{path:{type:'string'}},required:['path']}}},
    {type:'function',function:{name:'append_file',description:'Append text to end of file. Use for new functions, imports, or __main__ blocks without read+edit.',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'}},required:['path','content']}}},
    {type:'function',function:{name:'find_in_workspace',description:'Find files by name under workspace root only (not whole drives). Use instead of search_files on C:/D:.',parameters:{type:'object',properties:{pattern:{type:'string'},depth:{type:'integer'}},required:['pattern']}}},
    {type:'function',function:{name:'workspace_path',description:'Return current workspace folder and export directory',parameters:{type:'object',properties:{},required:[]}}},
    {type:'function',function:{name:'grep_file',description:'Search for pattern; returns path, line, preview (max 20 hits).',parameters:{type:'object',properties:{path:{type:'string'},pattern:{type:'string'},limit:{type:'integer'}},required:['path','pattern']}}},
    {type:'function',function:{name:'write_file',description:'Create a new file. Set overwrite:true to replace existing.',parameters:{type:'object',properties:{path:{type:'string'},content:{type:'string'},overwrite:{type:'boolean'}},required:['path','content']}}},
    {type:'function',function:{name:'edit_file',description:'In-place edits. replace must match exactly once unless replace_all.',parameters:{type:'object',properties:{path:{type:'string'},edits:{type:'array',items:{type:'object'}}},required:['path','edits']}}},
    {type:'function',function:{name:'create_folder',description:'Create folder (creates intermediate directories as needed)',parameters:{type:'object',properties:{path:{type:'string'}},required:['path']}}},
    {type:'function',function:{name:'move_file',description:'Move/rename',parameters:{type:'object',properties:{source:{type:'string'},dest:{type:'string'}},required:['source','dest']}}},
    {type:'function',function:{name:'copy_file',description:'Copy file/folder',parameters:{type:'object',properties:{source:{type:'string'},dest:{type:'string'}},required:['source','dest']}}},
    {type:'function',function:{name:'delete_file',description:'Delete file/folder',parameters:{type:'object',properties:{path:{type:'string'}},required:['path']}}},
    {type:'function',function:{name:'search_files',description:'Find files by name',parameters:{type:'object',properties:{query:{type:'string'},root:{type:'string'}},required:['query']}}},
    {type:'function',function:{name:'run_command',description:'Run shell command; returns exit_code, stdout, stderr (capped).',parameters:{type:'object',properties:{command:{type:'string'},cwd:{type:'string'},timeout_sec:{type:'integer'}},required:['command']}}},
    {type:'function',function:{name:'web_search',description:'Search the web',parameters:{type:'object',properties:{query:{type:'string'}},required:['query']}}},
    {type:'function',function:{name:'remember',description:'Store a fact in memory. Use slow for deadlines/plans (keeps until passed + 30d). Use permanent for preferences. Never use ephemeral for scheduled times — it expires in 5 minutes.',parameters:{type:'object',properties:{fact:{type:'string'},ttl_class:{type:'string',enum:['permanent','slow','ephemeral'],description:'Default slow. Use ephemeral only for throwaway session notes with no deadline.'}},required:['fact']}}},
    {type:'function',function:{name:'store_in_book',description:'Append a new passage to a book (always creates a new entry; same section name groups under one heading). Returns turn_id in the JSON response — save it for edit_book_passage. Does not overwrite existing passages unless replace:true.',parameters:{type:'object',properties:{book_name:{type:'string'},section:{type:'string',description:'Section heading — repeat to add more passages under the same heading'},passage:{type:'string'},title:{type:'string'},replace:{type:'boolean',description:'If true, update the passage last stored from this message instead of appending'}},required:['passage']}}},
    {type:'function',function:{name:'create_book',description:'Create named book',parameters:{type:'object',properties:{book_name:{type:'string'},display_title:{type:'string'}},required:['book_name']}}},
    {type:'function',function:{name:'list_books',description:'List books',parameters:{type:'object',properties:{},required:[]}}},
    {type:'function',function:{name:'read_book',description:'Read book passages',parameters:{type:'object',properties:{book_name:{type:'string'}},required:['book_name']}}},
    {type:'function',function:{name:'edit_book_passage',description:'Edit an existing book passage by turn_id (from store_in_book or read_book). Pass at least one of title, section, or passage to change.',parameters:{type:'object',properties:{turn_id:{type:'integer',description:'Passage id from store_in_book or read_book'},book_name:{type:'string'},position:{type:'string',description:'Alternative to turn_id: e.g. last or 1-based index after read_book'},title:{type:'string'},section:{type:'string'},passage:{type:'string'}},required:['turn_id']}}},
    {type:'function',function:{name:'delete_book_passage',description:'Delete book passage',parameters:{type:'object',properties:{turn_id:{type:'integer'},book_name:{type:'string'},position:{type:'string'}},required:[]}}},
    {type:'function',function:{name:'export_diagram',description:'Save SVG as PNG under the workspace export folder. Only when user explicitly asks to save — NOT for showing images in chat (use ```svg inline). Path optional; defaults to workspace/minima-exports/.',parameters:{type:'object',properties:{svg:{type:'string'},path:{type:'string',description:'Optional filename or path under workspace'},width:{type:'integer'}},required:['svg']}}},
    {type:'function',function:{name:'view_image',description:'Display a PNG/JPG from disk or a URL in the image viewport',parameters:{type:'object',properties:{path:{type:'string',description:'Local file path (Windows)'},url:{type:'string',description:'HTTP(S) image URL'},caption:{type:'string'}},required:[]}}}
  ];

  function cap(s, n){
    s = String(s || '');
    return s.length > n ? s.slice(0, n - 16) + '\n...(truncated)' : s;
  }

  function fileName(p){
    if(!p) return 'file';
    return p.split('\\').pop() || p.split('/').pop() || p;
  }

  function resolveExportPath(argsPath){
    var name = 'diagram-' + Date.now() + '.png';
    if(argsPath){
      var p = String(argsPath).trim().replace(/\//g, '\\');
      if(/\\Users\\default\\/i.test(p)){
        name = fileName(p) || name;
        p = '';
      } else if(/^[a-zA-Z]:\\/.test(p)){
        return p;
      } else {
        name = fileName(p) || p || name;
      }
    }
    if(global.getWorkspaceExportPath) return global.getWorkspaceExportPath(name);
    return 'D:\\minima\\minima-exports\\' + name;
  }

  async function execTool(name, args){
    args = args || {};
    var S = global.S;
    var fetchWithTimeout = global.fetchWithTimeout;

    if(name === 'list_files'){
      var lim = Math.min(Math.max(parseInt(args.limit, 10) || 100, 1), 100);
      var listPath = args.path || 'D:\\';
      var r = await fetch('/api/fs/list?path='+encodeURIComponent(listPath)+'&limit='+lim);
      if(!r.ok) return JSON.stringify({error:'HTTP '+r.status});
      var items = await r.json();
      if(!Array.isArray(items)) return JSON.stringify(items);
      var ws = (global.getWorkspaceRoot && global.getWorkspaceRoot()) || '';
      var ex = (global.getWorkspaceExportPath && global.getWorkspaceExportPath('')) || '';
      ex = ex.replace(/\\[^\\]+$/, '');
      var out = { path:listPath, count:items.length, items:items, workspace:ws, exports:ex };
      if(!items.length){
        out.note = ws
          ? 'Folder is empty. Project workspace: '+ws+' — save new files there (exports: '+ex+').'
          : 'Folder is empty. Open Files panel or call workspace_path to set a project folder.';
      } else if(ws && /^[A-Za-z]:\\?$/.test(String(listPath).replace(/\\+$/, ''))){
        out.note = 'Project workspace: '+ws+' (exports: '+ex+'). Prefer listing that folder for project files.';
      }
      return JSON.stringify(out);
    }
    if(name === 'read_file'){
      if(!args.path) return JSON.stringify({error:'path required'});
      if(/^https?:\/\//i.test(args.path)) return JSON.stringify({error:'read_file is for local paths only'});
      var offset = Math.max(1, parseInt(args.offset, 10) || 1);
      var limit = Math.min(Math.max(parseInt(args.limit, 10) || 100, 1), 160);
      var url = '/api/fs/read?path='+encodeURIComponent(args.path)+'&offset='+offset+'&limit='+limit;
      var r2 = await fetch(url);
      if(!r2.ok) return JSON.stringify({error:'HTTP '+r2.status, detail:cap(await r2.text(), 300)});
      var text = await r2.text();
      if(global.sinceStamp) await global.sinceStamp(args.path);
      var total = r2.headers.get('X-Total-Lines') || '0';
      var retFrom = r2.headers.get('X-Returned-From') || offset;
      var retTo = r2.headers.get('X-Returned-To') || '';
      var next = r2.headers.get('X-Next-Offset') || '';
      var hash = r2.headers.get('X-Content-Hash') || '';
      text = cap(text, 8192);
      var note = '\n\n[total_lines='+total+'; lines '+retFrom+'-'+retTo+'; next_offset='+next+'; hash='+hash+']';
      return text + note;
    }
    if(name === 'head_file'){
      if(!args.path) return JSON.stringify({error:'path required'});
      var nLines = Math.min(Math.max(parseInt(args.lines, 10) || 10, 1), 40);
      var hUrl = '/api/fs/head?path='+encodeURIComponent(args.path)+'&lines='+nLines;
      var rH = await fetch(hUrl);
      if(!rH.ok) return JSON.stringify({error:'HTTP '+rH.status, detail:cap(await rH.text(), 300)});
      var hText = cap(await rH.text(), 4096);
      var hTotal = rH.headers.get('X-Total-Lines') || '?';
      var hHash = rH.headers.get('X-Content-Hash') || '';
      var hMtime = rH.headers.get('X-Mtime-Utc') || '';
      return hText + '\n\n[head '+nLines+' of '+hTotal+'; hash='+hHash+'; mtime='+hMtime+']';
    }
    if(name === 'mtime'){
      if(!args.path) return JSON.stringify({error:'path required'});
      var rMt = await fetch('/api/fs/mtime?path='+encodeURIComponent(args.path));
      if(!rMt.ok) return cap(await rMt.text(), 500);
      return cap(await rMt.text(), 500);
    }
    if(name === 'append_file'){
      if(!args.path) return JSON.stringify({error:'path required'});
      if(/\.(ps1|bat|cmd|vbs|sh)$/i.test(args.path)) return JSON.stringify({error:'Refusing script file append'});
      var rAp = await fetch('/api/fs/append', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ path:args.path, content:args.content||'' })
      });
      return cap(await rAp.text(), 500);
    }
    if(name === 'find_in_workspace'){
      var pat = (args.pattern || args.query || '').trim();
      if(!pat) return JSON.stringify({error:'pattern required'});
      var root = (global.getWorkspaceRoot && global.getWorkspaceRoot()) || '';
      var depth = Math.min(parseInt(args.depth, 10) || 6, 12);
      var fUrl = '/api/fs/find?pattern='+encodeURIComponent(pat)+'&root='+encodeURIComponent(root)+'&depth='+depth;
      var rF = await fetch(fUrl);
      if(!rF.ok) return JSON.stringify({error:'HTTP '+rF.status});
      return cap(await rF.text(), 3000);
    }
    if(name === 'workspace_path'){
      var ws = (global.getWorkspaceRoot && global.getWorkspaceRoot()) || '';
      var ex = (global.getWorkspaceExportPath && global.getWorkspaceExportPath('')) || '';
      ex = ex.replace(/\\[^\\]+$/,'');
      return JSON.stringify({ workspace: ws, exports: ex });
    }
    if(name === 'grep_file'){
      if(!args.path || !args.pattern) return JSON.stringify({error:'path and pattern required'});
      var glim = Math.min(parseInt(args.limit, 10) || 20, 20);
      var gUrl = '/api/fs/grep?path='+encodeURIComponent(args.path)+'&pattern='+encodeURIComponent(args.pattern)+'&limit='+glim;
      var rG = await fetch(gUrl);
      if(!rG.ok) return JSON.stringify({error:'HTTP '+rG.status});
      return cap(await rG.text(), 4096);
    }
    if(name === 'write_file'){
      if(!args.path) return JSON.stringify({error:'path required'});
      if(/\.(ps1|bat|cmd|vbs|sh)$/i.test(args.path)) return JSON.stringify({error:'Refusing script file write'});
      var body = { path:args.path, content:args.content||'', overwrite:!!args.overwrite };
      var rW = await fetch('/api/fs/write', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
      var wText = await rW.text();
      if(!rW.ok) return cap(wText, 500);
      return cap(wText, 500);
    }
    if(name === 'edit_file'){
      if(!args.path || !args.edits || !args.edits.length) return JSON.stringify({error:'path and edits required'});
      var rEd = await fetch('/api/fs/edit', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ path:args.path, edits:args.edits, expected_hash:args.expected_hash||null }) });
      return cap(await rEd.text(), 800);
    }
    if(name === 'create_folder'){
      var rMk = await fetch('/api/fs/mkdir', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ path:args.path }) });
      return cap(await rMk.text(), 500);
    }
    if(name === 'move_file'){
      var rMv = await fetch('/api/fs/move', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ source:args.source, dest:args.dest }) });
      return cap(await rMv.text(), 500);
    }
    if(name === 'copy_file'){
      var rCp = await fetch('/api/fs/copy', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ source:args.source, dest:args.dest }) });
      return cap(await rCp.text(), 500);
    }
    if(name === 'delete_file'){
      var rDel = await fetch('/api/fs/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ path:args.path }) });
      return cap(await rDel.text(), 500);
    }
    if(name === 'search_files'){
      var r3 = await fetch('/api/fs/search?query='+encodeURIComponent(args.query)+'&root='+encodeURIComponent(args.root||'D:\\'));
      return cap(await r3.text(), 4000);
    }
    if(name === 'run_command'){
      var cmd = (args.command || '').trim();
      if(!cmd) return JSON.stringify({error:'command required'});
      var toutSec = Math.min(Math.max(parseInt(args.timeout_sec, 10) || 60, 5), 180);
      try{
        var rRun = await fetchWithTimeout('/api/run', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({ command:cmd, cwd:args.cwd||null, timeout_sec:toutSec }),
          signal: S && S.abortController && S.abortController.signal
        }, toutSec * 1000 + 10000);
        return cap(await rRun.text(), 8192);
      }catch(e){
        return JSON.stringify({ ok:false, exit_code:-1, stderr: e.message || String(e), aborted: !!(S && S.aborted) });
      }
    }
    if(name === 'web_search'){
      var r4 = await fetch('/api/web/search?q='+encodeURIComponent(args.query));
      return cap(await r4.text(), 4096);
    }
    if(name === 'remember'){
      var fact = (args.fact || '').trim();
      if(!fact) return JSON.stringify({error:'fact required'});
      var rM = await fetch('/api/since/memory', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          content:fact, session_id:S.chatId,
          source_msg_id: global.lastUserMsgId ? global.lastUserMsgId() : null,
          source_excerpt: global.lastUserMsgText ? global.lastUserMsgText() : '',
          timezone: global.clientTimezone ? global.clientTimezone() : 'UTC',
          now_ms:Date.now(), ttl_class:args.ttl_class||'permanent'
        })
      });
      var pinText = await rM.text();
      if(global.fetchMemory) global.fetchMemory();
      return cap(pinText, 2000);
    }
    if(name === 'store_in_book' || name === 'create_book' || name === 'list_books' || name === 'read_book' || name === 'edit_book_passage' || name === 'delete_book_passage'){
      return JSON.stringify({error:'book tool requires index.html bridge'});
    }
    if(name === 'export_diagram'){
      var svgIn = (args.svg || '').trim();
      if(!svgIn) return JSON.stringify({error:'svg required'});
      var outPath = resolveExportPath(args.path || '');
      var bg = document.documentElement.getAttribute('data-theme') === 'night' ? '#0a0d16' : '#faf8f4';
      var rDx = await fetch('/api/diagram/export', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ svg:svgIn, path:outPath, width:args.width||1200, background:bg, workspace_root:(global.getWorkspaceRoot && global.getWorkspaceRoot()) || null })
      });
      if(!rDx.ok){
        var errDx = '';
        try{ errDx = await rDx.text(); }catch(e){}
        return JSON.stringify({error:'HTTP '+rDx.status, detail:cap(errDx, 500)});
      }
      var data = await rDx.json();
      if(global.onExportDiagram) global.onExportDiagram(data);
      var summary = { ok:!!data.ok, path:data.path || outPath, bytes:data.bytes || 0 };
      if(data.save_error) summary.save_error = data.save_error;
      if(data.save_fallback) summary.save_fallback = true;
      if(data.png_b64) summary.has_preview = true;
      return JSON.stringify(summary, null, 2);
    }
    if(name === 'view_image'){
      var imgPath = (args.path || '').trim();
      var imgUrl = (args.url || '').trim();
      if(!imgPath && !imgUrl) return JSON.stringify({error:'path or url required'});
      var caption = (args.caption || '').trim();
      var displaySrc = imgUrl;
      if(!displaySrc && imgPath){
        displaySrc = '/api/fs/image?path=' + encodeURIComponent(imgPath.replace(/\//g, '\\'));
      }
      if(global.openImageViewport) global.openImageViewport(displaySrc, caption || fileName(imgPath) || 'image');
      return JSON.stringify({ ok:true, displayed: displaySrc, path: imgPath || null, url: imgUrl || null });
    }
    return JSON.stringify({error:'unknown tool: '+name});
  }

  global.Minima = global.Minima || {};
  global.Minima.tools = { TOOLS: TOOLS, execTool: execTool, cap: cap };
})(typeof window !== 'undefined' ? window : globalThis);
