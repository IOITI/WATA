import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { LinkProps } from 'react-router'

import { Button, buttonClassName } from '@/components/ui/button'
import type { ButtonSize, ButtonVariant } from '@/components/ui/button'

interface ButtonLinkProps extends Omit<LinkProps, 'to' | 'className'> {
  /** Where it leads. Nothing: a button that cannot be pressed, there being nowhere to go (no day before the first). */
  to: string | null | undefined
  variant?: ButtonVariant
  size?: ButtonSize
  leftIcon?: ReactNode
  rightIcon?: ReactNode
  className?: string
}

/**
 * What leads to another page, drawn as a button: a link, so that it can be opened in another tab
 * (a right click, a middle click, ⌘ or Ctrl and a click) as well as followed.
 */
export function ButtonLink({ to, variant = 'primary', size = 'md', leftIcon, rightIcon, className, children, ...props }: ButtonLinkProps) {
  if (!to) {
    return (
      <Button variant={variant} size={size} leftIcon={leftIcon} rightIcon={rightIcon} className={className} disabled>
        {children}
      </Button>
    )
  }
  return (
    <Link to={to} className={buttonClassName({ variant, size, className })} {...props}>
      {leftIcon && <span className="shrink-0">{leftIcon}</span>}
      {children && <span>{children}</span>}
      {rightIcon && <span className="shrink-0">{rightIcon}</span>}
    </Link>
  )
}
