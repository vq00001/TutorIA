import { lazy, Suspense } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
const MermaidDiagram = lazy(() => import('./MermaidDiagram').then((module) => ({ default: module.MermaidDiagram })))

export function MarkdownContent({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{
    code({ className, children, ...props }) {
      const language = /language-(\w+)/.exec(className ?? '')?.[1]
      const text = String(children).replace(/\n$/, '')
      if (language === 'mermaid') return <Suspense fallback={<div className="mermaid-output">Preparando diagrama…</div>}><MermaidDiagram source={text} /></Suspense>
      return <code className={className} {...props}>{children}</code>
    },
  }}>{content}</ReactMarkdown>
}
