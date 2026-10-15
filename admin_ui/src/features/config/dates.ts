/** The config writes market-closed dates as DD/MM/YYYY. */
const CONFIG_DATE = /^(\d{2})\/(\d{2})\/(\d{4})$/

export function toConfigDate(date: Date): string {
  const day = String(date.getDate()).padStart(2, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  return `${day}/${month}/${date.getFullYear()}`
}

/** The local date a config date stands for, or null when it is not a real DD/MM/YYYY date. */
export function fromConfigDate(text: string): Date | null {
  const match = CONFIG_DATE.exec(text)
  if (!match) return null
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(year, month - 1, day)
  // new Date() rolls 31/02 over to March: such a date is not one.
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null
}

/** In calendar order, without duplicates; anything unreadable goes last, as written. */
export function sortConfigDates(dates: readonly string[]): string[] {
  return [...new Set(dates)].sort((a, b) => {
    const [first, second] = [fromConfigDate(a), fromConfigDate(b)]
    if (first && second) return first.getTime() - second.getTime()
    if (first) return -1
    if (second) return 1
    return a.localeCompare(b)
  })
}
