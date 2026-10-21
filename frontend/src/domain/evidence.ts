// API offsets count Unicode code points (Python strings), rather than UTF-16 units.
export function textRange(text: string, start: number | null, end: number | null) {
  const chars = Array.from(text)
  if (start == null || end == null || !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > chars.length) return null
  return { before: chars.slice(0, start).join(''), quote: chars.slice(start, end).join(''), after: chars.slice(end).join('') }
}
