import { FormEvent, useEffect, useState } from 'react'
import { api, ApiError, Article, ArticleSummary, ImportAttempt, Language } from './api'

type EntryMode = 'manual' | 'url'

function dateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function shortHost(url: string) {
  try { return new URL(url).hostname } catch { return url }
}

export default function App() {
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
          <footer className="footer">ECHOFlow · Content first, audio next.</footer>
        </div>
      </main>
    </div>
  )
}

