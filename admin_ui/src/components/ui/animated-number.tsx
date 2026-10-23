'use client';

import React, { useEffect } from 'react';
import { motion, useReducedMotion, useSpring, useTransform } from 'framer-motion';
import { cn } from '../../lib/utils';

export interface AnimatedNumberProps {
  /** Numeric value to animate to */
  value: number;
  /** Number of decimal places (default: 0) */
  decimals?: number;
  /** Prepend text or currency symbol (e.g. "$", "+") */
  prefix?: string;
  /** Append text (e.g. "%", " users", "ms") */
  suffix?: string;
  /** WATA: the value as written (the admin's formats: sign, € and %), in place of the formatting below: its
   * digits roll, the rest stays. */
  text?: string;
  /** Spring physics stiffness */
  stiffness?: number;
  /** Spring physics damping */
  damping?: number;
  /** Mass of spring */
  mass?: number;
  /** Format as locale string (e.g. "1,250") */
  useGrouping?: boolean;
  /** Compact notation (e.g. 1.2M, 45K) */
  compact?: boolean;
  /** Custom class name */
  className?: string;
}

// WATA: each character, digit or not, in a box of one line's height, so that they all share one baseline with the
// text around them: the box is clipped by clip-path, which leaves the baseline where it is (EasyUI's overflow
// hidden moved it to the bottom of the box, and its digits stood higher than a sign or a % beside them).
const BOX = 'inline-flex h-[1.15em] leading-[1.15em] [clip-path:inset(0)]';

// Single Digit Rolling Column
const DigitColumn: React.FC<{
  digit: string;
  stiffness?: number;
  damping?: number;
  mass?: number;
}> = ({ digit, stiffness = 160, damping = 18, mass = 0.8 }) => {
  const isNumber = !isNaN(parseInt(digit, 10));
  const numValue = isNumber ? parseInt(digit, 10) : 0;

  const spring = useSpring(numValue, {
    stiffness,
    damping,
    mass,
  });

  const y = useTransform(spring, (current) => `-${current * 10}%`);

  useEffect(() => {
    spring.set(numValue);
  }, [spring, numValue]);

  if (!isNumber) {
    return <span className={BOX}>{digit}</span>;
  }

  // WATA: as wide as its digits (tabular figures of the admin's font, not EasyUI's monospace at 0.58em); as tall as
  // its ten digits (`self-start`: stretched to the box, one digit tall, a roll of 10 % moved it by a tenth of one).
  return (
    <span className={BOX}>
      <motion.span style={{ y }} className="flex flex-col self-start">
        {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
          <span key={n} className="h-[1.15em]">
            {n}
          </span>
        ))}
      </motion.span>
    </span>
  );
};

export const AnimatedNumber: React.FC<AnimatedNumberProps> = ({
  value,
  decimals = 0,
  prefix = '',
  suffix = '',
  text,
  stiffness = 170,
  damping = 22,
  mass = 0.6,
  useGrouping = true,
  compact = false,
  className,
}) => {
  const reduce = useReducedMotion();
  const formatNumber = (val: number): string => {
    if (compact) {
      const formatter = new Intl.NumberFormat('en-US', {
        notation: 'compact',
        maximumFractionDigits: decimals,
        minimumFractionDigits: decimals,
      });
      return formatter.format(val);
    }

    const fixed = Math.abs(val).toFixed(decimals);
    const [intPart, decPart] = fixed.split('.');

    let formattedInt = intPart;
    if (useGrouping) {
      formattedInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    const sign = val < 0 ? '-' : '';
    return decPart !== undefined ? `${sign}${formattedInt}.${decPart}` : `${sign}${formattedInt}`;
  };

  const formattedStr = text ?? formatNumber(value);
  const chars = formattedStr.split('');
  const label = text ?? `${prefix}${formattedStr}${suffix}`;

  // WATA: still where motion is reduced: the value as text.
  if (reduce) {
    return <span className={cn('tabular-nums', className)}>{label}</span>;
  }

  // WATA: read as the value it shows (an aria-label on a span is not), its rolling digits hidden from screen
  // readers; colours inherited (EasyUI's text was white whatever the theme).
  return (
    <span className={cn('inline-flex items-baseline tabular-nums', className)}>
      <span className="sr-only">{label}</span>
      <span aria-hidden="true" className="inline-flex items-baseline select-none">
        {!text && prefix && <span className="mr-0.5">{prefix}</span>}
        {chars.map((char, index) => (
          <DigitColumn
            key={`${index}-${/\d/.test(char) ? 'num' : char}`}
            digit={char}
            stiffness={stiffness}
            damping={damping}
            mass={mass}
          />
        ))}
        {!text && suffix && <span className="ml-0.5">{suffix}</span>}
      </span>
    </span>
  );
};
