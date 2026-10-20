import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type JobEvent, type JobExecution, type Page, type ProductionJob, type ProviderCall } from './api'
import MonitorShell, { activeJob, displayTime, label } from './MonitorShell'

const emptyPage = <T,>(): Page<T> => ({items: [], total: 0, limit: 20, offset: 0})
const statuses = ['queued','running','retry_scheduled','succeeded','failed','needs_attention']
const types = ['script_generation','script_evaluation','audio_generation']

export default function JobMonitor() {
  const {jobId} = useParams()
  return <MonitorShell title="Job Monitor">{jobId ? <JobDetail key={jobId} id={jobId} /> : <JobList />}</MonitorShell>
}

function JobList() {
  const [page, setPage] = useState(emptyPage<ProductionJob>())
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState({q:'', type:'', status:searchParams.get('status') || '', created_from:'', created_to:''})
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      const params: Record<string, string> = {limit:'20', offset:String(offset)}
      for (const [key, value] of Object.entries(filters)) if (value) params[key] = key.startsWith('created_') ? new Date(value).toISOString() : value
      try {
        const result = await api.monitorJobs(params)
        if (!cancelled) {setPage(result); setError('')}
      } catch (caught) {if (!cancelled) setError(caught instanceof Error ? caught.message : 'Could not load jobs')}
      if (!cancelled) timer = setTimeout(() => void load(), 5000)
    }
    void load()
    return () => {cancelled = true; clearTimeout(timer)}
  }, [filters, offset, refresh])
  const change = (key: string, value: string) => {setFilters(previous => ({...previous,[key]:value})); setOffset(0)}
  return <>
    <section className="review-title"><div><p className="eyebrow">EVERY ATTEMPT, ACCOUNTED FOR</p><h1>Follow the<br /><em>production.</em></h1></div><button className="secondary-button" onClick={() => setRefresh(v => v+1)}>Refresh</button></section>
    {error && <div className="notice error" role="alert">{error}</div>}
    <section className="card monitor-card"><div className="monitor-filters">
      <label>Article title<input value={filters.q} onChange={e => change('q', e.target.value)} placeholder="Search articles" /></label>
      <label>Task type<select value={filters.type} onChange={e => change('type',e.target.value)}><option value="">All types</option>{types.map(v => <option key={v} value={v}>{label(v)}</option>)}</select></label>
      <label>Status<select value={filters.status} onChange={e => change('status',e.target.value)}><option value="">All statuses</option>{statuses.map(v => <option key={v} value={v}>{label(v)}</option>)}</select></label>
      <label>Created from (local time)<input type="datetime-local" value={filters.created_from} onChange={e => change('created_from',e.target.value)} /></label>
      <label>Created before (local time)<input type="datetime-local" value={filters.created_to} onChange={e => change('created_to',e.target.value)} /></label>
    </div><div className="monitor-table-wrap"><table className="monitor-table"><thead><tr><th>Task / article</th><th>Status</th><th>Stage</th><th>Attempts</th><th>Created</th></tr></thead><tbody>
      {page.items.map(job => <tr key={job.id}><td><Link to={`/jobs/${job.id}`}>{label(job.job_type)} ↗</Link><small>{job.article_title || job.article_id}</small>{job.retry_of_job_id && <small>Manual retry</small>}</td><td><span className={`monitor-badge ${job.status}`}>{label(job.status)}</span></td><td>{label(job.stage)}</td><td>{job.attempt_count} / {job.max_attempts}</td><td>{displayTime(job.created_at)}</td></tr>)}
    </tbody></table></div>{!page.items.length && <p className="monitor-empty">No jobs match these filters.</p>}
    <Pager page={page} onChange={setOffset} /></section>
  </>
}

export function Pager({page, onChange}: {page: {total:number;offset:number;limit:number};onChange:(offset:number)=>void}) {
  return <div className="monitor-pager"><span>{page.total ? `${page.offset+1}–${Math.min(page.offset+page.limit,page.total)} of ${page.total}` : '0 results'}</span><button className="secondary-button" disabled={page.offset === 0} onClick={() => onChange(Math.max(0,page.offset-page.limit))}>Previous</button><button className="secondary-button" disabled={page.offset+page.limit >= page.total} onClick={() => onChange(page.offset+page.limit)}>Next</button></div>
}

