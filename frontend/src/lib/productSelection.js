export const DEFAULT_PRODUCT_CODE = '7709'

export function productCodeOrDefault(master, code) {
  return code && master[code] ? code : DEFAULT_PRODUCT_CODE
}
