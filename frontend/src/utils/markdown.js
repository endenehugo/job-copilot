import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 引用角标：把回答中的 [N] 渲染成 sup（DOMPurify 白名单保留 sup 与 class）
export function renderMarkdown(content) {
  if (!content) return ''
  const html = md.render(String(content))
  const withCites = html.replace(/\[(\d{1,2})\]/g, '<sup class="cite-ref">$1</sup>')
  return DOMPurify.sanitize(withCites)
}
