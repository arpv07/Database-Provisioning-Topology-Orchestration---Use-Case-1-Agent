import React, { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = 'http://localhost:8000';
const API_KEY = 'dev-secret-key-123';
const authHeaders = {
  Authorization: `Bearer ${API_KEY}`,
  'Content-Type': 'application/json',
};

const SAMPLE_CLUSTERS = [
  { id: 'cluster-exa-dev01',  label: 'cluster-exa-dev01 (Frame X11M · us-west-2)' },
  { id: 'cluster-exa-prod01', label: 'cluster-exa-prod01 (Frame X9M · us-east-1)' },
  { id: 'cluster-exa-test01', label: 'cluster-exa-test01 (Frame X8M · eu-central-1)' },
  { id: 'cluster-exa-stg01',  label: 'cluster-exa-stg01 (Frame X8 · ap-southeast-1)' },
];

// ─────────────────────────── Health & Setup Modal ───────────────────────────
function HealthModal({ isOpen, onClose, healthy }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl bg-slate-900 border border-white/10 p-6 shadow-2xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            <span className={`w-3 h-3 rounded-full ${healthy ? 'bg-emerald-400' : 'bg-amber-400 animate-ping'}`} />
            Docker Execution Diagnostics
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm">✕</button>
        </div>
        <p className="text-xs text-gray-300 leading-relaxed">
          {healthy
            ? 'Docker daemon is reachable and attached to oracle-exadata-dev. Live containers are healthy.'
            : 'Docker daemon is currently offline or unreachable. The system is operating in Resilient Simulation Mode.'}
        </p>
        <div className="rounded-xl bg-black/60 p-4 border border-white/5 font-mono text-xs text-cyan-300 space-y-2">
          <p className="text-gray-500 select-none"># 1-Click Environment Setup Command:</p>
          <p className="select-all font-bold">bash scripts/setup_test_env.sh</p>
          <p className="text-gray-500 select-none mt-2"># Or via Docker Compose directly:</p>
          <p className="select-all text-violet-300">docker compose up -d</p>
        </div>
        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs tracking-wide transition-all"
        >
          Close Diagnostics
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── RCA AI Modal ───────────────────────────────────
function RcaModal({ isOpen, onClose, rcaData, loading }) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl bg-slate-900 border border-purple-500/30 p-6 shadow-2xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-purple-300 flex items-center gap-2">
            <span>✨</span> AI Root Cause Analysis (Llama 3.3-70b)
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm">✕</button>
        </div>

        {loading ? (
          <div className="py-8 text-center space-y-2">
            <div className="w-6 h-6 border-2 border-purple-400 border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-xs text-purple-300 font-mono">Analyzing log patterns & ORA- error signatures...</p>
          </div>
        ) : rcaData ? (
          <div className="space-y-3 text-xs">
            <div className="rounded-xl bg-purple-950/40 border border-purple-500/20 p-3.5 space-y-1">
              <span className="font-semibold text-purple-300 uppercase tracking-wider text-[10px]">Root Cause</span>
              <p className="text-gray-200">{rcaData.root_cause}</p>
              {rcaData.ora_code && (
                <span className="inline-block mt-1 px-2 py-0.5 rounded bg-red-500/20 text-red-300 font-mono">
                  {rcaData.ora_code}
                </span>
              )}
            </div>

            <div className="rounded-xl bg-slate-950/80 border border-white/10 p-3.5 space-y-1">
              <span className="font-semibold text-emerald-400 uppercase tracking-wider text-[10px]">Recommended Resolution</span>
              <p className="text-gray-300 leading-relaxed">{rcaData.recommended_fix}</p>
            </div>
          </div>
        ) : null}

        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-medium text-xs transition-all"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── Main Dashboard ──────────────────────────────────
export default function ProvisioningDashboard() {
  const [jobs, setJobs]               = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [streamLogs, setStreamLogs]   = useState([]);
  const [loading, setLoading]         = useState(false);
  const [toast, setToast]             = useState(null);
  const [healthy, setHealthy]         = useState(null);
  const [cloneSources, setCloneSources] = useState([]);
  const [showHealthModal, setShowHealthModal] = useState(false);
  const [aiPrompt, setAiPrompt]       = useState('');
  const [aiParsing, setAiParsing]     = useState(false);
  const [rcaModalOpen, setRcaModalOpen] = useState(false);
  const [rcaData, setRcaData]         = useState(null);
  const [rcaLoading, setRcaLoading]   = useState(false);

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

  // ── Handlers for Fluid Input Editing ──
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

  // ── Fetch Clone Sources ──
  useEffect(() => {
    const fetchSources = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/topology/clone-sources`, { headers: authHeaders });
        if (r.ok) setCloneSources(await r.json());
      } catch {}
    };
    fetchSources();
  }, []);

  // ── Health Check ──
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

  // ── Poll Job Queue ──
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/jobs`, { headers: authHeaders });
        if (r.ok) {
          const d = await r.json();
          const all = [
            ...(d.running   || []),
            ...(d.pending   || []),
            ...(d.completed || []),
            ...(d.failed    || []),
          ];
          setJobs(all);
        }
      } catch {}
    };
    poll();
    const t = setInterval(poll, 2_500);
    return () => clearInterval(t);
  }, []);

  // ── SSE Log Stream ──
  const startStream = useCallback((jobId) => {
    if (eventSourceRef.current) eventSourceRef.current.close();
    setStreamLogs([]);
    const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream?token=${encodeURIComponent(API_KEY)}`);
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === 'log') {
        setStreamLogs((prev) => [...prev, data.message]);
      } else if (data.type === 'status') {
        es.close();
      }
    };
    es.onerror = () => es.close();
  }, []);

  // ── AI Natural Language Intent Parsing (Llama-3.3-70b / Groq) ──
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
        setToast({ type: 'success', msg: `AI Auto-Filled: ${parsed.explanation || 'Intent parsed successfully.'}` });
      }
    } catch {
      setToast({ type: 'error', msg: 'Failed to invoke AI Agent parser.' });
    } finally {
      setAiParsing(false);
    }
  };

  // ── AI Error Diagnosis (RCA) ──
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
      if (r.ok) {
        setRcaData(await r.json());
      }
    } catch {
      setToast({ type: 'error', msg: 'Failed to generate AI RCA report.' });
    } finally {
      setRcaLoading(false);
    }
  };

  // ── Validation & Submit ──
  const validateForm = () => {
    const errs = {};
    const { db_name, db_unique_name, target_cluster_id, provisioning_type, source_cluster_id } = form;

    if (!target_cluster_id) errs.target_cluster_id = 'Target cluster required.';
    if (provisioning_type === 'clone' && !source_cluster_id) errs.source_cluster_id = 'Clone source database required.';

    if (!db_name) {
      errs.db_name = 'Required.';
    } else {
      if (db_name.length > 8) errs.db_name = 'Must be ≤ 8 characters.';
      else if (!/^[A-Za-z0-9]+$/.test(db_name)) errs.db_name = 'Alphanumeric letters and digits only.';
      else if (!/[A-Za-z]/.test(db_name) || !/[0-9]/.test(db_name)) errs.db_name = 'Must contain BOTH letters AND digits.';
      else if (/[0-9]$/.test(db_name)) errs.db_name = 'Must NOT end with a digit.';
    }

    if (!db_unique_name) {
      errs.db_unique_name = 'Required.';
    } else {
      if (db_unique_name.length > 15) errs.db_unique_name = 'Must be ≤ 15 characters.';
      else if (!/^[A-Za-z0-9_]+$/.test(db_unique_name)) errs.db_unique_name = 'Letters, digits, underscore only.';
      else if (!/[A-Za-z]/.test(db_unique_name) || !/[0-9]/.test(db_unique_name)) errs.db_unique_name = 'Must contain BOTH letters AND digits.';
      else if (/[0-9]$/.test(db_unique_name)) errs.db_unique_name = 'Must NOT end with a digit.';
    }

    return errs;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const errs = validateForm();
    if (Object.keys(errs).length) { setErrors(errs); return; }
    setErrors({});
    setLoading(true);

    try {
      const r = await fetch(`${API_BASE}/api/provision`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify(form),
      });

      if (!r.ok) {
        const body = await r.json();
        const errList = body.detail?.validation_errors || [body.detail];
        setToast({ type: 'error', msg: errList.join(' | ') });
        return;
      }

      const body = await r.json();
      const newJob = { job_id: body.job_id, ...form, status: 'pending', logs: [] };
      setSelectedJob(newJob);
      startStream(body.job_id);
      setToast({ type: 'success', msg: `Pipeline launched! Job ID: ${body.job_id.slice(0, 8)}` });
    } catch (err) {
      setToast({ type: 'error', msg: 'Network error connecting to backend service.' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#080c14] text-white font-sans flex flex-col">
      <HealthModal isOpen={showHealthModal} onClose={() => setShowHealthModal(false)} healthy={healthy} />
      <RcaModal isOpen={rcaModalOpen} onClose={() => setRcaModalOpen(false)} rcaData={rcaData} loading={rcaLoading} />

      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 rounded-xl px-4 py-3 text-xs font-medium shadow-2xl backdrop-blur border transition-all duration-300 ${toast.type === 'error' ? 'bg-red-950/90 border-red-500/40 text-red-300' : 'bg-emerald-950/90 border-emerald-500/40 text-emerald-300'}`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <header className="border-b border-white/10 px-6 py-3.5 flex items-center justify-between bg-slate-950/60 backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center text-xs font-bold text-white shadow-lg">
            ⚡
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-2">
              Oracle DB Provisioning Agent
              <span className="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 text-[10px] border border-purple-500/30">Llama 3.3 AI</span>
            </h1>
            <p className="text-[11px] text-gray-400">Autonomous Exadata Topology Orchestration Engine</p>
          </div>
        </div>

        {/* 1-Click Docker Health Button */}
        <button
          onClick={() => setShowHealthModal(true)}
          className="flex items-center gap-2.5 px-3 py-1.5 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 transition-all text-xs"
        >
          <span className={`w-2 h-2 rounded-full ${healthy === null ? 'bg-gray-500' : healthy ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
          <span className="text-gray-300 font-medium">
            {healthy === null ? 'Checking Docker…' : healthy ? 'Docker Connected' : 'Docker Resilient Mode'}
          </span>
          <span className="text-gray-500 text-[10px]">⚙ Diagnostics</span>
        </button>
      </header>

      {/* Main Grid */}
      <div className="flex-1 grid grid-cols-[340px_1fr] gap-0 overflow-hidden">
        
        {/* Left Drawer: AI Prompt + Form */}
        <aside className="border-r border-white/10 p-5 overflow-y-auto bg-slate-950/30 space-y-5">
          
          {/* AI Prompt Input Bar */}
          <div className="rounded-2xl bg-gradient-to-b from-purple-900/20 to-indigo-900/20 border border-purple-500/25 p-3.5 space-y-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-purple-300 flex items-center gap-1.5">
              <span>✨</span> AI Natural Language Auto-Fill
            </span>
            <input
              type="text"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              placeholder="e.g. Clone production DB ORD1P to dev cluster for testing..."
              className="w-full rounded-xl bg-black/40 border border-purple-500/20 px-3 py-2 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-purple-400"
            />
            <button
              type="button"
              onClick={handleAiParseIntent}
              disabled={aiParsing}
              className="w-full py-1.5 rounded-xl bg-purple-600/80 hover:bg-purple-500 text-white font-medium text-xs transition-all flex items-center justify-center gap-1.5"
            >
              {aiParsing ? 'Parsing Intent…' : 'Auto-Fill Form with AI'}
            </button>
          </div>

          {/* Provisioning Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            
            {/* Target Cluster */}
            <div>
              <label className="block text-[11px] font-semibold text-gray-400 mb-1 uppercase tracking-wider">Target Cluster</label>
              <select
                value={form.target_cluster_id}
                onChange={(e) => setForm((f) => ({ ...f, target_cluster_id: e.target.value }))}
                className="w-full rounded-xl bg-white/5 border border-white/10 px-3 py-2 text-xs text-white font-mono focus:outline-none focus:border-indigo-500"
              >
                {SAMPLE_CLUSTERS.map((c) => (
                  <option key={c.id} value={c.id} className="bg-slate-900 text-white">{c.label}</option>
                ))}
              </select>
            </div>

            {/* Provisioning Type Tabs */}
            <div>
              <label className="block text-[11px] font-semibold text-gray-400 mb-1.5 uppercase tracking-wider">Type</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { value: 'seed', label: '⬡ Seed', sub: 'From Scratch' },
                  { value: 'clone', label: '⎘ Clone', sub: 'Active DB' },
                ].map(({ value, label, sub }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, provisioning_type: value }))}
                    className={`rounded-xl p-2.5 text-left border transition-all ${form.provisioning_type === value ? (value === 'seed' ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300' : 'border-cyan-500 bg-cyan-500/10 text-cyan-300') : 'border-white/10 bg-white/5 text-gray-400 hover:bg-white/10'}`}
                  >
                    <p className="text-xs font-bold">{label}</p>
                    <p className="text-[10px] opacity-60">{sub}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* Clone Source Selection */}
            {form.provisioning_type === 'clone' && (
              <div>
                <label className="block text-[11px] font-semibold text-cyan-400 mb-1 uppercase tracking-wider">Clone Source Database</label>
                <select
                  value={form.source_cluster_id || ''}
                  onChange={(e) => setForm((f) => ({ ...f, source_cluster_id: e.target.value }))}
                  className="w-full rounded-xl bg-white/5 border border-cyan-500/30 px-3 py-2 text-xs text-white font-mono focus:outline-none focus:border-cyan-400"
                >
                  <option value="" className="bg-slate-900 text-gray-400">Select source...</option>
                  {cloneSources.map((cs) => (
                    <option key={cs.source_cluster_id} value={cs.source_cluster_id} className="bg-slate-900 text-white">
                      {cs.db_name} ({cs.db_unique_name}) — {cs.source_cluster_id}
                    </option>
                  ))}
                </select>
                {errors.source_cluster_id && <p className="text-[10px] text-red-400 mt-1">{errors.source_cluster_id}</p>}
              </div>
            )}

            {/* DB Name Input */}
            <div>
              <label className="block text-[11px] font-semibold text-gray-400 mb-1 uppercase tracking-wider">DB Name (SID)</label>
              <input
                type="text"
                maxLength={8}
                value={form.db_name}
                onChange={handleDbNameChange}
                placeholder="MYDB1A"
                className="w-full rounded-xl bg-white/5 border border-white/10 px-3 py-2 text-xs text-white font-mono focus:outline-none focus:border-indigo-500 uppercase"
              />
              {errors.db_name ? <p className="text-[10px] text-red-400 mt-1">{errors.db_name}</p> : <p className="text-[10px] text-gray-500 mt-1">Max 8 chars · alphanumeric · letters+digits</p>}
            </div>

            {/* DB Unique Name Input */}
            <div>
              <label className="block text-[11px] font-semibold text-gray-400 mb-1 uppercase tracking-wider">DB Unique Name</label>
              <input
                type="text"
                maxLength={15}
                value={form.db_unique_name}
                onChange={handleDbUniqueNameChange}
                placeholder="MYDB1A_SITE1"
                className="w-full rounded-xl bg-white/5 border border-white/10 px-3 py-2 text-xs text-white font-mono focus:outline-none focus:border-indigo-500 uppercase"
              />
              {errors.db_unique_name ? <p className="text-[10px] text-red-400 mt-1">{errors.db_unique_name}</p> : <p className="text-[10px] text-gray-500 mt-1">Max 15 chars · letters+digits+_</p>}
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold text-xs shadow-lg tracking-wide transition-all"
            >
              {loading ? 'Submitting Request…' : 'Launch Provisioning Pipeline'}
            </button>
          </form>
        </aside>

        {/* Right Area: Log Viewer + Execution Queue */}
        <main className="p-5 flex flex-col space-y-4 overflow-hidden bg-slate-950/20">
          
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xs font-bold text-gray-300 uppercase tracking-widest">Live Execution Stream</h2>
              {selectedJob && <p className="text-[11px] font-mono text-indigo-400">Job ID: {selectedJob.job_id}</p>}
            </div>

            {/* AI Diagnosis Trigger */}
            <button
              onClick={() => handleRunRca(selectedJob?.logs || streamLogs)}
              className="px-3 py-1.5 rounded-xl bg-purple-900/30 hover:bg-purple-900/50 border border-purple-500/30 text-purple-300 text-xs font-medium flex items-center gap-1.5 transition-all"
            >
              <span>⚡</span> AI Error Diagnosis (RCA)
            </button>
          </div>

          {/* Terminal Log Window */}
          <div className="flex-1 rounded-2xl bg-[#090d16] border border-white/10 p-4 font-mono text-xs overflow-y-auto space-y-1">
            {streamLogs.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-600 italic">
                No active execution output. Submit a new provision request or select a job.
              </div>
            ) : (
              streamLogs.map((line, idx) => (
                <div key={idx} className="whitespace-pre-wrap break-all text-gray-300 leading-relaxed">
                  <span className="text-gray-600 select-none mr-2">{String(idx + 1).padStart(3, ' ')} │</span>
                  {line}
                </div>
              ))
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
