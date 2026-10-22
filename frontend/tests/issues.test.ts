import { describe, expect, it } from 'vitest'
import { visibleIssues, type IssueFilter } from '../src/domain/issues'
import type { EvaluationIssue, IssueSeverity } from '../src/api/types'

const issue = (id: string, severity: IssueSeverity) => Object.freeze({ id, severity }) as EvaluationIssue
const issues = Object.freeze([issue('info-1', 'info'), issue('warning-1', 'warning'), issue('critical-1', 'critical'), issue('warning-2', 'warning'), issue('critical-2', 'critical')])

describe('review issue presentation', () => {
  it('sorts by severity, keeps same-severity order and leaves the report untouched', () => {
    const result = visibleIssues(issues, 'all')
    expect(result.map(item => item.id)).toEqual(['critical-1', 'critical-2', 'warning-1', 'warning-2', 'info-1'])
    expect(issues.map(item => item.id)).toEqual(['info-1', 'warning-1', 'critical-1', 'warning-2', 'critical-2'])
    expect(result[0]).toBe(issues[2])
  })
  it.each(['critical', 'warning', 'info'] as IssueFilter[])('filters %s without removing other report issues', filter => {
    expect(visibleIssues(issues, filter)).toEqual(issues.filter(item => item.severity === filter))
    expect(issues).toHaveLength(5)
  })
  it('returns an empty list for empty reports and unmatched severities', () => {
    expect(visibleIssues([], 'all')).toEqual([])
    expect(visibleIssues([issue('info', 'info')], 'critical')).toEqual([])
  })
})
