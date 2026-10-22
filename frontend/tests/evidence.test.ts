import { expect, it } from 'vitest'
import { textRange } from '../src/domain/evidence'

it('uses API code-point offsets after emoji and preserves German characters', () => {
  expect(textRange('🌍 Die Größe beträgt 20.', 6, 11)).toEqual({ before: '🌍 Die ', quote: 'Größe', after: ' beträgt 20.' })
})
it.each([[null, null], [-1, 3], [2, 2], [4, 100], [0.5, 2]])('does not invent a highlight for invalid offsets %s:%s', (start, end) => {
  expect(textRange('source text', start, end)).toBeNull()
})
