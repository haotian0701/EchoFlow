const base = 'https://echoflow.invalid'
const knownPath = /^(?:\/|\/(?:review|audio|jobs)\/[0-9a-f-]{36}|\/(?:audio|jobs|dashboard))$/i
const studioKeys = ['articleId', 'scriptId', 'section', 'q', 'offset', 'tab', 'importOffset']

export function safeReturn(value: string | null | undefined, fallback = '/') {
  if (!value || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020]/.test(value)) return fallback
  try {
    const url = new URL(value, base)
    if (url.origin !== base || !knownPath.test(url.pathname)) return fallback
    return url.pathname + url.search + url.hash
  } catch { return fallback }
}

export function studioUrl(articleId: string, scriptId?: string, original?: string | null) {
  const candidate = new URL(safeReturn(original), base)
  const params = new URLSearchParams()
  if (candidate.pathname === '/') for (const key of studioKeys) if (candidate.searchParams.has(key)) params.set(key, candidate.searchParams.get(key)!)
  params.set('articleId', articleId)
  if (scriptId) params.set('scriptId', scriptId)
  else params.delete('scriptId')
  params.set('section', 'production')
  return '/?' + params
}

export const withStudio = (path: string, studio: string) => path + '?' + new URLSearchParams({ studio })
export const jobUrl = (id: string, origin: string) => `/jobs/${id}?` + new URLSearchParams({ returnTo: safeReturn(origin) })
export function pageOffset(value: string | null) {
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= 0 ? Math.floor(number / 20) * 20 : 0
}
