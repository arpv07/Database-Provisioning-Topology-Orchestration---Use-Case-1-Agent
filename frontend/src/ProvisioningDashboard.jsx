import React, { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = 'http://localhost:8000';
const API_KEY = 'dev-secret-key-123';
const authHeaders = {
  Authorization: `Bearer ${API_KEY}`,
  'Content-Type': 'application/json',
};

const CLUSTERS = [
  { id: 'cluster-exa-dev01',  label: 'cluster-exa-dev01 · Frame X11M (Development)' },
  { id: 'cluster-exa-prod01', label: 'cluster-exa-prod01 · Frame X9M (Production)' },
  { id: 'cluster-exa-test01', label: 'cluster-exa-test01 · Frame X8M (Testing)' },
  { id: 'cluster-exa-stg01',  label: 'cluster-exa-stg01 · Frame X8 (Staging)' },
];

// ─────────────────────────── Forge Health Modal ─────────────────────────────
function HealthModal({ isOpen, onClose, healthy }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-xl p-4">
      <div className="w-full max-w-lg rounded-3xl bg-[#0d111a] border border-violet-500/30 p-6 shadow-[0_0_50px_rgba(139,92,246,0.2)] space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`w-3.5 h-3.5 rounded-full ${healthy ? 'bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.8)]' : 'bg-amber-400 animate-ping'}`} />
            <h3 className="text-sm font-bold text-white tracking-wide">Forge Container Diagnostic</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xs">✕</button>
        </div>

        <p className="text-xs text-gray-300 leading-relaxed font-sans">
          {healthy
            ? 'Docker Engine is active and connected to container oracle-exadata-dev. Local Exadata test environment is fully operational.'
            : 'Docker Engine is offline or unreachable. The agent is executing under Resilient Simulation Mode.'}
        </p>

        <div className="rounded-2xl bg-[#06080d] p-4 border border-white/10 font-mono text-xs text-cyan-300 space-y-2">
          <p className="text-gray-500 select-none"># 1-Click Terminal Setup:</p>
          <p className="select-all font-semibold">bash scripts/setup_test_env.sh</p>
          <p className="text-gray-500 select-none mt-2"># Or via Docker Compose:</p>
          <p className="select-all text-purple-300">docker compose up -d</p>
        </div>

        <button
          onClick={onClose}
          className="w-full py-3 rounded-2xl bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 text-white font-bold text-xs shadow-lg tracking-wider uppercase transition-all"
        >
          Close Diagnostics
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── Forge RCA Modal ────────────────────────────────
function RcaModal({ isOpen, onClose, rcaData, loading }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-xl p-4">
      <div className="w-full max-w-lg rounded-3xl bg-[#0d111a] border border-purple-500/40 p-6 shadow-[0_0_50px_rgba(168,85,247,0.25)] space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-purple-300 tracking-wide flex items-center gap-2">
            <span>✨</span> Llama 3.3-70b AI Diagnostic (Groq API)
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xs">✕</button>
        </div>

        {loading ? (
          <div className="py-8 text-center space-y-3">
            <div className="w-7 h-7 border-2 border-purple-400 border-t-transparent rounded-full animate-spin mx-auto shadow-[0_0_15px_rgba(168,85,247,0.6)]" />
            <p className="text-xs text-purple-300 font-mono">Running neural RCA analysis over execution logs...</p>
          </div>
        ) : rcaData ? (
          <div className="space-y-3 text-xs">
            <div className="rounded-2xl bg-purple-950/30 border border-purple-500/20 p-4 space-y-1">
              <span className="font-bold text-purple-400 uppercase tracking-widest text-[10px]">Root Cause</span>
              <p className="text-gray-200 leading-relaxed">{rcaData.root_cause}</p>
              {rcaData.ora_code && (
                <span className="inline-block mt-1 px-2.5 py-0.5 rounded-full bg-red-500/20 text-red-300 font-mono text-[10px] border border-red-500/30">
                  {rcaData.ora_code}
                </span>
              )}
            </div>

            <div className="rounded-2xl bg-black/50 border border-white/10 p-4 space-y-1">
              <span className="font-bold text-emerald-400 uppercase tracking-widest text-[10px]">DBA Fix Guidance</span>
              <p className="text-gray-300 leading-relaxed font-mono text-[11px]">{rcaData.recommended_fix}</p>
            </div>
          </div>
        ) : null}

        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs uppercase tracking-wider transition-all"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── Forge Dashboard Component ──────────────────────
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
  const [useLangGraph, setUseLangGraph] = useState(true);

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

  // Input editing handlers
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

  // Fetch Clone Sources & Health Check
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

  // AI Intent Parsing (Groq Llama 3.3-70b)
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

  // AI Error Diagnosis (RCA)
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

  // Form Submit (LangGraph or Standard)
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setActiveNode(1);

    const endpoint = useLangGraph ? `${API_BASE}/api/ai/langgraph-provision` : `${API_BASE}/api/provision`;

    try {
      const r = await fetch(endpoint, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify(form),
      });

      if (!r.ok) {
        const body = await r.json();
        setActiveNode(4);
        return;
      }

      const body = await r.json();
      if (useLangGraph) {
        setStreamLogs(body.logs || []);
        setActiveNode(body.status === 'completed' ? 3 : 4);
        if (body.rca_report) setRcaData(body.rca_report);
      } else {
        setSelectedJob({ job_id: body.job_id, ...form });
        startStream(body.job_id);
      }
    } catch (err) {
      setActiveNode(4);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#06080d] text-white font-sans flex flex-col selection:bg-purple-500 selection:text-white">
      <HealthModal isOpen={showHealthModal} onClose={() => setShowHealthModal(false)} healthy={healthy} />
      <RcaModal isOpen={rcaModalOpen} onClose={() => setRcaModalOpen(false)} rcaData={rcaData} loading={rcaLoading} />

      {/* Top Navigation */}
      <header className="border-b border-white/[0.08] px-8 py-4 flex items-center justify-between bg-[#080b12]/80 backdrop-blur-2xl sticky top-0 z-40">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-2xl bg-gradient-to-br from-violet-600 via-indigo-600 to-purple-600 p-[1px] shadow-[0_0_25px_rgba(139,92,246,0.3)]">
            <div className="w-full h-full rounded-2xl bg-[#06080d] flex items-center justify-center text-[10px] font-black text-purple-300">
              ORA
            </div>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-extrabold tracking-tight bg-gradient-to-r from-white via-gray-200 to-purple-300 bg-clip-text text-transparent">
                Oracle DB Provisioning Agent
              </h1>
              <span className="px-2.5 py-0.5 rounded-full bg-violet-500/10 text-violet-300 text-[10px] font-mono border border-violet-500/20">
                LangGraph StateGraph
              </span>
            </div>
            <p className="text-[11px] text-gray-400 font-mono">Llama 3.3-70b AI Intent & Error Diagnostics</p>
          </div>
        </div>

        {/* Header Right Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setUseLangGraph(!useLangGraph)}
            className={`px-3.5 py-1.5 rounded-xl border text-xs font-mono transition-all ${useLangGraph ? 'bg-purple-500/15 border-purple-500/40 text-purple-300 shadow-[0_0_20px_rgba(168,85,247,0.2)]' : 'bg-white/5 border-white/10 text-gray-400'}`}
          >
            Engine: {useLangGraph ? '⚡ LangGraph StateGraph' : '⚙ Standard Engine'}
          </button>

          <button
            onClick={() => setShowHealthModal(true)}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-white/[0.03] hover:bg-white/[0.07] border border-white/10 transition-all text-xs font-mono"
          >
            <span className={`w-2 h-2 rounded-full ${healthy ? 'bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.8)]' : 'bg-amber-400'}`} />
            <span className="text-gray-300">{healthy ? 'Docker Active' : 'Simulation Mode'}</span>
          </button>
        </div>
      </header>

      {/* Command Bar */}
      <section className="px-8 pt-6 pb-2">
        <div className="rounded-3xl bg-gradient-to-r from-violet-950/30 via-indigo-950/20 to-purple-950/30 border border-violet-500/30 p-4 shadow-[0_0_40px_rgba(139,92,246,0.12)] flex items-center gap-3">
          <span className="text-lg">✨</span>
          <input
            type="text"
            value={aiPrompt}
            onChange={(e) => setAiPrompt(e.target.value)}
            placeholder="Type AI intent, e.g. Clone production DB ORD1P to dev cluster for QA benchmarking..."
            className="flex-1 bg-transparent text-xs text-white placeholder-gray-500 focus:outline-none font-mono"
          />
          <button
            onClick={handleAiParseIntent}
            disabled={aiParsing}
            className="px-5 py-2.5 rounded-2xl bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-white font-bold text-xs tracking-wider uppercase transition-all shadow-[0_0_20px_rgba(139,92,246,0.4)]"
          >
            {aiParsing ? 'Parsing…' : 'Execute AI Intent'}
          </button>
        </div>
      </section>

      {/* Main Container */}
      <div className="flex-1 grid grid-cols-[380px_1fr] gap-6 p-8 overflow-hidden">

        {/* Left Drawer: Provision Form */}
        <aside className="rounded-3xl bg-[#0b0e17]/60 border border-white/[0.08] p-6 space-y-6 backdrop-blur-2xl shadow-xl overflow-y-auto">
          
          <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
            <h2 className="text-xs font-extrabold uppercase tracking-widest text-gray-300">Topology Configuration</h2>
            <span className="text-[10px] font-mono text-purple-400">Oracle 19c Exadata</span>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            
            {/* Target Cluster */}
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Target Exadata Cluster</label>
              <select
                value={form.target_cluster_id}
                onChange={(e) => setForm((f) => ({ ...f, target_cluster_id: e.target.value }))}
                className="w-full rounded-2xl bg-[#06080d] border border-white/10 px-4 py-3 text-xs text-white font-mono focus:outline-none focus:border-violet-500 transition-all"
              >
                {CLUSTERS.map((c) => (
                  <option key={c.id} value={c.id} className="bg-[#0b0e17] text-white">{c.label}</option>
                ))}
              </select>
            </div>

            {/* Type Selector */}
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Provisioning Type</label>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { id: 'seed', label: '⬡ Seed', sub: 'Build from Scratch' },
                  { id: 'clone', label: '⎘ Clone', sub: 'RMAN Active DB' },
                ].map(({ id, label, sub }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, provisioning_type: id }))}
                    className={`rounded-2xl p-3.5 text-left border transition-all ${form.provisioning_type === id ? 'border-violet-500 bg-violet-500/10 text-violet-300 shadow-[0_0_20px_rgba(139,92,246,0.15)]' : 'border-white/10 bg-white/[0.02] text-gray-400 hover:bg-white/[0.05]'}`}
                  >
                    <p className="text-xs font-bold">{label}</p>
                    <p className="text-[10px] opacity-60 mt-0.5">{sub}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* Clone Source Selection */}
            {form.provisioning_type === 'clone' && (
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold uppercase tracking-wider text-cyan-400">Clone Source Database</label>
                <select
                  value={form.source_cluster_id || ''}
                  onChange={(e) => setForm((f) => ({ ...f, source_cluster_id: e.target.value }))}
                  className="w-full rounded-2xl bg-[#06080d] border border-cyan-500/30 px-4 py-3 text-xs text-white font-mono focus:outline-none focus:border-cyan-400 transition-all"
                >
                  <option value="" className="bg-[#0b0e17] text-gray-400">Select clone source...</option>
                  {cloneSources.map((cs) => (
                    <option key={cs.source_cluster_id} value={cs.source_cluster_id} className="bg-[#0b0e17] text-white">
                      {cs.db_name} ({cs.db_unique_name}) — {cs.source_cluster_id}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* DB Name */}
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400">DB Name (SID)</label>
              <input
                type="text"
                maxLength={8}
                value={form.db_name}
                onChange={handleDbNameChange}
                placeholder="MYDB1A"
                className="w-full rounded-2xl bg-[#06080d] border border-white/10 px-4 py-3 text-xs text-white font-mono focus:outline-none focus:border-violet-500 transition-all uppercase"
              />
              <p className="text-[10px] text-gray-500 font-mono">≤8 chars · letter+digit mix · no trailing digit</p>
            </div>

            {/* DB Unique Name */}
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400">DB Unique Name</label>
              <input
                type="text"
                maxLength={15}
                value={form.db_unique_name}
                onChange={handleDbUniqueNameChange}
                placeholder="MYDB1A_SITE1"
                className="w-full rounded-2xl bg-[#06080d] border border-white/10 px-4 py-3 text-xs text-white font-mono focus:outline-none focus:border-violet-500 transition-all uppercase"
              />
              <p className="text-[10px] text-gray-500 font-mono">≤15 chars · letters+digits+_</p>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-3.5 rounded-2xl bg-gradient-to-r from-violet-600 via-indigo-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-white font-extrabold text-xs tracking-wider uppercase shadow-[0_0_30px_rgba(139,92,246,0.3)] transition-all"
            >
              {loading ? 'Executing Workflow…' : 'Launch Provisioning Pipeline'}
            </button>
          </form>
        </aside>

        {/* Right Area: Interactive LangGraph Node Diagram + Logs */}
        <main className="flex flex-col space-y-6 overflow-hidden">
          
          {/* Interactive LangGraph Node Step Diagram */}
          <div className="rounded-3xl bg-[#0b0e17]/60 border border-white/[0.08] p-5 backdrop-blur-2xl shadow-xl flex items-center justify-between">
            {[
              { num: 1, title: 'Node 1: Intent', desc: 'Validation Tool' },
              { num: 2, title: 'Node 2: Provision', desc: 'DBCA / RMAN Tool' },
              { num: 3, title: 'Node 3: Tuning', desc: '13 Params + QA' },
              { num: 4, title: 'Node 4: AI RCA', desc: 'Llama 3.3 Diagnostic' },
            ].map((node, i) => (
              <React.Fragment key={node.num}>
                <div className={`flex items-center gap-3 p-3 rounded-2xl border transition-all ${activeNode === node.num ? 'border-purple-500 bg-purple-500/10 shadow-[0_0_20px_rgba(168,85,247,0.2)]' : activeNode > node.num ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-300' : 'border-white/5 bg-white/[0.01] text-gray-500'}`}>
                  <div className={`w-7 h-7 rounded-xl flex items-center justify-center text-xs font-bold ${activeNode === node.num ? 'bg-purple-600 text-white' : activeNode > node.num ? 'bg-emerald-500 text-black' : 'bg-white/5 text-gray-500'}`}>
                    {node.num}
                  </div>
                  <div>
                    <p className="text-xs font-bold text-gray-200">{node.title}</p>
                    <p className="text-[10px] opacity-60 font-mono">{node.desc}</p>
                  </div>
                </div>
                {i < 3 && <div className="h-[1px] w-6 bg-white/10" />}
              </React.Fragment>
            ))}
          </div>

          {/* Log Window */}
          <div className="flex-1 rounded-3xl bg-[#05070c] border border-white/[0.08] p-6 font-mono text-xs overflow-y-auto space-y-1.5 shadow-2xl flex flex-col justify-between">
            <div className="space-y-1">
              <div className="flex items-center justify-between border-b border-white/5 pb-3 mb-3">
                <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">LangGraph State Execution Output</span>
                <button
                  onClick={() => handleRunRca(streamLogs)}
                  className="px-3 py-1 rounded-xl bg-purple-950/40 hover:bg-purple-900/60 border border-purple-500/30 text-purple-300 text-[11px] font-mono transition-all flex items-center gap-1.5"
                >
                  <span>⚡</span> AI Error Diagnosis
                </button>
              </div>

              {streamLogs.length === 0 ? (
                <div className="py-24 text-center text-gray-600 italic">
                  LangGraph workflow awaiting prompt submission. Select target cluster or type AI intent above.
                </div>
              ) : (
                streamLogs.map((line, idx) => (
                  <div key={idx} className="whitespace-pre-wrap break-all text-gray-300 leading-relaxed">
                    <span className="text-gray-600 select-none mr-3">{String(idx + 1).padStart(3, '0')} │</span>
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
