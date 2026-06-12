// WFlow — Workflow Orchestrator
window.appData = function () {

  var TYPE_ICONS = { claude: '\u{1F916}', opencode: '\u{1F916}', script: '⚙', human_review: '\u{1F464}' };
  var TYPE_LABELS = { claude: 'Claude', opencode: 'OpenCode', script: 'Script', human_review: 'Human Review' };

  // ── DAG builder (dagre layout + loop-back arc routing + zoom) ───────────
  var _dagIdCounter = 0;
  function buildDAG(specNodes, specEdges, nodeStatuses) {
    if (!specNodes || specNodes.length === 0) return '';

    _dagIdCounter++;
    var dagId = 'dag-' + _dagIdCounter;

    var nodeMap = {};
    specNodes.forEach(function (n) { nodeMap[n.id] = n; });

    var g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 50, marginx: 30, marginy: 30 });
    g.setDefaultEdgeLabel(function () { return {}; });

    specNodes.forEach(function (n) {
      var labelLen = Math.max(n.id.length, (n.type || '?').length + 12);
      g.setNode(n.id, { width: labelLen * 7.6 + 32, height: 54 });
    });

    specEdges.forEach(function (e) {
      if (e.from && e.to && nodeMap[e.from] && nodeMap[e.to]) {
        g.setEdge(e.from, e.to, { condition: e.condition || '' });
      }
    });

    dagre.layout(g);

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    g.nodes().forEach(function (v) {
      var nd = g.node(v);
      if (nd.x - nd.width/2 < minX) minX = nd.x - nd.width/2;
      if (nd.y - nd.height/2 < minY) minY = nd.y - nd.height/2;
      if (nd.x + nd.width/2 > maxX) maxX = nd.x + nd.width/2;
      if (nd.y + nd.height/2 > maxY) maxY = nd.y + nd.height/2;
    });

    var hasLoopback = false;
    g.edges().forEach(function (e) { if (g.node(e.v).x >= g.node(e.w).x) hasLoopback = true; });

    var pad = 30, arcSpace = hasLoopback ? 55 : 0;
    var svgW = (maxX - minX) + pad * 2 + 16;
    var svgH = (maxY - minY) + pad * 2 + arcSpace + 16;
    if (svgW < 220) svgW = 220; if (svgH < 120) svgH = 120;
    var ox = -minX + pad, oy = -minY + pad;

    var edgeColors = { completed: '#059669', running: '#d97706', failed: '#dc2626', awaiting_review: '#7c3aed', pending: '#c4c9d4' };
    var edgeSVG = '', arrowId = 0;

    g.edges().forEach(function (e) {
      var edge = g.edge(e), fn = g.node(e.v), tn = g.node(e.w), pts = edge.points || [];
      var srcSt = nodeStatuses[e.v] || 'pending';

      // Edge color: for conditional edges, check whether the target was
      // actually reached (target completed = condition was true → taken).
      // Otherwise use the source node's status.
      var ec;
      if (edge.condition) {
        var tgtSt = nodeStatuses[e.w] || 'pending';
        ec = (srcSt === 'completed' && tgtSt === 'completed') ? edgeColors.completed : edgeColors.pending;
      } else {
        ec = edgeColors[srcSt] || edgeColors.pending;
      }

      var sx = fn.x + ox, sy = fn.y + oy, tx = tn.x + ox, ty = tn.y + oy;
      var fw2 = fn.width/2, fh2 = fn.height/2, tw2 = tn.width/2, th2 = tn.height/2;
      var isLoopback = (fn.x >= tn.x);
      arrowId++;
      var mid = dagId + '-arr-' + arrowId;
      edgeSVG += '<defs><marker id="' + mid + '" markerWidth="6" markerHeight="5" refX="6" refY="2.5" orient="auto"><path d="M0,0 L6,2.5 L0,5 Z" fill="' + ec + '"/></marker></defs>';

      var pathD;
      if (isLoopback) {
        var sxb = sx, syb = sy + fh2, txb = tx, tyb = ty + th2, my = Math.max(syb, tyb) + arcSpace;
        pathD = 'M' + sxb + ',' + syb + ' C' + sxb + ',' + my + ' ' + txb + ',' + my + ' ' + txb + ',' + tyb;
      } else {
        pathD = 'M' + (sx + fw2) + ',' + sy;
        for (var pi = 0; pi < pts.length; pi++) pathD += ' L' + (pts[pi].x + ox) + ',' + (pts[pi].y + oy);
        pathD += ' L' + (tx - tw2) + ',' + ty;
      }

      edgeSVG += '<path d="' + pathD + '" stroke="' + ec + '" stroke-width="1.6" fill="none"';
      if (isLoopback) edgeSVG += ' stroke-dasharray="6,3.5" opacity="0.85"';
      edgeSVG += ' marker-end="url(#' + mid + ')"/>';

      if (edge.condition) {
        var rawCond = edge.condition;
        var lbl = rawCond.replace(/\{\{.*?\.(.*?)\}\}/g, '$1');
        if (lbl.length > 50) lbl = lbl.slice(0, 47) + '…';
        var lx, ly;
        if (isLoopback) { lx = (txb+sxb)/2; ly = my - 12; }
        else if (pts.length > 0) { lx = pts[0].x+ox; ly = pts[0].y+oy-12; }
        else { lx = (sx+tx)/2; ly = (sy+ty)/2-12; }
        var lw = lbl.length*7.2+14;
        edgeSVG += '<rect x="'+(lx-lw/2)+'" y="'+(ly-10)+'" width="'+lw+'" height="20" rx="5" fill="'+ec+'30" stroke="'+ec+'" stroke-width="0.8"/>';
        edgeSVG += '<text x="'+lx+'" y="'+(ly+4)+'" text-anchor="middle" font-size="10" font-weight="600" fill="'+ec+'" font-family="monospace"><title>'+esc(rawCond)+'</title>'+esc(lbl)+'</text>';
      }
    });

    var nodeSVG = '';
    var ncolors = {
      completed: { stroke: '#059669', fill: '#ecfdf5' },
      running: { stroke: '#d97706', fill: '#fffbeb' },
      failed: { stroke: '#dc2626', fill: '#fef2f2' },
      awaiting_review: { stroke: '#7c3aed', fill: '#f5f3ff' },
      pending: { stroke: '#c4c9d4', fill: '#f9fafb' }
    };

    g.nodes().forEach(function (v) {
      var nd = g.node(v), sn = nodeMap[v] || {};
      var st = nodeStatuses[v] || 'pending', c = ncolors[st] || ncolors.pending;
      var icon = TYPE_ICONS[sn.type] || '◆';
      var rx = nd.x+ox-nd.width/2, ry = nd.y+oy-nd.height/2, rw = nd.width, rh = nd.height;

      nodeSVG += '<rect x="'+(rx+2)+'" y="'+(ry+3)+'" width="'+rw+'" height="'+rh+'" rx="8" fill="rgba(0,0,0,0.05)"/>';
      nodeSVG += '<rect x="'+rx+'" y="'+ry+'" width="'+rw+'" height="'+rh+'" rx="8" fill="'+c.fill+'" stroke="'+c.stroke+'" stroke-width="2"/>';
      if (st === 'running') nodeSVG += '<rect x="'+rx+'" y="'+ry+'" width="'+rw+'" height="'+rh+'" rx="8" fill="none" stroke="'+c.stroke+'" stroke-width="2.5" opacity="0.5"><animate attributeName="opacity" values="0.6;0.05;0.6" dur="2.2s" repeatCount="indefinite"/></rect>';
      if (st === 'awaiting_review') nodeSVG += '<rect x="'+rx+'" y="'+ry+'" width="'+rw+'" height="'+rh+'" rx="8" fill="none" stroke="'+c.stroke+'" stroke-width="2.5" opacity="0.5"><animate attributeName="opacity" values="0.6;0.05;0.6" dur="3s" repeatCount="indefinite"/></rect>';

      var idLabel = v.length > 18 ? v.slice(0,16)+'…' : v;
      var typeLabel = (sn.type||'?');
      nodeSVG += '<title>'+esc(v)+' ('+esc(typeLabel)+')</title>';
      nodeSVG += '<text x="'+(rx+rw/2)+'" y="'+(ry+23)+'" text-anchor="middle" font-size="11" font-weight="700" fill="#111827" font-family="monospace">'+icon+' '+esc(idLabel)+'</text>';
      nodeSVG += '<text x="'+(rx+rw/2)+'" y="'+(ry+41)+'" text-anchor="middle" font-size="9" fill="#6b7280" font-family="monospace">'+esc(typeLabel)+' · '+st+'</text>';
    });

    // ── Zoom controls + scrollable wrapper ──
    var svgInner = '<svg id="'+dagId+'" viewBox="0 0 '+svgW+' '+svgH+'" style="width:100%;height:auto;min-height:'+svgH+'px;cursor:grab"><rect width="'+svgW+'" height="'+svgH+'" fill="#fff" rx="10"/>'+edgeSVG+nodeSVG+'</svg>';

    return '<div class="dag-zoom-wrap">'+
      '<div class="dag-zoom-bar">'+
        '<button class="btn sm ghost dag-zoom-btn" onclick="window._dagZoom(\''+dagId+'\', 0.15)" title="Zoom in">'+
          '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M5 1v4H1v2h4v4h2V7h4V5H7V1H5z" fill="currentColor"/></svg>'+
        '</button>'+
        '<button class="btn sm ghost dag-zoom-btn" onclick="window._dagZoom(\''+dagId+'\', -0.15)" title="Zoom out">'+
          '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M1 5v2h10V5H1z" fill="currentColor"/></svg>'+
        '</button>'+
        '<button class="btn sm ghost dag-zoom-btn" onclick="window._dagReset(\''+dagId+'\')" title="Reset">'+
          '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 2v3h3V4H3.5A4.5 4.5 0 111 6.5H0A6 6 0 103.5 2.5L2 2z" fill="currentColor"/></svg>'+
        '</button>'+
      '</div>'+
      '<div class="dag-pan-area" id="'+dagId+'-pan" style="overflow:hidden;cursor:grab;border:1px solid var(--border-subtle);border-radius:8px;background:#fafbfc">'+
        '<div id="'+dagId+'-inner" style="transform-origin:0 0">'+svgInner+'</div>'+
      '</div>'+
    '</div>';
  }

  // ── Helpers ────────────────────────────────────────────────────────────
  function truncate(text, maxLen) {
    if (!text) return ''; maxLen = maxLen || 100;
    return text.length <= maxLen ? text : text.slice(0, maxLen) + '…';
  }
  function safeJSON(val) {
    if (!val) return '';
    try { return JSON.stringify(typeof val === 'string' ? JSON.parse(val) : val); }
    catch (e) { return String(val); }
  }
  function esc(s) { return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function getFileIcon(name) {
    var ext = (name.split('.').pop()||'').toLowerCase();
    var map = {
      py:'py', js:'js', ts:'ts', jsx:'react', tsx:'react',
      json:'json', yaml:'yaml', yml:'yaml', toml:'config', ini:'config', cfg:'config',
      html:'html', css:'css', scss:'css', less:'css',
      md:'md', txt:'txt', log:'txt', csv:'csv',
      sh:'shell', bat:'shell', ps1:'shell',
      png:'img', jpg:'img', jpeg:'img', gif:'img', svg:'img', ico:'img',
      zip:'zip', tar:'zip', gz:'zip',
      sql:'db', db:'db', sqlite:'db',
      dockerfile:'docker', gitignore:'git', gitattributes:'git',
      lock:'lock', exe:'binary', dll:'binary', so:'binary',
    };
    return 'fi-'+ (map[ext] || 'default');
  }

  // ══════════════════════════════════════════════════════════════════════════
  return {
    page: 'dashboard',
    workflows: [], workflowDetails: {},
    runs: [], cronJobs: [],
    dashboardHTML: '', runsHTML: '',
    showCreateWorkflow: false, showCreateCron: false, viewRunId: null,
    runFormWfId: null, runFormWfName: '', runFormInputs: {}, runFormValues: {},
    // File browser
    fileTreeHTML: '', fileContentHTML: '', fileLoading: false,
    // DAG modal
    dagWorkflowId: null, dagWorkflowHTML: '',
    // Cron form
    cronFormInputs: {}, cronFormValues: {},
    // Review modal
    showReviewModal: false, reviewRunId: '', reviewNodeId: '',
    reviewNodeLabel: '', reviewPrompt: '', reviewUpstreamJSON: '',
    reviewFeedback: '', reviewDecision: null, reviewSubmitting: false,

    async init() {
      console.log('[WFlow] init called, page:', this.page);
      await this.loadDashboard();
      console.log('[WFlow] dashboardHTML set, length:', (this.dashboardHTML||'').length);
      var s = this;
      this.$watch('page', async function(v) {
        if (v==='workflows') await s.loadWorkflows();
        if (v==='runs') await s.loadRuns();
        if (v==='cron') { await s.loadWorkflows(); await s.loadCron(); }
        if (v==='dashboard') await s.loadDashboard();
      });
    },

    async loadDashboard() {
      try {
        var st = await apiGet('/status');
        this.dashboardHTML = '<div class="page-header"><h2>Dashboard</h2></div>'+
          '<div class="stats-grid">'+
            '<div class="stat-card"><div class="stat-label">Running</div><div class="stat-value'+(st.running_workflows>0?' amber':'')+'">'+st.running_workflows+'</div><div class="stat-sub">Active executions</div></div>'+
            '<div class="stat-card"><div class="stat-label">⏳ Awaiting Review</div><div class="stat-value" style="color:var(--purple)">'+st.awaiting_review+'</div><div class="stat-sub">Human review needed</div></div>'+
            '<div class="stat-card"><div class="stat-label">Completed</div><div class="stat-value green">'+st.completed_workflows+'</div><div class="stat-sub">Successful runs</div></div>'+
            '<div class="stat-card"><div class="stat-label">Failed</div><div class="stat-value'+(st.failed_workflows>0?' red':'')+'" style="'+(st.failed_workflows>0?'color:var(--red)':'')+'">'+st.failed_workflows+'</div><div class="stat-sub">Failed runs</div></div>'+
          '</div>';
      } catch(e) { console.error('loadDashboard error:', e); }
    },

    async loadWorkflows() {
      try {
        this.workflows = await apiGet('/workflows');
        for (var i=0;i<this.workflows.length;i++) {
          var w=this.workflows[i];
          if (!this.workflowDetails[w.id]) {
            try { var d=await apiGet('/workflows/'+w.id); this.workflowDetails[w.id]=d.config||{}; }
            catch(e) { this.workflowDetails[w.id]={}; }
          }
        }
      } catch(e) { console.error('loadWorkflows error:', e); }
    },

    getWorkflowInputs(id) { return (this.workflowDetails[id]||{}).inputs||{}; },
    getWorkflowNodeCount(id) { return ((this.workflowDetails[id]||{}).nodes||[]).length; },
    getWorkflowEdgeCount(id) { return ((this.workflowDetails[id]||{}).edges||[]).length; },

    openRunForm(wfId) {
      for (var i=0;i<this.workflows.length;i++) { if (this.workflows[i].id===wfId) { var w=this.workflows[i]; break; } }
      if (!w) return;
      this.runFormWfId=wfId; this.runFormWfName=w.name;
      this.runFormInputs=this.getWorkflowInputs(wfId); this.runFormValues={};
      Object.keys(this.runFormInputs).forEach(function(k){ this.runFormValues[k]=this.runFormInputs[k].default||''; }, this);
    },
    closeRunForm() { this.runFormWfId=null; this.runFormValues={}; },
    viewWorkflowDAG(wfId) {
      var cfg = this.workflowDetails[wfId] || {};
      var nodes = cfg.nodes || [], edges = cfg.edges || [];
      if (nodes.length === 0) { alert('No nodes in this workflow.'); return; }
      var statuses = {}; nodes.forEach(function(n){ statuses[n.id]='pending'; });
      var svg = buildDAG(nodes, edges, statuses);
      var wfName = '';
      for (var i=0;i<this.workflows.length;i++) { if (this.workflows[i].id===wfId) { wfName=this.workflows[i].name; break; } }
      this.dagWorkflowHTML = '<div class="dag-modal-header">'+
        '<h3>'+esc(wfName||'Workflow')+' <span class="muted">DAG</span></h3>'+
        '<span class="text-sm">'+nodes.length+' nodes, '+edges.length+' edges</span>'+
        '</div>'+
        '<div class="dag-wrap modal-dag-wrap">'+svg+'</div>';
      this.dagWorkflowId = wfId;
    },
    closeWorkflowDAG() { this.dagWorkflowId = null; this.dagWorkflowHTML = ''; },
    async submitRunForm() {
      var inputs={};
      for (var k in this.runFormValues) { var v=this.runFormValues[k]; if (v) inputs[k]=v; }
      try { await apiPost('/runs',{workflow_id:this.runFormWfId,inputs:inputs}); this.closeRunForm(); await this.loadRuns(); }
      catch(e) { alert('Error: '+e.message); }
    },

    // ── File Browser ──────────────────────────────────────────────────────
    async browseFiles(runId) {
      if (!runId) return;
      this.fileLoading = true;
      this.fileTreeHTML = '<div class="file-tree-loading">Loading...</div>';
      try {
        var data = await apiGet('/runs/'+runId+'/files');
        this.buildFileTree(data.entries, data.work_dir, runId, '');
      } catch(e) {
        this.fileTreeHTML = '<div class="file-tree-empty">Failed to load files</div>';
      }
      this.fileLoading = false;
    },

    buildFileTree(entries, workDir, runId, currentPath) {
      var h = '';
      var parts = (currentPath||'').split('/').filter(Boolean);
      var depth = parts.length;

      if (entries.length === 0 && depth === 0) {
        this.fileTreeHTML = '<div class="file-tree-empty">No files yet</div>';
        return;
      }

      // Parent directory link when inside a subdirectory
      if (depth > 0) {
        var parentPath = parts.slice(0, -1).join('/');
        h += '<div class="tree-row folder" onclick="getWFlowApp().loadFilePath(\''+runId+'\',\''+(parentPath||'')+'\')">'+
          '<span class="tree-arrow">\u{2190}</span>'+
          '<span class="tree-icon folder-icon" style="opacity:0.6"></span>'+
          '<span class="tree-label" style="font-style:italic">..</span>'+
          '</div>';
      }

      for (var i=0;i<entries.length;i++) {
        var e = entries[i];
        var guide = '';
        for (var d=0;d<depth;d++) guide += '<span class="tree-guide"></span>';

        if (e.is_dir) {
          h += '<div class="tree-row folder" onclick="getWFlowApp().loadFilePath(\''+runId+'\',\''+e.path+'\')">'+
            guide +
            '<span class="tree-arrow">▶</span>'+
            '<span class="tree-icon folder-icon"></span>'+
            '<span class="tree-label">'+esc(e.name)+'</span>'+
            '</div>';
        } else {
          var iconCls = getFileIcon(e.name);
          h += '<div class="tree-row file" onclick="getWFlowApp().viewFile(\''+runId+'\',\''+e.path+'\')" '+
            'title="'+esc(e.name)+' — '+formatSize(e.size)+'">'+
            guide +
            '<span class="tree-icon '+iconCls+'"></span>'+
            '<span class="tree-label">'+esc(e.name)+'</span>'+
            '</div>';
        }
      }

      this.fileTreeHTML = h;
    },

    async loadFilePath(runId, path) {
      try {
        var data = await apiGet('/runs/'+runId+'/files',{path:path});
        this.buildFileTree(data.entries, data.work_dir, runId, path);
      } catch(e) {
        this.fileTreeHTML = '<div class="file-tree-empty">Error loading</div>';
      }
    },

    async viewFile(runId, path) {
      try {
        var data = await apiGet('/runs/'+runId+'/files/content',{path:path});
        var lines = data.content.split('\n');
        var numbered = '';
        for (var i=0;i<lines.length;i++) {
          numbered += '<div class="code-line"><span class="line-num">'+(i+1)+'</span><span class="line-text">'+esc(lines[i]||' ')+'</span></div>';
        }
        this.fileContentHTML =
          '<div class="editor-tab">'+
            '<span class="editor-tab-name">'+esc(path.split('/').pop())+'</span>'+
            '<span class="editor-tab-info">'+formatSize(data.size)+' | '+(lines.length)+' lines</span>'+
            '<button class="editor-tab-close" onclick="getWFlowApp().fileContentHTML=\'\'">✕</button>'+
          '</div>'+
          '<div class="editor-body">'+numbered+'</div>';
      } catch(e) {
        this.fileContentHTML = '<div class="editor-body muted">Error reading file</div>';
      }
    },

    initResizeHandle(runId) {
      var handle = document.getElementById('resize-'+runId.slice(0,8));
      var explorer = document.getElementById('explorer-'+runId.slice(0,8));
      if (!handle || !explorer) return;
      var app = this;
      var startX, startW;
      function onDown(e) {
        startX = e.clientX;
        startW = explorer.offsetWidth;
        handle.classList.add('active');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      }
      function onMove(e) {
        var dx = e.clientX - startX;
        var newW = Math.max(120, Math.min(600, startW + dx));
        explorer.style.width = newW + 'px';
        app._explorerWidth = newW;
      }
      function onUp() {
        handle.classList.remove('active');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      handle.addEventListener('mousedown', onDown);
    },

    // ── Human Review ───────────────────────────────────────────────────────
    openReviewModal(runId, nodeId) {
      // Find the node execution and spec node to build review context
      var runData = null;
      // Access via the rendered HTML's data — re-fetch run detail for fresh data
      var self = this;
      // _=Date.now() prevents browser caching of the run detail
      apiGet('/runs/' + runId, {_: Date.now()}).then(function(run) {
        // Find the LATEST execution for this node (DB returns sorted by started_at ASC)
        var matching = (run.nodes||[]).filter(function(n){return n.node_id === nodeId;});
        if (matching.length === 0) return;
        var ne = matching[matching.length - 1]; // last = newest
        if (!ne) return;

        // Parse upstream output from node input
        var upstreamJSON = '';
        try {
          var inputObj = typeof ne.input === 'string' ? JSON.parse(ne.input) : ne.input;
          upstreamJSON = JSON.stringify(inputObj, null, 2);
        } catch(e) { upstreamJSON = ne.input || '{}'; }

        // Find review node prompt from spec
        var specNode = (run.spec.nodes||[]).find(function(n){return n.id === nodeId;});
        var prompt = specNode ? (specNode.prompt || '') : '';

        self.reviewRunId = runId;
        self.reviewNodeId = nodeId;
        self.reviewNodeLabel = nodeId;
        self.reviewPrompt = prompt;
        self.reviewUpstreamJSON = upstreamJSON;
        self.reviewFeedback = '';
        self.reviewDecision = null;
        self.reviewSubmitting = false;
        self.showReviewModal = true;
      }).catch(function(e) {
        alert('Failed to load review data: ' + e.message);
      });
    },

    closeReviewModal() {
      this.showReviewModal = false;
      this.reviewRunId = '';
      this.reviewNodeId = '';
      this.reviewFeedback = '';
      this.reviewDecision = null;
    },

    selectReviewDecision(decision) {
      this.reviewDecision = decision;
      if (decision === 'approved') this.reviewFeedback = '';
    },

    async submitReviewDecision() {
      if (this.reviewDecision === 'rejected' && !this.reviewFeedback.trim()) return;
      this.reviewSubmitting = true;
      var self = this;
      try {
        await apiPost(
          '/runs/' + this.reviewRunId + '/nodes/' + this.reviewNodeId + '/review',
          { approved: this.reviewDecision === 'approved', feedback: this.reviewFeedback }
        );
        this.closeReviewModal();
        setTimeout(function() { self.viewRunId = self.reviewRunId; self.loadRuns(); }, 1500);
      } catch(e) {
        alert('Review submission error: ' + e.message);
      } finally {
        this.reviewSubmitting = false;
      }
    },

    // ── Runs page ────────────────────────────────────────────────────────
    async loadRuns() {
      try {
        this.runs = await apiGet('/runs');
        var html = '';

        if (this.viewRunId) {
          try {
            var run = await apiGet('/runs/' + this.viewRunId);

            html += '<div class="run-detail-top">'+
              '<button class="btn primary" @click="viewRunId=null;loadRuns()">\u{2190} Back to Runs</button>'+
              '<code>'+run.id+'</code>'+
              '<span class="badge '+run.status+'">'+run.status+'</span>'+
              (run.status==='running'||run.status==='awaiting_review' ? '<button class="btn sm" onclick="location.reload()">Refresh</button>' : '')+
              '</div>';

            if (run.work_dir) {
              html += '<div class="workdir-info"><span>\u{1F4C1} Work Dir</span> <code>'+run.work_dir+'</code></div>';
            }

            // VS Code-style file browser (collapsible)
            this.browseFiles(run.id);
            html += '<div class="section-title collapsible" onclick="var b=this.nextElementSibling;b.style.display=b.style.display==\'none\'?\'flex\':\'none\';this.querySelector(\'.toggle-arrow\').textContent=b.style.display==\'none\'?\'▶\':\'▼\'">'+
              '<span class="toggle-arrow">▶</span> Files'+
              '<button class="btn ghost sm" style="margin-left:auto;padding:3px 10px;font-size:0.68rem;color:#4b5563" onclick="event.stopPropagation();getWFlowApp().browseFiles(\''+run.id+'\')">🔄 Refresh</button>'+
              '</div>';
            html += '<div class="run-files-wrap" id="fb-'+run.id.slice(0,8)+'" style="display:none">'+
              '<div class="explorer" id="explorer-'+run.id.slice(0,8)+'" style="width:'+(this._explorerWidth||260)+'px">'+
                '<div class="explorer-body">'+
                  '<div class="explorer-tree" x-html="fileTreeHTML"></div>'+
                '</div>'+
              '</div>'+
              '<div class="resize-handle" id="resize-'+run.id.slice(0,8)+'"></div>'+
              '<div class="editor-pane">'+
                '<div x-show="!fileContentHTML" class="editor-empty">Select a file to view</div>'+
                '<div class="editor-inner" x-show="fileContentHTML" x-html="fileContentHTML"></div>'+
              '</div>'+
            '</div>';
            var self = this;
            setTimeout(function() { self.initResizeHandle(run.id); }, 50);

            // DAG
            var spec = run.spec || {};
            if (spec.nodes && spec.nodes.length) {
              var sts = {};
              (run.nodes||[]).forEach(function(n){sts[n.node_id]=n.status;});
              try { html += '<div class="section-title collapsible" onclick="var b=this.nextElementSibling;b.style.display=b.style.display==\'none\'?\'block\':\'none\';this.querySelector(\'.toggle-arrow\').textContent=b.style.display==\'none\'?\'▶\':\'▼\'">'+
                '<span class="toggle-arrow">▶</span> Workflow Graph</div><div style="display:none">'+buildDAG(spec.nodes,spec.edges||[],sts)+'</div>'; }
              catch(e) { html += '<p class="muted">Graph error</p>'; }
            }

            // Node Executions (collapsible)
            if (run.nodes && run.nodes.length) {
              html += '<div class="section-title">Node Executions</div><div class="node-list">';
              for (var ni=0;ni<run.nodes.length;ni++) {
                var n = run.nodes[ni];
                var cls = n.status||'pending';
                var icon = TYPE_ICONS[n.type]||'◆';
                var sid = n.session_id||'';

                html += '<div class="node-item status-'+cls+'">'+
                  '<div class="node-item-header" onclick="var b=this.nextElementSibling;if(b.classList.contains(\'review-bar\'))b=b.nextElementSibling;b.style.display=b.style.display===\'none\'?\'block\':\'none\';this.querySelector(\'.expand-arrow\').textContent=b.style.display===\'none\'?\'▶\':\'▼\'">'+
                    '<span class="expand-arrow">▶</span>'+
                    '<div class="node-item-icon">'+icon+'</div>'+
                    '<span class="node-item-id">'+n.node_id+'</span>'+
                    '<span class="badge '+cls+'">'+cls+'</span>'+
                    '<div class="node-item-meta">'+
                      '<span>'+(TYPE_LABELS[n.type]||n.type||'?')+'</span>'+
                      (sid?'<span>sid:'+sid+'</span>':'')+
                      (n.retry_count>0?'<span>retry:'+n.retry_count+'</span>':'')+
                    '</div>'+
                  '</div>';

                // Human review button — placed OUTSIDE the collapsible body so it's always visible
                if (n.status === 'awaiting_review' && n.type === 'human_review') {
                  html += '<div class="review-bar">'+
                    '<div class="review-bar-left">'+
                      '<span class="review-bar-dot"></span>'+
                      '<span class="review-bar-label">Awaiting human review</span>'+
                    '</div>'+
                    '<button class="btn primary sm" onclick="getWFlowApp().openReviewModal(\''+run.id+'\',\''+n.node_id+'\')">Review Now →</button>'+
                    '</div>';
                }

                html += '<div class="node-item-body" style="display:none">';

                // Input
                if (n.input && n.input !== '{}') {
                  html += '<div class="io-block"><div class="io-block-header"><span class="io-tag in">IN</span></div>'+
                    '<pre class="io-full">'+esc(safeJSON(n.input))+'</pre></div>';
                }
                // Output
                if (n.output) {
                  html += '<div class="io-block"><div class="io-block-header"><span class="io-tag out">OUT</span></div>'+
                    '<pre class="io-full">'+esc(safeJSON(n.output))+'</pre></div>';
                }
                // Error
                if (n.error) {
                  html += '<div class="io-block" style="border-color:var(--red-border)"><div class="io-block-header"><span class="io-tag err">ERR</span></div>'+
                    '<pre class="io-full" style="color:var(--red)">'+esc(n.error)+'</pre></div>';
                }

                html += '</div></div>';
              }
              html += '</div>';
            }

            // Logs (newest first, textarea)
            try {
              var logs = await apiGet('/runs/'+this.viewRunId+'/logs?limit=50');
              if (logs&&logs.length) {
                html += '<div class="section-title collapsible" onclick="var b=this.nextElementSibling;b.style.display=b.style.display==\'none\'?\'block\':\'none\';this.querySelector(\'.toggle-arrow\').textContent=b.style.display==\'none\'?\'▶\':\'▼\'">'+
                  '<span class="toggle-arrow">▶</span> Recent Logs <span class="muted" style="font-size:0.6rem">('+logs.length+' entries, newest first)</span></div>';
                var logText = '';
                for (var li=logs.length-1;li>=0;li--) {
                  var l=logs[li];
                  var lv = l.level||'info';
                  var timeStr = (l.timestamp||'').slice(0,19) || '';
                  logText += '['+timeStr+'] ['+lv.toUpperCase()+'] '+l.message+'\n';
                }
                html += '<div style="display:none"><div class="logs-panel"><textarea class="logs-textarea" readonly spellcheck="false" x-ref="logstext">'+esc(logText)+'</textarea></div></div>';
              }
            } catch(e) { console.error('loadLogs error:', e); }

          } catch(e) { html += '<div class="empty-state mt"><p>Error loading run</p></div>'; }
        } else {
          html += '<div class="page-header"><h2>Runs</h2></div>';
          if (!this.runs.length) {
            html += '<div class="empty-state"><div class="empty-icon">◈</div><p>No runs yet.</p></div>';
          } else {
            html += '<div class="card"><table><thead><tr><th>ID</th><th>Workflow</th><th>Status</th><th>Started</th><th></th></tr></thead><tbody>';
            for (var ri=0;ri<this.runs.length;ri++) {
              var r=this.runs[ri];
              var st=(r.started_at||'').slice(0,16);
              var rerun=r.status==='failed'||r.status==='completed'||r.status==='paused';
              var running=r.status==='running'||r.status==='pending'||r.status==='awaiting_review';
              var wfName = r.workflow_name || '';
              var wfDisplay = wfName ? wfName + ' <span class="muted"><code>'+r.workflow_id.slice(0,8)+'</code></span>' : '<code>'+r.workflow_id.slice(0,8)+'</code>';
              html += '<tr><td><code>'+r.id.slice(0,8)+'</code></td><td>'+wfDisplay+'</td>'+
                '<td><span class="badge '+r.status+'">'+r.status+'</span></td><td class="muted">'+st+'</td>'+
                '<td class="actions-cell"><button class="btn sm fixed" @click="viewRun(\''+r.id+'\')">Details</button>'+
                (rerun?'<button class="btn sm fixed" @click="rerunRun(\''+r.id+'\')">Re-run</button>':'')+
                (running?'<button class="btn danger sm fixed" @click="stopRun(\''+r.id+'\')">Stop</button>':'')+
                '<button class="btn danger sm fixed" @click="deleteRun(\''+r.id+'\')">Del</button>'+
                '</td></tr>';
            }
            html += '</tbody></table></div>';
          }
        }
        this.runsHTML = html;
      } catch(e) { this.runsHTML = '<div class="empty-state mt"><p>Failed to load runs</p></div>'; }
    },

    async loadCron() { try { this.cronJobs = await apiGet('/cron'); } catch(e) { console.error('loadCron error:', e); } },
    async createWorkflow() {
      try { var raw=this.$refs.wfConfig.value; var cfg=JSON.parse(raw); var nm=cfg.name||'untitled-'+Date.now(); delete cfg.name;
        await apiPost('/workflows',{name:nm,config:cfg}); this.showCreateWorkflow=false; this.$refs.wfConfig.value=''; await this.loadWorkflows(); }
      catch(e) { alert('Error: '+e.message); }
    },
    async viewRun(rid) { this.viewRunId=rid; this.fileTreeHTML=''; this.fileContentHTML=''; this.page='runs'; await this.loadRuns(); },
    async pauseRun(rid) { try { await apiPost('/runs/'+rid+'/pause'); await this.loadRuns(); } catch(e) { console.error(e); alert('Error pausing run: '+(e.message||e)); } },
    async resumeRun(rid) { try { await apiPost('/runs/'+rid+'/resume'); await this.loadRuns(); } catch(e) { console.error(e); alert('Error resuming run: '+(e.message||e)); } },
    async stopRun(rid) { try { await apiPost('/runs/'+rid+'/stop'); await this.loadRuns(); } catch(e) { console.error(e); alert('Error stopping run: '+(e.message||e)); } },
    async deleteRun(rid) { if(!confirm('Delete run '+rid.slice(0,8)+'?'))return; try { await apiDelete('/runs/'+rid); await this.loadRuns(); } catch(e) { console.error(e); alert('Error deleting run: '+(e.message||e)); } },
    async rerunRun(rid) { try { await apiPost('/runs/'+rid+'/rerun'); await this.loadRuns(); } catch(e) { alert('Error: '+e.message); } },
    async deleteWorkflow(wid) { if(!confirm('Delete workflow?'))return; try { await apiDelete('/workflows/'+wid); this.workflowDetails[wid]=undefined; await this.loadWorkflows(); } catch(e) { console.error(e); alert('Error deleting workflow: '+(e.message||e)); } },
    onCronWfSelect() {
      var wfId = this.$refs.cronWfSelect.value;
      this.cronFormInputs = wfId ? this.getWorkflowInputs(wfId) : {};
      this.cronFormValues = {};
      if (wfId) {
        var inputs = this.cronFormInputs;
        Object.keys(inputs).forEach(function(k){ this.cronFormValues[k] = inputs[k].default || ''; }, this);
      }
    },
    applyCronPreset(expr) {
      var el = document.querySelector('[x-ref="cronExpr"]');
      if (el) { el.value = expr; el.dispatchEvent(new Event('input', {bubbles:true})); }
    },
    async createCron() {
      var wfId=this.$refs.cronWfSelect.value, expr=this.$refs.cronExpr.value.trim();
      if(!wfId){alert('Select a workflow.');return;} if(!expr){alert('Enter a cron expression.');return;}
      var inputs = {};
      for (var k in this.cronFormValues) { var v = this.cronFormValues[k]; if (v) inputs[k] = v; }
      try { await apiPost('/cron',{workflow_id:wfId,cron_expr:expr,inputs:inputs}); this.showCreateCron=false;
        this.$refs.cronExpr.value=''; this.cronFormInputs={}; this.cronFormValues={}; await this.loadCron(); }
      catch(e) { alert('Error: '+e.message); }
    },
    async toggleCron(id) {
      try { await apiPost('/cron/'+id+'/toggle'); await this.loadCron(); }
      catch(e) { alert('Toggle error: '+e.message); }
    },
    async deleteCron(id) { if(!confirm('Delete?'))return; try { await apiDelete('/cron/'+id); await this.loadCron(); } catch(e) { console.error(e); alert('Error deleting cron job: '+(e.message||e)); } },
  };
};

// ── DAG Zoom / Pan ────────────────────────────────────────────────────
window._dagZooms = {};
window._dagZoom = function(dagId, delta) {
  var current = window._dagZooms[dagId] || 1;
  var z = Math.max(0.25, Math.min(3, current + delta));
  window._dagZooms[dagId] = z;
  var inner = document.getElementById(dagId + '-inner');
  if (inner) inner.style.transform = 'scale(' + z + ')';
};
window._dagReset = function(dagId) {
  window._dagZooms[dagId] = 1;
  var inner = document.getElementById(dagId + '-inner');
  if (inner) inner.style.transform = 'scale(1)';
};
(function() {
  if (typeof document !== 'undefined') {
    document.addEventListener('wheel', function(ev) {
      var panArea = ev.target.closest('.dag-pan-area');
      if (!panArea || !ev.ctrlKey) return;
      ev.preventDefault();
      var dagId = panArea.id.replace('-pan', '');
      window._dagZoom(dagId, ev.deltaY < 0 ? 0.1 : -0.1);
    }, {passive: false});
    // Pan via mouse drag
    document.addEventListener('mousedown', function(ev) {
      var panArea = ev.target.closest('.dag-pan-area');
      if (!panArea) return;
      var inner = panArea.querySelector('[id$="-inner"]');
      if (!inner) return;
      var startX = ev.clientX, startY = ev.clientY;
      var scrollLeft = panArea.scrollLeft, scrollTop = panArea.scrollTop;
      panArea.style.cursor = 'grabbing';
      function onMove(mv) {
        panArea.scrollLeft = scrollLeft - (mv.clientX - startX);
        panArea.scrollTop = scrollTop - (mv.clientY - startY);
      }
      function onUp() {
        panArea.style.cursor = 'grab';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
})();

// ── Global accessor for onclick handlers ───────────────────────────────
window.getWFlowApp = function() {
  var el = document.querySelector('[x-data]');
  if (el && el._x_dataStack && el._x_dataStack[0]) return el._x_dataStack[0];
  return null;
};

function formatSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes<1024) return bytes+' B';
  if (bytes<1048576) return (bytes/1024).toFixed(1)+' KB';
  return (bytes/1048576).toFixed(1)+' MB';
}

window._toggleIO = function(elId, btn) {
  var el=document.getElementById(elId); if(!el)return;
  if(el.classList.contains('expanded')){el.classList.remove('expanded');el.classList.add('clamped');
    var s=el.textContent;if(s.length>120)s=s.slice(0,120)+'…';el.textContent=s;btn.textContent='Show more';}
  else{var full=(btn.getAttribute('data-full')||'').replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>');
    el.classList.remove('clamped');el.classList.add('expanded');el.textContent=full;btn.textContent='Show less';}
};
