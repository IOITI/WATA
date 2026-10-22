/** A check a page says the outcome of: passed, failed, or left open (not asked, not known yet). */
export type CheckState = 'pass' | 'fail' | 'open'

export interface Check {
  id: string
  label: string
  state: CheckState
  /** What was found, in a sentence. */
  detail: string
}