function JobDetail({id}: {id:string}) {
  const navigate = useNavigate()
  const [job, setJob] = useState<ProductionJob | null>(null)
  const [executions, setExecutions] = useState(emptyPage<JobExecution>())
  const [calls, setCalls] = useState(emptyPage<ProviderCall>())
  const [events, setEvents] = useState(emptyPage<JobEvent>())
  const [offsets, setOffsets] = useState({executions:0,calls:0,events:0})
  const [error, setError] = useState('')
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const retryKey = useRef(crypto.randomUUID())
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const [j,e,c,v] = await Promise.all([api.job(id),api.executions(id,offsets.executions),api.calls(id,offsets.calls),api.eventsPage(id,offsets.events)])
        if (cancelled) return
        setJob(j);setExecutions(e);setCalls(c);setEvents(v);setError('')
        if (activeJob(j.status)) timer = setTimeout(() => void load(),2000)
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : 'Could not load job')
          timer = setTimeout(() => void load(), 2000)
        }
      }
    }
    void load()
    return () => {cancelled = true;clearTimeout(timer)}
  }, [id, offsets])
  async function retry() {
    setBusy(true);setError('')
    try {const result = await api.retryJob(id,ack,retryKey.current);navigate(`/jobs/${result.job.id}`)}
    catch (caught) {setError(caught instanceof Error ? caught.message : 'Retry failed')}
    finally {setBusy(false)}
  }
  return <>
    <section className="review-title"><div><p className="eyebrow">EXECUTION RECORD</p><h1>Behind the<br /><em>result.</em></h1></div><Link className="secondary-button review-link" to="/jobs">All jobs</Link></section>
    {error && <div className="notice error" role="alert">{error}</div>}
    {!job ? <p>Loading job…</p> : <>
      <section className="card monitor-card"><div className="monitor-detail-heading"><div><h2>{label(job.job_type)}</h2><p>{job.article_title}</p><code>{job.id}</code></div><span className={`monitor-badge ${job.status}`}>{label(job.status)}</span></div>
        <div className="monitor-facts"><p>Stage <strong>{label(job.stage)}</strong></p><p>Attempts <strong>{job.attempt_count} / {job.max_attempts}</strong></p><p>Started <strong>{displayTime(job.started_at)}</strong></p><p>Completed <strong>{displayTime(job.completed_at)}</strong></p></div>
        {job.error_message && <div className="notice error"><strong>{job.error_code}</strong><p>{job.error_message}</p></div>}
        <div className="monitor-links"><Link to="/">Content Studio</Link>{job.source_script_version_id && <Link to={`/review/${job.source_script_version_id}`}>Source script</Link>}{job.result_script_version_id && <Link to={`/review/${job.result_script_version_id}`}>Result script ↗</Link>}{job.result_evaluation_id && <Link to={`/review/${job.source_script_version_id}`}>Evaluation report ↗</Link>}{job.result_audio_asset_id && <Link to={`/audio/${job.source_script_version_id}`}>Result audio ↗</Link>}</div>
        <div className="monitor-chain"><strong>Retry history</strong>{job.root_job_id && job.root_job_id !== id && <Link to={`/jobs/${job.root_job_id}`}>Root job</Link>}{job.retry_of_job_id && <Link to={`/jobs/${job.retry_of_job_id}`}>← Parent job</Link>}<span>Current job</span>{job.retry_child_job_id && <Link to={`/jobs/${job.retry_child_job_id}`}>Retry child →</Link>}</div>
        {job.can_retry && <div className="monitor-retry"><h3>{job.retry_mode === 'resume_upload' ? 'Recover saved audio' : 'Retry with a new provider call'}</h3><p>{job.retry_mode === 'resume_upload' ? 'The existing synthesis and its approval will be reused. No new TTS call is made.' : 'A new linked job will use the current server provider configuration. Correct configuration errors before retrying.'}</p>
          {job.recovery_expires_at && <p>Saved audio recovery deadline: {displayTime(job.recovery_expires_at)}</p>}
          {job.requires_charge_acknowledgement && <label className="monitor-ack"><input type="checkbox" checked={ack} onChange={e => {setAck(e.target.checked);retryKey.current=crypto.randomUUID()}} />I understand the previous call may have been charged and a new call may incur another charge.</label>}
          <button className="primary-button" disabled={busy || (job.requires_charge_acknowledgement && !ack)} onClick={() => void retry()}>{busy ? 'Queuing…' : job.retry_mode === 'resume_upload' ? 'Resume upload' : 'Create retry job'}</button></div>}
        {job.retry_unavailable_reason && <p className="monitor-empty">{job.retry_unavailable_reason}</p>}
        <details><summary>Saved provider configuration</summary><pre className="monitor-json">{JSON.stringify(job.provider_snapshot,null,2)}</pre></details>
      </section>
      <section className="card monitor-card"><h2>Execution attempts</h2>{executions.items.map(e => <div className="monitor-record" key={e.id}><strong>Attempt {e.attempt_no} · {e.status}</strong><span>{label(e.stage)}</span><small>Started {displayTime(e.started_at)} · Ended {displayTime(e.completed_at)}</small><small>Heartbeat {displayTime(e.heartbeat_at)} · Lease {displayTime(e.lease_expires_at)}</small></div>)}{!executions.total && <p className="monitor-empty">No execution has been claimed. Older jobs may predate execution tracking.</p>}<Pager page={executions} onChange={v => setOffsets(o => ({...o,executions:v}))} /></section>
      <section className="card monitor-card"><h2>Provider calls</h2>{calls.items.map(c => <div className="monitor-record" key={c.id}><strong>{c.provider === 'mock' ? 'Mock · no charge' : c.provider} · {c.status}{c.uncertain ? ' · result unknown' : ''}</strong><span>{c.actual_model || c.requested_model}</span><small>Request {c.provider_request_id || 'unknown'} · {c.latency_ms ?? 'unknown'} ms · Tokens {c.total_tokens ?? 'unknown'}</small><small>{c.provider === 'openrouter' ? `USD ${c.cost ?? 'unknown'}` : c.provider === 'elevenlabs' ? `Credits ${c.provider_credits ?? 'unknown'}` : 'Local demonstration'}</small>{c.error_message && <p>{c.error_code}: {c.error_message}</p>}</div>)}{!calls.total && <p className="monitor-empty">No new provider calls. An upload recovery reuses its source job’s synthesis call.</p>}<Pager page={calls} onChange={v => setOffsets(o => ({...o,calls:v}))} /></section>
      <section className="card monitor-card"><h2>Stage timeline</h2>{events.items.map(e => <div className="monitor-record" key={e.id}><strong>{label(e.stage)}</strong><span>{e.message}</span><small>{displayTime(e.created_at)}</small></div>)}<Pager page={events} onChange={v => setOffsets(o => ({...o,events:v}))} /></section>
    </>}
  </>
}
