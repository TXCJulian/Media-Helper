import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import EncoderSelect from '@/components/encoder/EncoderSelect'

describe('EncoderSelect', () => {
  const options = [
    { value: 'nvenc', label: 'NVIDIA NVENC' },
    { value: 'qsv', label: 'Intel QuickSync' },
    { value: 'software', label: 'Software (x265)', disabled: true },
  ]

  it('renders accessible combobox trigger button with aria-label', () => {
    const onChange = vi.fn()
    render(
      <EncoderSelect
        value="nvenc"
        options={options}
        onChange={onChange}
        aria-label="Video encoder"
      />,
    )

    const button = screen.getByRole('combobox', { name: 'Video encoder' })
    expect(button).toBeTruthy()
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(button.getAttribute('aria-haspopup')).toBe('listbox')
    expect(button.textContent).toContain('NVIDIA NVENC')
  })

  it('opens listbox and sets aria-activedescendant during keyboard navigation', () => {
    const onChange = vi.fn()
    render(
      <EncoderSelect
        value="nvenc"
        options={options}
        onChange={onChange}
        aria-label="Video encoder"
      />,
    )

    const button = screen.getByRole('combobox', { name: 'Video encoder' })
    fireEvent.click(button)

    expect(button.getAttribute('aria-expanded')).toBe('true')
    const listbox = screen.getByRole('listbox', { name: 'Video encoder' })
    expect(listbox).toBeTruthy()
    expect(button.getAttribute('aria-controls')).toBe(listbox.id)

    // Option 0 (nvenc) is active
    const activeOptionId = button.getAttribute('aria-activedescendant')
    expect(activeOptionId).toBeTruthy()
    const activeOption = document.getElementById(activeOptionId!)
    expect(activeOption?.textContent).toContain('NVIDIA NVENC')
    expect(activeOption?.getAttribute('aria-selected')).toBe('true')

    // Navigate down to QSV
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    const nextOptionId = button.getAttribute('aria-activedescendant')
    expect(nextOptionId).not.toBe(activeOptionId)
    const nextOption = document.getElementById(nextOptionId!)
    expect(nextOption?.textContent).toContain('Intel QuickSync')

    // Press Enter to select
    fireEvent.keyDown(button, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        target: { value: 'qsv' },
      }),
    )
    expect(button.getAttribute('aria-expanded')).toBe('false')
  })

  it('does not select disabled options on Enter', () => {
    const onChange = vi.fn()
    render(
      <EncoderSelect
        value="nvenc"
        options={options}
        onChange={onChange}
        aria-label="Video encoder"
      />,
    )

    const button = screen.getByRole('combobox', { name: 'Video encoder' })
    fireEvent.click(button)

    // End key moves focus to disabled option
    fireEvent.keyDown(button, { key: 'End' })
    const disabledOptionId = button.getAttribute('aria-activedescendant')
    const disabledOption = document.getElementById(disabledOptionId!)
    expect(disabledOption?.getAttribute('aria-disabled')).toBe('true')

    fireEvent.keyDown(button, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('closes on Escape and keeps focus on trigger', () => {
    const onChange = vi.fn()
    render(
      <EncoderSelect
        value="nvenc"
        options={options}
        onChange={onChange}
        aria-label="Video encoder"
      />,
    )

    const button = screen.getByRole('combobox', { name: 'Video encoder' })
    fireEvent.click(button)
    expect(button.getAttribute('aria-expanded')).toBe('true')

    fireEvent.keyDown(button, { key: 'Escape' })
    expect(button.getAttribute('aria-expanded')).toBe('false')
  })
})
