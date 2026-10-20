import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api, ApiError, Approval, Article, Evaluation, EvaluationIssue,
  EvaluationSummary, JobEvent, ProductionJob, ScriptSummary, ScriptVersion,
} from './api'

const terminalStatuses = new Set(['succeeded', 'failed', 'needs_attention'])

function dateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function score(value: number | null) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.body.message : error instanceof Error ? error.message : 'The request failed.'
}

function HighlightedText({ text, start, end }: { text: string; start: number | null; end: number | null }) {
  if (start == null || end == null || start < 0 || end <= start || end > text.length) return <>{text}</>
  return <>{text.slice(0, start)}<mark>{text.slice(start, end)}</mark>{text.slice(end)}</>
}

export default function ScriptReview() {
  const { scriptId } = useParams()
  const navigate = useNavigate()
  const [script, setScript] = useState<ScriptVersion | null>(null)
  const [article, setArticle] = useState<Article | null>(null)
  const [versions, setVersions] = useState<ScriptSummary[]>([])
  const [evaluationHistory, setEvaluationHistory] = useState<EvaluationSummary[]>([])
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [job, setJob] = useState<ProductionJob | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [activeIssue, setActiveIssue] = useState<EvaluationIssue | null>(null)
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const load = useCallback(async (id: string) => {
    const loadedScript = await api.script(id)
    const [loadedArticle, loadedVersions, loadedEvaluations, loadedApprovals] = await Promise.all([
      api.article(loadedScript.article_id),
      api.scripts(loadedScript.article_id),
      api.evaluations(id, 100),
      api.approvals(id),
    ])
    const latestEvaluation = loadedEvaluations.items[0]
    const [fullEvaluation, latestJob, latestEvents] = latestEvaluation
      ? await Promise.all([
          api.evaluation(latestEvaluation.id),
          api.job(latestEvaluation.job_id),
          api.jobEvents(latestEvaluation.job_id),
        ])
      : [null, null, { items: [] as JobEvent[] }]
    setScript(loadedScript)
    setDraft(loadedScript.text)
    setArticle(loadedArticle)
    setVersions(loadedVersions.items)
    setEvaluationHistory(loadedEvaluations.items)
    setEvaluation(fullEvaluation)
    setApprovals(loadedApprovals.items)
    setJob(latestJob)
    setEvents(latestEvents.items)
    setActiveIssue(null)
    setAcknowledged(new Set())
    setEditing(false)
  }, [])

  useEffect(() => {
    if (!scriptId) return
    setNotice(null)
    void load(scriptId).catch(error => setNotice({ kind: 'error', text: errorMessage(error) }))
  }, [load, scriptId])

  useEffect(() => {
    if (!job || terminalStatuses.has(job.status) || !scriptId) return
    const timer = window.setInterval(async () => {
      try {
        const [nextJob, nextEvents] = await Promise.all([api.job(job.id), api.jobEvents(job.id)])
        setJob(nextJob)
        setEvents(nextEvents.items)
        if (terminalStatuses.has(nextJob.status)) await load(scriptId)
      } catch (error) {
        setNotice({ kind: 'error', text: errorMessage(error) })
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status, load, scriptId])

  const isLatest = !!script && versions[0]?.id === script.id
  const latestEvaluation = evaluationHistory[0] ?? null
  const isLatestEvaluation = !!evaluation && evaluation.id === latestEvaluation?.id
  const currentApproval = evaluation
    ? approvals.find(item => item.evaluation_id === evaluation.id) ?? null
    : null
  const latestApproval = latestEvaluation
    ? approvals.find(item => item.evaluation_id === latestEvaluation.id) ?? null
    : null
  const reviewStatus = !isLatest
    ? 'Superseded'
    : !latestEvaluation
      ? 'Pending evaluation'
      : latestEvaluation.status === 'queued' || latestEvaluation.status === 'running'
        ? 'Evaluating'
        : latestEvaluation.status === 'failed'
          ? 'Evaluation failed'
          : latestEvaluation.status === 'needs_attention'
            ? 'Needs attention'
            : latestApproval
              ? 'Approved'
              : 'Pending review'
  const criticalIssues = evaluation?.issues.filter(issue => issue.severity === 'critical') ?? []
  const allCriticalAcknowledged = criticalIssues.every(issue => acknowledged.has(issue.id))
  const canApprove = isLatest && isLatestEvaluation && evaluation?.status === 'succeeded' && !currentApproval && allCriticalAcknowledged

  const issueCounts = useMemo(() => ({
    critical: evaluation?.issues.filter(issue => issue.severity === 'critical').length ?? 0,
    warning: evaluation?.issues.filter(issue => issue.severity === 'warning').length ?? 0,
    info: evaluation?.issues.filter(issue => issue.severity === 'info').length ?? 0,
  }), [evaluation])

  async function startEvaluation() {
    if (!script) return
    setBusy(true)
    setNotice(null)
    try {
      const created = await api.evaluate(script.id, crypto.randomUUID())
      setJob(created.job)
      setEvaluation({ ...created.evaluation, issues: [] })
      setEvaluationHistory(current => [created.evaluation, ...current])
      setEvents((await api.jobEvents(created.job.id)).items)
      setApprovals([])
      setAcknowledged(new Set())
      setNotice({ kind: 'success', text: 'Evaluation queued.' })
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  async function saveEdit() {
    if (!script) return
    setBusy(true)
    setNotice(null)
    try {
      const edited = await api.editScript(script.id, draft, script.version)
      navigate(`/review/${edited.id}`, { replace: true })
      setNotice({ kind: 'success', text: `Script version ${edited.version} saved. Evaluate it when ready.` })
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  async function approve() {
    if (!script || !evaluation) return
    setBusy(true)
    setNotice(null)
    try {
      const approval = await api.approve(script.id, {
        evaluation_id: evaluation.id,
        expected_version: script.version,
        acknowledged_critical_issue_ids: [...acknowledged],
      })
      setApprovals(current => [approval, ...current.filter(item => item.id !== approval.id)])
      setNotice({ kind: 'success', text: 'Script approved for Stage 4 audio production.' })
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return <div className="app-shell review-shell">
    <aside className="rail">
      <Link className="brand-mark brand-link" to="/">E<span>◉</span></Link>
      <div className="rail-divider" />
      <Link className="rail-icon" title="Content Studio" aria-label="Content Studio" to="/">▦</Link>
      <div className="rail-icon active" title="Script Review" aria-label="Script Review">✓</div>
      <div className="rail-spacer" />
      <div className="rail-bottom">03</div>
    </aside>
    <main className="main">
      <header className="topbar">
        <div className="brand-name">EchoFlow <span>/ Script Review</span></div>
        <div className="stage-pill"><span className="stage-dot" /> Stage 03 · Review</div>
      </header>
      <div className="workspace review-workspace">
        <section className="review-title">
          <div><p className="eyebrow">EDITORIAL QUALITY WORKSPACE</p><h1>Review every word<br /><em>before it is heard.</em></h1></div>
          <div className={`review-status ${reviewStatus.toLowerCase().replaceAll(' ', '-')}`}><span />{reviewStatus}</div>
        </section>
        {notice && <div className={`notice ${notice.kind}`} role="status">{notice.text}</div>}
        {!script || !article ? <section className="card loading-card">Loading review…</section> : <>
          <section className="review-toolbar card">
            <div><span className="job-label">SCRIPT VERSION</span><select value={script.id} onChange={event => navigate(`/review/${event.target.value}`)}>{versions.map(item => <option value={item.id} key={item.id}>Version {item.version} · {item.source}</option>)}</select></div>
            <div className="review-meta"><strong>{article.title}</strong><span>{article.source} · {script.target_language.toUpperCase()} · {script.actual_model}</span></div>
            <div className="toolbar-actions">
              {isLatest && !editing && <button className="secondary-button" type="button" onClick={() => setEditing(true)}>Edit script</button>}
              <button className="primary-button" type="button" disabled={busy || !isLatest || (!!job && !terminalStatuses.has(job.status))} onClick={() => void startEvaluation()}>{evaluation ? 'Re-evaluate' : 'Evaluate'}</button>
            </div>
          </section>

          <section className="review-columns">
            <article className="card review-pane">
              <div className="pane-heading"><div><p className="section-number">SOURCE</p><h2>Original article</h2></div>{activeIssue?.evidence_status && <span className={`evidence-badge ${activeIssue.evidence_status}`}>{activeIssue.evidence_status}</span>}</div>
              <div className="review-text"><HighlightedText text={article.original_text} start={activeIssue?.evidence_start ?? null} end={activeIssue?.evidence_end ?? null} /></div>
            </article>
            <article className="card review-pane">
              <div className="pane-heading"><div><p className="section-number">SCRIPT · V{script.version}</p><h2>Audio script</h2></div>{script.source === 'edited' && <span className="source-tag">Human edit</span>}</div>
              {editing ? <div className="edit-area"><textarea value={draft} minLength={100} maxLength={30000} onChange={event => setDraft(event.target.value)} /><div><span>{draft.length.toLocaleString()} / 30,000</span><button type="button" className="secondary-button" onClick={() => { setEditing(false); setDraft(script.text) }}>Cancel</button><button type="button" className="primary-button" disabled={busy || draft.trim().length < 100} onClick={() => void saveEdit()}>Save new version</button></div></div>
                : <div className="review-text"><HighlightedText text={script.text} start={activeIssue?.script_start ?? null} end={activeIssue?.script_end ?? null} /></div>}
            </article>
          </section>

          {job && !terminalStatuses.has(job.status) && <section className="job-card review-job">
            <div className="job-summary"><div><span className="job-label">EVALUATION JOB</span><strong>{job.status.replace('_', ' ')}</strong></div><span>Attempt {job.attempt_count} / {job.max_attempts}</span></div>
            <div className="progress-track"><span style={{ width: job.status === 'running' ? '65%' : '25%' }} /></div>
            <div className="event-list">{events.map(event => <div key={event.id}><time>{dateTime(event.created_at)}</time><span>{event.message}</span></div>)}</div>
          </section>}

          <section className="card evaluation-card">
            <div className="card-heading"><div><p className="section-number">QUALITY REPORT</p><h2>Evaluation</h2></div>{evaluation && <span className="source-tag">{evaluation.provider || 'Pending'} · {evaluation.actual_model || evaluation.status}</span>}</div>
            {!evaluation ? <p className="detail-placeholder">Run a manual evaluation to compare this script with its source article.</p> : evaluation.status !== 'succeeded' ? <div className={`evaluation-empty ${evaluation.status}`}><strong>{evaluation.status.replace('_', ' ')}</strong><p>{job?.error_message || 'The report is not complete yet.'}{evaluation.status === 'needs_attention' ? ' Re-evaluating may create another billed provider call.' : ''}</p></div> : <>
              <div className="score-grid">
                <div><span>Factual consistency</span><strong>{score(evaluation.factual_consistency)}</strong></div>
                <div><span>Content coverage</span><strong>{score(evaluation.content_coverage)}</strong></div>
                <div><span>Audio readiness</span><strong>{score(evaluation.audio_readiness)}</strong></div>
                <div><span>Review flags</span><strong>{evaluation.issues.length}</strong><small>{issueCounts.critical} critical · {issueCounts.warning} warning</small></div>
              </div>
              <div className="report-layout">
                <div className="issue-list">
                  {evaluation.issues.length === 0 ? <p className="clean-report">No review flags were found. Scores remain estimates.</p> : evaluation.issues.map(issue => <button type="button" key={issue.id} className={`issue-row ${issue.severity} ${activeIssue?.id === issue.id ? 'active' : ''}`} onClick={() => setActiveIssue(issue)}>
                    <span className="issue-top"><b>{issue.severity}</b><small>{issue.origin} · {issue.dimension.replaceAll('_', ' ')}</small></span><strong>{issue.title}</strong><p>{issue.explanation}</p><em>{issue.evidence_status === 'verified' ? 'Source evidence located' : issue.evidence_status === 'insufficient' ? 'Evidence could not be verified' : 'Source evidence not applicable'}</em>
                  </button>)}
                </div>
                <aside className="approval-panel">
                  <span className="job-label">HUMAN DECISION</span>
                  {currentApproval ? <div className="approved-panel"><strong>Approved</strong><p>{dateTime(currentApproval.created_at)}</p><small>This approval references evaluation {currentApproval.evaluation_id.slice(0, 8)}.</small></div> : <>
                    <h3>Approve version {script.version}</h3><p>Approval records this exact script and the latest evaluation. Scores do not guarantee factual accuracy.</p>
                    {criticalIssues.map(issue => <label className="acknowledge" key={issue.id}><input type="checkbox" checked={acknowledged.has(issue.id)} onChange={event => setAcknowledged(current => { const next = new Set(current); event.target.checked ? next.add(issue.id) : next.delete(issue.id); return next })} /><span>I reviewed: {issue.title}</span></label>)}
                    <button type="button" className="primary-button approve-button" disabled={busy || !canApprove} onClick={() => void approve()}>Approve script</button>
                    {!isLatest && <small>Historical versions cannot be approved.</small>}
                    {isLatest && !isLatestEvaluation && <small>Only the latest evaluation can be approved.</small>}
                  </>}
                </aside>
              </div>
            </>}
          </section>

          {evaluationHistory.length > 1 && <section className="evaluation-history"><span className="job-label">EVALUATION HISTORY</span>{evaluationHistory.map(item => <button type="button" key={item.id} className={evaluation?.id === item.id ? 'active' : ''} onClick={() => { setActiveIssue(null); void api.evaluation(item.id).then(setEvaluation) }}><strong>{item.status}</strong><span>{dateTime(item.created_at)}</span></button>)}</section>}
        </>}
        <footer className="footer">ECHOFlow · Review before release.</footer>
      </div>
    </main>
  </div>
}
