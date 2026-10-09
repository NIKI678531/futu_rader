const APP_ROOT = '.opinion-radar-app'

module.exports = {
  plugins: {
    'postcss-prefix-selector': {
      prefix: APP_ROOT,
      transform(prefix, selector, prefixedSelector, filePath) {
        if (filePath.endsWith('standalone.css')) return selector
        if (selector === ':root' || selector === 'html' || selector === 'body') return prefix
        if (selector.startsWith(':root ')) return selector.replace(':root', prefix)
        if (/^(?:html|body)(?=[\s.#:[>+~])/.test(selector)) {
          return selector.replace(/^(?:html|body)/, prefix)
        }
        return prefixedSelector
      },
    },
  },
}
