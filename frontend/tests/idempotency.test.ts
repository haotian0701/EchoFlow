import { expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { createRequestRunner } from '../src/domain/idempotency'

function storage() {
  const values = new Map<string, string>()
  return { values, getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } }
}
const apiError = (status: number) => new ApiError(status, { code: 'TEST', message: 'Controlled error', request_id: 'test', details: {} })

it('reuses a pending key after response loss and a fresh page runner', async () => {
  const store = storage()
  const first = createRequestRunner(() => store, () => 'first-key')
  const keys: string[] = []
  await expect(first('evaluate', 'script-a', {}, async key => { keys.push(key); throw new TypeError('Response lost') })).rejects.toThrow()
  const restored = createRequestRunner(() => store, () => 'second-key')
  await restored('evaluate', 'script-a', {}, async key => { keys.push(key); return { id: 'existing-job' } })
  expect(keys).toEqual(['first-key', 'first-key'])
  expect(store.values.size).toBe(0)
  expect([...store.values.keys()].some(key => key.includes('script-a'))).toBe(false)
})
it('coalesces simultaneous submissions and creates a fresh key for a later intentional call', async () => {
  const store = storage()
  const random = vi.fn().mockReturnValueOnce('one').mockReturnValueOnce('two')
  const run = createRequestRunner(() => store, random)
  let finish!: (value: string) => void
  const request = vi.fn((key: string) => new Promise<string>(resolve => { finish = () => resolve(key) }))
  const first = run('generate', 'article-a', { length: 'short' }, request)
  const replay = run('generate', 'article-a', { length: 'short' }, request)
  expect(first).toBe(replay)
  await vi.waitFor(() => expect(request).toHaveBeenCalledOnce())
  finish('unused')
  expect(await first).toBe('one')
  expect(await run('generate', 'article-a', { length: 'short' }, async key => key)).toBe('two')
})
it.each([500, 503, 408])('retains keys for uncertain HTTP %s results', async status => {
  const store = storage()
  const run = createRequestRunner(() => store, () => 'pending')
  await expect(run('audio', 'script-a', {}, async () => { throw apiError(status) })).rejects.toThrow()
  expect(store.values.size).toBe(1)
  expect([...store.values.keys()][0]).toMatch(/^echoflow\.pending\.[0-9a-f]{64}$/)
  expect([...store.values.values()]).toEqual(['pending'])
})
it('releases a rejected request key and separates targets and changed configurations', async () => {
  const store = storage()
  const random = vi.fn().mockReturnValueOnce('one').mockReturnValueOnce('two').mockReturnValueOnce('three')
  const run = createRequestRunner(() => store, random)
  await expect(run('audio', 'a', { speed: 1 }, async () => { throw apiError(422) })).rejects.toThrow()
  expect(store.values.size).toBe(0)
  expect(await run('audio', 'a', { speed: 1.1 }, async key => key)).toBe('two')
  expect(await run('audio', 'b', { speed: 1.1 }, async key => key)).toBe('three')
})
it('handles disabled storage and equivalent object key ordering', async () => {
  const random = vi.fn().mockReturnValueOnce('same').mockReturnValueOnce('new')
  const run = createRequestRunner(() => { throw new Error('Storage disabled') }, random)
  await expect(run('generate', 'a', { style: 'news', length: 'short' }, async () => { throw new TypeError() })).rejects.toThrow()
  expect(await run('generate', 'a', { length: 'short', style: 'news' }, async key => key)).toBe('same')
})
