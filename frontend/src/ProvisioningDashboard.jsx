import React, { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_PROVISIONING_API_KEY || 'dev-secret-key-123';

const authHeaders = {
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${API_KEY}`,
};

// ─────────────────────────── tiny helpers ────────────────────────────────────
const statusColors = {
  pending:   { bg: 'bg-amber-500/15',   text: 'text-amber-400',  dot: 'bg-amber-400'  },
  running:   { bg: 'bg-blue-500/15',    text: 'text-blue-400',   dot: 'bg-blue-400'   },
  completed: { bg: 'bg-emerald-500/15', text: 'text-emerald-400',dot: 'bg-emerald-400'},
  failed:    { bg: 'bg-red-500/15',     text: 'text-red-400',    dot: 'bg-red-400'    },
};

function StatusBadge({ status }) {
  const c = statusColors[status] || statusColors.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${c.bg} ${c.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot} ${status === 'running' ? 'animate-pulse' : ''}`} />
      {status.toUpperCase()}
    </span>
  );
}

function JobCard({ job, onSelect, isSelected }) {
  return (
    <button
      onClick={() => onSelect(job)}
      className={`w-full text-left rounded-xl p-4 border transition-all duration-150
        ${isSelected
          ? 'border-blue-500/60 bg-blue-500/10'
          : 'border-white/8 bg-white/4 hover:bg-white/8 hover:border-white/15'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-white font-mono">{job.db_name}</p>
          <p className="text-xs text-gray-500 font-mono mt-0.5">{job.db_unique_name}</p>
        </div>
        <StatusBadge status={job.status} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
        <span className={`uppercase font-medium ${job.provisioning_type === 'seed' ? 'text-violet-400' : 'text-cyan-400'}`}>
          {job.provisioning_type === 'seed' ? '⬡ Seed' : '⎘ Clone'}
        </span>
        <span>·</span>
        <span className="font-mono text-gray-400">{job.target_cluster_id}</span>
      </div>
    </button>
  );
}

// ─────────────────────────── Log Viewer ──────────────────────────────────────
function LogViewer({ logs, status }) {
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const colorize = (line) => {
    if (line.includes('✔') || line.includes('PASS'))  return 'text-emerald-400';
    if (line.includes('✘') || line.includes('FAIL') || line.includes('error') || line.includes('Error'))
      return 'text-red-400';
    if (line.startsWith('[SEED]'))     return 'text-violet-300';
    if (line.startsWith('[CLONE]'))    return 'text-cyan-300';
    if (line.startsWith('[POST-PROV]'))return 'text-amber-300';
    if (line.startsWith('[QA'))        return 'text-emerald-300';
    if (line.startsWith('[RMAN]'))     return 'text-sky-300';
    if (line.startsWith('[SHELL]'))    return 'text-gray-400';
    if (line.startsWith('[SQLPLUS]'))  return 'text-indigo-300';
    return 'text-gray-300';
  };

  return (
    <div className="relative h-full flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold uppercase tracking-widest text-gray-500">Execution Log</span>
        {status === 'running' && (
          <span className="flex items-center gap-1.5 text-xs text-blue-400">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
            STREAMING
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto rounded-lg bg-[#0d1117] border border-white/6 p-4 font-mono text-xs leading-relaxed">
        {logs.length === 0 ? (
          <p className="text-gray-600 italic">No output yet. Select a job or start a new build.</p>
        ) : (
          logs.map((line, i) => (
            <div key={i} className={`${colorize(line)} whitespace-pre-wrap break-all`}>
              <span className="text-gray-700 select-none mr-2">{String(i + 1).padStart(4, ' ')} │</span>
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ─────────────────────────── Provision Form ──────────────────────────────────
const INITIAL_FORM = {
  db_name: '',
  db_unique_name: '',
  target_cluster_id: 'cluster-exa-dev01',
  provisioning_type: 'seed',
  character_set: 'AL32UTF8',
  national_character_set: 'AL16UTF16',
};

const SAMPLE_CLUSTERS = [
  { id: 'cluster-exa-dev01',  label: 'cluster-exa-dev01 (Frame X11M · us-west-2)' },
  { id: 'cluster-exa-prod01', label: 'cluster-exa-prod01 (Frame X9M · us-east-1)' },
  { id: 'cluster-exa-test01', label: 'cluster-exa-test01 (Frame X8M · eu-central-1)' },
  { id: 'cluster-exa-stg01',  label: 'cluster-exa-stg01 (Frame X8 · ap-southeast-1)' },
];

function ProvisionForm({ onSubmit, loading }) {
  const [form, setForm] = useState(INITIAL_FORM);
  const [errors, setErrors] = useState({});

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const validate = () => {
    const errs = {};
    const { db_name, db_unique_name, target_cluster_id } = form;

    if (!target_cluster_id) errs.target_cluster_id = 'Target cluster required.';

    if (!db_name) {
      errs.db_name = 'Required.';
    } else {
      if (db_name.length > 8) errs.db_name = 'Must be ≤ 8 characters.';
      else if (!/^[A-Za-z0-9]+$/.test(db_name)) errs.db_name = 'Letters and digits only.';
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

  const handleSubmit = (e) => {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length) { setErrors(errs); return; }
    setErrors({});
    onSubmit(form);
  };

  const Field = ({ id, label, hint, children }) => (
    <div>
      <label htmlFor={id} className="block text-xs font-semibold text-gray-400 mb-1.5 uppercase tracking-wide">
        {label}
      </label>
      {children}
      {errors[id]
        ? <p className="mt-1 text-xs text-red-400">{errors[id]}</p>
        : hint && <p className="mt-1 text-xs text-gray-600">{hint}</p>}
    </div>
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-5">

      {/* Target Cluster */}
      <Field id="target_cluster_id" label="Target Exadata Cluster" hint="Resolved against topology inventory">
        <select
          id="target_cluster_id"
          value={form.target_cluster_id}
          onChange={set('target_cluster_id')}
          className="w-full rounded-lg bg-white/5 border border-white/10 px-3 py-2.5 text-xs text-white font-mono focus:outline-none focus:border-blue-500/60"
        >
          {SAMPLE_CLUSTERS.map((c) => (
            <option key={c.id} value={c.id} className="bg-slate-900 text-white">
              {c.label}
            </option>
          ))}
        </select>
      </Field>

      {/* Provisioning Type */}
      <div>
        <p className="text-xs font-semibold text-gray-400 mb-2 uppercase tracking-wide">Provisioning Type</p>
        <div className="grid grid-cols-2 gap-2">
          {[
            { value: 'seed',  label: '⬡ Seed',  sub: 'Build from scratch' },
            { value: 'clone', label: '⎘ Clone', sub: 'ARS / RMAN Duplicate' },
          ].map(({ value, label, sub }) => (
            <button
              key={value}
              type="button"
              onClick={() => setForm((f) => ({ ...f, provisioning_type: value }))}
              className={`rounded-lg p-3 text-left border transition-all duration-150
                ${form.provisioning_type === value
                  ? value === 'seed'
                    ? 'border-violet-500/60 bg-violet-500/10'
                    : 'border-cyan-500/60 bg-cyan-500/10'
                  : 'border-white/8 bg-white/4 hover:bg-white/8'}`}
            >
              <p className={`text-sm font-semibold ${form.provisioning_type === value
                ? value === 'seed' ? 'text-violet-300' : 'text-cyan-300'
                : 'text-gray-300'}`}>
                {label}
              </p>
              <p className="text-xs text-gray-500 mt-0.5">{sub}</p>
            </button>
          ))}
        </div>
      </div>

      {/* DB Name */}
      <Field id="db_name" label="DB Name" hint="≤8 chars · letters+digits · must not end in digit">
        <input
          id="db_name"
          type="text"
          maxLength={8}
          value={form.db_name}
          onChange={set('db_name')}
          placeholder="mydb1a"
          className={`w-full rounded-lg bg-white/5 border px-3 py-2.5 text-sm text-white font-mono
            placeholder-gray-600 focus:outline-none focus:ring-1
            ${errors.db_name
              ? 'border-red-500/60 focus:ring-red-500/40'
              : 'border-white/10 focus:border-blue-500/60 focus:ring-blue-500/30'}`}
        />
      </Field>

      {/* DB Unique Name */}
      <Field id="db_unique_name" label="DB Unique Name" hint="≤15 chars · letters+digits+_ · must not end in digit">
        <input
          id="db_unique_name"
          type="text"
          maxLength={15}
          value={form.db_unique_name}
          onChange={set('db_unique_name')}
          placeholder="mydb1a_site1"
          className={`w-full rounded-lg bg-white/5 border px-3 py-2.5 text-sm text-white font-mono
            placeholder-gray-600 focus:outline-none focus:ring-1
            ${errors.db_unique_name
              ? 'border-red-500/60 focus:ring-red-500/40'
              : 'border-white/10 focus:border-blue-500/60 focus:ring-blue-500/30'}`}
        />
      </Field>

      {/* Character Sets (read-only) */}
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: 'Character Set',          value: 'AL32UTF8'  },
          { label: 'National Character Set',  value: 'AL16UTF16' },
        ].map(({ label, value }) => (
          <div key={label}>
            <p className="text-xs font-semibold text-gray-400 mb-1.5 uppercase tracking-wide">{label}</p>
            <div className="rounded-lg bg-white/3 border border-white/6 px-3 py-2.5 text-sm font-mono text-emerald-400">
              {value}
            </div>
          </div>
        ))}
      </div>

      <button
        type="submit"
        disabled={loading}
        className="w-full rounded-lg py-2.5 px-4 text-sm font-semibold
          bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
          text-white transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
      >
        {loading ? 'Submitting…' : '⚡ Provision Database'}
      </button>
    </form>
  );
}

// ─────────────────────────── Queue Panel ─────────────────────────────────────
function QueuePanel({ jobs, selectedJob, onSelect }) {
  const sections = [
    { key: 'running',   label: 'Active',    icon: '⚡' },
    { key: 'pending',   label: 'Pending',   icon: '⏳' },
    { key: 'completed', label: 'Completed', icon: '✔' },
    { key: 'failed',    label: 'Failed',    icon: '✘' },
  ];

  return (
    <div className="space-y-5">
      {sections.map(({ key, label, icon }) => {
        const group = jobs.filter((j) => j.status === key);
        if (group.length === 0) return null;
        return (
          <div key={key}>
            <p className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-2">
              {icon} {label} ({group.length})
            </p>
            <div className="space-y-2">
              {group.map((j) => (
                <JobCard
                  key={j.job_id}
                  job={j}
                  onSelect={onSelect}
                  isSelected={selectedJob?.job_id === j.job_id}
                />
              ))}
            </div>
          </div>
        );
      })}
      {jobs.length === 0 && (
        <p className="text-xs text-gray-600 italic text-center py-4">No jobs yet.</p>
      )}
    </div>
  );
}

// ─────────────────────────── Health Indicator ────────────────────────────────
function HealthDot({ healthy }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-2 h-2 rounded-full ${healthy === null ? 'bg-gray-600' : healthy ? 'bg-emerald-400 animate-pulse' : 'bg-red-500'}`} />
      <span className="text-xs text-gray-500">
        {healthy === null ? 'Checking…' : healthy ? 'oracle-exadata-dev · connected' : 'Container unreachable'}
      </span>
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
  const eventSourceRef = useRef(null);

  // ── health check ──
  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/health`);
        const d = await r.json();
        setHealthy(d.reachable);
      } catch { setHealthy(false); }
    };
    check();
    const t = setInterval(check, 15_000);
    return () => clearInterval(t);
  }, []);

  // ── poll job queue ──
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/jobs`, { headers: authHeaders });
        const d = await r.json();
        const all = [
          ...(d.running   || []),
          ...(d.pending   || []),
          ...(d.completed || []),
          ...(d.failed    || []),
        ];
        setJobs(all);
        if (selectedJob) {
          const updated = all.find((j) => j.job_id === selectedJob.job_id);
          if (updated && ['completed','failed'].includes(updated.status)) {
            setStreamLogs(updated.logs || []);
            setSelectedJob(updated);
          }
        }
      } catch {}
    };
    poll();
    const t = setInterval(poll, 2_000);
    return () => clearInterval(t);
  }, [selectedJob]);

  // ── SSE stream ──
  const startStream = useCallback((jobId) => {
    if (eventSourceRef.current) eventSourceRef.current.close();
    setStreamLogs([]);
    const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`);
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

  const handleSelect = useCallback((job) => {
    setSelectedJob(job);
    if (job.status === 'running') {
      startStream(job.job_id);
    } else {
      setStreamLogs(job.logs || []);
      if (eventSourceRef.current) { eventSourceRef.current.close(); }
    }
  }, [startStream]);

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const handleSubmit = async (form) => {
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/provision`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify(form),
      });
      const d = await r.json();
      if (!r.ok) {
        const errs = d.detail?.validation_errors || [d.detail || 'Unknown error'];
        showToast(errs.join(' · '), 'error');
        return;
      }
      showToast(`Job ${d.job_id.slice(0, 8)}… enqueued.`, 'success');
      startStream(d.job_id);
      setSelectedJob({ job_id: d.job_id, db_name: form.db_name, target_cluster_id: form.target_cluster_id, status: 'pending', logs: [] });
    } catch (e) {
      showToast(`Network error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0e1a] text-white font-sans">

      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 rounded-xl px-4 py-3 text-sm font-medium shadow-xl
          backdrop-blur border transition-all duration-300
          ${toast.type === 'error'   ? 'bg-red-950/80 border-red-500/40 text-red-300' :
            toast.type === 'success' ? 'bg-emerald-950/80 border-emerald-500/40 text-emerald-300' :
            'bg-slate-800/90 border-white/10 text-gray-200'}`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <header className="border-b border-white/6 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-violet-600 flex items-center justify-center text-xs font-bold">
            ORA
          </div>
          <div>
            <h1 className="text-sm font-bold text-white tracking-tight">Oracle DB Provisioning Agent</h1>
            <p className="text-xs text-gray-500">Autonomous Exadata Topology Orchestration</p>
          </div>
        </div>
        <HealthDot healthy={healthy} />
      </header>

      {/* Main layout */}
      <div className="grid grid-cols-[320px_1fr_280px] gap-0 h-[calc(100vh-65px)]">

        {/* ── Left: Provision Form ── */}
        <aside className="border-r border-white/6 p-5 overflow-y-auto">
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-4">New Provision Request</p>
          <ProvisionForm onSubmit={handleSubmit} loading={loading} />
        </aside>

        {/* ── Center: Log Viewer ── */}
        <main className="p-5 overflow-hidden flex flex-col">
          {selectedJob && (
            <div className="mb-3 flex items-center gap-3">
              <span className="font-mono text-sm font-semibold text-white">{selectedJob.db_name}</span>
              <StatusBadge status={selectedJob.status} />
              <span className="text-xs text-gray-600 font-mono">{selectedJob.job_id?.slice(0,8)}…</span>
              <span className="text-xs text-blue-400 font-mono">[{selectedJob.target_cluster_id}]</span>
            </div>
          )}
          <div className="flex-1 overflow-hidden">
            <LogViewer
              logs={streamLogs}
              status={selectedJob?.status}
            />
          </div>
        </main>

        {/* ── Right: Queue ── */}
        <aside className="border-l border-white/6 p-5 overflow-y-auto">
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-4">Build Queue</p>
          <QueuePanel jobs={jobs} selectedJob={selectedJob} onSelect={handleSelect} />
        </aside>
      </div>
    </div>
  );
}
