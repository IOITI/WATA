import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OTPInput } from './ui/otp-input'

// As the login page has it: left to itself, each digit stays in the box it was typed in.
function renderCode() {
  const onComplete = vi.fn()
  render(<OTPInput id="code" aria-label="One-time code" onComplete={onComplete} />)
  const boxes = screen.getAllByRole('textbox') as HTMLInputElement[]
  return { boxes, onComplete, values: () => boxes.map((box) => box.value).join('|') }
}

describe('the one-time code', () => {
  it('moves on a box at each digit, back at Backspace, and says when the code is whole', async () => {
    const user = userEvent.setup()
    const { boxes, onComplete, values } = renderCode()
    expect(screen.getByRole('group', { name: 'One-time code' })).toBeInTheDocument()
    expect(boxes[0]).toHaveAttribute('id', 'code')

    await user.click(boxes[0])
    await user.keyboard('12a3')
    expect(values()).toBe('1|2|3|||')
    expect(boxes[3]).toHaveFocus()
    await user.keyboard('{Backspace}')
    expect(values()).toBe('1|2||||')
    await user.keyboard('3456')
    expect(onComplete).toHaveBeenCalledWith('123456')
  })

  it('takes several digits arriving in one box, as an autofill or a keyboard gives them', () => {
    const { boxes, onComplete, values } = renderCode()

    // A few digits go on from the box they arrived in.
    fireEvent.change(boxes[1], { target: { value: '0128' } })
    expect(values()).toBe('|0|1|2|8|')
    expect(boxes[5]).toHaveFocus()
    expect(onComplete).not.toHaveBeenCalled()

    // A whole code fills every box, wherever it arrived.
    fireEvent.change(boxes[3], { target: { value: '654 321' } })
    expect(values()).toBe('6|5|4|3|2|1')
    expect(onComplete).toHaveBeenLastCalledWith('654321')
  })

  it('replaces a box’s digit by one typed beside it, and keeps it when only a letter is', () => {
    const { boxes, values } = renderCode()
    fireEvent.change(boxes[0], { target: { value: '3' } })

    fireEvent.change(boxes[0], { target: { value: '3x' } })
    expect(values()).toBe('3|||||')
    fireEvent.change(boxes[0], { target: { value: '37' } })
    expect(values()).toBe('7|||||')
  })
})
