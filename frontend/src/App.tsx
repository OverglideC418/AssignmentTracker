import { FormEvent, useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from './api'
import { cacheSnapshot, clearOperations, queueOperation, readOperations, readSnapshot } from './offline'
import type { Assignment, Preview, Source } from './types'

type Theme = 'vscode-dark' | 'soft-light' | 'blue-gray' | 'colored-dark'
function newId() {
  const browserCrypto = globalThis.crypto
  if (typeof browserCrypto?.randomUUID === 'function') return browserCrypto.randomUUID()
  if (typeof browserCrypto?.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    browserCrypto.getRandomValues(bytes)
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

const deviceId = (() => {
  const key = 'unysync-device-id'
  const existing = localStorage.getItem(key)
  if (existing) return existing
  const created = newId()
  localStorage.setItem(key, created)
  return created
})()

function formatDate(value: string, withYear = false, timezone?: string) {
  const date = value.length === 10 ? new Date(`${value}T12:00:00`) : new Date(value)
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', ...(withYear ? { year: 'numeric' } : {}), ...(timezone ? { timeZone: timezone } : {}) }).format(date)
}

function formatDue(item: Assignment, timezone: string) {
  const start = formatDate(item.start_at, item.start_at.slice(0, 4) !== new Date().getFullYear().toString(), timezone)
  const end = formatDate(item.due_at, item.due_at.slice(0, 4) !== new Date().getFullYear().toString(), timezone)
  const range = start !== end ? `${start} – ${end}` : start
  if (item.all_day) return range
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZone: timezone }).format(new Date(item.due_at))
  return `${range} · due ${time}`
}

function dateOnly(value: string, timezone: string) {
  if (value.length === 10) return value
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  const get = (type: string) => parts.find((part) => part.type === type)?.value || ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

function weekEnd(startDay: number, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(new Date())
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value)
  const today = new Date(Date.UTC(get('year'), get('month') - 1, get('day')))
  const day = today.getUTCDay()
  const offset = (day - startDay + 7) % 7
  const start = new Date(today)
  start.setUTCDate(today.getUTCDate() - offset)
  const end = new Date(start)
  end.setUTCDate(start.getUTCDate() + 6)
  return { today, end }
}

function Login({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    try { await api(setup ? '/api/setup' : '/api/login', { method: 'POST', body: JSON.stringify({ password }) }); onDone() }
    catch (err) { setError((err as Error).message) }
  }
  return <main className="auth-page"><section className="auth-card"><div className="brand-mark">U</div><p className="eyebrow">PRIVATE ASSIGNMENT TRACKER</p><h1>{setup ? 'Create your UniSync password' : 'Welcome back'}</h1><p className="muted">{setup ? 'This account stays on your Raspberry Pi.' : 'Sign in to continue.'}</p><form onSubmit={submit}><label>Password<input type="password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>{error && <p className="error">{error}</p>}<button className="primary" type="submit">{setup ? 'Create account' : 'Sign in'}</button></form></section></main>
}

function AssignmentCard({ item, onComplete, timezone, onEdit, onDelete }: { item: Assignment; onComplete: (item: Assignment) => void; timezone: string; onEdit?: (item: Assignment) => void; onDelete?: (item: Assignment) => void }) {
  return <article className={`assignment-card ${item.completed ? 'is-complete' : ''}`} style={{ '--accent': item.source_color } as CSSProperties}><button className="check" aria-label={item.completed ? `Mark ${item.title} incomplete` : `Mark ${item.title} complete`} onClick={() => onComplete(item)}>{item.completed ? '✓' : ''}</button><div className="assignment-content"><div className="card-title-row"><h3>{item.title}</h3>{item.kind === 'custom' && <span className="card-actions"><button onClick={() => onEdit?.(item)}>Edit</button><button className="danger-text" onClick={() => onDelete?.(item)}>Delete</button></span>}</div><p className="due">{formatDue(item, timezone)}</p><p className="source"><span className="dot" />{item.source_name}</p>{item.description && <p className="description">{item.description}</p>}</div></article>
}

function TaskModal({ existing, onClose, onSaved }: { existing?: Assignment | null; onClose: () => void; onSaved: (task: Assignment) => void }) {
  const [title, setTitle] = useState(existing?.title || ''); const [due, setDue] = useState(existing?.due_at || ''); const [notes, setNotes] = useState(existing?.description || ''); const [error, setError] = useState('')
  async function submit(event: FormEvent) { event.preventDefault(); try { const task = await api<Assignment>(existing ? `/api/custom-tasks/${existing.id}` : '/api/custom-tasks', { method: existing ? 'PATCH' : 'POST', body: JSON.stringify({ title, description: notes, start_at: due, due_at: due, all_day: !due.includes('T') }) }); onSaved(task); onClose() } catch (err) { setError((err as Error).message) } }
  return <div className="modal-backdrop"><section className="modal"><div className="modal-header"><div><p className="eyebrow">{existing ? 'EDIT ITEM' : 'NEW ITEM'}</p><h2>Custom task</h2></div><button className="icon-button" onClick={onClose} aria-label="Close">×</button></div><form onSubmit={submit}><label>Task name<input autoFocus required value={title} onChange={(e) => setTitle(e.target.value)} /></label><label>Due date and time<input required type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></label><label>Notes <span className="muted">(optional)</span><textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>{error && <p className="error">{error}</p>}<div className="form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" type="submit">{existing ? 'Save changes' : 'Add task'}</button></div></form></section></div>
}

function Settings({ sources, preferences, onClose, onRefresh }: { sources: Source[]; preferences: Record<string, unknown>; onClose: () => void; onRefresh: () => Promise<void> }) {
  const [name, setName] = useState(''); const [url, setUrl] = useState(''); const [color, setColor] = useState('#4fc1e9'); const [include, setInclude] = useState(''); const [exclude, setExclude] = useState(''); const [message, setMessage] = useState(''); const [preview, setPreview] = useState<Preview | null>(null); const [previewSourceId, setPreviewSourceId] = useState<number | null>(null)
  async function addSource(event: FormEvent) { event.preventDefault(); setMessage(''); try { const source = await api<Source>('/api/sources', { method: 'POST', body: JSON.stringify({ name, url, color, filter_rules: { include: include.split(',').map((v) => v.trim()).filter(Boolean), exclude: exclude.split(',').map((v) => v.trim()).filter(Boolean) } }) }); const result = await api<Preview>(`/api/sources/${source.id}/preview`, { method: 'POST' }); setPreviewSourceId(source.id); setPreview(result); setMessage(`Added ${source.name}. Review ${result.review.length} uncertain events, then sync.`); await onRefresh() } catch (err) { setMessage((err as Error).message) } }
  async function sourceAction(id: number, action: 'sync' | 'delete' | 'preview' | 'color', value?: string) { try { if (action === 'delete') await api(`/api/sources/${id}`, { method: 'DELETE' }); if (action === 'sync') await api(`/api/sources/${id}/sync`, { method: 'POST' }); if (action === 'preview') { setPreviewSourceId(id); setPreview(await api<Preview>(`/api/sources/${id}/preview`, { method: 'POST' })) }; if (action === 'color') await api(`/api/sources/${id}`, { method: 'PATCH', body: JSON.stringify({ color: value }) }); await onRefresh() } catch (err) { setMessage((err as Error).message) } }
  async function reviewEvent(uid: string, decision: 'include' | 'exclude') { if (!previewSourceId) return; const source = sources.find((entry) => entry.id === previewSourceId); if (!source) return; const overrides = { ...(source.filter_rules.overrides || {}), [uid]: decision }; try { await api(`/api/sources/${previewSourceId}`, { method: 'PATCH', body: JSON.stringify({ filter_rules: { ...source.filter_rules, overrides } }) }); setPreview(await api<Preview>(`/api/sources/${previewSourceId}/preview`, { method: 'POST' })); await onRefresh() } catch (err) { setMessage((err as Error).message) } }
  async function savePreference(key: string, value: unknown) { await api('/api/preferences', { method: 'PUT', body: JSON.stringify({ [key]: value }) }); }
  return <div className="modal-backdrop"><section className="modal settings-modal"><div className="modal-header"><div><p className="eyebrow">CONTROL CENTER</p><h2>Settings</h2></div><button className="icon-button" onClick={onClose} aria-label="Close">×</button></div><div className="settings-grid"><div><h3>Appearance</h3><label>Theme<select value={String(preferences.theme || 'vscode-dark')} onChange={(e) => savePreference('theme', e.target.value).then(onRefresh)}><option value="vscode-dark">VSCode Dark (default)</option><option value="soft-light">Soft Light</option><option value="blue-gray">Blue Gray Dark</option><option value="colored-dark">Colored Dark</option></select></label><label>Week starts on<select value={String(preferences.week_start ?? 1)} onChange={(e) => savePreference('week_start', Number(e.target.value)).then(onRefresh)}><option value="1">Monday</option><option value="0">Sunday</option></select></label><label>Timezone<input value={String(preferences.timezone || 'America/Denver')} onChange={(e) => savePreference('timezone', e.target.value)} onBlur={onRefresh} /></label></div><div><h3>Calendar sources</h3>{sources.length === 0 && <p className="muted">No calendars added yet.</p>}{sources.map((source) => <div className="source-row" key={source.id}><input className="source-color" type="color" value={source.color} onChange={(e) => sourceAction(source.id, 'color', e.target.value)} aria-label={`Color for ${source.name}`} /><div><strong>{source.name}</strong><small>{source.last_error ? `Error: ${source.last_error}` : source.last_success ? `Synced ${formatDate(source.last_success)}` : 'Not synced yet'}</small></div><button onClick={() => sourceAction(source.id, 'preview')}>Review</button><button onClick={() => sourceAction(source.id, 'sync')}>Sync</button><button className="danger-text" onClick={() => sourceAction(source.id, 'delete')}>Hide</button></div>)}<form className="source-form" onSubmit={addSource}><h3>Add calendar</h3><label>Class name<input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Engineering Mechanics" /></label><label>Private iCal URL<input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." /></label><div className="two-col"><label>Color<input type="color" value={color} onChange={(e) => setColor(e.target.value)} /></label><label>Include regex <span className="muted">comma-separated</span><input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="optional" /></label></div><label>Exclude regex <span className="muted">comma-separated</span><input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="optional" /></label><button className="primary" type="submit">Add and preview</button></form></div></div>{message && <p className="notice">{message}</p>}{preview && <div className="preview"><h3>Import preview</h3><p><span className="pill included">{preview.include.length} included</span><span className="pill review">{preview.review.length} review</span><span className="pill excluded">{preview.exclude.length} excluded</span></p>{preview.review.slice(0, 12).map((item) => <div className="preview-row" key={item.uid}><span>{item.title}</span><span><button onClick={() => reviewEvent(item.uid || '', 'include')}>Include</button> <button onClick={() => reviewEvent(item.uid || '', 'exclude')}>Exclude</button></span></div>)}</div>}</section></div>
}

export default function App() {
  const [setup, setSetup] = useState<boolean | null>(null); const [authenticated, setAuthenticated] = useState(false); const [items, setItems] = useState<Assignment[]>([]); const [sources, setSources] = useState<Source[]>([]); const [preferences, setPreferences] = useState<Record<string, unknown>>({}); const [settings, setSettings] = useState(false); const [taskModal, setTaskModal] = useState(false); const [editingTask, setEditingTask] = useState<Assignment | null>(null); const [view, setView] = useState<'list' | 'calendar'>('list'); const [online, setOnline] = useState(navigator.onLine); const [notice, setNotice] = useState('')
  useEffect(() => { document.documentElement.dataset.theme = String(preferences.theme || 'vscode-dark') }, [preferences.theme])
  async function load() { try { const state = await api<{ setup_required: boolean }>('/api/status'); setSetup(state.setup_required); if (state.setup_required) return; await api('/api/preferences'); setAuthenticated(true); const [nextItems, nextSources, prefs] = await Promise.all([api<Assignment[]>('/api/assignments'), api<Source[]>('/api/sources'), api<Record<string, unknown>>('/api/preferences')]); setItems(nextItems); setSources(nextSources); setPreferences(prefs); await cacheSnapshot({ items: nextItems, sources: nextSources, preferences: prefs }) } catch (err) { const snapshot = await readSnapshot<{ items: Assignment[]; sources: Source[]; preferences: Record<string, unknown> }>().catch(() => undefined); if (snapshot) { setSetup(false); setAuthenticated(true); setItems(snapshot.items); setSources(snapshot.sources); setPreferences(snapshot.preferences); setNotice('Offline mode: showing the last saved data.') } else if ((err as Error).message.includes('Sign in')) setAuthenticated(false); else setNotice((err as Error).message) } }
  useEffect(() => { load(); const onlineHandler = () => { setOnline(true); flushQueue() }; const offlineHandler = () => setOnline(false); window.addEventListener('online', onlineHandler); window.addEventListener('offline', offlineHandler); return () => { window.removeEventListener('online', onlineHandler); window.removeEventListener('offline', offlineHandler) } }, [])
  async function flushQueue() { if (!navigator.onLine) return; const operations = await readOperations(); if (!operations.length) return; try { await api('/api/sync', { method: 'POST', body: JSON.stringify({ operations }) }); await clearOperations(operations.map((operation) => operation.operation_id)); setNotice('Offline changes synced.') ; await load() } catch { setNotice('Could not sync queued changes yet.') } }
  async function complete(item: Assignment) { const completed = !item.completed; setItems((old) => old.map((entry) => entry.id === item.id && entry.kind === item.kind ? { ...entry, completed } : entry)); const changed = new Date().toISOString(); const operation = { operation_id: newId(), entity: item.kind === 'custom' ? 'custom_task' : 'assignment', entity_id: item.id, action: 'complete', payload: { completed }, client_changed_at: changed, device_id: deviceId } as const; if (!navigator.onLine) return queueOperation(operation); try { await api(item.kind === 'custom' ? `/api/custom-tasks/${item.id}/completion` : `/api/assignments/${item.id}/completion`, { method: 'PATCH', body: JSON.stringify({ completed, client_changed_at: changed, device_id: deviceId }) }); } catch { await queueOperation(operation); setNotice('Saved locally; will sync when you reconnect.') } }
  const timezone = String(preferences.timezone || 'America/Denver')
  const grouped = useMemo(() => { const { today, end } = weekEnd(Number(preferences.week_start ?? 1), timezone); const todayKey = today.toISOString().slice(0, 10); const endKey = end.toISOString().slice(0, 10); const active = items.filter((item) => !item.completed); return { overdue: active.filter((item) => dateOnly(item.due_at, timezone) < todayKey), week: active.filter((item) => dateOnly(item.due_at, timezone) >= todayKey && dateOnly(item.due_at, timezone) <= endKey), later: active.filter((item) => dateOnly(item.due_at, timezone) > endKey), completed: items.filter((item) => item.completed) } }, [items, preferences.week_start, timezone])
  if (setup === null) return <main className="auth-page"><section className="auth-card"><div className="brand-mark">U</div><p className="eyebrow">PRIVATE ASSIGNMENT TRACKER</p><h1>Loading UniSync</h1><p className="muted">Connecting to your private server…</p></section></main>
  if (!authenticated) return <Login setup={setup} onDone={() => { setAuthenticated(true); load() }} />
  async function deleteTask(item: Assignment) { if (!window.confirm(`Delete “${item.title}”?`)) return; try { await api(`/api/custom-tasks/${item.id}`, { method: 'DELETE' }); setItems((old) => old.filter((entry) => !(entry.kind === 'custom' && entry.id === item.id))) } catch (err) { setNotice((err as Error).message) } }
  function saveTask(task: Assignment) { setItems((old) => editingTask ? old.map((entry) => entry.kind === 'custom' && entry.id === task.id ? task : entry) : [...old, task]); setEditingTask(null) }
  const cardProps = { onComplete: complete, timezone, onEdit: (item: Assignment) => { setEditingTask(item); setTaskModal(false) }, onDelete: deleteTask }
  return <div className="app-shell"><header className="topbar"><div className="brand"><div className="brand-mark">U</div><div><strong>UniSync</strong><span>your academic flow</span></div></div><div className="top-actions"><span className={`connection ${online ? 'online' : 'offline'}`}><i />{online ? 'Connected' : 'Offline'}</span><button className="icon-button" onClick={() => setSettings(true)} aria-label="Open settings">⚙</button></div></header><main className="content"><section className="hero"><div><p className="eyebrow">OVERVIEW</p><h1>Your assignments</h1><p className="muted">{grouped.week.length} due this week · {grouped.overdue.length} overdue</p></div><div className="hero-actions"><div className="view-toggle"><button className={view === 'list' ? 'active' : ''} onClick={() => setView('list')}>List</button><button className={view === 'calendar' ? 'active' : ''} onClick={() => setView('calendar')}>Calendar</button></div><button className="primary" onClick={() => { setEditingTask(null); setTaskModal(true) }}>＋ Custom task</button></div></section>{notice && <div className="notice">{notice}<button onClick={() => setNotice('')}>×</button></div>}{view === 'calendar' ? <Calendar items={items} timezone={timezone} /> : <div className="assignment-groups"><Group title="Overdue" items={grouped.overdue} {...cardProps} tone="overdue" /><Group title="This week" items={grouped.week} {...cardProps} /><Group title="Later" items={grouped.later} {...cardProps} /><details className="completed-group"><summary>Completed <span>{grouped.completed.length}</span></summary><div>{grouped.completed.map((item) => <AssignmentCard key={`${item.kind}-${item.id}`} item={item} {...cardProps} />)}</div></details>{!items.length && <div className="empty"><div>✦</div><h2>Your workspace is clear</h2><p>Add a calendar source or create a custom task to get started.</p></div>}</div>}</main>{settings && <Settings sources={sources} preferences={preferences} onClose={() => setSettings(false)} onRefresh={load} />}{taskModal && <TaskModal existing={editingTask} onClose={() => { setTaskModal(false); setEditingTask(null) }} onSaved={saveTask} />}{editingTask && !taskModal && <TaskModal existing={editingTask} onClose={() => { setEditingTask(null) }} onSaved={saveTask} />}</div>
}

function Group({ title, items, onComplete, timezone, onEdit, onDelete, tone = '' }: { title: string; items: Assignment[]; onComplete: (item: Assignment) => void; timezone: string; onEdit?: (item: Assignment) => void; onDelete?: (item: Assignment) => void; tone?: string }) { return <section className={`group ${tone}`}><div className="group-heading"><h2>{title}</h2><span>{items.length}</span></div>{items.length ? items.map((item) => <AssignmentCard key={`${item.kind}-${item.id}`} item={item} onComplete={onComplete} timezone={timezone} onEdit={onEdit} onDelete={onDelete} />) : <p className="group-empty">Nothing here.</p>}</section> }

function Calendar({ items, timezone }: { items: Assignment[]; timezone: string }) { const days = [...new Set(items.map((item) => dateOnly(item.due_at, timezone)))].sort(); return <section className="calendar-view"><div className="calendar-intro"><p className="eyebrow">OPTIONAL VIEW</p><h2>Due-date calendar</h2><p className="muted">Your assignments grouped by their real due date.</p></div>{days.map((day) => <div className="calendar-day" key={day}><time>{formatDate(day, true, timezone)}</time><div>{items.filter((item) => dateOnly(item.due_at, timezone) === day).map((item) => <div className="calendar-item" key={`${item.kind}-${item.id}`}><span className="dot" style={{ background: item.source_color }} />{item.title}<small>{item.source_name}</small></div>)}</div></div>)}</section> }
