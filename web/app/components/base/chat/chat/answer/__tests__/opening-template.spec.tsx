import { render, screen } from '@testing-library/react'
import OpeningTemplate, { buildOpeningTemplateDocument } from '../opening-template'

describe('OpeningTemplate', () => {
  it('renders nothing without html', () => {
    const { container } = render(<OpeningTemplate html="  " />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders html in a sandboxed iframe without script permission', () => {
    render(<OpeningTemplate html="<b>Hello</b>" />)
    const frame = screen.getByTestId('opening-template')
    expect(frame.getAttribute('sandbox')).not.toContain('allow-scripts')
    expect(frame.getAttribute('srcdoc')).toContain('<b>Hello</b>')
  })

  it('adds a CSP that blocks scripts', () => {
    expect(buildOpeningTemplateDocument('<p>x</p>')).toContain("default-src 'none'")
  })
})
