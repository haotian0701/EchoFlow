import { jobStage } from '../domain/review'
import type { ProductionJob } from '../api/types'

export default function JobProgress({ job }: { job: ProductionJob }) {
  return <p className="job-stage" role="status"><strong>{jobStage(job)}</strong><span>Attempt {job.attempt_count} / {job.max_attempts}</span></p>
}
