import React, { useEffect, useMemo, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { invoke } from '@tauri-apps/api/core';
import {
  Activity,
  Clock3,
  FolderSync,
  History,
  Pause,
  Play,
  Power,
  RefreshCw,
  Settings,
  TerminalSquare,
} from 'lucide-react';
import './styles.css';

type SyncSettings = {
  enabled: boolean;
  interval_seconds: number;
  providers: string[];
  codex_home: string;
  log_file: string;
};

type SyncReport = {
  files_scanned: number;
  source_sessions: number;
  mirror_needed: number;
  mirror_created: number;
  mirror_existing: number;
  mirror_stale: number;
  mirror_refreshed: number;
  mirror_conflicts: number;
  index_needed: number;
  index_added: number;
  index_stale: number;
  index_updated: number;
  provider_counts: Record<string, number>;
  last_run_at?: string;
  last_error?: string;
};

const emptyReport: SyncReport = {
  files_scanned: 0,
  source_sessions: 0,
  mirror_needed: 0,
  mirror_created: 0,
  mirror_existing: 0,
  mirror_stale: 0,
  mirror_refreshed: 0,
  mirror_conflicts: 0,
  index_needed: 0,
  index_added: 0,
  index_stale: 0,
  index_updated: 0,
  provider_counts: {},
};

function providerTone(index: number) {
  const tones = ['text-emerald-600 bg-emerald-50', 'text-sky-600 bg-sky-50', 'text-violet-600 bg-violet-50', 'text-rose-600 bg-rose-50'];
  return tones[index % tones.length];
}

function shortProviderName(provider: string) {
  return provider.trim().slice(0, 1).toUpperCase() || 'P';
}

function App() {
  const [settings, setSettings] = useState<SyncSettings | null>(null);
  const [report, setReport] = useState<SyncReport>(emptyReport);
  const [busy, setBusy] = useState(false);
  const [autostart, setAutostart] = useState(false);
  const [intervalDraft, setIntervalDraft] = useState('300');
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const [nextSettings, nextReport] = await Promise.all([
      invoke<SyncSettings>('get_settings'),
      invoke<SyncReport | null>('get_last_report'),
    ]);
    const nextAutostart = await invoke<boolean>('get_autostart_enabled');
    setSettings(nextSettings);
    setAutostart(nextAutostart);
    setIntervalDraft(String(nextSettings.interval_seconds));
    setReport(nextReport ?? emptyReport);
  }

  useEffect(() => {
    refresh().catch((err) => setError(String(err)));
    const id = window.setInterval(() => {
      refresh().catch((err) => setError(String(err)));
    }, 3000);
    return () => window.clearInterval(id);
  }, []);

  const providerRows = useMemo(() => {
    if (!settings) return [];
    return settings.providers.map((provider, index) => ({
      provider,
      count: report.provider_counts[provider] ?? 0,
      tone: providerTone(index),
      primary: index === 0,
    }));
  }, [settings, report.provider_counts]);

  async function toggleSync() {
    if (!settings) return;
    setBusy(true);
    setError(null);
    try {
      await invoke('set_enabled', { enabled: !settings.enabled });
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveInterval() {
    const interval = Number(intervalDraft);
    if (!Number.isInteger(interval) || interval < 30) {
      setError('同步间隔必须是大于等于 30 的整数秒。');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await invoke('set_interval', { intervalSeconds: interval });
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function syncNow() {
    setBusy(true);
    setError(null);
    try {
      const nextReport = await invoke<SyncReport>('sync_now');
      setReport(nextReport);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleAutostart() {
    setBusy(true);
    setError(null);
    try {
      const enabled = await invoke<boolean>('set_autostart', { enabled: !autostart });
      setAutostart(enabled);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function openLog() {
    try {
      await invoke('open_log');
    } catch (err) {
      setError(String(err));
    }
  }

  async function exitApp() {
    await invoke('exit_app');
  }

  if (!settings) {
    return <div className="flex h-screen items-center justify-center text-slate-500">Loading...</div>;
  }

  return (
    <main className="min-h-screen bg-[#fbfbfc] px-6 py-5 text-slate-950">
      <header className="flex items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-xl bg-orange-50 text-orange-600">
            <FolderSync size={20} />
          </div>
          <h1 className="text-xl font-semibold text-blue-600">Codex Sync</h1>
        </div>

        <button
          className={`ml-4 flex h-9 items-center gap-2 rounded-full px-3 text-sm transition ${
            settings.enabled ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'
          }`}
          disabled={busy}
          onClick={toggleSync}
        >
          {settings.enabled ? <Play size={15} /> : <Pause size={15} />}
          {settings.enabled ? '自动同步' : '已暂停'}
        </button>

        <nav className="mx-auto flex rounded-2xl bg-white p-1 shadow-sm ring-1 ring-slate-100">
          {['聚合', 'Provider', '日志', '设置'].map((item, index) => (
            <span
              className={`rounded-xl px-5 py-2 text-sm ${index === 0 ? 'bg-slate-50 font-semibold text-slate-950' : 'text-slate-500'}`}
              key={item}
            >
              {item}
            </span>
          ))}
        </nav>

        <div className="flex rounded-2xl bg-white p-1 shadow-sm ring-1 ring-slate-100">
          <button className="icon-button" onClick={toggleAutostart} disabled={busy} title={autostart ? '关闭开机自启' : '开启开机自启'}>
            <Settings size={17} className={autostart ? 'text-blue-600' : undefined} />
          </button>
          <button className="icon-button" onClick={syncNow} disabled={busy} title="立即同步">
            <RefreshCw size={17} />
          </button>
          <button className="icon-button" onClick={openLog} title="打开日志">
            <TerminalSquare size={17} />
          </button>
          <button className="icon-button" onClick={exitApp} title="退出">
            <Power size={17} />
          </button>
        </div>
      </header>

      {error && <section className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</section>}

      <section className="mt-8 grid grid-cols-3 gap-4">
        <Metric icon={<Activity size={18} />} label="源会话" value={String(report.source_sessions)} />
        <Metric icon={<RefreshCw size={18} />} label="刷新镜像" value={String(report.mirror_refreshed)} />
        <Metric icon={<History size={18} />} label="上次同步" value={report.last_run_at ?? '未运行'} />
      </section>

      <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold">同步配置</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-500">{settings.codex_home}</p>
          </div>
          <div className="flex items-center gap-2 rounded-2xl bg-slate-50 p-1">
            <Clock3 className="ml-3 text-slate-500" size={16} />
            <input
              className="h-9 w-24 bg-transparent text-center text-sm font-semibold outline-none"
              value={intervalDraft}
              onChange={(event) => setIntervalDraft(event.target.value)}
            />
            <span className="pr-1 text-sm text-slate-500">秒</span>
            <button className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-blue-600 shadow-sm" onClick={saveInterval} disabled={busy}>
              保存
            </button>
          </div>
        </div>
      </section>

      <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">Provider 聚合列表</h2>
          <span className="text-sm text-slate-500">{providerRows.length} 个 provider</span>
        </div>
        <div className="space-y-3">
          {providerRows.map((row) => (
            <article
              className={`flex items-center rounded-2xl border p-4 ${row.primary ? 'border-blue-300 bg-blue-50/60' : 'border-slate-200 bg-white'}`}
              key={row.provider}
            >
              <span className="mr-4 cursor-grab text-slate-300">⋮⋮</span>
              <span className={`mr-4 flex size-9 items-center justify-center rounded-xl text-sm font-semibold ${row.tone}`}>
                {shortProviderName(row.provider)}
              </span>
              <div>
                <h3 className="font-semibold">{row.provider}</h3>
                <p className="text-sm text-blue-600">{row.count} 条会话记录</p>
              </div>
              <span className="ml-auto rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500 shadow-sm">
                {row.primary ? '优先源' : '自动聚合'}
              </span>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-4 flex size-9 items-center justify-center rounded-xl bg-slate-50 text-slate-600">{icon}</div>
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </section>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
