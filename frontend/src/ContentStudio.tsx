import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, ApiError, Article, ArticleSummary, GenerationConfig, ImportAttempt,
  JobEvent, Language, ProductionJob, ScriptSummary, ScriptVersion,
} from './api'

type EntryMode = 'manual' | 'url'

function dateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function shortHost(url: string) {
  try { return new URL(url).hostname } catch { return url }
}

const terminalStatuses = new Set(['succeeded', 'failed', 'needs_attention'])

function duration(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${remainder.toString().padStart(2, '0')}`
}

export default function ContentStudio() {
  const [mode, setMode] = useState<EntryMode>('manual')
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [language, setLanguage] = useState<Language>('en')
  const [articles, setArticles] = useState<ArticleSummary[]>([])
  const [imports, setImports] = useState<ImportAttempt[]>([])
  const [importTotal, setImportTotal] = useState(0)
  const [importOffset, setImportOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Article | null>(null)
  const [activePanel, setActivePanel] = useState<'articles' | 'imports'>('articles')
  const [busy, setBusy] = useState(false)
  const [generationBusy, setGenerationBusy] = useState(false)
  const [generationConfig, setGenerationConfig] = useState<GenerationConfig>({
    target_language: 'en', length: 'short', style: 'news', audience: 'general',
  })
  const [job, setJob] = useState<ProductionJob | null>(null)
  const [jobEvents, setJobEvents] = useState<JobEvent[]>([])
  const [scripts, setScripts] = useState<ScriptSummary[]>([])
  const [script, setScript] = useState<ScriptVersion | null>(null)
  const [reviewStatuses, setReviewStatuses] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  async function refreshArticles(nextOffset = offset, nextQuery = query) {
    const page = await api.articles(nextOffset, nextQuery)
    setArticles(page.items)
    setTotal(page.total)
  }

  async function refreshImports(nextOffset = importOffset) {
    const page = await api.imports(nextOffset)
    setImports(page.items)
    setImportTotal(page.total)
  }

  useEffect(() => {
    void Promise.all([refreshArticles(offset, query), refreshImports(importOffset)]).catch(() => {
      setNotice({ kind: 'error', text: 'Could not load the studio. Check that the API is running.' })
    })
  }, [offset, query, importOffset])

  useEffect(() => {
    if (!selected) {
      setJob(null)
      setJobEvents([])
      setScripts([])
      setScript(null)
      setReviewStatuses({})
      return
    }
    setGenerationConfig(current => ({ ...current, target_language: selected.language }))
    void loadProduction(selected.id)
  }, [selected?.id])

  useEffect(() => {
    if (!job || terminalStatuses.has(job.status)) return
    const timer = window.setInterval(() => {
      void refreshJob(job.id)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status])

  async function loadProduction(articleId: string) {
    try {
      const [jobPage, scriptPage] = await Promise.all([api.jobs(articleId), api.scripts(articleId)])
      const latestJob = jobPage.items[0] || null
      setJob(latestJob)
      setScripts(scriptPage.items)
      await loadReviewStatuses(scriptPage.items)
      setJobEvents(latestJob ? (await api.jobEvents(latestJob.id)).items : [])
      const scriptId = latestJob?.result_script_version_id || scriptPage.items[0]?.id
      setScript(scriptId ? await api.script(scriptId) : null)
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not load script production.' })
    }
  }

  async function loadReviewStatuses(items: ScriptSummary[]) {
    const entries = await Promise.all(items.map(async (item, index) => {
      if (index > 0) return [item.id, 'Superseded'] as const
      const evaluations = await api.evaluations(item.id, 1)
      const latest = evaluations.items[0]
      if (!latest) return [item.id, 'Pending evaluation'] as const
      if (latest.status === 'queued' || latest.status === 'running') return [item.id, 'Evaluating'] as const
      if (latest.status === 'failed') return [item.id, 'Evaluation failed'] as const
      if (latest.status === 'needs_attention') return [item.id, 'Needs attention'] as const
      const approvals = await api.approvals(item.id)
      return [item.id, approvals.items.some(approval => approval.evaluation_id === latest.id) ? 'Approved' : 'Pending review'] as const
    }))
    setReviewStatuses(Object.fromEntries(entries))
  }

  async function refreshJob(id: string) {
    try {
      const [nextJob, events] = await Promise.all([api.job(id), api.jobEvents(id)])
      setJob(nextJob)
      setJobEvents(events.items)
      if (nextJob.status === 'succeeded' && nextJob.result_script_version_id && selected) {
        const [nextScript, scriptPage] = await Promise.all([
          api.script(nextJob.result_script_version_id), api.scripts(selected.id),
        ])
        setScript(nextScript)
        setScripts(scriptPage.items)
        await loadReviewStatuses(scriptPage.items)
      }
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not refresh the generation job.' })
    }
  }

  async function generate(regenerate = false) {
    if (!selected) return
    setGenerationBusy(true)
    setNotice(null)
    try {
      const key = crypto.randomUUID()
      const result = regenerate && script
        ? await api.regenerate(script.id, generationConfig, key)
        : await api.createScriptJob(selected.id, generationConfig, key)
      setJob(result.job)
      setJobEvents((await api.jobEvents(result.job.id)).items)
      setNotice({ kind: 'success', text: regenerate ? 'A new script version is queued.' : 'Script generation is queued.' })
    } catch (error) {
      const message = error instanceof ApiError ? error.body.message : error instanceof Error ? error.message : 'Could not start generation.'
      setNotice({ kind: 'error', text: message })
    } finally {
      setGenerationBusy(false)
    }
  }

  async function selectScript(id: string) {
    try {
      setScript(await api.script(id))
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not load the script.' })
    }
  }

  async function selectArticle(id: string) {
    try {
      const article = await api.article(id)
      setSelected(article)
      setNotice(null)
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not load article.' })
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      const result = mode === 'manual'
        ? await api.create({ title, text, source, language })
        : await api.import({ url, title: title || undefined, source: source || undefined, language })
      setSelected(result.article)
      setNotice({
        kind: 'success',
        text: result.duplicate ? 'This content already exists. The saved article is open.' : 'Article saved and ready for the next stage.',
      })
      setTitle('')
      setSource('')
      setText('')
      setUrl('')
      setOffset(0)
      setImportOffset(0)
      await Promise.all([refreshArticles(0, query), refreshImports(0)])
    } catch (error) {
      const message = error instanceof ApiError ? error.body.message : error instanceof Error ? error.message : 'The request failed.'
      setNotice({ kind: 'error', text: mode === 'url' ? `${message} You can paste the article text instead.` : message })
      if (mode === 'url') {
        setImportOffset(0)
        await refreshImports(0).catch(() => undefined)
      }
    } finally {
      setBusy(false)
    }
  }

  function searchArticles(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setOffset(0)
    setQuery(search.trim())
  }

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="brand-mark">E<span>◉</span></div>
        <div className="rail-divider" />
        <div className="rail-icon active" title="Content Studio" aria-label="Content Studio">▦</div>
        <div className="rail-spacer" />
        <div className="rail-bottom">01</div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="brand-name">EchoFlow <span>/ Content Studio</span></div>
          <div className="stage-pill"><span className="stage-dot" /> Stage 01 · Content</div>
        </header>

        <div className="workspace">
          <section className="intro">
            <p className="eyebrow">CONTENT PRODUCTION WORKSPACE</p>
            <h1>Turn source material<br /><em>into something heard.</em></h1>
            <p className="intro-copy">Start with the article. Keep its original words, source and language in one place before creating an audio script.</p>
          </section>

          {notice && <div className={`notice ${notice.kind}`} role="status">{notice.text}</div>}

          <div className="content-grid">
            <section className="card entry-card">
              <div className="card-heading">
                <div>
                  <p className="section-number">01 / INPUT</p>
                  <h2>Add an article</h2>
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
              <div className="article-body">{selected.original_text}</div>
            </div> : <p className="detail-placeholder">Select a saved article to read its original text here.</p>}
          </section>

          <section className="card production-card">
            <div className="card-heading">
              <div><p className="section-number">04 / SCRIPT GENERATION</p><h2>Prepare the audio script</h2></div>
              {script && <span className="pending-tag">Pending evaluation · Stage 3</span>}
            </div>
            {!selected ? <p className="detail-placeholder">Select an article to generate an audio script.</p> : <>
              <div className="generation-controls">
                <label className="field">Target language<select value={generationConfig.target_language} onChange={event => setGenerationConfig({ ...generationConfig, target_language: event.target.value as Language })}><option value="en">English</option><option value="de">German</option></select></label>
                <label className="field">Length<select value={generationConfig.length} onChange={event => setGenerationConfig({ ...generationConfig, length: event.target.value as GenerationConfig['length'] })}><option value="short">Short · ~2 min</option><option value="medium">Medium · ~5 min</option><option value="long">Long · ~9 min</option></select></label>
                <label className="field">Style<select value={generationConfig.style} onChange={event => setGenerationConfig({ ...generationConfig, style: event.target.value as GenerationConfig['style'] })}><option value="news">News</option><option value="conversational">Conversational</option><option value="professional">Professional</option></select></label>
                <label className="field">Audience<select value={generationConfig.audience} onChange={event => setGenerationConfig({ ...generationConfig, audience: event.target.value as GenerationConfig['audience'] })}><option value="general">General audience</option><option value="business">Business audience</option></select></label>
                <button className="primary-button generate-button" type="button" disabled={generationBusy || (!!job && !terminalStatuses.has(job.status))} onClick={() => void generate(false)}>{generationBusy ? 'Queuing…' : 'Generate script →'}</button>
              </div>

              {job && <div className={`job-card ${job.status}`}>
                <div className="job-summary"><div><span className="job-label">LATEST JOB</span><strong>{job.status.replace('_', ' ')}</strong></div><span>Attempt {job.attempt_count} / {job.max_attempts}</span></div>
                <div className="progress-track"><span style={{ width: job.status === 'succeeded' ? '100%' : job.status === 'running' ? '62%' : job.status === 'queued' || job.status === 'retry_scheduled' ? '25%' : '100%' }} /></div>
                {job.error_message && <p className="job-error">{job.error_message}{job.status === 'needs_attention' ? ' A new request may create another provider charge.' : ''}</p>}
                <div className="event-list">{jobEvents.map(event => <div key={event.id}><time>{dateTime(event.created_at)}</time><span>{event.message}</span></div>)}</div>
              </div>}

              <div className="script-workspace">
                <aside className="version-list">
                  <span className="job-label">VERSIONS</span>
                  {scripts.length === 0 ? <p>No scripts yet.</p> : scripts.map(item => <button type="button" className={script?.id === item.id ? 'active' : ''} key={item.id} onClick={() => void selectScript(item.id)}><strong>Version {item.version}</strong><span>{item.target_language.toUpperCase()} · {duration(item.estimated_duration_seconds)}</span><em>{reviewStatuses[item.id] || 'Pending evaluation'}</em></button>)}
                </aside>
                <div className="script-preview">
                  {script ? <>
                    <div className="script-meta"><div><h3>Script version {script.version}</h3><p>{script.provider} · {script.actual_model} · {duration(script.estimated_duration_seconds)}</p></div><div className="script-actions"><Link className="secondary-button review-link" to={`/review/${script.id}`}>Review</Link><button type="button" className="secondary-button" disabled={generationBusy || (!!job && !terminalStatuses.has(job.status))} onClick={() => void generate(true)}>Regenerate</button></div></div>
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

