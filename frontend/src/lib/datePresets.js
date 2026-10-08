export const TODAY_RANGE_PLACEHOLDER = Object.freeze({
  k: 'd0',
  days: 1,
  label: '当天',
  placeholder: true,
})

export const TODAY_RANGE_KEY = TODAY_RANGE_PLACEHOLDER.k

/* “当天”先由前端占住 d0 档位。后端开始下发 d0 后，直接使用真实项，
   不再额外插入占位，也不会出现两个“当天”。 */
export function withTodayPlaceholder(presets) {
  return presets.some((preset) =>
    preset.k === TODAY_RANGE_KEY ||
    preset.key === TODAY_RANGE_KEY,
  )
    ? presets
    : [TODAY_RANGE_PLACEHOLDER, ...presets]
}
