import { ApiError } from '../api/client'

interface KeyStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}

export function createRequestRunner(storage: () => KeyStorage | null, uuid = () => crypto.randomUUID()) {
  const pending = new Map<string, string>()
  const running = new Map<string, Promise<unknown>>()
  return function run<T>(operation: string, target: string, config: unknown, request: (key: string) => Promise<T>): Promise<T> {
    const identity = canonical({ operation, target, config })
    const existing = running.get(identity)
    if (existing) return existing as Promise<T>
    const task = (async () => {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(identity))
      const slot = 'echoflow.pending.' + Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
      let store: KeyStorage | null = null
      try { store = storage() } catch { /* Browser storage can be disabled. */ }
      let key = pending.get(slot)
      try { key ||= store?.getItem(slot) || undefined } catch { /* Use the in-memory key. */ }
      key ||= uuid()
      pending.set(slot, key)
      try { store?.setItem(slot, key) } catch { /* Retain the in-memory key. */ }
      const clear = () => { pending.delete(slot); try { store?.removeItem(slot) } catch { /* Storage is optional. */ } }
      try {
        const result = await request(key)
        clear()
        return result
      } catch (error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) clear()
        throw error
      }
    })()
    running.set(identity, task)
    void task.finally(() => { if (running.get(identity) === task) running.delete(identity) }).catch(() => undefined)
    return task
  }
}

export const submitRequest = createRequestRunner(() => typeof window === 'undefined' ? null : window.sessionStorage)
