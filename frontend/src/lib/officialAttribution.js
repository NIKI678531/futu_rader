export function attributedCodesOf(post) {
  if (!Array.isArray(post?.attributedProducts)) return []
  return post.attributedProducts
    .map((product) => product?.code)
    .filter((code) => typeof code === 'string' && code.length > 0)
}

export function attributedCampOf(post) {
  return ['own', 'competitor', 'both', 'none'].includes(post?.attributedCamp)
    ? post.attributedCamp
    : 'none'
}
