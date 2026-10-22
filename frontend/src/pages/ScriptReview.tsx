import { issueFilters, visibleIssues, type IssueFilter } from '../domain/issues'
import AppNavigation from '../components/AppNavigation'
import { useEffect, useRef, useState } from 'react'
import { Link, useBlocker, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, EvaluationIssue } from '../api'
import { readReview } from '../api/review'
import { useResource } from '../hooks/useResource'
import { activeJob, reviewLabels, reviewState } from '../domain/review'
import { jobUrl, studioUrl, withStudio } from '../domain/navigation'
import { textRange } from '../domain/evidence'
import { submitRequest } from '../domain/idempotency'
import JobProgress from '../components/JobProgress'
import ConfirmDialog from '../components/ConfirmDialog'

const dateTime = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
const score = (value: number | null) => value == null ? '—' : `${Math.round(value * 100)}%`
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'The request failed.'
function HighlightedText({ text, start, end }: { text: string; start: number | null; end: number | null }) {
  const range = textRange(text, start, end)
  return range ? <>{range.before}<mark tabIndex={-1}>{range.quote}</mark>{range.after}</> : <>{text}</>
}

export default function ScriptReview() {
  const { scriptId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const route = useRef(scriptId); route.current = scriptId
  const mutation = useRef(false)
  const allowNavigation = useRef(false)
  const draftScript = useRef('')
  const acknowledgedEvaluation = useRef('')
  const sourcePane = useRef<HTMLDivElement>(null)
  const scriptPane = useRef<HTMLDivElement>(null)
  const reportPane = useRef<HTMLElement>(null)
  const [historyId, setHistoryId] = useState<string | null>(null)
  const [issueFilter, setIssueFilter] = useState<IssueFilter>('all')
  const [activeIssue, setActiveIssue] = useState<EvaluationIssue | null>(null)
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmEvaluation, setConfirmEvaluation] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const resource = useResource(scriptId, async signal => {
    const script = await api.script(scriptId, signal)
    const [article, versions, review] = await Promise.all([api.article(script.article_id, signal), api.scripts(script.article_id, signal), readReview(scriptId, signal)])
    const timeline = review.job ? await api.jobEvents(review.job.id, signal).catch(() => ({ items: [], unavailable: true })) : { items: [] }
    return { script, article, versions: versions.items, ...review, events: timeline.items, timelineUnavailable: 'unavailable' in timeline }
  }, data => activeJob(data.job))
  const historical = useResource(historyId && historyId !== resource.data?.evaluation?.id ? `${scriptId}:${historyId}` : null, signal => api.evaluation(historyId!, signal))
  const data = resource.data
  const script = data?.script || null
  const article = data?.article || null
  const versions = data?.versions || []
  const evaluationHistory = data?.history || []
  const approvals = data?.approvals || []
  const job = data?.job || null
  const events = data?.events || []
  const latestEvaluation = data?.evaluation || null
  const evaluation = historyId && historyId !== latestEvaluation?.id ? historical.data : latestEvaluation
  const unavailable = !!resource.error || resource.loading
  const isLatest = !!script && versions[0]?.id === script.id
  const isLatestEvaluation = !!evaluation && evaluation.id === latestEvaluation?.id
  const state = reviewState(isLatest, latestEvaluation, approvals, job, unavailable)
  const reviewStatus = reviewLabels[state]
  const currentApproval = evaluation ? approvals.find(item => item.evaluation_id === evaluation.id) || null : null
  const criticalIssues = evaluation?.issues.filter(issue => issue.severity === 'critical') || []
  const remainingCritical = criticalIssues.filter(issue => !acknowledged.has(issue.id)).length
  const canApprove = state === 'pending_review' && isLatestEvaluation && !editing && !remainingCritical
  const filteredIssues = visibleIssues(evaluation?.issues || [], issueFilter)
  const issueCounts = { critical: criticalIssues.length, warning: evaluation?.issues.filter(issue => issue.severity === 'warning').length || 0 }
  const studio = script ? studioUrl(script.article_id, script.id, params.get('studio')) : '/'
  const thisPage = location.pathname + location.search
  const dirty = editing && !!script && draft.trim() !== script.text.trim()
  const blocker = useBlocker(() => dirty && !allowNavigation.current)
  const approvalReason = unavailable ? 'Status cannot be confirmed. Refresh before approving.' : !isLatest ? 'Historical versions cannot be approved. Return to the latest version.' : !isLatestEvaluation ? 'Only the latest evaluation can be approved.' : editing ? 'Save or cancel editing before approving.' : remainingCritical ? `Review and acknowledge ${remainingCritical} remaining critical issue(s).` : ''

  useEffect(() => {
    setHistoryId(null); setActiveIssue(null); setNotice(null); setEditing(false); setConfirmEvaluation(false)
    allowNavigation.current = false
  }, [scriptId])
  useEffect(() => {
    if (script && draftScript.current !== script.id) { draftScript.current = script.id; setDraft(script.text) }
  }, [script])
  useEffect(() => {
    const id = evaluation?.id || ''
    if (acknowledgedEvaluation.current !== id) { acknowledgedEvaluation.current = id; setAcknowledged(new Set()); setActiveIssue(null); setIssueFilter('all') }
  }, [evaluation?.id])
  useEffect(() => {
    if (!dirty) return
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [dirty])
  useEffect(() => {
    if (!activeIssue || editing) return
    const marks = [scriptPane.current, sourcePane.current].map(pane => {
      const mark = pane?.querySelector('mark')
      if (pane && mark) pane.scrollTop += mark.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight / 2
      return mark
    })
    const target = marks.find(Boolean) as HTMLElement | undefined
    target?.scrollIntoView({ block: 'center', behavior: 'instant' })
    target?.focus({ preventScroll: true })
  }, [activeIssue, editing])

  async function evaluate() {
    if (!script || mutation.current || unavailable || editing) return
    const id = script.id
    mutation.current = true; setBusy(true); setNotice(null); setConfirmEvaluation(false)
    try {
      const created = await submitRequest('evaluate', id, {}, key => api.evaluate(id, key))
      if (route.current !== id) return
      setHistoryId(null)
      resource.update(current => ({ ...current, job: created.job, evaluation: { ...created.evaluation, issues: [] }, history: [created.evaluation, ...current.history.filter(item => item.id !== created.evaluation.id)], events: [] }))
      setNotice({ kind: 'success', text: 'Evaluation accepted. You can leave this page while it runs.' })
      resource.refresh()
    } catch (error) { if (route.current === id) setNotice({ kind: 'error', text: errorMessage(error) }) }
    finally { mutation.current = false; setBusy(false) }
  }

  function startEvaluation() {
    if (state === 'approved' || state === 'needs_attention') setConfirmEvaluation(true)
    else void evaluate()
  }

  async function saveEdit() {
    if (!script || mutation.current || !dirty) return
    const id = script.id
    mutation.current = true; setBusy(true); setNotice(null)
    try {
      const edited = await api.editScript(id, draft, script.version)
      if (route.current !== id) return
      allowNavigation.current = true
      setDraft(edited.text); setEditing(false)
      navigate(withStudio(`/review/${edited.id}`, studioUrl(edited.article_id, edited.id, studio)), { replace: true, state: { saved: true } })
    } catch (error) {
      if (route.current === id) {
        setNotice({ kind: 'error', text: error instanceof ApiError && error.body.code === 'SCRIPT_VERSION_CONFLICT' ? 'A newer script version exists. Your draft is preserved. Refresh status, then open the latest version when ready.' : errorMessage(error) })
        resource.refresh()
      }
    } finally { mutation.current = false; setBusy(false) }
  }

  async function approve() {
    if (!script || !evaluation || !canApprove || mutation.current) return
    const id = script.id
    mutation.current = true; setBusy(true); setNotice(null)
    try {
      const approval = await api.approve(id, { evaluation_id: evaluation.id, expected_version: script.version, acknowledged_critical_issue_ids: [...acknowledged] })
      if (route.current !== id) return
      resource.update(current => ({ ...current, approvals: [approval, ...current.approvals.filter(item => item.id !== approval.id)] }))
      setNotice({ kind: 'success', text: 'Script approved. Choose a voice to produce audio.' })
      resource.refresh()
    } catch (error) { if (route.current === id) { setNotice({ kind: 'error', text: errorMessage(error) }); resource.refresh() } }
    finally { mutation.current = false; setBusy(false) }
  }
  return <div className="app-shell review-shell">
    <AppNavigation />
    <main className="main">
      <header className="topbar">
        <div className="brand-name">EchoFlow <span>/ Script Review</span></div>
        <div className="stage-pill"><span className="stage-dot" /> Script Review</div>
      </header>
      <div className="workspace review-workspace">
        <section className="review-title">
          <div><p className="eyebrow">EDITORIAL QUALITY WORKSPACE</p><h1>Review every word<br /><em>before it is heard.</em></h1></div>
          <div className={`review-status ${reviewStatus.toLowerCase().replaceAll(' ', '-')}`}><span />{reviewStatus}</div>
        </section>
        {script && <Link className="back-link" to={studio}>← Back to this article</Link>}
        {(resource.error || historical.error) && <div className="notice error" role="alert">{resource.error || historical.error} Status cannot be confirmed. <button onClick={() => { resource.refresh(); historical.refresh() }}>Refresh status</button><Link to="/">Back to content</Link></div>}
        {location.state?.saved && <p className="notice success">New version saved. Start evaluation when ready.</p>}
        {notice && <div className={`notice ${notice.kind}`} role="status">{notice.text}</div>}
        {!script || !article ? <section className="card loading-card">Loading review…</section> : <>
          <section className="review-toolbar card">
            <div><span className="job-label">SCRIPT VERSION</span><select value={script.id} onChange={event => navigate(withStudio(`/review/${event.target.value}`, studioUrl(script.article_id, event.target.value, studio)))}>{versions.map(item => <option value={item.id} key={item.id}>Version {item.version} · {item.source}</option>)}</select></div>
            <div className="review-meta"><strong>{article.title}</strong><span>{article.source} · {script.target_language.toUpperCase()} · {script.actual_model}</span></div>
            <div className="toolbar-actions">
              {job && <Link className="secondary-button review-link" to={jobUrl(job.id, thisPage)}>Job details ↗</Link>}
              {state === 'approved' && <Link className="primary-button review-link" to={withStudio(`/audio/${script.id}`, studio)}>Produce audio</Link>}
              {isLatest && !editing && <button className="secondary-button" type="button" onClick={() => setEditing(true)}>Edit script</button>}
              {state === 'superseded' && <Link className="primary-button review-link" to={withStudio(`/review/${versions[0].id}`, studioUrl(script.article_id, versions[0].id, studio))}>Return to latest version</Link>}
              {state === 'pending_review' && <button type="button" className="primary-button" onClick={() => reportPane.current?.scrollIntoView({ block: 'start' })}>Review issues</button>}
              {activeJob(job) && <button type="button" className="secondary-button" onClick={() => reportPane.current?.scrollIntoView({ block: 'start' })}>View evaluation progress</button>}
              {job && ['evaluation_failed', 'needs_attention'].includes(state) && <Link className="primary-button review-link" to={jobUrl(job.id, thisPage)}>View failure details</Link>}
              <button className={state === 'pending_evaluation' ? 'primary-button' : 'secondary-button'} type="button" disabled={busy || unavailable || !isLatest || activeJob(job) || editing} onClick={startEvaluation}>{latestEvaluation ? 'Re-evaluate' : 'Start evaluation'}</button>
            </div>
          </section>

          <section className="review-columns">
            <article className="card review-pane">
              <div className="pane-heading"><div><p className="section-number">SOURCE</p><h2>Original article</h2></div>{activeIssue?.evidence_status && <span className={`evidence-badge ${activeIssue.evidence_status}`}>{activeIssue.evidence_status}</span>}</div>
              <div className="review-text" ref={sourcePane} aria-label="Source text"><HighlightedText text={article.original_text} start={activeIssue?.evidence_status === 'verified' ? activeIssue.evidence_start : null} end={activeIssue?.evidence_status === 'verified' ? activeIssue.evidence_end : null} /></div>
            </article>
            <article className="card review-pane">
              <div className="pane-heading"><div><p className="section-number">SCRIPT · V{script.version}</p><h2>Audio script</h2></div>{script.source === 'edited' && <span className="source-tag">Human edit</span>}</div>
              {editing ? <div className="edit-area"><p>Saving creates a new version that needs a new evaluation.</p><textarea aria-label="Edit script text" value={draft} minLength={100} maxLength={30000} onChange={event => setDraft(event.target.value)} /><div><span>{draft.length.toLocaleString()} / 30,000</span><button type="button" className="secondary-button" onClick={() => { setEditing(false); setDraft(script.text) }}>Cancel</button><button type="button" className="primary-button" disabled={busy || !dirty || Array.from(draft.trim()).length < 100 || Array.from(draft.trim()).length > 30000} onClick={() => void saveEdit()}>Save new version</button></div></div>
                : <div className="review-text" ref={scriptPane} aria-label="Script text"><HighlightedText text={script.text} start={activeIssue?.script_start ?? null} end={activeIssue?.script_end ?? null} /></div>}
            </article>
          </section>

          {job && activeJob(job) && <section className="job-card review-job">
            <div className="job-summary"><div><span className="job-label">EVALUATION JOB</span><strong>{job.status.replace('_', ' ')}</strong></div><span>Attempt {job.attempt_count} / {job.max_attempts}</span></div>
            <JobProgress job={job} />
            <div className="event-list">{events.map(event => <div key={event.id}><time>{dateTime(event.created_at)}</time><span>{event.message}</span></div>)}</div>
          </section>}

          <section className="card evaluation-card" ref={reportPane} id="evaluation-report">
            <div className="card-heading"><div><p className="section-number">QUALITY REPORT</p><h2>Evaluation</h2></div>{evaluation && <span className="source-tag">{evaluation.provider || 'Pending'} · {evaluation.actual_model || evaluation.status}</span>}</div>
            {!evaluation ? <p className="detail-placeholder">Run a manual evaluation to compare this script with its source article.</p> : evaluation.status !== 'succeeded' ? <div className={`evaluation-empty ${evaluation.status}`}><strong>{evaluation.status.replace('_', ' ')}</strong><p>{job?.error_message || 'The report is not complete yet. You can leave this page while it runs.'}{evaluation.status === 'needs_attention' ? ' Re-evaluating may create another billed provider call.' : ''}</p></div> : <>
              <div className="score-grid">
                <div><span>Factual consistency</span><strong>{score(evaluation.factual_consistency)}</strong></div>
                <div><span>Content coverage</span><strong>{score(evaluation.content_coverage)}</strong></div>
                <div><span>Audio readiness</span><strong>{score(evaluation.audio_readiness)}</strong></div>
                <div><span>Review flags</span><strong>{evaluation.issues.length}</strong><small>{issueCounts.critical} critical · {issueCounts.warning} warning</small></div>
              </div>
              <div className="report-layout">
                <div className="issue-report">
                  <div className="issue-filters" role="group" aria-label="Filter review issues">
                    {issueFilters.map(filter => <button key={filter} type="button" aria-pressed={issueFilter === filter} onClick={() => {
                      setIssueFilter(filter)
                      if (activeIssue && filter !== 'all' && activeIssue.severity !== filter) setActiveIssue(null)
                    }}>{filter === 'all' ? 'All' : filter[0].toUpperCase() + filter.slice(1)} ({filter === 'all' ? evaluation.issues.length : evaluation.issues.filter(issue => issue.severity === filter).length})</button>)}
                  </div>
                <div className="issue-list">
                  {evaluation.issues.length === 0 ? <p className="clean-report">No review flags were found. Scores remain estimates.</p> : filteredIssues.length === 0 ? <p className="clean-report" role="status">No {issueFilter} issues in this report.</p> : filteredIssues.map(issue => <button type="button" key={issue.id} className={`issue-row ${issue.severity} ${activeIssue?.id === issue.id ? 'active' : ''}`} onClick={() => setActiveIssue(issue)}>
                    <span className="issue-top"><b>{issue.severity}</b><small>{issue.origin} · {issue.dimension.replaceAll('_', ' ')}</small></span><strong>{issue.title}</strong><p>{issue.explanation}</p><em>{issue.evidence_status === 'verified' ? 'Source evidence located' : issue.evidence_status === 'insufficient' ? 'Evidence could not be verified' : 'Source evidence not applicable'}</em>
                  </button>)}
                </div>
                </div>
                <aside className="approval-panel">
                  <span className="job-label">HUMAN DECISION</span>
                  {currentApproval ? <div className="approved-panel"><strong>{!isLatest || !isLatestEvaluation ? 'Historical approval' : unavailable ? 'Checking approval' : state === 'approved' ? 'Approved' : 'Historical approval'}</strong><p>{dateTime(currentApproval.created_at)}</p><small>This approval references evaluation {currentApproval.evaluation_id.slice(0, 8)}.</small>{state === 'approved' && isLatestEvaluation && <Link className="primary-button review-link" to={withStudio(`/audio/${script.id}`, studio)}>Produce audio</Link>}</div> : <>
                    <h3>Approve version {script.version}</h3><p>Approval records this exact script and the latest evaluation. Scores do not guarantee factual accuracy.</p>
                    <p role="status">{criticalIssues.length - remainingCritical} / {criticalIssues.length} critical issues acknowledged</p>
                    {criticalIssues.map(issue => <label className="acknowledge" key={issue.id}><input type="checkbox" checked={acknowledged.has(issue.id)} onChange={event => setAcknowledged(current => { const next = new Set(current); event.target.checked ? next.add(issue.id) : next.delete(issue.id); return next })} /><span>I reviewed: {issue.title}</span></label>)}
                    <button type="button" className="primary-button approve-button" disabled={busy || !canApprove} onClick={() => void approve()}>Approve script</button>
                    {approvalReason && <small>{approvalReason}</small>}
                  </>}
                </aside>
              </div>
            </>}
          </section>

          {evaluationHistory.length > 1 && <section className="evaluation-history"><span className="job-label">EVALUATION HISTORY</span>{evaluationHistory.map(item => <button type="button" key={item.id} className={evaluation?.id === item.id ? 'active' : ''} onClick={() => { setActiveIssue(null); setHistoryId(item.id) }}><strong>{item.status}</strong><span>{dateTime(item.created_at)}</span></button>)}</section>}
        </>}
        <ConfirmDialog open={blocker.state === 'blocked'} title="Unsaved script changes" description="Leaving will discard your unsaved edits." confirmLabel="Discard changes" cancelLabel="Keep editing" onCancel={() => { if (blocker.state === 'blocked') blocker.reset() }} onConfirm={() => { if (blocker.state === 'blocked') blocker.proceed() }} />
        <ConfirmDialog open={confirmEvaluation} title="Start a new evaluation?" description={state === 'approved' ? 'Your current approval will become historical. Wait for the new report and approve again before producing new audio. This starts a new provider call.' : 'The previous provider result is uncertain and may have been charged. A new evaluation may incur another charge.'} confirmLabel="Start new evaluation" onCancel={() => setConfirmEvaluation(false)} onConfirm={() => void evaluate()} />
        {data?.timelineUnavailable && <p role="status">Timeline unavailable. Your evaluation is still saved.</p>}
        <footer className="footer">ECHOFlow · Review before release.</footer>
      </div>
    </main>
  </div>
}
