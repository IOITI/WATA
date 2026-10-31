// A run's tags as chips (ioiti-ui's MultiSelect): the tags the runs carry offered, a new one made by typing
// it. The run page's "Name and tags" and the form's last step use it, within the limits the server checks.

import { useQuery } from '@tanstack/react-query'
import { MultiSelect } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { BacktestOptions } from '@/lib/types'

import { tagOf, tagsInUse } from './model'

export function TagsField({
  value,
  onChange,
  limits,
  id,
}: {
  value: string[]
  onChange: (tags: string[]) => void
  limits: Pick<BacktestOptions['limits'], 'tags' | 'tag_length'> | undefined
  id?: string
}) {
  const listQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests })
  const options = [...new Set([...tagsInUse(listQuery.data?.items ?? []), ...value])].map((tag) => ({ value: tag, label: tag }))
  const most = limits?.tags
  return (
    <MultiSelect
      id={id}
      label="Tags"
      options={options}
      value={value}
      onChange={(next) => onChange(most ? next.slice(0, most) : next)}
      onCreateOption={(text) => tagOf(text, limits?.tag_length ?? 24)}
      placeholder={value.length === 0 ? 'Type a tag, or choose one' : undefined}
      emptyText="No run has a tag yet: type one"
    />
  )
}
