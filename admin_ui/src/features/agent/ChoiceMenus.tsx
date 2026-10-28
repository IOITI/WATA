// The model and the effort a request goes to, as two menus: in the composer under the field, and in the form of a
// research task. A model the key may not use is shown and cannot be chosen; one that cannot read the notes others
// wrote in the conversation says so.

import { Button, DropdownMenu } from 'ioiti-ui'
import { ChevronDown } from 'lucide-react'

import type { AgentChoice, AgentEffort, AgentModel, AgentState } from '@/lib/types'

import { EFFORT_SAID, EFFORTS, modelSaid, shortModel, unreadBy } from './model'

const NO_WRITERS: ReadonlySet<string> = new Set()

export function ChoiceMenus({ state, choice, onChoose, writers = NO_WRITERS }: {
  state: AgentState | undefined
  choice: AgentChoice
  onChoose: (choice: AgentChoice) => void
  /** The models whose notes the conversation holds. */
  writers?: ReadonlySet<string>
}) {
  const models: AgentModel[] = state?.models ?? []
  const model = models.find((candidate) => candidate.id === choice.model)
  if (!model) return null
  const efforts = model.efforts ?? EFFORTS
  return (
    <>
      <DropdownMenu
        label="Model"
        side="top"
        trigger={
          <Button size="sm" variant="ghost" aria-label={`Model: ${model.label}`} rightIcon={<ChevronDown aria-hidden className="size-3.5" />}>
            {shortModel(model.label)}
          </Button>
        }
        items={[{
          type: 'radio',
          label: 'Model',
          value: choice.model,
          onValueChange: (next) => {
            const chosen = models.find((candidate) => candidate.id === next)
            // An effort the new model does not take: its nearest below, or its first.
            const effort = chosen && !chosen.efforts.includes(choice.effort)
              ? [...chosen.efforts].reverse().find((level) => EFFORTS.indexOf(level) <= EFFORTS.indexOf(choice.effort)) ?? chosen.efforts[0]
              : choice.effort
            onChoose({ model: next, effort })
          },
          options: models.map((candidate) => {
            const lost = unreadBy(candidate, writers, models)
            return {
              value: candidate.id,
              label: `${candidate.label}${candidate.id === state?.model ? ' (default)' : ''}`,
              description: `${modelSaid(candidate)}${lost.length && candidate.available !== false ? `. Reads none of ${lost.join(' and ')}'s notes here` : ''}`,
              disabled: candidate.available === false,
            }
          }),
        }]}
      />
      <DropdownMenu
        label="Effort"
        side="top"
        trigger={
          <Button size="sm" variant="ghost" aria-label={`Effort: ${choice.effort}`} rightIcon={<ChevronDown aria-hidden className="size-3.5" />}>
            {choice.effort}
          </Button>
        }
        items={[{
          type: 'radio',
          label: 'Effort: how much it thinks and acts',
          value: choice.effort,
          onValueChange: (next) => onChoose({ ...choice, effort: next as AgentEffort }),
          options: EFFORTS.filter((level) => efforts.includes(level)).map((level) => ({
            value: level,
            label: `${level}${level === state?.effort ? ' (default)' : ''}`,
            description: EFFORT_SAID[level],
          })),
        }]}
      />
    </>
  )
}
