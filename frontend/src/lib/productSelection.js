export const DEFAULT_PRODUCT_CODE = '7709'
export const DEFAULT_PRODUCT_RANGE = 'd7'

export function productCodeOrDefault(master, code) {
  return code && master[code] ? code : DEFAULT_PRODUCT_CODE
}

export function productSelectionFromSearch(master, presets, search = '') {
  const params = new URLSearchParams(search)
  const requestedRange = params.get('range')
  return {
    code: productCodeOrDefault(master, params.get('code')),
    rangeKey: presets.some(item => item.k === requestedRange)
      ? requestedRange
      : DEFAULT_PRODUCT_RANGE,
  }
}
