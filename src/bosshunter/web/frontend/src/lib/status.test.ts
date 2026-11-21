import { describe, expect, it } from 'vitest'
import { APPLIED_JOB_STATUSES, isAppliedJobStatus } from './status'

describe('applied job statuses', () => {
  it('covers exactly the five post-send statuses', () => {
    expect([...APPLIED_JOB_STATUSES]).toEqual(['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'])
  })

  it('recognises applied statuses and rejects pre-send ones', () => {
    for (const status of APPLIED_JOB_STATUSES) {
      expect(isAppliedJobStatus(status)).toBe(true)
    }
    for (const status of ['pending', 'scored', 'ready', 'approved', 'rejected', 'error', 'filtered', 'skipped']) {
      expect(isAppliedJobStatus(status)).toBe(false)
    }
    expect(isAppliedJobStatus(null)).toBe(false)
    expect(isAppliedJobStatus(undefined)).toBe(false)
  })
})
