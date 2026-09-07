import React, { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = 'http://localhost:8000';
const API_KEY = 'dev-secret-key-123';
const authHeaders = {
  Authorization: `Bearer ${API_KEY}`,
  'Content-Type': 'application/json',
};

// Accurate Docker container cluster mappings
const CLUSTERS = [
  { id: 'cluster-exa-dev01',  label: 'cluster-dev01 · Development Cluster (Docker Container)' },
  { id: 'cluster-exa-prod01', label: 'cluster-prod01 · Production Cluster (Docker Container)' },
  { id: 'cluster-exa-test01', label: 'cluster-test01 · Test Cluster (Docker Container)' },
  { id: 'cluster-exa-stg01',  label: 'cluster-stg01 · Staging Cluster (Docker Container)' },
];

// ─────────────────────────── Health Modal (Light Theme) ─────────────────────
function HealthModal({ isOpen, onClose, healthy }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-md p-4">
      <div className="w-full max-w-lg rounded-2xl bg-white border border-slate-200 p-6 shadow-xl space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`w-3 h-3 rounded-full ${healthy ? 'bg-emerald-500' : 'bg-amber-500 animate-ping'}`} />
            <h3 className="text-sm font-bold text-slate-900 tracking-tight">Docker Environment Status</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xs">✕</button>
        </div>

        <p className="text-xs text-slate-600 leading-relaxed">
          {healthy
            ? 'Docker daemon is active and connected to container oracle-exadata-dev. Local gvenzl/oracle-free test container is running.'
            : 'Docker daemon is offline or unreachable. The system is operating in Resilient Simulation Mode.'}
        </p>

        <div className="rounded-xl bg-slate-50 p-4 border border-slate-200 font-mono text-xs text-slate-800 space-y-2">
          <p className="text-slate-500 select-none"># Terminal Setup Command:</p>
          <p className="select-all font-semibold text-indigo-600">bash scripts/setup_test_env.sh</p>
          <p className="text-slate-500 select-none mt-2"># Or via Docker Compose:</p>
          <p className="select-all text-slate-700">docker compose up -d</p>
        </div>

        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs transition-all shadow-sm"
        >
          Close Status
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── RCA Modal (Light Theme) ────────────────────────
function RcaModal({ isOpen, onClose, rcaData, loading }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-md p-4">
      <div className="w-full max-w-lg rounded-2xl bg-white border border-purple-200 p-6 shadow-xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-purple-900 flex items-center gap-2">
            <span>✨</span> AI Error Diagnostic (Llama 3.3-70b)
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xs">✕</button>
        </div>

        {loading ? (
          <div className="py-8 text-center space-y-3">
            <div className="w-6 h-6 border-2 border-purple-600 border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-xs text-purple-700 font-mono">Analyzing error log output...</p>
          </div>
        ) : rcaData ? (
          <div className="space-y-3 text-xs">
            <div className="rounded-xl bg-purple-50 border border-purple-200 p-4 space-y-1">
              <span className="font-bold text-purple-800 uppercase text-[10px] tracking-wider">Root Cause</span>
              <p className="text-slate-800 leading-relaxed">{rcaData.root_cause}</p>
              {rcaData.ora_code && (
                <span className="inline-block mt-1 px-2.5 py-0.5 rounded-md bg-red-100 text-red-700 font-mono text-[10px] border border-red-200">
                  {rcaData.ora_code}
                </span>
              )}
            </div>

            <div className="rounded-xl bg-slate-50 border border-slate-200 p-4 space-y-1">
              <span className="font-bold text-slate-700 uppercase text-[10px] tracking-wider">Suggested Resolution</span>
              <p className="text-slate-700 leading-relaxed font-mono text-[11px]">{rcaData.recommended_fix}</p>
            </div>
          </div>
        ) : null}

        <button
          onClick={onClose}
          className="w-full py-2 rounded-xl bg-purple-600 hover:bg-purple-700 text-white font-semibold text-xs transition-all shadow-sm"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── Dashboard Component (Light Theme) ──────────────
export default function ProvisioningDashboard() {
  const [jobs, setJobs]               = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [streamLogs, setStreamLogs]   = useState([]);
  const [loading, setLoading]         = useState(false);
  const [healthy, setHealthy]         = useState(null);
  const [cloneSources, setCloneSources] = useState([]);
  const [showHealthModal, setShowHealthModal] = useState(false);
  const [aiPrompt, setAiPrompt]       = useState('');
  const [aiParsing, setAiParsing]     = useState(false);
  const [rcaModalOpen, setRcaModalOpen] = useState(false);
  const [rcaData, setRcaData]         = useState(null);
  const [rcaLoading, setRcaLoading]   = useState(false);
  const [activeNode, setActiveNode]   = useState(1);

  // Form State
  const [form, setForm] = useState({
    db_name: 'MYDB1A',
    db_unique_name: 'MYDB1A_SITE1',
    target_cluster_id: 'cluster-exa-dev01',
    source_cluster_id: 'cluster-exa-prod01',
    provisioning_type: 'seed',
  });
  const [errors, setErrors] = useState({});
  const eventSourceRef = useRef(null);

  // Fluid input controls
  const handleDbNameChange = (e) => {
    const val = e.target.value.toUpperCase();
    setForm((f) => ({
      ...f,
      db_name: val,
      db_unique_name: val ? `${val}_SITE1` : '',
    }));
    if (errors.db_name) setErrors((errs) => ({ ...errs, db_name: null }));
  };

  const handleDbUniqueNameChange = (e) => {
    const val = e.target.value.toUpperCase();
    setForm((f) => ({ ...f, db_unique_name: val }));
    if (errors.db_unique_name) setErrors((errs) => ({ ...errs, db_unique_name: null }));
  };

  // Fetch Sources & Health Check
  useEffect(() => {
    const fetchSources = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/topology/clone-sources`, { headers: authHeaders });
        if (r.ok) setCloneSources(await r.json());
      } catch {}
    };
    fetchSources();
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/health`);
        const d = await r.json();
        setHealthy(d.reachable);
      } catch { setHealthy(false); }
    };
    check();
    const t = setInterval(check, 12_000);
    return () => clearInterval(t);
  }, []);

  // Poll Job Queue
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/jobs`, { headers: authHeaders });
        if (r.ok) {
          const d = await r.json();
          setJobs([...(d.running || []), ...(d.pending || []), ...(d.completed || []), ...(d.failed || [])]);
        }
      } catch {}
    };
    poll();
    const t = setInterval(poll, 3_000);
    return () => clearInterval(t);
  }, []);

  // SSE Stream
  const startStream = useCallback((jobId) => {
    if (eventSourceRef.current) eventSourceRef.current.close();
    setStreamLogs([]);
    setActiveNode(2);
    const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream?token=${encodeURIComponent(API_KEY)}`);
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === 'log') {
        setStreamLogs((prev) => [...prev, data.message]);
        if (data.message.includes('[POST-PROV]')) setActiveNode(3);
        if (data.message.includes('[QA]')) setActiveNode(3);
      } else if (data.type === 'status') {
        es.close();
      }
    };
    es.onerror = () => es.close();
  }, []);

  // AI Intent Parsing
  const handleAiParseIntent = async () => {
    if (!aiPrompt.trim()) return;
    setAiParsing(true);
    try {
      const r = await fetch(`${API_BASE}/api/ai/parse-intent`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ prompt: aiPrompt }),
      });
      if (r.ok) {
        const parsed = await r.json();
        setForm((f) => ({
          ...f,
          db_name: (parsed.db_name || f.db_name).toUpperCase(),
          db_unique_name: (parsed.db_unique_name || f.db_unique_name).toUpperCase(),
          target_cluster_id: parsed.target_cluster_id || f.target_cluster_id,
          source_cluster_id: parsed.source_cluster_id || f.source_cluster_id,
          provisioning_type: parsed.provisioning_type || f.provisioning_type,
        }));
      }
    } catch {} finally {
      setAiParsing(false);
    }
  };

  // AI Diagnostic
  const handleRunRca = async (jobLogs) => {
    setRcaModalOpen(true);
    setRcaLoading(true);
    setRcaData(null);
    try {
      const r = await fetch(`${API_BASE}/api/ai/diagnose`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ logs: jobLogs || streamLogs }),
      });
      if (r.ok) setRcaData(await r.json());
    } catch {} finally {
      setRcaLoading(false);
    }
  };

  // Submit
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (healthy === false) {
      setStreamLogs(['[ERROR] ✘ Cannot submit: Target Docker container is offline. Please run "docker compose up -d".']);
      return;
    }
    setLoading(true);
    setActiveNode(1);

    try {
      const r = await fetch(`${API_BASE}/api/ai/langgraph-provision`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify(form),
      });

      if (!r.ok) {
        const body = await r.json();
        const msg = body.detail?.validation_errors?.[0] || body.detail || 'Docker container offline or unreachable.';
        setStreamLogs([`[ERROR] ✘ ${msg}`]);
        setActiveNode(4);
        return;
      }

      const body = await r.json();
      setStreamLogs(body.logs || []);
      setActiveNode(body.status === 'completed' ? 3 : 4);
      if (body.rca_report) setRcaData(body.rca_report);
    } catch (err) {
      setStreamLogs(['[ERROR] ✘ Network error connecting to backend API or Docker container.']);
      setActiveNode(4);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans flex flex-col selection:bg-indigo-500 selection:text-white">
      <HealthModal isOpen={showHealthModal} onClose={() => setShowHealthModal(false)} healthy={healthy} />
      <RcaModal isOpen={rcaModalOpen} onClose={() => setRcaModalOpen(false)} rcaData={rcaData} loading={rcaLoading} />

      {/* Top Header Bar (Light Theme) */}
      <header className="border-b border-slate-200 px-8 py-4 flex items-center justify-between bg-white shadow-sm sticky top-0 z-40">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-xs font-bold text-white shadow-md">
            ORA
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold tracking-tight text-slate-900">
                Oracle DB Provisioning Agent
              </h1>
              <span className="px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-[10px] font-mono border border-indigo-200 font-medium">
                Local Docker Prototype (gvenzl/oracle-free)
              </span>
            </div>
            <p className="text-[11px] text-slate-500 font-mono">LangGraph StateGraph & Llama 3.3-70b AI Intent Engine</p>
          </div>
        </div>

        {/* Header Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowHealthModal(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 border border-slate-200 transition-all text-xs font-mono"
          >
            <span className={`w-2 h-2 rounded-full ${healthy ? 'bg-emerald-500' : 'bg-amber-500'}`} />
            <span className="text-slate-700">{healthy ? 'Docker Active' : 'Simulation Mode'}</span>
          </button>
        </div>
      </header>

      {/* AI Intent Command Bar */}
      <section className="px-8 pt-6 pb-2">
        <div className="rounded-2xl bg-white border border-indigo-100 p-3.5 shadow-sm flex items-center gap-3">
          <span className="text-base">✨</span>
          <input
            type="text"
            value={aiPrompt}
            onChange={(e) => setAiPrompt(e.target.value)}
            placeholder="Type AI intent, e.g. Clone production DB ORD1P to dev cluster for QA testing..."
            className="flex-1 bg-transparent text-xs text-slate-900 placeholder-slate-400 focus:outline-none font-mono"
          />
          <button
            onClick={handleAiParseIntent}
            disabled={aiParsing}
            className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs tracking-wide transition-all shadow-sm"
          >
            {aiParsing ? 'Parsing…' : 'Execute AI Intent'}
          </button>
        </div>
      </section>

      {/* Main Grid */}
      <div className="flex-1 grid grid-cols-[380px_1fr] gap-6 p-8 overflow-hidden">

        {/* Left Form Panel */}
        <aside className="rounded-2xl bg-white border border-slate-200 p-6 space-y-5 shadow-sm overflow-y-auto">
          
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-700">Container Configuration</h2>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            
            {/* Docker Offline Red Warning Banner */}
            {healthy === false && (
              <div className="rounded-xl bg-red-50 border border-red-200 p-3 text-xs text-red-700 space-y-1">
                <p className="font-bold flex items-center gap-1.5">
                  <span>⚠️</span> Docker Container Offline
                </p>
                <p className="text-[11px] text-red-600 leading-relaxed">
                  Target container <code className="font-mono bg-red-100 px-1 py-0.5 rounded text-red-800">oracle-exadata-dev</code> is unreachable. Please run <code className="font-mono bg-red-100 px-1 py-0.5 rounded text-red-800">docker compose up -d</code> in your terminal before provisioning.
                </p>
              </div>
            )}

            {/* Target Cluster Container */}
            <div className="space-y-1">
              <label className="text-[10px] font-bold uppercase tracking-wider text-slate-600">Target Container Cluster</label>
              <select
                value={form.target_cluster_id}
                onChange={(e) => setForm((f) => ({ ...f, target_cluster_id: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 border border-slate-200 px-3.5 py-2.5 text-xs text-slate-900 font-mono focus:outline-none focus:border-indigo-500 transition-all"
              >
                {CLUSTERS.map((c) => (
                  <option key={c.id} value={c.id} className="bg-white text-slate-900">{c.label}</option>
                ))}
              </select>
            </div>

            {/* Type Selector */}
            <div className="space-y-1">
              <label className="text-[10px] font-bold uppercase tracking-wider text-slate-600">Provisioning Type</label>
              <div className="grid grid-cols-2 gap-2.5">
                {[
                  { id: 'seed', label: '⬡ Seed', sub: 'DBCA Response File' },
                  { id: 'clone', label: '⎘ Clone', sub: 'Database Duplicate' },
                ].map(({ id, label, sub }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, provisioning_type: id }))}
                    className={`rounded-xl p-3 text-left border transition-all ${form.provisioning_type === id ? 'border-indigo-600 bg-indigo-50/60 text-indigo-900 font-medium' : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100'}`}
                  >
                    <p className="text-xs font-bold">{label}</p>
                    <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* Clone Source Selection */}
            {form.provisioning_type === 'clone' && (
              <div className="space-y-1">
                <label className="text-[10px] font-bold uppercase tracking-wider text-indigo-600">Clone Source Database</label>
                <select
                  value={form.source_cluster_id || ''}
                  onChange={(e) => setForm((f) => ({ ...f, source_cluster_id: e.target.value }))}
                  className="w-full rounded-xl bg-indigo-50/40 border border-indigo-200 px-3.5 py-2.5 text-xs text-slate-900 font-mono focus:outline-none focus:border-indigo-500 transition-all"
                >
                  <option value="" className="bg-white text-slate-500">Select clone source...</option>
                  {cloneSources.map((cs) => (
                    <option key={cs.source_cluster_id} value={cs.source_cluster_id} className="bg-white text-slate-900">
                      {cs.db_name} ({cs.db_unique_name}) — {cs.source_cluster_id}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* DB Name */}
            <div className="space-y-1">
              <label className="text-[10px] font-bold uppercase tracking-wider text-slate-600">DB Name (SID)</label>
              <input
                type="text"
                maxLength={8}
                value={form.db_name}
                onChange={handleDbNameChange}
                placeholder="MYDB1A"
                className="w-full rounded-xl bg-slate-50 border border-slate-200 px-3.5 py-2.5 text-xs text-slate-900 font-mono focus:outline-none focus:border-indigo-500 transition-all uppercase"
              />
              <p className="text-[10px] text-slate-400 font-mono">≤8 chars · letters+digits mix · no trailing digit</p>
            </div>

            {/* DB Unique Name */}
            <div className="space-y-1">
              <label className="text-[10px] font-bold uppercase tracking-wider text-slate-600">DB Unique Name</label>
              <input
                type="text"
                maxLength={15}
                value={form.db_unique_name}
                onChange={handleDbUniqueNameChange}
                placeholder="MYDB1A_SITE1"
                className="w-full rounded-xl bg-slate-50 border border-slate-200 px-3.5 py-2.5 text-xs text-slate-900 font-mono focus:outline-none focus:border-indigo-500 transition-all uppercase"
              />
              <p className="text-[10px] text-slate-400 font-mono">≤15 chars · letters+digits+_</p>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || healthy === false}
              className={`w-full py-3 rounded-xl font-bold text-xs tracking-wide transition-all shadow-sm ${healthy === false ? 'bg-slate-300 text-slate-500 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 text-white'}`}
            >
              {loading ? 'Executing Workflow…' : healthy === false ? 'Docker Container Offline' : 'Launch Provisioning Pipeline'}
            </button>
          </form>
        </aside>

        {/* Right Execution Window */}
        <main className="flex flex-col space-y-4 overflow-hidden">
          
          {/* LangGraph Node Diagram (Light Theme) */}
          <div className="rounded-2xl bg-white border border-slate-200 p-4 shadow-sm flex items-center justify-between">
            {[
              { num: 1, title: 'Node 1: Intent', desc: 'Validation Tool' },
              { num: 2, title: 'Node 2: Provision', desc: 'DBCA / Duplicate Tool' },
              { num: 3, title: 'Node 3: Tuning', desc: '13 Params + QA' },
              { num: 4, title: 'Node 4: AI RCA', desc: 'Llama 3.3 Diagnostic' },
            ].map((node, i) => (
              <React.Fragment key={node.num}>
                <div className={`flex items-center gap-3 p-2.5 rounded-xl border transition-all ${activeNode === node.num ? 'border-indigo-500 bg-indigo-50 text-indigo-900 font-medium' : activeNode > node.num ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-slate-100 bg-slate-50 text-slate-400'}`}>
                  <div className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs font-bold ${activeNode === node.num ? 'bg-indigo-600 text-white' : activeNode > node.num ? 'bg-emerald-500 text-white' : 'bg-slate-200 text-slate-500'}`}>
                    {node.num}
                  </div>
                  <div>
                    <p className="text-xs font-bold text-slate-800">{node.title}</p>
                    <p className="text-[10px] text-slate-500 font-mono">{node.desc}</p>
                  </div>
                </div>
                {i < 3 && <div className="h-[1px] w-6 bg-slate-200" />}
              </React.Fragment>
            ))}
          </div>

          {/* Log Window Console */}
          <div className="flex-1 rounded-2xl bg-slate-900 border border-slate-800 p-5 font-mono text-xs overflow-y-auto space-y-1.5 shadow-md flex flex-col justify-between text-slate-200">
            <div className="space-y-1">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2.5 mb-2.5">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Execution Stream Output</span>
                <button
                  onClick={() => handleRunRca(streamLogs)}
                  className="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-purple-300 text-[11px] font-mono transition-all flex items-center gap-1.5"
                >
                  <span>⚡</span> AI Error Diagnosis
                </button>
              </div>

              {streamLogs.length === 0 ? (
                <div className="py-24 text-center text-slate-500 italic">
                  Pipeline awaiting submission. Select target container cluster or type AI prompt above.
                </div>
              ) : (
                streamLogs.map((line, idx) => (
                  <div key={idx} className="whitespace-pre-wrap break-all leading-relaxed">
                    <span className="text-slate-500 select-none mr-3">{String(idx + 1).padStart(3, '0')} │</span>
                    {line}
                  </div>
                ))
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
