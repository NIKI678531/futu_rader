const DIRECTIONS = new Set(['add', 'open', 'reduce', 'close', 'hold'])

/* Pure business choice used by rendering and filter counts. */
export function kolDisplayTypeChoice(post) {
  if (post?.directionPending) return { source: 'direction', key: 'pending' }
  if (DIRECTIONS.has(post?.direction)) return { source: 'direction', key: post.direction }
  if (post?.postType != null) return { source: 'content', key: post.postType }
  return { source: 'content', key: null }
}
