// 工具函数

import { marked } from 'marked'

function normalizeDoiForLink(raw) {
  let doi = String(raw || '').replace(/<[^>]*>/g, '').trim()
  if (!doi) return ''

  doi = doi.replace(/^doi\s*=\s*/i, '')
  doi = doi.replace(/·\s*查看原文.*/i, '')
  doi = doi.replace(/[)\],;:]+$/g, '')

  const m = doi.match(/10\.[^\s;)\]·]+/i)
  if (m) doi = m[0]

  if (doi.includes('_') && !doi.includes('/')) {
    doi = doi.replace('_', '/')
  }
  return doi
}

function applyDoiLinksToHtml(html) {
  let nextHtml = String(html || '')
  nextHtml = nextHtml.replace(/\[DOI:\s*([^\]]+)\]/gi, (match, doi) => {
    const cleanDoi = normalizeDoiForLink(doi)
    if (!cleanDoi) return match
    return `<a href="#" class="doi-link" data-doi="${cleanDoi}">[DOI: ${cleanDoi}]</a>`
  })

  nextHtml = nextHtml.replace(/\(doi\s*=\s*([^)\s]+(?:\s*·\s*查看原文[^)]*)?)\)/gi, (match, doi) => {
    const cleanDoi = normalizeDoiForLink(doi)
    if (!cleanDoi) return match
    return `(<a href="#" class="doi-link" data-doi="${cleanDoi}">${cleanDoi}</a>)`
  })

  return nextHtml
}

// 格式化时间
export function formatTime(date) {
  const d = new Date(date)
  const now = new Date()
  const diff = now - d
  
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
  return d.toLocaleDateString()
}

// 格式化答案 - Markdown 渲染
export function formatAnswer(text, referenceSnippets = []) {
  if (!text) return ''
  
  // 预处理：确保表格格式正确
  // 检查是否有不完整的表格（缺少分隔行）
  text = fixTableFormat(text)
  
  // 预处理 LaTeX
  text = cleanLaTeX(text)
  
  // 配置marked选项
  marked.setOptions({
    breaks: true,
    gfm: true,
    tables: true,
    mangle: false,
    headerIds: false
  })
  
  // 使用 marked 渲染 Markdown
  let html = ''
  try {
    html = marked.parse(text)
  } catch (e) {
    console.error('Markdown解析失败:', e)
    html = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  }
  
  // 在渲染后的HTML中处理DOI链接  // 将 [DOI: xxx] 格式转换为可点击的链接（新格式）
  return applyDoiLinksToHtml(html)
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const escaped = escapeHtml(String(text))
  const normalized = escaped
    .replace(/\r\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')

  const html = normalized
    .replace(/^###\s+(.+)$/gm, '<h3>$1</h3>')
    .replace(/^##\s+(.+)$/gm, '<h2>$1</h2>')
    .replace(/^-\s+(.+)$/gm, '<div class="stream-bullet">• $1</div>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')

  return applyDoiLinksToHtml(html)
}

// 修复表格格式
function fixTableFormat(text) {
  // 查找可能的表格（多行都包含|的段落）
  const lines = text.split('\n')
  const result = []
  let i = 0
  
  while (i < lines.length) {
    const line = lines[i]
    
    // 检查是否是表格行（包含|且不是代码块）
    if (line.includes('|') && !line.trim().startsWith('```')) {
      // 查找连续的表格行
      const tableLines = []
      let j = i
      while (j < lines.length && lines[j].includes('|')) {
        tableLines.push(lines[j])
        j++
      }
      
      // 如果有至少2行，可能是表格
      if (tableLines.length >= 2) {
        // 检查第二行是否是分隔行
        const hasSeparator = tableLines[1].match(/^\s*\|[\s\-:|]+\|\s*$/)
        
        if (!hasSeparator) {
          // 缺少分隔行，自动插入
          const headerCols = (tableLines[0].match(/\|/g) || []).length - 1
          const separator = '|' + Array(headerCols).fill('------').join('|') + '|'
          tableLines.splice(1, 0, separator)
        }
        
        result.push(...tableLines)
        i = j
        continue
      }
    }
    
    result.push(line)
    i++
  }
  
  return result.join('\n')
}

// 清理 LaTeX 公式
function cleanLaTeX(text) {
  text = text.replace(/\\\[[\s\S]*?\\\]/g, m => cleanLaTeXCommands(m.replace(/\\\[|\]/g, '')))
  text = text.replace(/\$\$[\s\S]*?\$\$/g, m => cleanLaTeXCommands(m.replace(/\$\$/g, '')))
  text = text.replace(/\\\([\s\S]*?\\\)/g, m => cleanLaTeXCommands(m.replace(/\\\(|\\\)/g, '')))
  text = text.replace(/\$[^$]+\$/g, m => cleanLaTeXCommands(m.replace(/\$/g, '')))
  return text
}

// 清理 LaTeX 命令
function cleanLaTeXCommands(text) {
  const subs = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉'}
  const sups = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹'}
  
  text = text.replace(/_(\d+)/g, m => m.slice(1).split('').map(c => subs[c] || c).join(''))
  text = text.replace(/\^(\d+)/g, m => m.slice(1).split('').map(c => sups[c] || c).join(''))
  text = text.replace(/\\rightarrow/g, '→').replace(/\\leftarrow/g, '←')
  text = text.replace(/\\Rightarrow/g, '⇐').replace(/\\Leftarrow/g, '⇒')
  text = text.replace(/\\[a-zA-Z]+\{([^}]+)\}/g, '$1')
  text = text.replace(/\\[a-zA-Z]+/g, '')
  return text.trim()
}

// HTML 转义
export function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}
