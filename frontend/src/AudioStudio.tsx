import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, Article, AudioAsset, AudioDetail, JobEvent, ProductionJob, ScriptVersion, Voice } from './api'
import './audio.css'

const terminal = new Set(['succeeded', 'failed', 'needs_attention'])
const message = (error: unknown) => error instanceof ApiError ? error.body.message : error instanceof Error ? error.message : 'The request failed.'
const timestamp = (value: string) => new Date(value).toLocaleString()
const duration = (seconds: number) => `${Math.floor(Math.round(seconds) / 60)}:${String(Math.round(seconds) % 60).padStart(2, '0')}`

export default function AudioStudio() {
  const { scriptId } = useParams()
  const [script, setScript] = useState<ScriptVersion | null>(null)
  const [article, setArticle] = useState<Article | null>(null)
  const [eligible, setEligible] = useState(false)
  const [voices, setVoices] = useState<Voice[]>([])
  const [voiceId, setVoiceId] = useState('')
  const [speed, setSpeed] = useState(1)
  const [job, setJob] = useState<ProductionJob | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [assets, setAssets] = useState<AudioAsset[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, AudioDetail>>({})
  const [revision, setRevision] = useState(0)
  const route = useRef(scriptId)
  route.current = scriptId
  const requestKey = useRef<{ payload: string; key: string } | null>(null)
  const voice = voices.find(item => item.id === voiceId)

  useEffect(() => {
    let active = true
    setScript(null); setArticle(null); setEligible(false); setVoices([]); setVoiceId('')
    setJob(null); setEvents([]); setError(null); setVoiceError(null); setNotice(null)
    setOffset(0); setSearch(''); setQuery(''); setDetails({}); requestKey.current = null
    if (!scriptId) return () => { active = false }
    void (async () => {
      const loaded = await api.script(scriptId)
      const [source, versions, evaluations, approvals, jobs] = await Promise.all([
        api.article(loaded.article_id), api.scripts(loaded.article_id), api.evaluations(scriptId),
        api.approvals(scriptId), api.audioJobs(scriptId),
      ])
      if (!active) return
      setScript(loaded); setArticle(source)
      const latest = evaluations.items[0]
      setEligible(versions.items[0]?.id === scriptId && latest?.status === 'succeeded' && approvals.items.some(item => item.evaluation_id === latest.id))
      const newest = jobs.items[0] ?? null
      setJob(newest)
      if (newest) {
        const timeline = await api.jobEvents(newest.id)
        if (active) setEvents(timeline.items)
      }
    })().catch(caught => { if (active) setError(message(caught)) })
    return () => { active = false }
  }, [scriptId])

  useEffect(() => {
    if (!script) return
    let active = true
    void api.voices(script.target_language).then(result => {
      if (active) { setVoices(result.items); setVoiceId(current => result.items.some(item => item.id === current) ? current : result.items[0]?.id ?? ''); setVoiceError(null) }
    }).catch(caught => { if (active) setVoiceError(message(caught)) })
    return () => { active = false }
  }, [script?.id, revision])

  useEffect(() => {
    let active = true
    setLoading(true)
    void api.audioList({ scriptId, q: query, offset }).then(result => {
      if (active) { setAssets(result.items); setTotal(result.total) }
    }).catch(caught => { if (active) setError(message(caught)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [scriptId, query, offset, revision])

  useEffect(() => {
    if (!job || terminal.has(job.status)) return
    let active = true
    let timer: number
    const poll = async () => {
      try {
        const [next, timeline] = await Promise.all([api.job(job.id), api.jobEvents(job.id)])
        if (!active) return
        setJob(next); setEvents(timeline.items)
        if (terminal.has(next.status)) { setRevision(current => current + 1); return }
      } catch (caught) { if (active) setError(message(caught)) }
      if (active) timer = window.setTimeout(() => void poll(), 2000)
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => { active = false; window.clearTimeout(timer) }
  }, [job?.id, job?.status, scriptId])

  async function generate() {
    if (!script || !voice) return
    const selectedScript = script.id
    const body = { language: script.target_language, voice_id: voice.id, speed }
    const payload = JSON.stringify({ scriptId: script.id, ...body })
    // Retain the key after a transport failure, so a second click replays the request.
    if (requestKey.current?.payload !== payload) requestKey.current = { payload, key: crypto.randomUUID() }
    setBusy(true); setError(null); setNotice(null)
    try {
      const result = await api.createAudio(script.id, body, requestKey.current.key)
      if (route.current !== selectedScript) return
      requestKey.current = null
      setJob(result.job)
      setEvents((await api.jobEvents(result.job.id)).items)
      setNotice(result.reused_existing ? 'Reused an existing task or audio with these settings.' : 'Audio production queued.')
      setRevision(current => current + 1)
    } catch (caught) {
      if (route.current === selectedScript) {
        setError(message(caught))
        if (caught instanceof ApiError) requestKey.current = null
        if (caught instanceof ApiError && caught.body.code === 'APPROVAL_STALE') setEligible(false)
      }
    } finally { setBusy(false) }
  }

  async function remove(id: string) {
    setBusy(true); setError(null)
    try { await api.deleteAudio(id); setDeleteId(null); setRevision(current => current + 1); setNotice('Audio file deleted. Its job and audit history are retained.') }
    catch (caught) { setError(message(caught)); setRevision(current => current + 1) }
    finally { setBusy(false) }
  }

  async function showDetails(id: string) {
    try {
      const result = await api.audio(id)
      setDetails(current => ({ ...current, [id]: result }))
    } catch (caught) { setError(message(caught)) }
  }

  return <div className="app-shell">
    <aside className="rail">
      <Link className="brand-mark brand-link" to="/">E<span>●</span></Link>
      <div className="rail-divider" />
      <Link className="rail-icon" to="/" title="Content Studio" aria-label="Content Studio">▤</Link>
      <Link className="rail-icon active" to="/audio" title="Audio Studio" aria-label="Audio Studio">♫</Link>
      <div className="rail-spacer" /><div className="rail-bottom">04</div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="brand-name">EchoFlow <span>/ Audio Studio</span></div><div className="stage-pill"><span className="stage-dot" />Stage 04 · Audio</div></header>
      <div className="workspace">
        <section className="review-title"><div><p className="eyebrow">FROM APPROVED WORDS TO SOUND</p><h1>Give your story<br /><em>a voice.</em></h1></div><Link className="secondary-button review-link" to="/audio">All audio</Link></section>
        {error && <div className="notice error" role="alert">{error}</div>}
        {notice && <div className="notice success" role="status">{notice}</div>}
        {scriptId && !script && !error && <p className="detail-placeholder">Loading script…</p>}
        {script && article && <section className="card audio-production">
          <div className="audio-heading"><div><p className="section-number">PRODUCTION · SCRIPT V{script.version}</p><h2>{article.title}</h2><p>{article.source} · {script.target_language.toUpperCase()} · {script.text.length.toLocaleString()} characters</p></div><Link className="secondary-button review-link" to={`/review/${script.id}`}>Review script</Link></div>
          {!eligible && <div className="notice error">This script needs approval of its latest successful evaluation and must be the current article version.</div>}
          {voiceError && <div className="notice error">{voiceError} <button type="button" onClick={() => setRevision(current => current + 1)}>Reload voices</button></div>}
          <div className="audio-controls">
            <label className="field">Voice<select value={voiceId} onChange={event => setVoiceId(event.target.value)} disabled={!voices.length}>{!voices.length && <option value="">Loading available voices…</option>}{voices.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label className="field">Language<input value={script.target_language === 'en' ? 'English' : 'German'} readOnly /></label>
            <label className="field">Speed · {speed.toFixed(2)}×<input type="range" min="0.7" max="1.2" step="0.05" value={speed} onChange={event => setSpeed(Number(event.target.value))} /></label>
            <button type="button" className="primary-button" disabled={busy || !eligible || !voice || (!!job && !terminal.has(job.status))} onClick={() => void generate()}>Generate audio</button>
          </div>
          {voice?.provider === 'mock' && <p className="audio-help">Mock mode creates a two-second demonstration tone, not narration. No external service is called.</p>}
          {voice?.provider === 'elevenlabs' && <p className="audio-help">AI-generated speech · ElevenLabs · {script.text.length.toLocaleString()} input characters. Generating audio uses your ElevenLabs credits.</p>}
          {voice?.preview_url && <div className="voice-preview"><span>Voice sample</span><audio controls preload="none" src={voice.preview_url} /></div>}
          <details className="audio-script"><summary>Read the approved script</summary><div className="script-body">{script.text}</div></details>
        </section>}
        {job && <section className={`card job-card audio-job ${job.status}`} aria-live="polite">
          <div className="job-heading"><span className="job-label">LATEST AUDIO JOB</span><strong>{job.status.replaceAll('_', ' ')}</strong></div>
          <p>{job.stage.replaceAll('_', ' ')} · Attempt {job.attempt_count} / {job.max_attempts}</p>
          {job.error_message && <p className="job-error">{job.error_message}</p>}
          {job.status === 'needs_attention' && <p className="audio-help">The previous call may have been billed. Generating again creates a new job and may incur another charge.</p>}
          <ol className="audio-events">{events.map(item => <li key={item.id}><span>{timestamp(item.created_at)}</span>{item.message}</li>)}</ol>
        </section>}
        <section className="card audio-library">
          <div className="audio-heading"><div><p className="section-number">AUDIO LIBRARY</p><h2>{scriptId ? 'This script’s recordings' : 'Your recordings'}</h2></div><span className="count-badge">{total}</span></div>
          <form className="search" onSubmit={event => { event.preventDefault(); setOffset(0); setQuery(search) }}><input aria-label="Search audio by article title" placeholder="Search article titles…" value={search} onChange={event => setSearch(event.target.value)} /><button type="submit">Search</button></form>
          {loading ? <p className="detail-placeholder">Loading audio…</p> : !assets.length && <p className="detail-placeholder">No audio yet. Open an approved script in Script Review and choose Produce audio.</p>}
          <div className="audio-grid">{assets.map(asset => <article className="audio-item" key={asset.id}>
            <div className="audio-heading"><h3>{asset.article_title}</h3><span className="source-tag">V{asset.script_version} · {asset.language.toUpperCase()}</span></div>
            <p>{asset.voice_name} · {asset.speed}× · {duration(asset.duration_seconds)} · {(asset.file_size / 1024).toFixed(1)} KiB</p>
            <p>{asset.provider} / {asset.model}<br />{timestamp(asset.created_at)}</p>
            {asset.provider === 'mock' && <p className="audio-help">Mock demonstration tone</p>}
            {asset.status === 'available' ? <audio aria-label={`Play ${asset.article_title}`} controls preload="none" src={api.audioStream(asset.id)} /> : <p className="notice error">{asset.delete_error ?? 'Deletion is pending. Retry to finish removing this file.'}</p>}
            <div className="audio-actions"><Link to={`/review/${asset.script_version_id}`}>Script</Link><Link to={`/audio/${asset.script_version_id}`}>Production</Link>{asset.status === 'available' && <a href={api.audioDownload(asset.id)}>Download MP3</a>}<button type="button" onClick={() => void showDetails(asset.id)}>Job details</button><button type="button" className="delete-action" onClick={() => setDeleteId(asset.id)}>{asset.status === 'available' ? 'Delete' : 'Retry deletion'}</button></div>
            {details[asset.id] && <div className="audio-audit"><strong>{details[asset.id].job.status}</strong><p>Job {asset.job_id}</p><p>Approval {asset.approval_id}</p><p>{asset.format} · {asset.input_characters} input characters</p><p>{details[asset.id].job.error_message}</p>{details[asset.id].provider_calls.map(call => <p key={call.id}>{call.status} · Request {call.provider_request_id ?? 'unavailable'} · {call.latency_ms ?? '—'} ms{call.provider_credits != null && ` · ${call.provider_credits} provider credits`}</p>)}</div>}
            {deleteId === asset.id && <div className="audio-delete-confirm"><p>Delete this MP3? Its file will be removed; its job and audit history will remain.</p><button type="button" disabled={busy} onClick={() => void remove(asset.id)}>Delete MP3</button><button type="button" disabled={busy} onClick={() => setDeleteId(null)}>Cancel</button></div>}
          </article>)}</div>
          <div className="pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(current => Math.max(0, current - 20))}>Previous</button><span>{total ? `${offset + 1}–${Math.min(offset + 20, total)} of ${total}` : '0 recordings'}</span><button type="button" disabled={offset + 20 >= total} onClick={() => setOffset(current => current + 20)}>Next</button></div>
        </section>
      </div>
    </main>
  </div>
}
