import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'

export function useResource<T>(key: string | null, read: (signal: AbortSignal) => Promise<T>, pollWhile?: (data: T) => boolean) {
  const reader = useRef(read); reader.current = read
  const polling = useRef(pollWhile); polling.current = pollWhile
  const currentKey = useRef(key); currentKey.current = key
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string; loading: boolean }>({ key, data: null, error: '', loading: !!key })
  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let previous: T | null = null
    const valid = () => !controller.signal.aborted && currentKey.current === key
    setState(old => ({ key, data: old.key === key ? old.data : null, error: '', loading: true }))
    async function load() {
      try {
        const data = await reader.current(controller.signal)
        if (!valid()) return
        previous = data
        setState({ key, data, error: '', loading: false })
        if (polling.current?.(data)) timer = setTimeout(() => void load(), 2000)
      } catch (error) {
        if (!valid()) return
        setState(old => ({ key, data: old.key === key ? old.data : previous, error: error instanceof Error ? error.message : 'Could not connect. Try refreshing.', loading: false }))
        if (!(error instanceof ApiError && error.status >= 400 && error.status < 500)) timer = setTimeout(() => void load(), 2000)
      }
    }
    void load()
    const focus = () => { if (!controller.signal.aborted) setRevision(value => value + 1) }
    window.addEventListener('focus', focus)
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('focus', focus) }
  }, [key, revision])
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  const update = useCallback((change: (data: T) => T) => setState(old => old.key === currentKey.current && old.data ? { ...old, data: change(old.data) } : old), [])
  return { ...(state.key === key ? state : { data: null, error: '', loading: !!key }), refresh, update }
}
