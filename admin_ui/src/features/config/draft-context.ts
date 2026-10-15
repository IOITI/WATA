import { createContext, useContext, useEffect, useId } from 'react'

import type { JsonObject, JsonValue } from '@/lib/types'

import type { ConfigPath } from './configModel'

/** The configuration being edited, shared by every field of the form. */
export interface DraftContextValue {
  draft: JsonObject
  update: (path: ConfigPath, value: JsonValue) => void
  disabled: boolean
  /**
   * A field tells the form that what is typed in it cannot be used. The draft
   * keeps its previous value meanwhile, so saving has to wait for the field.
   */
  reportInvalid: (fieldId: string, invalid: boolean) => void
}

export const DraftContext = createContext<DraftContextValue | null>(null)

export function useDraft(): DraftContextValue {
  const context = useContext(DraftContext)
  if (!context) throw new Error('Config fields must be used inside <DraftProvider>')
  return context
}

/** Whether what is typed in a number field can be used as a number. */
export function isNumberText(text: string): boolean {
  return text.trim() !== '' && Number.isFinite(Number(text))
}

/** Keeps the form informed of a field's validity, for as long as the field is on screen. */
export function useReportInvalid(invalid: boolean): void {
  const { reportInvalid } = useDraft()
  const fieldId = useId()
  useEffect(() => {
    reportInvalid(fieldId, invalid)
    return () => reportInvalid(fieldId, false)
  }, [reportInvalid, fieldId, invalid])
}
