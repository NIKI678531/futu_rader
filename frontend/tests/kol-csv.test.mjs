import test from 'node:test'
import assert from 'node:assert/strict'

import { KOL_POST_CSV_HEADERS, kolCsvEtfFields } from '../src/lib/kolCsv.js'

test('KOL post CSV aligns every mentioned ETF code, name and issuer', () => {
  const fields = kolCsvEtfFields({
    mentionedEtfs: [
      { code: '3033', ticker: '03033.HK', name: '恒生科技指数ETF', issuer: '南方东英', inPool: true },
      { code: '2800', ticker: '02800.HK', name: '盈富基金', issuer: '恒生投资', inPool: true },
      { code: 'us:QQQ', ticker: 'QQQ.US', name: 'Invesco QQQ Trust', issuer: null, inPool: false },
    ],
  })

  assert.deepEqual(fields, {
    codes: '03033.HK / 02800.HK / QQQ.US',
    names: '恒生科技指数ETF / 盈富基金 / Invesco QQQ Trust',
    issuers: '南方东英 / 恒生投资 / 其他公司产品',
    mentionedTickers: '03033.HK / 02800.HK / QQQ.US',
  })
})

test('KOL post CSV preserves position when a mentioned ETF name is unavailable', () => {
  const fields = kolCsvEtfFields({
    mentionedEtfs: [
      { ticker: '03033.HK', name: '恒生科技指数ETF', issuer: '南方东英', inPool: true },
      { ticker: 'QQQ.US', name: null, issuer: null, inPool: false },
    ],
  })

  assert.equal(fields.names, '恒生科技指数ETF / 数据暂不可用')
  assert.equal(fields.issuers, '南方东英 / 其他公司产品')
  assert.deepEqual(kolCsvEtfFields({ mentionedEtfs: [] }), {
    codes: '未提及', names: '未提及', issuers: '未提及', mentionedTickers: '未提及',
  })
})

test('KOL post CSV omits confidence and pending-confirmation columns', () => {
  assert.equal(KOL_POST_CSV_HEADERS.includes('类型置信度'), false)
  assert.equal(KOL_POST_CSV_HEADERS.includes('是否待确认'), false)
  assert.deepEqual(KOL_POST_CSV_HEADERS.slice(3, 6), ['ETF代码', 'ETF名称', '发行商'])
})
