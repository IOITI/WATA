// An entry of the AI agent's memory written or changed by the operator: its name (made from its description until
// typed), kind, line and text, and whether the agent may only read it. A change is sent with the revision the page
// showed: one the agent made meanwhile is never written over unread (409).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  Button,
  FormDescription,
  FormItem,
  FormLabel,
  Input,
  SegmentedControl,
  SettleModal,
  StretchSwitch,
  Textarea,
} from 'ioiti-ui'
import { useId, useState } from 'react'

import { ApiError, api } from '@/lib/api'
import type { AgentMemoryDraft, AgentMemoryEntry, AgentMemoryKind } from '@/lib/types'

import { draftOf, draftProblem, KIND_HINT, KIND_LABEL, KINDS, MAX_NAME, nameFrom, nameProblem } from './memoryModel'

function errorText(error: Error | null): string | null {
  if (error instanceof ApiError && error.status === 409) {
    return `${error.message}. Close this and open the entry again to see what it says now.`
  }
  return error?.message ?? null
}

export function MemoryEntryDialog({ entry, limits, open, onClose, onSaved }: {
  /** The entry changed, or null for a new one. */
  entry: AgentMemoryEntry | null
  limits: { description: number; body: number }
  open: boolean
  onClose: () => void
  onSaved: (saved: AgentMemoryEntry) => void
}) {
  const queryClient = useQueryClient()
  const id = useId()
  const [draft, setDraft] = useState<AgentMemoryDraft>(() => draftOf(entry))
  // A new entry's name follows its description until it is typed.
  const [named, setNamed] = useState(entry !== null)
  const save = useMutation({
    mutationFn: () => {
      const sent = { ...draft, description: draft.description.trim(), body: draft.body.trim() }
      return entry ? api.changeAgentMemory(entry.name, { ...sent, revision: entry.revision }) : api.createAgentMemory(sent)
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['agent-memory'] })
      onSaved(saved)
    },
  })
  const change = (part: Partial<AgentMemoryDraft>) => setDraft((current) => ({ ...current, ...part }))
  const problem = draftProblem(draft, limits)
  const nameSaid = draft.name ? nameProblem(draft.name) : null

  return (
    <SettleModal
      open={open}
      onClose={save.isPending ? () => {} : onClose}
      title={entry ? `Change ${entry.name}` : 'A new entry of its memory'}
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button size="sm" isLoading={save.isPending} disabled={!!problem} onClick={() => save.mutate()}>
            {entry ? 'Save the change' : 'Save the entry'}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (!problem) save.mutate()
        }}
      >
        <FormItem>
          <FormLabel htmlFor={`${id}-description`}>When it matters</FormLabel>
          <Input
            id={`${id}-description`}
            value={draft.description}
            maxLength={limits.description}
            placeholder="e.g. The verdict first, five lines at most: I read the answers on a phone"
            onChange={(event) => {
              const description = event.target.value
              change(named ? { description } : { description, name: nameFrom(description) })
            }}
          />
          <FormDescription>
            The line its memory's list shows at each conversation's start ({draft.description.length} of {limits.description}).
          </FormDescription>
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-name`}>Name</FormLabel>
          <Input
            id={`${id}-name`}
            value={draft.name}
            maxLength={MAX_NAME}
            spellCheck={false}
            autoComplete="off"
            className="font-mono"
            onChange={(event) => {
              setNamed(true)
              change({ name: event.target.value })
            }}
          />
          <FormDescription className={nameSaid ? 'text-negative' : undefined}>
            {nameSaid ?? 'How it is named in its list and in its tools: lowercase letters, digits and dashes.'}
          </FormDescription>
        </FormItem>
        <FormItem>
          <FormLabel id={`${id}-kind`}>Kind</FormLabel>
          <SegmentedControl<AgentMemoryKind>
            labelledBy={`${id}-kind`}
            size="sm"
            options={KINDS.map((kind) => ({ value: kind, label: KIND_LABEL[kind] }))}
            value={draft.kind}
            onChange={(kind) => change({ kind })}
          />
          <FormDescription>{KIND_HINT[draft.kind]}</FormDescription>
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-body`}>What it should remember</FormLabel>
          <Textarea
            id={`${id}-body`}
            rows={8}
            value={draft.body}
            maxLength={limits.body}
            placeholder="Markdown. Say what it rests on (a run, a day, your words), and date what can change."
            onChange={(event) => change({ body: event.target.value })}
          />
          <FormDescription>
            {draft.body.length.toLocaleString('en-GB')} of {limits.body.toLocaleString('en-GB')} characters. It goes to
            Anthropic with every conversation: never a key, a token or a password.
          </FormDescription>
        </FormItem>
        <StretchSwitch
          label="The agent may only read it"
          description="Locked, it is read at each conversation and never changed or forgotten by the agent."
          checked={draft.locked}
          onChange={(locked) => change({ locked })}
        />
        {save.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{errorText(save.error)}</AlertDescription>
          </Alert>
        )}
      </form>
    </SettleModal>
  )
}
