import { Download } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { downloadCsv, toCsv } from '@/lib/csv'
import type { CsvColumn } from '@/lib/csv'
import { toIsoDay } from '@/lib/format'

interface ExportButtonProps<T> {
  /** File name without date or extension, e.g. "wata-positions". */
  name: string
  rows: readonly T[]
  columns: readonly CsvColumn<T>[]
}

/** Saves the rows currently loaded on the page as a CSV file. */
export function ExportButton<T>({ name, rows, columns }: ExportButtonProps<T>) {
  return (
    <Button
      variant="outline"
      size="sm"
      leftIcon={<Download className="size-3.5" />}
      disabled={rows.length === 0}
      onClick={() => downloadCsv(`${name}-${toIsoDay(new Date())}.csv`, toCsv(rows, columns))}
    >
      Export CSV
    </Button>
  )
}
