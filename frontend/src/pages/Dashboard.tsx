import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Overview } from '../api'
import MonitorShell, { displayTime, label } from '../components/MonitorShell'

export default function Dashboard() {
  const [period,setPeriod] = useState('7')
  const [data,setData] = useState<Overview | null>(null)
  const [error,setError] = useState('')
  const [refresh,setRefresh] = useState(0)
  useEffect(() => {
    let cancelled = false
    api.metrics(period).then(value => {if (!cancelled) {setData(value);setError('')}})
      .catch(caught => {if (!cancelled) setError(caught instanceof Error ? caught.message : 'Could not load metrics')})
    return () => {cancelled = true}
  },[period,refresh])
  return <MonitorShell title="Dashboard">
    <section className="review-title"><div><p className="eyebrow">YOUR PRODUCTION, IN PERSPECTIVE</p><h1>From activity<br /><em>to insight.</em></h1></div><div className="monitor-period"><label>Time range<select aria-label="Time range" value={period} onChange={e => setPeriod(e.target.value)}><option value="1">24 hours</option><option value="7">7 days</option><option value="30">30 days</option><option value="all">All time</option></select></label><button className="secondary-button" onClick={() => setRefresh(v => v+1)}>Refresh</button></div></section>
    {error && <div className="notice error" role="alert">{error}</div>}
    {!data ? <p>Loading production metrics…</p> : <>
      <div className="monitor-stat-grid">
        <Stat title="Production jobs" value={String(data.total_jobs)} help={`${data.manual_retry_count} manual retry jobs`} />
        <Stat title="Success rate" value={data.success_rate === null ? '—' : `${Math.round(data.success_rate*100)}%`} help="Succeeded / all jobs created in range" />
        <Stat title="Awaiting human review" value={String(data.pending_review_count)} help="Current latest scripts · all time" />
        <Stat title="Needs attention" value={String(data.unresolved_needs_attention_count)} help="Unresolved retry-chain leaves in range" />
      </div>
      <section className="card monitor-card"><h2>Task outcomes</h2><div className="monitor-outcomes">{Object.entries(data.status_counts).map(([status,count]) => <Link to={`/jobs?status=${status}`} key={status} className={`monitor-outcome ${status}`}><span>{label(status)}</span><strong>{count}</strong><div><i style={{width:`${data.total_jobs ? count/data.total_jobs*100 : 0}%`}} /></div></Link>)}</div></section>
      <div className="monitor-two-columns"><section className="card monitor-card"><h2>Active processing time</h2><div className="monitor-large-number">{data.active_processing.average_seconds === null ? '—' : `${data.active_processing.average_seconds.toFixed(1)} s`}</div><p className="monitor-muted">{data.active_processing.sample_count} / {data.active_processing.terminal_job_count} terminal jobs covered. Queue, backoff and human waiting are excluded. Interrupted executions with uncertain duration are excluded.</p>
        <h3>Provider latency</h3>{Object.entries(data.provider_latency).map(([operation,modes]) => <div className="monitor-record" key={operation}><strong>{label(operation)}</strong>{Object.entries(modes).map(([mode,value]) => <small key={mode}>{mode === 'mock' ? 'Mock' : 'Real'}: {value.average_ms === null ? '—' : `${Math.round(value.average_ms)} ms`} · {value.sample_count} calls measured</small>)}</div>)}</section>
      <section className="card monitor-card"><h2>Recorded provider usage</h2><p className="monitor-muted">Calls are counted by their UTC call time. Upload recovery does not create another synthesis call.</p>
        <div className="monitor-cost"><span>OpenRouter · known cost</span><strong>${data.usage.openrouter_usd.toFixed(6)} USD</strong></div><div className="monitor-cost"><span>ElevenLabs · known usage</span><strong>{data.usage.elevenlabs_credits.toLocaleString()} credits</strong></div><div className="monitor-cost"><span>Unknown cost</span><strong>{data.usage.unknown_cost_calls} calls</strong></div><div className="monitor-cost"><span>Mock · local, no charge</span><strong>{data.usage.mock_calls} calls</strong></div><p className="monitor-muted">{data.usage.total_calls} total calls. USD and credits are separate units; unknown costs are not treated as zero.</p></section></div>
      <div className="monitor-two-columns"><section className="card monitor-card"><h2>Recent audio</h2>{data.recent_audio.map(audio => <Link className="monitor-record" key={audio.id} to={`/audio/${audio.script_version_id}`}><strong>{audio.article_title} ↗</strong><small>{displayTime(audio.created_at)}</small></Link>)}{!data.recent_audio.length && <p className="monitor-empty">No audio created in this range.</p>}</section>
      <section className="card monitor-card"><h2>Recent failed tasks</h2>{data.recent_failures.map(job => <Link className="monitor-record" key={job.id} to={`/jobs/${job.id}`}><strong>{label(job.job_type)} · {label(job.status)} ↗</strong><small>{job.error_code}</small><span>{job.error_message}</span></Link>)}{!data.recent_failures.length && <p className="monitor-empty">No failed tasks in this range.</p>}</section></div>
    </>}
  </MonitorShell>
}

function Stat({title,value,help}:{title:string;value:string;help:string}) {
  return <section className="card monitor-stat"><p className="eyebrow">{title}</p><strong>{value}</strong><small>{help}</small></section>
}
