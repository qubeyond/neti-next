import { memo, useLayoutEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { safeURL } from './api'

const components: React.ComponentProps<typeof Markdown>['components'] = {
  a: ({ href, children }) => {
    const target = safeURL(href)
    return target ? <a href={target} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>
  },
  img: ({ src, alt }) => {
    const target = safeURL(src)
    return target ? <a href={target} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a> : <span>{alt}</span>
  },
  table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
}

export default memo(function MarkdownContent({ text, animate, onTyping }: {
  text: string
  animate: boolean
  onTyping: (active: boolean) => void
}) {
  const content = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const root = content.current
    const preference = matchMedia('(prefers-reduced-motion: reduce)')
    if (!root || !animate || preference.matches) return

    const nodes: { node: Text; full: string; ends: number[] }[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    const segmenter = new Intl.Segmenter('ru', { granularity: 'grapheme' })
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const value = node as Text
      if (!value.data.trim()) continue
      const full = value.data
      const ends = Array.from(segmenter.segment(full), ({ index, segment }) => index + segment.length)
      nodes.push({ node: value, full, ends })
      value.data = ''
    }
    if (!nodes.length) return
    const total = nodes.reduce((length, item) => length + item.ends.length, 0)
    const delay = Math.max(8, Math.min(27, 6800 / total))
    let index = 0
    let printed = 0
    let request = 0
    let started = 0

    root.dataset.typing = 'true'
    root.setAttribute('aria-live', 'off')

    function finish() {
      cancelAnimationFrame(request)
      for (const item of nodes) item.node.data = item.full
      root?.removeAttribute('data-typing')
      root?.removeAttribute('aria-live')
      onTyping(false)
    }

    function frame(now: number) {
      if (started === 0) {
        started = now
        onTyping(true)
      }
      const count = Math.min(total, Math.floor((now - started) / delay) + 1)
      while (index < nodes.length) {
        const item = nodes[index]!
        const length = count - printed
        if (length < 1) break
        const visible = Math.min(length, item.ends.length)
        const next = item.full.slice(0, item.ends[visible - 1])
        if (item.node.data !== next) item.node.data = next
        if (visible < item.ends.length) break
        printed += item.ends.length
        index++
      }
      if (count === total) finish()
      else request = requestAnimationFrame(frame)
    }
    request = requestAnimationFrame(frame)
    preference.addEventListener('change', finish)
    return () => {
      preference.removeEventListener('change', finish)
      finish()
    }
  }, [text, animate, onTyping])

  return <div ref={content} className="markdown-rendered">
    <Markdown skipHtml remarkPlugins={[remarkGfm]} components={components}>{text}</Markdown>
  </div>
})
