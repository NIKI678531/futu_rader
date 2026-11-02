import { mentionedEtfsOf } from './kolEtfs.js'

const NA = '数据暂不可用'
const UNMENTIONED = '未提及'
const LIST_SEPARATOR = ' / '

export const KOL_POST_CSV_HEADERS = Object.freeze([
  '重点KOL', 'KOL标签', '发帖时间', 'ETF代码', 'ETF名称', '发行商',
  '内容形式', '操作方向', 'AI摘要', 'ETF归属', '正文提及ETF(全部ticker)',
  '赞', '评论数', '转发', '浏览量', '原帖链接'
])

function joined(items, valueOf) {
  if (!items.length) return UNMENTIONED
  return items.map(function (item) { return valueOf(item); }).join(LIST_SEPARATOR)
}

/* CSV 的代码、名称与发行商必须由同一份列表逐项生成，保证多 ETF 帖子中
   第 N 个代码始终对应第 N 个名称，不因某个字段缺失而错位。 */
export function kolCsvEtfFields(post) {
  var items = mentionedEtfsOf(post)
  var tickers = joined(items, function (item) { return item.ticker || item.code || NA; })
  return {
    codes: tickers,
    names: joined(items, function (item) { return item.name || NA; }),
    issuers: joined(items, function (item) { return item.issuer || (item.inPool === false ? '其他公司产品' : NA); }),
    mentionedTickers: tickers,
  }
}
