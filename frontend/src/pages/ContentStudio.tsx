import AppNavigation from '../components/AppNavigation'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError, GenerationConfig, Language, ProductionJob } from '../api'
import { readReview } from '../api/review'
import { useResource } from '../hooks/useResource'
import { activeJob, nextAction, reviewLabels, reviewState } from '../domain/review'
import { jobUrl, pageOffset, studioUrl, withStudio } from '../domain/navigation'
import { submitRequest } from '../domain/idempotency'
import JobProgress from '../components/JobProgress'

type EntryMode = 'manual' | 'url'
const dateTime = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
function shortHost(url: string) { try { return new URL(url).hostname } catch { return url } }
function duration(seconds: number) { return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }

export default function ContentStudio() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const articleId = params.get('articleId') || ''
  const requestedScript = params.get('scriptId') || ''
  const section = params.get('section')
  const offset = pageOffset(params.get('offset'))
  const importOffset = pageOffset(params.get('importOffset'))
  const query = params.get('q') || ''
  const activePanel = params.get('tab') === 'imports' ? 'imports' : 'articles'
  const productionRef = useRef<HTMLElement>(null)
  const scrolledSelection = useRef('')
  const route = useRef(''); route.current = articleId + ':' + requestedScript
  const mutation = useRef(false)
  const savedNotice = useRef<string | null>(null)
  const [entryOpen, setEntryOpen] = useState(!articleId)
  const [mode, setMode] = useState<EntryMode>('manual')
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  const [search, setSearch] = useState(query)
  const [busy, setBusy] = useState(false)
  const [generationBusy, setGenerationBusy] = useState(false)
  const [acceptedJob, setAcceptedJob] = useState<ProductionJob | null>(null)
  const [generationConfig, setGenerationConfig] = useState<GenerationConfig>({ target_language: 'en', length: 'short', style: 'news', audience: 'general' })
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const patchUrl = (values: Record<string, string | null>, replace = false) => setParams(current => {
    const next = new URLSearchParams(current)
    for (const [key, value] of Object.entries(values)) value === null ? next.delete(key) : next.set(key, value)
    return next
  }, { replace })
  const setOffset = (value: number) => patchUrl({ offset: String(value) })
  const setImportOffset = (value: number) => patchUrl({ importOffset: String(value) })
  const setActivePanel = (value: string) => patchUrl({ tab: value })

  const library = useResource(`library:${offset}:${query}:${importOffset}`, async signal => {
    const [articles, imports] = await Promise.all([api.articles(offset, query, signal), api.imports(importOffset, signal)])
    return { articles, imports }
  })
  const article = useResource(articleId || null, signal => api.article(articleId, signal))
  const production = useResource(articleId ? route.current : null, async signal => {
    const [jobs, scripts] = await Promise.all([api.jobs(articleId, signal), api.scripts(articleId, signal)])
    const scriptId = requestedScript || scripts.items[0]?.id
    const script = scriptId ? await api.script(scriptId, signal) : null
    if (script && script.article_id !== articleId) throw new Error('This script does not belong to the selected article. Select another article or return to the latest version.')
    const review = script ? await readReview(script.id, signal) : null
    const job = jobs.items[0] || null
    const timeline = job ? await api.jobEvents(job.id, signal).catch(() => ({ items: [], unavailable: true })) : { items: [] }
    return { scripts: scripts.items, script, job, review, events: timeline.items, timelineUnavailable: 'unavailable' in timeline }
  }, data => activeJob(data.job) || activeJob(data.review?.job))
  const selected = article.data
  const articles = library.data?.articles.items || []
  const imports = library.data?.imports.items || []
  const total = library.data?.articles.total || 0
  const importTotal = library.data?.imports.total || 0
  const scripts = production.data?.scripts || []
  const script = production.data?.script || null
  const job = acceptedJob?.article_id === articleId ? acceptedJob : production.data?.job || null
  const jobEvents = production.data?.events || []
  const review = production.data?.review
  const statusUnavailable = !!production.error || production.loading || !!article.error
  const state = reviewState(scripts[0]?.id === script?.id, review?.evaluation || null, review?.approvals || [], review?.job, statusUnavailable)
  const studio = script && selected ? studioUrl(selected.id, script.id, location.pathname + location.search) : location.pathname + location.search
  const reviewPath = script ? withStudio(`/review/${script.id}`, studio) : '/'
  const reviewStatuses = Object.fromEntries(scripts.map(item => [item.id, item.id === scripts[0]?.id ? (item.id === script?.id ? reviewLabels[state] : 'Open to check status') : 'Superseded']))

  useEffect(() => { setSearch(query) }, [query])
  useEffect(() => { setEntryOpen(!articleId) }, [articleId])
  useEffect(() => { if (savedNotice.current !== articleId) setNotice(null); savedNotice.current = null; setAcceptedJob(null) }, [articleId])
  useEffect(() => { if (selected) setGenerationConfig(current => ({ ...current, target_language: selected.language })) }, [selected?.id])
  useEffect(() => { if (acceptedJob && production.data?.job?.id === acceptedJob.id) setAcceptedJob(null) }, [production.data, acceptedJob])
  useEffect(() => {
    if (script && !requestedScript && !activeJob(job)) patchUrl({ scriptId: script.id }, true)
  }, [script?.id, requestedScript, job?.status])
  useEffect(() => {
    const selection = `${articleId}:${requestedScript}:${section}`
    if (selected && production.data && !production.loading && section === 'production' && scrolledSelection.current !== selection) {
      productionRef.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
      scrolledSelection.current = selection
    }
  }, [selected?.id, requestedScript, section, production.loading, !!production.data])

  async function generate(regenerate = false) {
    if (!selected || mutation.current || activeJob(job) || statusUnavailable) return
    mutation.current = true; setGenerationBusy(true); setNotice(null)
    const currentRoute = route.current
    try {
      const result = regenerate && script
        ? await submitRequest('regenerate', script.id, generationConfig, key => api.regenerate(script.id, generationConfig, key))
        : await submitRequest('generate', selected.id, generationConfig, key => api.createScriptJob(selected.id, generationConfig, key))
      if (route.current !== currentRoute) return
      setAcceptedJob(result.job)
      patchUrl({ scriptId: null, section: 'production' }, true)
      production.refresh()
      setNotice({ kind: 'success', text: 'Script generation accepted. You can leave this page while it runs.' })
    } catch (error) {
      if (route.current === currentRoute) setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not start generation.' })
    } finally { mutation.current = false; setGenerationBusy(false) }
  }

  async function startEvaluation() {
    if (!script || mutation.current || statusUnavailable) return
    mutation.current = true; setGenerationBusy(true); setNotice(null)
    const currentRoute = route.current
    try {
      await submitRequest('evaluate', script.id, {}, key => api.evaluate(script.id, key))
      if (route.current === currentRoute) navigate(reviewPath)
    } catch (error) {
      if (route.current === currentRoute) setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not start evaluation.' })
    } finally { mutation.current = false; setGenerationBusy(false) }
  }

  function selectScript(id: string) { patchUrl({ scriptId: id, section: 'production' }) }
  function selectArticle(id: string) { setEntryOpen(false); patchUrl({ articleId: id, scriptId: null, section: 'production' }) }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setNotice(null)
    try {
      const result = mode === 'manual' ? await api.create({ title, text, source, language }) : await api.import({ url, title: title || undefined, source: source || undefined, language })
      setEntryOpen(false)
      savedNotice.current = result.article.id
      patchUrl({ articleId: result.article.id, scriptId: null, section: 'production', offset: '0', importOffset: '0' })
      setNotice({ kind: 'success', text: result.duplicate ? 'This content already exists. The saved article is open.' : 'Article saved. Configure your audio script below.' })
      setTitle(''); setSource(''); setText(''); setUrl('')
      library.refresh(); article.refresh(); production.refresh()
    } catch (error) {
      setEntryOpen(true)
      const message = error instanceof ApiError ? error.body.message : error instanceof Error ? error.message : 'The request failed.'
      setNotice({ kind: 'error', text: mode === 'url' ? `${message} You can paste the article text instead.` : message })
      if (mode === 'url') library.refresh()
    } finally { setBusy(false) }
  }

  function searchArticles(event: FormEvent<HTMLFormElement>) { event.preventDefault(); patchUrl({ offset: '0', q: search.trim() }) }
  const scriptAction = script ? (state === 'pending_evaluation' ? <button type="button" className="primary-button" disabled={generationBusy || statusUnavailable || activeJob(job)} onClick={() => void startEvaluation()}>Start evaluation</button>
                      : state === 'approved' ? <Link className="primary-button review-link" to={withStudio(`/audio/${script.id}`, studio)}>Produce audio</Link>
                      : state === 'superseded' ? <button className="primary-button" onClick={() => selectScript(scripts[0].id)}>Return to latest version</button>
                      : state === 'unavailable' ? <button className="primary-button" onClick={production.refresh}>Refresh status</button>
                      : <Link className="primary-button review-link" to={review?.job && ['evaluation_failed', 'needs_attention'].includes(state) ? jobUrl(review.job.id, location.pathname + location.search) : reviewPath}>{nextAction(state)}</Link>) : null

  return (
    <div className="app-shell">
      <AppNavigation />

      <main className="main">
        <header className="topbar">
          <div className="brand-name">EchoFlow <span>/ Content Studio</span></div>
          <div className="stage-pill"><span className="stage-dot" /> Content Studio</div>
        </header>

        <div className="workspace">
          <section className="intro">
            <p className="eyebrow">CONTENT PRODUCTION WORKSPACE</p>
            <h1>Turn source material<br /><em>into something heard.</em></h1>
            <p className="intro-copy">Start with the article. Keep its original words, source and language in one place before creating an audio script.</p>
          </section>

          {(library.error || article.error || production.error) && <div className="notice error" role="alert">{article.error || production.error || library.error} Status cannot be confirmed. <button onClick={() => { library.refresh(); article.refresh(); production.refresh() }}>Refresh status</button><Link to="/">Back to content</Link></div>}
          {notice && <div className={`notice ${notice.kind}`} role="status">{notice.text}</div>}

          <div className="content-grid">
            <section className="card entry-card">
              <details className="article-entry" open={entryOpen} onToggle={event => { if (event.currentTarget.open !== entryOpen) setEntryOpen(event.currentTarget.open) }}>
              <summary>Add an article</summary>
              <div className="card-heading">
                <div>
                  <p className="section-number">01 / INPUT</p>
                  <h2>Article input</h2>
                </div>
                <div className="mode-switch" role="tablist" aria-label="Input method">
                  <button type="button" role="tab" aria-selected={mode === 'manual'} className={mode === 'manual' ? 'selected' : ''} onClick={() => setMode('manual')}>Paste text</button>
                  <button type="button" role="tab" aria-selected={mode === 'url'} className={mode === 'url' ? 'selected' : ''} onClick={() => setMode('url')}>Import URL</button>
                </div>
              </div>
              <form onSubmit={submit}>
                {mode === 'url' && <label className="field">Article URL <input type="url" required placeholder="https://example.com/story" value={url} onChange={event => setUrl(event.target.value)} /><small>Public HTML articles only. Sign-in and dynamically rendered pages are not supported.</small></label>}
                <div className="two-fields">
                  <label className="field">Title {mode === 'url' && <span>optional</span>}<input required={mode === 'manual'} maxLength={300} placeholder={mode === 'url' ? 'Use page title' : 'Enter article title'} value={title} onChange={event => setTitle(event.target.value)} /></label>
                  <label className="field">Source {mode === 'url' && <span>optional</span>}<input required={mode === 'manual'} maxLength={200} placeholder={mode === 'url' ? 'Use website name' : 'Publication or team'} value={source} onChange={event => setSource(event.target.value)} /></label>
                </div>
                {mode === 'manual' && <label className="field">Article text <textarea required minLength={100} maxLength={30000} rows={12} placeholder="Paste the original article here. Paragraphs will be preserved." value={text} onChange={event => setText(event.target.value)} /><small>{text.length.toLocaleString()} / 30,000 characters · minimum 100</small></label>}
                <div className="form-footer">
                  <label className="field language-field">Original language<select value={language} onChange={event => setLanguage(event.target.value as Language)}><option value="en">English</option><option value="de">German</option></select></label>
                  <button className="primary-button" type="submit" disabled={busy}>{busy ? 'Working…' : mode === 'url' ? 'Import article ↗' : 'Save article ↗'}</button>
                </div>
              </form>
              </details>
            </section>

            <section className="card library-card">
              <div className="card-heading library-heading">
                <div><p className="section-number">02 / LIBRARY</p><h2>Saved content</h2></div>
                <span className="count-badge">{total}</span>
              </div>
              <div className="list-tabs">
                <button type="button" className={activePanel === 'articles' ? 'active' : ''} onClick={() => setActivePanel('articles')}>Articles</button>
                <button type="button" className={activePanel === 'imports' ? 'active' : ''} onClick={() => setActivePanel('imports')}>Import history</button>
              </div>
              {activePanel === 'articles' ? <>
                <form className="search" onSubmit={searchArticles}><input aria-label="Search article titles" placeholder="Search titles" value={search} onChange={event => setSearch(event.target.value)} /><button type="submit">Search</button></form>
                <div className="item-list">
                  {articles.length === 0 ? <p className="empty-state">No articles found. Add one to begin.</p> : articles.map(article => <button type="button" key={article.id} className={`list-item ${selected?.id === article.id ? 'current' : ''}`} onClick={() => void selectArticle(article.id)}>
                    <span className="item-title">{article.title}</span>
                    <span className="item-meta">{article.source} <span>·</span> {article.language.toUpperCase()} <span>·</span> {dateTime(article.created_at)}</span>
                  </button>)}
                </div>
                <div className="pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>← Previous</button><span>{total === 0 ? 0 : offset + 1}–{Math.min(offset + 20, total)} of {total}</span><button type="button" disabled={offset + 20 >= total} onClick={() => setOffset(offset + 20)}>Next →</button></div>
              </> : <><div className="item-list import-list">
                {imports.length === 0 ? <p className="empty-state">No URL imports yet.</p> : imports.map(attempt => <div className="import-item" key={attempt.id}>
                  <div className="import-top"><strong>{shortHost(attempt.url)}</strong><span className={`status ${attempt.status}`}>{attempt.status}</span></div>
                  <p title={attempt.url}>{attempt.url}</p>
                  {attempt.error_message && <small>{attempt.error_message}</small>}
                  {attempt.article_id && <button type="button" onClick={() => void selectArticle(attempt.article_id!)}>Open article →</button>}
                  <time>{dateTime(attempt.created_at)}</time>
                </div>)}
              </div><div className="pagination"><button type="button" disabled={importOffset === 0} onClick={() => setImportOffset(Math.max(0, importOffset - 20))}>← Previous</button><span>{importTotal === 0 ? 0 : importOffset + 1}–{Math.min(importOffset + 20, importTotal)} of {importTotal}</span><button type="button" disabled={importOffset + 20 >= importTotal} onClick={() => setImportOffset(importOffset + 20)}>Next →</button></div></>}
            </section>
          </div>

          <section className="card detail-card">
            <div className="card-heading"><div><p className="section-number">03 / SOURCE RECORD</p><h2>Article details</h2></div>{selected && <span className="source-tag">{selected.import_type === 'url' ? 'URL import' : 'Manual entry'}</span>}</div>
            {selected ? <div className="article-detail">
              <div className="detail-header"><h3>{selected.title}</h3><p>{selected.source} · {selected.language === 'en' ? 'English' : 'German'} · Saved {dateTime(selected.created_at)}</p>{selected.source_url && <a href={selected.source_url} target="_blank" rel="noreferrer">View original source ↗</a>}</div>
              <details key={selected.id} className="original-article"><summary>Read original article</summary><div className="article-body">{selected.original_text}</div></details>
            </div> : <p className="detail-placeholder">Select a saved article to read its original text here.</p>}
          </section>

          <section className="card production-card" ref={productionRef} id="production">
            <div className="card-heading">
              <div><p className="section-number">04 / SCRIPT GENERATION</p><h2>Prepare the audio script</h2></div>
              {script && <div className="production-actions"><span className={`pending-tag ${state}`}>{reviewLabels[state]}</span>{scriptAction}</div>}
            </div>
            {notice && selected && <div className={`notice ${notice.kind} production-feedback`} role="status">{notice.text}</div>}
            {production.error && <div className="notice error">{production.error} <button onClick={production.refresh}>Refresh status</button><Link to="/">Back to content</Link></div>}
            {!selected ? <p className="detail-placeholder">Select an article to generate an audio script.</p> : <>
              <div className="generation-controls">
                <label className="field">Target language<select value={generationConfig.target_language} onChange={event => setGenerationConfig({ ...generationConfig, target_language: event.target.value as Language })}><option value="en">English</option><option value="de">German</option></select></label>
                <label className="field">Length<select value={generationConfig.length} onChange={event => setGenerationConfig({ ...generationConfig, length: event.target.value as GenerationConfig['length'] })}><option value="short">Short · ~2 min</option><option value="medium">Medium · ~5 min</option><option value="long">Long · ~9 min</option></select></label>
                <label className="field">Style<select value={generationConfig.style} onChange={event => setGenerationConfig({ ...generationConfig, style: event.target.value as GenerationConfig['style'] })}><option value="news">News</option><option value="conversational">Conversational</option><option value="professional">Professional</option></select></label>
                <label className="field">Audience<select value={generationConfig.audience} onChange={event => setGenerationConfig({ ...generationConfig, audience: event.target.value as GenerationConfig['audience'] })}><option value="general">General audience</option><option value="business">Business audience</option></select></label>
                <button className={`${script ? 'secondary-button' : 'primary-button'} generate-button`} type="button" disabled={generationBusy || statusUnavailable || activeJob(job)} onClick={() => void generate(false)}>{generationBusy ? 'Queuing…' : 'Generate script →'}</button>
              </div>

              {job && <div className={`job-card ${job.status}`}>
                <div className="job-summary"><div><span className="job-label">LATEST JOB · <Link to={jobUrl(job.id, location.pathname + location.search)}>Details ↗</Link></span><strong>{job.status.replace('_', ' ')}</strong></div></div>
                <JobProgress job={job} />
                {job.error_message && <p className="job-error">{job.error_message}{job.status === 'needs_attention' ? ' A new request may create another provider charge.' : ''}</p>}
                <div className="event-list">{jobEvents.map(event => <div key={event.id}><time>{dateTime(event.created_at)}</time><span>{event.message}</span></div>)}</div>
              </div>}

              {production.data?.timelineUnavailable && <p role="status">Timeline unavailable. Your task is still saved.</p>}
              <div className="script-workspace">
                <aside className="version-list">
                  <span className="job-label">VERSIONS</span>
                  {scripts.length === 0 ? <p>No scripts yet.</p> : scripts.map(item => <button type="button" className={script?.id === item.id ? 'active' : ''} key={item.id} onClick={() => void selectScript(item.id)}><strong>Version {item.version}</strong><span>{item.target_language.toUpperCase()} · {duration(item.estimated_duration_seconds)}</span><em>{reviewStatuses[item.id] || 'Pending evaluation'}</em></button>)}
                </aside>
                <div className="script-preview">
                  {script ? <>
                    <div className="script-meta"><div><h3>Script version {script.version}</h3><p>{script.provider} · {script.actual_model} · {duration(script.estimated_duration_seconds)}</p></div><div className="script-actions">
                      <Link className="secondary-button review-link" to={reviewPath}>Review</Link><button type="button" className="secondary-button" disabled={generationBusy || statusUnavailable || activeJob(job)} onClick={() => void generate(true)}>Regenerate</button></div></div>
                    <div className="script-body">{script.text}</div>
                  </> : <p className="detail-placeholder">Configure the script and start generation. Progress and results remain available after refresh.</p>}
                </div>
              </div>
            </>}
          </section>
          <footer className="footer">ECHOFlow · Content first, audio next.</footer>
        </div>
      </main>
    </div>
  )
}

