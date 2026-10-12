export interface CsvColumn<T> {
  header: string
  value: (row: T) => string | number | null | undefined
}

/** Spreadsheets run a text cell that starts with one of these as a formula. */
const FORMULA_START = /^[=+\-@\t\r]/

function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
  // Numbers stay numbers; a text cell that could be read as a formula is neutralised.
  const text = FORMULA_START.test(value) ? `'${value}` : value
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

export function toCsv<T>(rows: readonly T[], columns: readonly CsvColumn<T>[]): string {
  const lines = [columns.map((column) => cell(column.header)).join(',')]
  for (const row of rows) lines.push(columns.map((column) => cell(column.value(row))).join(','))
  return `${lines.join('\r\n')}\r\n`
}

/** Hand `content` to the browser as a file to save. */
export function downloadCsv(filename: string, content: string): void {
  // The byte-order mark makes Excel read the file as UTF-8.
  const url = URL.createObjectURL(new Blob(['﻿', content], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
