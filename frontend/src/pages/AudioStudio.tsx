import AppNavigation from '../components/AppNavigation'
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, AudioDetail, ProductionJob } from '../api'
import { readReview } from '../api/review'
import { useResource } from '../hooks/useResource'
import { activeJob, reviewLabels, reviewState } from '../domain/review'
import { jobUrl, studioUrl, withStudio } from '../domain/navigation'
import { submitRequest } from '../domain/idempotency'
import JobProgress from '../components/JobProgress'
import '../styles/audio.css'

const message = (error: unknown) => error instanceof Error ? error.message : 'The request failed.'
const timestamp = (value: string) => new Date(value).toLocaleString()
const duration = (seconds: number) => `${Math.floor(Math.round(seconds) / 60)}:${String(Math.round(seconds) % 60).padStart(2, '0')}`

export default function AudioStudio() {
  const { scriptId } = useParams()
  const [params] = useSearchParams()
  const location = useLocation()
  const route = useRef(scriptId); route.current = scriptId
  const mutation = useRef(false)
  const [voiceId, setVoiceId] = useState('')
  const [speed, setSpeed] = useState(1)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, AudioDetail>>({})
  const [productionOpen, setProductionOpen] = useState<boolean | null>(null)
  const [hasRecording, setHasRecording] = useState(false)
  const [acceptedJob, setAcceptedJob] = useState<ProductionJob | null>(null)
  const resource = useResource(scriptId || null, async signal => {
    const script = await api.script(scriptId!, signal)
    const [article, versions, review, jobs] = await Promise.all([api.article(script.article_id, signal), api.scripts(script.article_id, signal), readReview(script.id, signal), api.audioJobs(script.id, signal)])
    const job = jobs.items[0] || null
    const timeline = job ? await api.jobEvents(job.id, signal).catch(() => ({ items: [], unavailable: true })) : { items: [] }
    return { script, article, versions: versions.items, review, job, events: timeline.items, timelineUnavailable: 'unavailable' in timeline }
  }, data => activeJob(data.job) || activeJob(data.review.job))
  const script = resource.data?.script || null
  const article = resource.data?.article || null
  const review = resource.data?.review
  const state = reviewState(resource.data?.versions[0]?.id === script?.id, review?.evaluation || null, review?.approvals || [], review?.job, !!resource.error || resource.loading)
  const eligible = state === 'approved'
  const job = acceptedJob?.source_script_version_id === scriptId ? acceptedJob : resource.data?.job || null
  const events = resource.data?.events || []
  const studio = script ? studioUrl(script.article_id, script.id, params.get('studio')) : '/'
  const voicesResource = useResource(script ? `voices:${script.id}` : null, signal => api.voices(script!.target_language, signal))
  const voices = voicesResource.data?.items || []
  const voiceError = voicesResource.error
  const voice = voices.find(item => item.id === voiceId)
  const recordings = useResource(`audio:${scriptId || 'all'}:${query}:${offset}`, signal => api.audioList({ scriptId, q: query, offset }, signal))
  const assets = recordings.data?.items || []
  const total = recordings.data?.total || 0
  const loading = recordings.loading
  const formOpen = productionOpen ?? (!!recordings.error || !hasRecording)

  useEffect(() => {
    setProductionOpen(null); setHasRecording(false)
    setVoiceId(''); setOffset(0); setSearch(''); setQuery(''); setDetails({}); setAcceptedJob(null); setError(null); setNotice(null); setDeleteId(null)
  }, [scriptId])
  useEffect(() => {
    if (voicesResource.data) setVoiceId(current => voices.some(item => item.id === current) ? current : voices[0]?.id || '')
  }, [voicesResource.data])
  useEffect(() => {
    if (acceptedJob && resource.data?.job?.id === acceptedJob.id) setAcceptedJob(null)
    if (resource.data?.job?.status === 'succeeded') recordings.refresh()
  }, [resource.data?.job?.id, resource.data?.job?.status, acceptedJob])

  useEffect(() => {
    if (recordings.data && !recordings.error && !query && offset === 0) {
      setHasRecording(recordings.data.items.some(asset => asset.status === 'available'))
    }
  }, [recordings.data, recordings.error, query, offset])

  async function generate() {
    if (!script || !voice || !eligible || mutation.current || activeJob(job)) return
    const id = script.id
    const body = { language: script.target_language, voice_id: voice.id, speed }
    mutation.current = true; setBusy(true); setError(null); setNotice(null)
    try {
      const result = await submitRequest('audio', id, body, key => api.createAudio(id, body, key))
      if (route.current !== id) return
      setAcceptedJob(result.job)
      resource.refresh(); recordings.refresh()
      setNotice(result.reused_existing ? 'Reused an existing task or audio with these settings.' : 'Audio production accepted. You can leave this page while it runs.')
    } catch (caught) {
      if (route.current === id) { setError(message(caught)); if (caught instanceof ApiError) resource.refresh() }
    } finally { mutation.current = false; setBusy(false) }
  }

  async function remove(id: string) {
    if (mutation.current) return
    const currentRoute = route.current
    mutation.current = true; setBusy(true); setError(null)
    try {
      await api.deleteAudio(id)
      if (route.current === currentRoute) { setDeleteId(null); recordings.refresh(); setNotice('Audio deleted. Job and audit history are retained.') }
    } catch (caught) { if (route.current === currentRoute) { setError(message(caught)); recordings.refresh() } }
    finally { mutation.current = false; setBusy(false) }
  }

  async function showDetails(id: string) {
    const currentRoute = route.current
    try { const result = await api.audio(id); if (route.current === currentRoute) setDetails(current => ({ ...current, [id]: result })) }
    catch (caught) { if (route.current === currentRoute) setError(message(caught)) }
  }
  return <div className="app-shell">
    <AppNavigation />
    <main className="main">
      <header className="topbar"><div className="brand-name">EchoFlow <span>/ Audio Studio</span></div><div className="stage-pill"><span className="stage-dot" />Audio Studio</div></header>
      <div className="workspace">
        <section className="review-title"><div><p className="eyebrow">FROM APPROVED WORDS TO SOUND</p><h1>Give your story<br /><em>a voice.</em></h1></div><Link className="secondary-button review-link" to="/audio">All audio</Link></section>
        {script && <Link className="back-link" to={studio}>← Back to this article</Link>}
        {resource.error && <div className="notice error" role="alert">{resource.error} Status cannot be confirmed. <button onClick={() => { resource.refresh(); recordings.refresh() }}>Refresh status</button><Link to="/">Back to content</Link></div>}
        {recordings.error && <div className="notice error" role="alert">Recordings could not be refreshed. Existing results are kept. {recordings.error} <button onClick={recordings.refresh}>Refresh recordings</button></div>}
        {error && <div className="notice error" role="alert">{error}</div>}
        {notice && <div className="notice success" role="status">{notice}</div>}
        {scriptId && !script && !error && <p className="detail-placeholder">Loading script…</p>}
        <section className="card audio-library" id="recordings">
          <div className="audio-heading"><div><p className="section-number">AUDIO LIBRARY</p><h2>{scriptId ? 'This script’s recordings' : 'Your recordings'}</h2></div><span className="count-badge">{total}</span></div>
          <form className="search" onSubmit={event => { event.preventDefault(); setOffset(0); setQuery(search) }}><input aria-label="Search audio by article title" placeholder="Search article titles…" value={search} onChange={event => setSearch(event.target.value)} /><button type="submit">Search</button></form>
          {loading ? <p className="detail-placeholder">Loading audio…</p> : !assets.length && <p className="detail-placeholder">{query ? 'No recordings match this search.' : scriptId ? 'No recordings yet. Use the form below to produce audio from an approved script.' : 'No audio yet. Open an approved script in Script Review and choose Produce audio.'}</p>}
          <div className="audio-grid">{assets.map(asset => <article className={`audio-item${asset.id === job?.result_audio_asset_id ? " result-audio" : ""}`} key={asset.id}>
            <div className="audio-heading"><h3>{asset.article_title}</h3><span className="source-tag">V{asset.script_version} · {asset.language.toUpperCase()}</span></div>
            <p>{asset.voice_name} · {asset.speed}× · {duration(asset.duration_seconds)} · {(asset.file_size / 1024).toFixed(1)} KiB</p>
            <p>{asset.provider} / {asset.model}<br />{timestamp(asset.created_at)}</p>
            {asset.provider === 'mock' && <p className="audio-help">Mock demonstration tone</p>}
            {asset.status === 'available' ? <audio aria-label={`Play ${asset.article_title}`} controls preload="none" src={api.audioStream(asset.id)} /> : <p className="notice error">{asset.delete_error ?? 'Deletion is pending. Retry to finish removing this file.'}</p>}
            <div className="audio-actions"><Link to={withStudio(`/review/${asset.script_version_id}`, studioUrl(asset.article_id, asset.script_version_id, studio))}>Script</Link><Link to={withStudio(`/audio/${asset.script_version_id}`, studioUrl(asset.article_id, asset.script_version_id, studio))}>Production</Link>{asset.status === 'available' && <a href={api.audioDownload(asset.id)}>Download MP3</a>}<button type="button" onClick={() => void showDetails(asset.id)}>Job details</button><button type="button" className="delete-action" onClick={() => setDeleteId(asset.id)}>{asset.status === 'available' ? 'Delete' : 'Retry deletion'}</button></div>
            {details[asset.id] && <div className="audio-audit"><strong>{details[asset.id].job.status}</strong><p>Job {asset.job_id}</p><p>Approval {asset.approval_id}</p><p>{asset.format} · {asset.input_characters} input characters</p><p>{details[asset.id].job.error_message}</p>{details[asset.id].provider_calls.map(call => <p key={call.id}>{call.status} · Request {call.provider_request_id ?? 'unavailable'} · {call.latency_ms ?? '—'} ms{call.provider_credits != null && ` · ${call.provider_credits} provider credits`}</p>)}</div>}
            {deleteId === asset.id && <div className="audio-delete-confirm"><p>Delete this MP3? Its file will be removed; its job and audit history will remain.</p><button type="button" disabled={busy} onClick={() => void remove(asset.id)}>Delete MP3</button><button type="button" disabled={busy} onClick={() => setDeleteId(null)}>Cancel</button></div>}
          </article>)}</div>
          <div className="pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(current => Math.max(0, current - 20))}>Previous</button><span>{total ? `${offset + 1}–${Math.min(offset + 20, total)} of ${total}` : '0 recordings'}</span><button type="button" disabled={offset + 20 >= total} onClick={() => setOffset(current => current + 20)}>Next</button></div>
        </section>
        {script && article && <section className="card audio-production">
          <div className="audio-heading"><div><p className="section-number">PRODUCTION · SCRIPT V{script.version}</p><h2>{article.title}</h2><p>{article.source} · {script.target_language.toUpperCase()} · {script.text.length.toLocaleString()} characters</p></div><Link className="secondary-button review-link" to={withStudio(`/review/${script.id}`, studio)}>Review script</Link></div>
          {!eligible && <div className="notice error">{reviewLabels[state]}. {state === 'unavailable' ? 'Refresh status before producing audio.' : 'Only the latest script with approval for its latest successful evaluation can produce new audio.'}</div>}
          <p className="source-tag">{reviewLabels[state]}</p>
          <details className="production-options" open={formOpen} onToggle={event => { if (event.currentTarget.open !== formOpen) setProductionOpen(event.currentTarget.open) }}>
          <summary>{hasRecording ? 'Create another voice version' : 'Create audio'}</summary>
          {voiceError && <div className="notice error">{voiceError} <button type="button" onClick={voicesResource.refresh}>Reload voices</button></div>}
          <div className="audio-controls">
            <label className="field">Voice<select value={voiceId} onChange={event => setVoiceId(event.target.value)} disabled={!voices.length}>{!voices.length && <option value="">Loading available voices…</option>}{voices.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label className="field">Language<input value={script.target_language === 'en' ? 'English' : 'German'} readOnly /></label>
            <label className="field">Speed · {speed.toFixed(2)}×<input type="range" min="0.7" max="1.2" step="0.05" value={speed} onChange={event => setSpeed(Number(event.target.value))} /></label>
            <button type="button" className="primary-button" disabled={busy || !eligible || !voice || (activeJob(job))} onClick={() => void generate()}>Generate audio</button>
          </div>
          {voice?.provider === 'mock' && <p className="audio-help">Mock mode creates a two-second demonstration tone, not narration. No external service is called.</p>}
          {voice?.provider === 'elevenlabs' && <p className="audio-help">AI-generated speech · ElevenLabs · {script.text.length.toLocaleString()} input characters. Generating audio uses your ElevenLabs credits.</p>}
          {voice?.preview_url && <div className="voice-preview"><span>Voice sample</span><audio controls preload="none" src={voice.preview_url} /></div>}
          <details className="audio-script"><summary>Read the approved script</summary><div className="script-body">{script.text}</div></details>
          </details>
        </section>}
        {job && <section className={`card job-card audio-job ${job.status}`} aria-live="polite">
          <div className="job-heading"><span className="job-label">LATEST AUDIO JOB · <Link to={jobUrl(job.id, location.pathname + location.search)}>Details ↗</Link></span><strong>{job.status.replaceAll('_', ' ')}</strong></div>
          <JobProgress job={job} />
          {job.error_message && <p className="job-error">{job.error_message}</p>}
          {job.status === 'needs_attention' && <p className="audio-help">Open job details to recover saved audio or review the charge risk before a new synthesis call.</p>}
          {job.status === 'succeeded' && <p className="audio-result-link">Audio generated. <a href="#recordings">Listen or download above ↑</a></p>}
          <details className="task-events"><summary>Task events</summary><ol className="audio-events">{events.map(item => <li key={item.id}><span>{timestamp(item.created_at)}</span>{item.message}</li>)}</ol></details>
        </section>}
        {resource.data?.timelineUnavailable && <p role="status">Timeline unavailable. Your audio task is still saved.</p>}

      </div>
    </main>
  </div>
}
