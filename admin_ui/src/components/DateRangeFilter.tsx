import { Button } from '@/components/ui/button'
import type { DateRange } from '@/components/ui/calendar'
import { DateRangePicker } from '@/components/ui/date-picker'
import { ALL_TIME, lastDays, rangeParams } from '@/lib/dateRange'

const PRESETS = [
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
]

interface DateRangeFilterProps {
  value: DateRange
  onChange: (range: DateRange) => void
}

/** Presets first (the ranges people actually ask for), the calendar for anything else. */
export function DateRangeFilter({ value, onChange }: DateRangeFilterProps) {
  const isAllTime = !value.from && !value.to
  const current = rangeParams(value)
  const isPreset = (days: number) => {
    const preset = rangeParams(lastDays(days))
    return preset.from === current.from && preset.to === current.to
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      {PRESETS.map((preset) => (
        <Button
          key={preset.days}
          // Filled: the one cue that says which range the page shows.
          variant={isPreset(preset.days) ? 'primary' : 'outline'}
          size="sm"
          aria-pressed={isPreset(preset.days)}
          onClick={() => onChange(lastDays(preset.days))}
        >
          {preset.label}
        </Button>
      ))}
      <Button
        variant={isAllTime ? 'primary' : 'outline'}
        size="sm"
        aria-pressed={isAllTime}
        onClick={() => onChange(ALL_TIME)}
      >
        All time
      </Button>
      <DateRangePicker
        value={value}
        onChange={onChange}
        placeholder="Custom range"
        align="end"
        locale="en-GB"
        weekStartsOn={1}
      />
    </div>
  )
}
