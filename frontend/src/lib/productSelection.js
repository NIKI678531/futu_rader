import { rangeFromSearch } from './customRange.js'

export const DEFAULT_PRODUCT_CODE = '7709'
export const DEFAULT_PRODUCT_RANGE = 'd7'

export function productCodeOrDefault(master, code) {
  return code && master[code] ? code : DEFAULT_PRODUCT_CODE
}

export function productSelectionFromSearch(master, presets, search = '') {
  const params = new URLSearchParams(search)
  return {
    code: productCodeOrDefault(master, params.get('code')),
    rangeKey: rangeFromSearch(search, presets, DEFAULT_PRODUCT_RANGE),
  }
}
