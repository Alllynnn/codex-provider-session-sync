import React, { useEffect, useMemo, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { invoke } from '@tauri-apps/api/core';
import {
  Activity,
  Clock3,
  FolderSync,
  History,
  RotateCcw,
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
  session_groups: number;
  mirrored_sessions: number;
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
  backup_snapshot_id?: string;
  backup_snapshot_path?: string;
};

type BackupSnapshot = {
  id: string;
  created_at: string;
  codex_home: string;
  path: string;
  file_count: number;
  total_bytes: number;
  reason: string;
};

const emptyReport: SyncReport = {
  files_scanned: 0,
  source_sessions: 0,
  session_groups: 0,
  mirrored_sessions: 0,
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

function formatDateTime(value?: string) {
  if (!value) return '未运行';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function App() {
  const [activeTab, setActiveTab] = useState<'聚合' | 'Provider' | '日志' | '设置'>('聚合');
  const [settings, setSettings] = useState<SyncSettings | null>(null);
  const [report, setReport] = useState<SyncReport>(emptyReport);
  const [busy, setBusy] = useState(false);
  const [autostart, setAutostart] = useState(false);
  const [snapshots, setSnapshots] = useState<BackupSnapshot[]>([]);
  const [logText, setLogText] = useState('');
  const [intervalDraft, setIntervalDraft] = useState('300');
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const [nextSettings, nextReport, nextSnapshots, nextLogText] = await Promise.all([
      invoke<SyncSettings>('get_settings'),
      invoke<SyncReport | null>('get_last_report'),
      invoke<BackupSnapshot[]>('get_backup_snapshots'),
      invoke<string>('get_log_tail'),
    ]);
    const nextAutostart = await invoke<boolean>('get_autostart_enabled');
    setSettings(nextSettings);
    setAutostart(nextAutostart);
    setIntervalDraft(String(nextSettings.interval_seconds));
    setReport(nextReport ?? emptyReport);
    setSnapshots(nextSnapshots);
    setLogText(nextLogText);
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

  async function createBackup() {
    setBusy(true);
    setError(null);
    try {
      await invoke<BackupSnapshot>('create_backup_now');
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function pruneBackups() {
    const confirmed = window.confirm('确定清理旧备份吗？会保留最近 24 个自动同步备份、10 个手动备份、10 个恢复前备份。');
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const removed = await invoke<number>('prune_backups');
      await refresh();
      setError(`已清理 ${removed} 个旧备份。`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function restoreBackup(snapshot: BackupSnapshot) {
    const confirmed = window.confirm(`确定恢复到备份 ${snapshot.id} 吗？这会覆盖当前 Codex 会话目录。`);
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await invoke('restore_backup', { snapshotId: snapshot.id });
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
      setActiveTab('日志');
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function openBackupDir() {
    try {
      await invoke('open_backup_dir');
    } catch (err) {
      setError(String(err));
    }
  }

  async function exitApp() {
    if (window.confirm('确定退出同步程序吗？')) {
      await invoke('exit_app');
    }
  }

  if (!settings) {
    return <div className="flex h-screen items-center justify-center text-slate-500">Loading...</div>;
  }

  return (
    <main className="min-h-screen bg-[#fbfbfc] px-6 py-5 text-slate-950">
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <FolderSync size={20} />
          </div>
          <h1>Codex Sync</h1>
        </div>

        <button
          className={`sync-toggle ${settings.enabled ? 'is-on' : 'is-off'}`}
          disabled={busy}
          onClick={toggleSync}
        >
          {settings.enabled ? <Play size={15} /> : <Pause size={15} />}
          <span>{settings.enabled ? '自动同步' : '已暂停'}</span>
        </button>

        <nav className="top-nav" aria-label="主导航">
          {(['聚合', 'Provider', '日志', '设置'] as const).map((item) => (
            <button
              className={`nav-button ${activeTab === item ? 'is-active' : ''}`}
              key={item}
              onClick={() => setActiveTab(item)}
            >
              <span className={item === 'Provider' ? 'nav-label-wide' : 'nav-label'}>{item}</span>
            </button>
          ))}
        </nav>

        <div className="top-actions" aria-label="快捷操作">
          <button className="action-button" onClick={() => setActiveTab('设置')} disabled={busy} title="设置" aria-label="设置">
            <Settings size={17} />
          </button>
          <button className="action-button" onClick={syncNow} disabled={busy} title="立即同步" aria-label="立即同步">
            <RefreshCw size={17} />
          </button>
          <button className="action-button" onClick={createBackup} disabled={busy} title="创建备份" aria-label="创建备份">
            <History size={17} />
          </button>
          <button className="action-button" onClick={openLog} title="打开日志" aria-label="打开日志">
            <TerminalSquare size={17} />
          </button>
          <button className="action-button" onClick={exitApp} title="退出" aria-label="退出">
            <Power size={17} />
          </button>
        </div>
      </header>

      {error && <section className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</section>}

      {activeTab === '聚合' && (
        <>
          <section className="mt-8 grid grid-cols-3 gap-4">
            <Metric icon={<Activity size={18} />} label="会话组" value={String(report.session_groups)} />
            <Metric icon={<RefreshCw size={18} />} label="同步副本" value={String(report.mirrored_sessions)} />
            <Metric icon={<History size={18} />} label="上次同步" value={formatDateTime(report.last_run_at)} />
          </section>
          <SyncConfig settings={settings} intervalDraft={intervalDraft} setIntervalDraft={setIntervalDraft} saveInterval={saveInterval} busy={busy} />
          <BackupPanel snapshots={snapshots} busy={busy} createBackup={createBackup} pruneBackups={pruneBackups} restoreBackup={restoreBackup} openBackupDir={openBackupDir} />
        </>
      )}

      {activeTab === 'Provider' && <ProviderPanel providerRows={providerRows} />}

      {activeTab === '日志' && <LogPanel logText={logText} openLog={openLog} />}

      {activeTab === '设置' && (
        <>
          <SyncConfig settings={settings} intervalDraft={intervalDraft} setIntervalDraft={setIntervalDraft} saveInterval={saveInterval} busy={busy} />
          <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5">
            <h2 className="text-base font-semibold">程序设置</h2>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <button className="large-button" onClick={toggleAutostart} disabled={busy}>
                <Settings size={17} />
                {autostart ? '关闭开机自启' : '开启开机自启'}
              </button>
              <button className="large-button" onClick={openBackupDir}>
                <History size={17} />
                打开备份目录
              </button>
              <button className="large-button" onClick={pruneBackups} disabled={busy}>
                <RotateCcw size={17} />
                清理旧备份
              </button>
              <button className="large-button" onClick={openLog}>
                <TerminalSquare size={17} />
                打开日志文件
              </button>
              <button className="large-button text-red-600" onClick={exitApp}>
                <Power size={17} />
                退出程序
              </button>
            </div>
          </section>
        </>
      )}
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

function SyncConfig({
  settings,
  intervalDraft,
  setIntervalDraft,
  saveInterval,
  busy,
}: {
  settings: SyncSettings;
  intervalDraft: string;
  setIntervalDraft: (value: string) => void;
  saveInterval: () => void;
  busy: boolean;
}) {
  return (
    <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">同步配置</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">{settings.codex_home}</p>
        </div>
        <div className="flex items-center gap-2 rounded-2xl bg-slate-50 p-1">
          <Clock3 className="ml-3 text-slate-500" size={16} />
          <input className="h-9 w-24 bg-transparent text-center text-sm font-semibold outline-none" value={intervalDraft} onChange={(event) => setIntervalDraft(event.target.value)} />
          <span className="pr-1 text-sm text-slate-500">秒</span>
          <button className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-blue-600 shadow-sm" onClick={saveInterval} disabled={busy}>
            保存
          </button>
        </div>
      </div>
    </section>
  );
}

function ProviderPanel({ providerRows }: { providerRows: Array<{ provider: string; count: number; tone: string; primary: boolean }> }) {
  return (
    <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-base font-semibold">Provider 聚合列表</h2>
        <span className="text-sm text-slate-500">{providerRows.length} 个 provider</span>
      </div>
      <div className="space-y-3">
        {providerRows.map((row) => (
          <article className={`flex items-center rounded-2xl border p-4 ${row.primary ? 'border-blue-300 bg-blue-50/60' : 'border-slate-200 bg-white'}`} key={row.provider}>
            <span className="mr-4 cursor-grab text-slate-300">⋮⋮</span>
            <span className={`mr-4 flex size-9 items-center justify-center rounded-xl text-sm font-semibold ${row.tone}`}>{shortProviderName(row.provider)}</span>
            <div>
              <h3 className="font-semibold">{row.provider}</h3>
              <p className="text-sm text-blue-600">{row.count} 条会话记录</p>
            </div>
            <span className="ml-auto rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500 shadow-sm">{row.primary ? '优先源' : '自动聚合'}</span>
          </article>
        ))}
      </div>
    </section>
  );
}

function LogPanel({ logText, openLog }: { logText: string; openLog: () => void }) {
  return (
    <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-base font-semibold">同步日志</h2>
        <button className="rounded-xl bg-slate-50 px-4 py-2 text-sm font-medium text-blue-600" onClick={openLog}>
          打开日志文件
        </button>
      </div>
      <pre className="max-h-[420px] overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-100">{logText || '暂无日志。'}</pre>
    </section>
  );
}

function BackupPanel({
  snapshots,
  busy,
  createBackup,
  pruneBackups,
  restoreBackup,
  openBackupDir,
}: {
  snapshots: BackupSnapshot[];
  busy: boolean;
  createBackup: () => void;
  pruneBackups: () => void;
  restoreBackup: (snapshot: BackupSnapshot) => void;
  openBackupDir: () => void;
}) {
  return (
    <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">备份与恢复</h2>
          <p className="mt-1 text-sm text-slate-500">每次同步前自动创建快照，也可以手动创建。</p>
        </div>
        <div className="flex gap-2">
          <button className="rounded-xl bg-slate-50 px-4 py-2 text-sm font-medium text-blue-600" onClick={openBackupDir}>
            打开目录
          </button>
          <button className="rounded-xl bg-slate-50 px-4 py-2 text-sm font-medium text-blue-600" onClick={pruneBackups} disabled={busy}>
            清理旧备份
          </button>
          <button className="rounded-xl bg-slate-50 px-4 py-2 text-sm font-medium text-blue-600" onClick={createBackup} disabled={busy}>
            创建备份
          </button>
        </div>
      </div>
      <div className="space-y-3">
        {snapshots.slice(0, 4).map((snapshot) => (
          <article className="flex items-center rounded-2xl border border-slate-200 bg-white p-4" key={snapshot.id}>
            <span className="mr-4 flex size-9 items-center justify-center rounded-xl bg-amber-50 text-amber-600">
              <History size={17} />
            </span>
            <div>
              <h3 className="font-semibold">{snapshot.id}</h3>
              <p className="text-sm text-slate-500">
                {snapshot.file_count} 个文件 · {Math.round(snapshot.total_bytes / 1024)} KB · {snapshot.reason}
              </p>
            </div>
            <button className="ml-auto flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-2 text-sm font-medium text-slate-700" disabled={busy} onClick={() => restoreBackup(snapshot)}>
              <RotateCcw size={15} />
              恢复
            </button>
          </article>
        ))}
        {snapshots.length === 0 && <p className="rounded-xl bg-slate-50 px-4 py-4 text-sm text-slate-500">还没有备份快照。</p>}
      </div>
    </section>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
