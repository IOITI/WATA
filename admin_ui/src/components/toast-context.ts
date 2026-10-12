import { createContext, useContext } from 'react'

import type { VelocityToastVariant } from '@/components/ui/velocity-toast'

export interface ToastMessage {
  title: string
  description?: string
  variant?: VelocityToastVariant
}

export type ShowToast = (message: ToastMessage) => void

export const ToastContext = createContext<ShowToast | null>(null)

export function useToast(): ShowToast {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}
