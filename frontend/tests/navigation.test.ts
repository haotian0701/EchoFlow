import { expect, it } from 'vitest'
import { jobUrl, pageOffset, safeReturn, studioUrl, withStudio } from '../src/domain/navigation'

const article = '11111111-1111-4111-8111-111111111111'
const script = '22222222-2222-4222-8222-222222222222'

it.each(['https://evil.example/', '//evil.example/', '/\\evil.example/', '/unknown', 'javascript:alert(1)', '/review/not-a-uuid', '/\n/jobs'])('rejects unsupported return targets: %s', value => {
  expect(safeReturn(value, '/audio')).toBe('/audio')
})
it('preserves list context without nesting navigation metadata', () => {
  const home = studioUrl(article, script, '/?q=NASA&offset=20&tab=imports&importOffset=40&studio=ignored&returnTo=ignored')
  const url = new URL(home, 'https://echoflow.invalid')
  expect(url.searchParams.get('articleId')).toBe(article)
  expect(url.searchParams.get('scriptId')).toBe(script)
  expect(url.searchParams.get('q')).toBe('NASA')
  expect(url.searchParams.get('offset')).toBe('20')
  expect(url.searchParams.get('tab')).toBe('imports')
  expect(url.searchParams.has('returnTo')).toBe(false)
  const review = withStudio(`/review/${script}`, home)
  expect(safeReturn(review)).toBe(review)
  expect(new URL(jobUrl(script, review), 'https://echoflow.invalid').searchParams.get('returnTo')).toBe(review)
})
it('falls back to the article when a standalone deep link has no studio context', () => {
  expect(studioUrl(article, script, `/audio/${script}`)).toContain('articleId=' + article)
})
it.each([['-1', 0], ['NaN', 0], ['35', 20], [null, 0], ['40', 40]])('normalizes list offset %s', (value, expected) => {
  expect(pageOffset(value as string | null)).toBe(expected)
})
