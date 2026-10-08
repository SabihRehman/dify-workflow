import type { FC } from 'react'
import { memo, useCallback, useEffect, useRef, useState } from 'react'

type OpeningTemplateProps = {
  html?: string
  className?: string
}

const MIN_HEIGHT = 40

/**
 * The template is author-provided raw HTML/CSS. It is rendered in an iframe with
 * `sandbox` (no `allow-scripts`) plus a CSP, so it can style content but can never
 * run code or reach the host page, even with `allow-same-origin` (needed only so the
 * parent can measure the content height).
 */
export const buildOpeningTemplateDocument = (html: string) =>
  `<!doctype html><html><head><meta charset="utf-8">` +
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline' https:; font-src https: data:; media-src https: data:; form-action 'none'">` +
  `<base target="_blank">` +
  `<style>html,body{margin:0;padding:0}body{font-family:inherit;overflow:hidden}</style>` +
  `</head><body>${html}</body></html>`

const OpeningTemplate: FC<OpeningTemplateProps> = ({ html, className }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const [height, setHeight] = useState(MIN_HEIGHT)

  const handleLoad = useCallback(() => {
    const doc = iframeRef.current?.contentDocument
    if (!doc?.body) return
    const measure = () => {
      const next = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight, MIN_HEIGHT)
      setHeight(next)
    }
    measure()
    observerRef.current?.disconnect()
    if (typeof ResizeObserver !== 'undefined') {
      observerRef.current = new ResizeObserver(measure)
      observerRef.current.observe(doc.body)
    }
  }, [])

  useEffect(() => () => observerRef.current?.disconnect(), [])

  if (!html?.trim()) return null

  return (
    <iframe
      ref={iframeRef}
      title="Conversation opener template"
      data-testid="opening-template"
      className={className}
      style={{ width: '100%', height, border: 0, display: 'block' }}
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      srcDoc={buildOpeningTemplateDocument(html)}
      onLoad={handleLoad}
    />
  )
}

export default memo(OpeningTemplate)
