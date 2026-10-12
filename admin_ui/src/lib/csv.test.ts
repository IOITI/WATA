import { describe, expect, it } from 'vitest'

import { toCsv } from './csv'

interface Row {
  name: string | null
  profit: number | null
}

const COLUMNS = [
  { header: 'instrument', value: (row: Row) => row.name },
  { header: 'profit_loss', value: (row: Row) => row.profit },
]

describe('toCsv', () => {
  it('writes a header and one line per row', () => {
    const csv = toCsv([{ name: 'TURBO A', profit: 12.5 }], COLUMNS)

    expect(csv).toBe('instrument,profit_loss\r\nTURBO A,12.5\r\n')
  })

  it('quotes text that contains a separator, a quote or a line break', () => {
    const csv = toCsv([{ name: 'TURBO "A", long\nKO 24150', profit: 1 }], COLUMNS)

    expect(csv).toContain('"TURBO ""A"", long\nKO 24150",1')
  })

  it('keeps negative numbers as numbers', () => {
    expect(toCsv([{ name: 'TURBO A', profit: -102.4 }], COLUMNS)).toContain('TURBO A,-102.4')
  })

  it('stops a text cell from being read as a formula by a spreadsheet', () => {
    const csv = toCsv(
      [
        { name: '=HYPERLINK("http://evil.example")', profit: 1 },
        { name: '+1+1', profit: 1 },
        { name: '-2', profit: 1 },
        { name: '@SUM(A1)', profit: 1 },
      ],
      COLUMNS,
    )

    const lines = csv.split('\r\n')
    expect(lines[1]).toBe(`"'=HYPERLINK(""http://evil.example"")",1`)
    expect(lines[2]).toBe(`'+1+1,1`)
    expect(lines[3]).toBe(`'-2,1`)
    expect(lines[4]).toBe(`'@SUM(A1),1`)
  })

  it('leaves missing and non-finite values empty', () => {
    const csv = toCsv([{ name: null, profit: null }, { name: 'x', profit: Number.NaN }], COLUMNS)

    expect(csv).toBe('instrument,profit_loss\r\n,\r\nx,\r\n')
  })
})
