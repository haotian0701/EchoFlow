import type { EvaluationIssue, IssueSeverity } from '../api/types'

export type IssueFilter = 'all' | IssueSeverity
export const issueFilters = ['all', 'critical', 'warning', 'info'] as const
const severityOrder: Record<IssueSeverity, number> = { critical: 0, warning: 1, info: 2 }

export function visibleIssues(issues: readonly EvaluationIssue[], filter: IssueFilter): EvaluationIssue[] {
  return issues.filter(issue => filter === 'all' || issue.severity === filter)
    .sort((a, b) => severityOrder[a.severity] - severityOrder[b.severity])
}
