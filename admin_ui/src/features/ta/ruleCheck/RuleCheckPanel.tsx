import { Button, Checkbox, CheckIcon, CheckList, Hint, Panel } from 'ioiti-ui'
import { useState } from 'react'
import type { ReactNode } from 'react'

import type { AlertCandidate, RuleCheckReport, RuleFinding } from '@/lib/types'

import { notesOf, placeSaid, sectionsOf, TESTING_SAID, testedSaid, verdictSaid, verdictState } from './ruleCheckModel'

export type ShownReport = Pick<RuleCheckReport, 'findings' | 'verdict' | 'tested' | 'alerts' | 'alert_calls' | 'candidates'>

/**
 * Whether a script can become a signal rule as it is, built as a backtest's "Can it be believed?": its verdict
 * over what its source says and what a run test on recorded bars found, in three parts (repainting, its alerts,
 * what WATA serves otherwise), each finding leading to its line. Then the events it only draws, alerts it could
 * have, and what is only worth knowing. Nothing here trades: a run test is a Pine study, asked here.
 */
export function RuleCheckPanel({
  report,
  testing = null,
  onTest,
  testLabel = 'Test on recorded bars',
  testRefusal = null,
  testPending = false,
  lineHref,
  onAddAlerts,
  addAlertsLabel = 'Add them in the editor',
  aside,
  title = 'Ready for a signal rule?',
}: {
  report: ShownReport
  testing?: 'queued' | 'testing' | null
  /** Asks a run test; none where it cannot be asked (a draft not saved). */
  onTest?: () => void
  testLabel?: string
  /** Why a run test cannot be asked now, said on its button. */
  testRefusal?: string | null
  testPending?: boolean
  lineHref?: (finding: RuleFinding) => string | null
  /** Writes the chosen alerts in: the script's editor, unsaved. */
  onAddAlerts?: (chosen: AlertCandidate[]) => void
  addAlertsLabel?: string
  aside?: ReactNode
  title?: string
}) {
  const notes = notesOf(report)
  const test = onTest ? (
    <Hint content={testRefusal ?? 'Runs it on the days WATA recorded, as a backtest does, then ended at chosen bars, as live had them'} wrap={Boolean(testRefusal)}>
      <Button variant="outline" size="sm" disabled={Boolean(testRefusal) || testing !== null} isLoading={testPending} onClick={onTest}>
        {testing ? TESTING_SAID[testing] : testLabel}
      </Button>
    </Hint>
  ) : null

  return (
    <Panel title={title} description="What its source says, and what a run test on recorded bars found. Nothing here trades.">
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <p className="flex items-start gap-2 text-sm font-medium">
              <CheckIcon state={verdictState(report)} named={false} />
              {verdictSaid(report)}
            </p>
            <p className="text-xs text-muted-foreground">{testing ? `${TESTING_SAID[testing]}…` : testedSaid(report.tested)}</p>
          </div>
          {test}
        </div>
        {sectionsOf(report).map((section) => (
          <section key={section.id} aria-label={section.label} className="space-y-2">
            <div>
              <h3 className="text-sm font-medium">{section.label}</h3>
              <p className="text-xs text-muted-foreground">{section.said}</p>
            </div>
            <CheckList
              checks={section.checks}
              label={section.label}
              hrefOf={(check) => (check.finding && lineHref ? lineHref(check.finding) : null)}
              className="md:grid-cols-2"
            />
          </section>
        ))}
        {report.candidates.length > 0 && <Candidates candidates={report.candidates} onAdd={onAddAlerts} label={addAlertsLabel} />}
        {notes.length > 0 && (
          <section aria-label="Worth knowing" className="space-y-2">
            <h3 className="text-sm font-medium">Worth knowing</h3>
            <ul className="space-y-1.5 text-xs text-muted-foreground">
              {notes.map((finding) => {
                const place = placeSaid(finding)
                const href = lineHref?.(finding) ?? null
                return (
                  <li key={finding.id}>
                    {place && href ? (
                      <a href={href} className="font-medium text-foreground underline-offset-4 hover:underline">
                        {place}
                      </a>
                    ) : (
                      place && <span className="font-medium text-foreground">{place}</span>
                    )}
                    {place ? ': ' : ''}
                    {finding.message}
                  </li>
                )
              })}
            </ul>
          </section>
        )}
        {aside}
      </div>
    </Panel>
  )
}

/** The events a script draws, alerts it could have: each chosen, then written in after the line it is drawn on. */
function Candidates({ candidates, onAdd, label }: { candidates: AlertCandidate[]; onAdd?: (chosen: AlertCandidate[]) => void; label: string }) {
  const [left, setLeft] = useState<Set<string>>(new Set())
  const chosen = candidates.filter((candidate) => !left.has(candidate.title))
  return (
    <section aria-label="Alerts it could have" className="space-y-2">
      <div>
        <h3 className="text-sm font-medium">Alerts it could have</h3>
        <p className="text-xs text-muted-foreground">
          The events it draws, or acts on under an if: an alertcondition() on each, titled as it is drawn, makes them signals a rule can map.
        </p>
      </div>
      <ul className="space-y-2 text-sm">
        {candidates.map((candidate) => (
          <li key={`${candidate.call}:${candidate.line}:${candidate.title}`} className="space-y-0.5">
            {onAdd ? (
              <Checkbox
                label={`“${candidate.title}”`}
                checked={!left.has(candidate.title)}
                onChange={(event) =>
                  setLeft((before) => {
                    const after = new Set(before)
                    if (event.target.checked) after.delete(candidate.title)
                    else after.add(candidate.title)
                    return after
                  })
                }
              />
            ) : (
              <span className="font-medium">“{candidate.title}”</span>
            )}
            <p className="text-xs text-muted-foreground">
              <code className="font-mono">{candidate.condition}</code>, from its {candidate.call} of line {candidate.line}
              {candidate.risks.length > 0 ? ': it reads what may repaint, see Repainting above' : ''}.
            </p>
          </li>
        ))}
      </ul>
      {onAdd && (
        <Button size="sm" disabled={chosen.length === 0} onClick={() => onAdd(chosen)}>
          {label}
        </Button>
      )}
    </section>
  )
}
