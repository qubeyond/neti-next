import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { safeURL } from './api'

export default function MarkdownContent({ text }: { text: string }) {
  return <Markdown skipHtml remarkPlugins={[remarkGfm]}
    components={{
      a: ({ href, children }) => {
        const target = safeURL(href)
        return target ? <a href={target} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>
      },
      img: ({ src, alt }) => {
        const target = safeURL(src)
        return target ? <a href={target} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a> : <span>{alt}</span>
      },
      table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
    }}>{text}</Markdown>
}
