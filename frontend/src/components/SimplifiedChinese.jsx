import { useLayoutEffect, useRef } from 'react'
import { Converter } from 'opencc-js/t2cn'

const toSimplified = Converter({ from: 't', to: 'cn' })
const TEXT_ATTRIBUTES = ['aria-label', 'aria-description', 'aria-valuetext', 'alt', 'placeholder', 'title']

/* Convert only the rendered copy. Names and post text stay unchanged in API/state because some of
   those original values are also query keys or source links. The observer covers later React updates. */
function simplifyTextNode(node) {
  const simplified = toSimplified(node.nodeValue || '')
  if (simplified !== node.nodeValue) node.nodeValue = simplified
}

function simplifyAttributes(element) {
  for (const attribute of TEXT_ATTRIBUTES) {
    if (!element.hasAttribute(attribute)) continue
    const original = element.getAttribute(attribute)
    const simplified = toSimplified(original)
    if (simplified !== original) element.setAttribute(attribute, simplified)
  }

  if (element.tagName === 'INPUT' && ['button', 'reset', 'submit'].includes(element.type)) {
    const simplified = toSimplified(element.value)
    if (simplified !== element.value) element.value = simplified
  }
}

function simplifyElement(element) {
  if (element.tagName === 'SCRIPT' || element.tagName === 'STYLE') return

  simplifyAttributes(element)
  for (const child of element.childNodes) simplifyNode(child)
}

function simplifyNode(node) {
  if (node.nodeType === 3) simplifyTextNode(node)
  else if (node.nodeType === 1) simplifyElement(node)
}

export default function SimplifiedChinese({ children, ...props }) {
  const root = useRef(null)

  useLayoutEffect(() => {
    simplifyElement(root.current)

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') simplifyTextNode(record.target)
        else if (record.type === 'attributes') simplifyAttributes(record.target)
        else for (const node of record.addedNodes) simplifyNode(node)
      }
    })
    observer.observe(root.current, {
      attributeFilter: TEXT_ATTRIBUTES,
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
    })
    return () => observer.disconnect()
  }, [])

  return <div ref={root} {...props} lang="zh-CN">{children}</div>
}
