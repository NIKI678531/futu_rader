/* 舆情雷达 v2 · 组合域共享数据与口径
   规范来源：《舆情雷达｜组合域完整重构规范（Claude Design V2）》
   产品清单与竞品映射来自客户上传的《竞品及核心账号.xlsx》；
   所有数值（时间桶、积极／消极／中性、观点主题、动态负面类别、benchmark）均为演示数据，
   由确定性散列生成，用于评审信息结构，不代表已接入生产数据。
   2026-09-04 增量：重点舆情（需合规关注）、产品相关 KOL、演示价格 K 线、同业产品标识（ownership: own | peer）。
   2026-09-06 改版：账号板块改为帖子级 AI 摘要 / 类型 / 自家竞品阵营；评论区内容类分析（好差评、每小时评论、评论摘录、评论人分层）全部下线。
   2026-09-08 第三轮：官号 × ETF 提及统计、帖子类型双标签（内容形式 × 操作方向）、热议总结、热度变化与阶段观点（第 14 节）。 */
(function () {

  /* ────────────── 0. 全局常量 ────────────── */
  var ANCHOR = '2026-09-01';          // 最近一个完整自然日（HKT）
  var NOW_DATE = '2026-09-02';
  var NOW_TIME = '09:00';
  var LOW_SAMPLE = 10;                // 第 3.5 节阈值，可配置
  var NEW_DAYS = 30;                  // 第 3.9 节新品定义

  /* 讨论热度口径（全站唯一定义，页面直接引用 HEAT_FORMULA 展示）：
     评论量＝被识别为讨论该产品的评论条数；
     点赞＝相关帖子获赞 ＋ 这些评论自身获赞；转发＝相关帖子转发数。 */
  var HEAT_W = { like: 0.3, share: 1 };
  var HEAT_FORMULA = '讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发';
  var HEAT_NOTE = '点赞含帖子获赞与评论获赞，转发为相关帖子的转发数。';
  function heatOf(comments, likes, shares) {
    return Math.round(comments + HEAT_W.like * likes + HEAT_W.share * shares);
  }

  var SECTORS = [
    { k: 'hk',   name: '港股',       hue: '#2361AD', tint: '#EAF1FB' },
    { k: 'a',    name: 'A股',        hue: '#B0483C', tint: '#FAECEA' },
    { k: 'us',   name: '美股',       hue: '#4A4E8C', tint: '#ECEDF6' },
    { k: 'sgl',  name: '单一股票',    hue: '#7A5C9E', tint: '#F1ECF7' },
    { k: 'apac', name: '亚太及区域',  hue: '#2E8095', tint: '#E8F2F5' },
    { k: 'fi',   name: '固收',       hue: '#6B7285', tint: '#EEF0F3' },
    { k: 'cm',   name: '商品',       hue: '#A8853C', tint: '#F7F1E3' },
    { k: 'va',   name: '虚拟资产',    hue: '#1F7A5C', tint: '#E6F2ED' }
  ];
  var SECTOR_NAME = {}; SECTORS.forEach(function (s) { SECTOR_NAME[s.k] = s.name; });

  /* 自家产品：[代码, 名称, 板块, 结构, 权重, 南向通] */
  var P = [
    ['3033', '恒生科技指數ETF', 'hk', 'ETF', 10, 1],
    ['3037', '恒生指數ETF', 'hk', 'ETF', 8, 1],
    ['2802', '國指備兌認購期權主動型ETF', 'hk', '备兑高息', 7, 0],
    ['3469', '恒生港股通紅利ETF', 'hk', '备兑高息', 6, 1],
    ['3174', '恒生生物科技ETF', 'hk', 'ETF', 5, 1],
    ['3442', '恒生港美科技ETF', 'hk', 'ETF', 4, 1],
    ['3441', '富時東西股票精選ETF', 'hk', 'ETF', 3, 1],
    ['3443', '富時香港股票ETF', 'hk', 'ETF', 3, 1],
    ['3431', '富時香港韓國科技+指數ETF', 'hk', 'ETF', 4, 1],
    ['3432', 'MSCI港股通精選ETF', 'hk', 'ETF', 2, 1],
    ['3535', '野村富時香港日本股票現金流聚焦指數ETF', 'hk', 'ETF', 2, 0],
    ['7226', '恒生科技指數每日槓桿(2x)產品', 'hk', '杠反', 9, 0],
    ['7552', '恒生科技指數每日反向(-2x)產品', 'hk', '杠反', 7, 0],
    ['7200', '恒生指數每日槓桿(2x)產品', 'hk', '杠反', 6, 0],
    ['7300', '恒生指數每日反向(-1x)產品', 'hk', '杠反', 4, 0],
    ['7500', '恒生指數每日反向(-2x)產品', 'hk', '杠反', 5, 0],
    ['7288', '恒生中國企業指數每日槓桿(2x)產品', 'hk', '杠反', 3, 0],
    ['7588', '恒生中國企業指數每日反向(-2x)產品', 'hk', '杠反', 2, 0],

    ['2822', '南方富時中國A50 ETF', 'a', 'ETF', 7, 0],
    ['3003', 'MSCI中國A50互聯互通ETF', 'a', 'ETF', 6, 0],
    ['3133', '華泰柏瑞滬深300ETF', 'a', 'ETF', 6, 0],
    ['3101', '華泰柏瑞中證A500ETF', 'a', 'ETF', 5, 0],
    ['3109', '科創板50指數ETF', 'a', 'ETF', 4, 0],
    ['3147', '中國創業板指數ETF', 'a', 'ETF', 3, 0],
    ['3005', '中證500 ETF', 'a', 'ETF', 2, 0],
    ['3167', '標普中國新經濟行業ETF', 'a', '主题', 3, 1],
    ['3193', '銀華中證5G通信主題ETF', 'a', '主题', 2, 0],
    ['3134', '華泰柏瑞中證太陽能產業指數ETF', 'a', '主题', 4, 0],
    ['7233', '滬深300指數每日槓桿(2x)產品', 'a', '杠反', 4, 0],

    ['3034', '納斯達克100 ETF', 'us', 'ETF', 8, 0],
    ['3454', '美股七巨頭ETF', 'us', 'ETF', 7, 0],
    ['7266', '納斯達克100指數每日槓桿(2x)產品', 'us', '杠反', 8, 0],
    ['7568', '納斯達克100指數每日反向(-2x)產品', 'us', '杠反', 6, 0],

    ['7709', '海力士每日槓桿(2x)產品', 'sgl', '杠反', 10, 0],
    ['7788', '英偉達每日槓桿(2x)產品', 'sgl', '杠反', 9, 0],
    ['7388', '英偉達每日反向(-2x)產品', 'sgl', '杠反', 6, 0],
    ['7766', '特斯拉每日槓桿(2x)產品', 'sgl', '杠反', 7, 0],
    ['7366', '特斯拉每日反向(-2x)產品', 'sgl', '杠反', 5, 0],
    ['7747', '三星電子每日槓桿(2x)產品', 'sgl', '杠反', 6, 0],
    ['7347', '三星電子每日反向(-2x)產品', 'sgl', '杠反', 3, 0],
    ['7711', 'Coinbase每日槓桿(2x)產品', 'sgl', '杠反', 4, 0],
    ['7311', 'Coinbase每日反向(-2x)產品', 'sgl', '杠反', 2, 0],
    ['7799', 'MicroStrategy每日槓桿(2x)產品', 'sgl', '杠反', 4, 0],
    ['7399', 'MicroStrategy每日反向(-2x)產品', 'sgl', '杠反', 2, 0],
    ['7777', 'Berkshire每日槓桿(2x)產品', 'sgl', '杠反', 3, 0],

    ['3153', '日經225指數ETF', 'apac', 'ETF', 5, 0],
    ['3004', '富時越南30ETF', 'apac', 'ETF', 4, 0],
    ['2830', '沙特阿拉伯ETF', 'apac', 'ETF', 3, 0],
    ['3473', '富時亞洲科技指數ETF', 'apac', 'ETF', 3, 0],
    ['7262', '日經225每日槓桿(2x)產品', 'apac', '杠反', 4, 0],
    ['7515', '日經225每日反向(-2x)產品', 'apac', '杠反', 2, 0],

    ['3053', '港元貨幣市場ETF', 'fi', '货币', 6, 0],
    ['3096', '美元貨幣市場ETF', 'fi', '货币', 7, 0],
    ['3122', '人民幣貨幣市場ETF', 'fi', '货币', 4, 0],
    ['3199', '富時中國國債及政策性銀行債券指數ETF', 'fi', 'ETF', 3, 0],
    ['3433', '富時美國國債20年+指數ETF', 'fi', 'ETF', 5, 0],

    ['3030', '黃金ETF', 'cm', 'ETF', 8, 0],
    ['7299', '黃金期貨每日槓桿(2x)產品', 'cm', '杠反', 5, 0],

    ['3066', '比特幣期貨ETF', 'va', 'ETF', 6, 0],
    ['3068', '以太幣期貨ETF', 'va', 'ETF', 4, 0],
    ['7376', '比特幣期貨每日反向(-1x)產品', 'va', '杠反', 3, 0]
  ];

  /* 竞品映射：[发行商, 竞品代码, 竞品名称, 对位自家代码] */
  var CMAP = [
    ['恒生投资', '3032', '恒生科技指数ETF', '3033'],
    ['恒生投资', '3589', '恒生科技备兑认购期权主动型ETF', '3033'],
    ['BlackRock', '3067', 'iShares 安碩恒生科技ETF', '3033'],
    ['Global X', '2837', 'Global X 恒生科技ETF', '3033'],
    ['AMC 华夏', '3088', '华夏恒生科技指数ETF', '3033'],
    ['Ping An', '3406', '平安科技精选ETF', '3033'],
    ['恒生投资', '2800', '盈富基金', '3037'],
    ['BlackRock', '3115', 'iShares 安碩核心恒生指數ETF', '3037'],
    ['恒生投资', '2828', '恒生中国企业指数上市基金', '2802'],
    ['恒生投资', '3519', '恒生国指备兑认购期权主动型ETF', '2802'],
    ['Global X', '3416', 'Global X 国指备兑认购期权主动型ETF', '2802'],
    ['Ping An', '3070', '中国平安CSI香港高息股ETF', '3469'],
    ['Value 惠理', '3488', '惠理港美红利低波ETF', '3469'],
    ['AMC 华夏', '3069', '华夏恒生生物科技ETF', '3174'],
    ['Ping An', '3477', '平安东西方股票精选ETF', '3441'],
    ['Global X', '3119', '亚洲半导体ETF', '3431'],
    ['DWS', '2848', 'Xtrackers MSCI 韓國 UCITS ETF', '3431'],
    ['Global X', '3104', 'Global X 新兴市场亚洲主动型ETF', '3431'],
    ['恒生投资', '2838', '恒生富时中国50指数上市基金', '2822'],
    ['BlackRock', '2823', 'iShares 安碩富時中國A50 ETF', '2822'],
    ['BlackRock', '2801', 'iShares 安碩核心MSCI 中國ETF', '3003'],
    ['DWS', '3007', 'Xtrackers MSCI 中國A UCITS ETF', '3003'],
    ['Global X', '3040', 'Global X MSCI 中国ETF', '3003'],
    ['AMC 华夏', '2839', '华夏MSCI中国A50互联互通ETF', '3003'],
    ['BlackRock', '2846', 'iShares 安碩核心滬深300 ETF', '3133'],
    ['AMC 华夏', '3188', '华夏沪深300指数ETF', '3133'],
    ['BOCI-Prudential', '2827', '標智滬深300中國指數基金', '3133'],
    ['Premia Partners', '3151', '中国科创50ETF', '3109'],
    ['Premia Partners', '3173', '中国新经济ETF', '3167'],
    ['AMC 华夏', '3086', '华夏纳斯达克100 ETF', '3034'],
    ['Global X', '3451', 'Global X 纳斯达克100备兑认购期权主动型ETF', '3034'],
    ['AMC 华夏', '7261', '华夏纳斯达克100指数每日杠杆(2x)产品', '7266'],
    ['AMC 华夏', '7522', '华夏纳斯达克100指数每日反向(-2x)产品', '7568'],
    ['DWS', '3087', 'Xtrackers 越南掉期 UCITS ETF', '3004'],
    ['Premia Partners', '2804', '越南市场ETF', '3004'],
    ['恒生投资', '3410', '恒生日本東證一百', '3153'],
    ['Bosera 博时', '3152', '博時港元貨幣市場ETF', '3053'],
    ['AMC 华夏', '3471', '华夏港元数字货币基金', '3053'],
    ['CICC 中金', '3071', '中金港元货币市场ETF', '3053'],
    ['Value 惠理', '3421', '惠理港元货币市场ETF', '3053'],
    ['Global X', '3137', 'Global X 美元货币市场ETF', '3096'],
    ['CICC 中金', '3011', '工银中金美元货币市场ETF', '3096'],
    ['AMC 华夏', '3472', '华夏美元数字货币基金', '3096'],
    ['Value 惠理', '3480', '惠理美元货币市场ETF', '3096'],
    ['Bosera 博时', '3196', '博時美元貨幣市場ETF', '3096'],
    ['AMC 华夏', '3461', '华夏人民币数字货币基金', '3122'],
    ['AMC 华夏', '3161', '华夏人民币货币ETF', '3122'],
    ['Value 惠理', '3420', '惠理人民币货币市场ETF', '3122'],
    ['BlackRock', '2829', 'iShares 安碩中國政府債券ETF', '3199'],
    ['Global X', '3041', 'Global X 富时中国政策性银行债券ETF', '3199'],
    ['Premia Partners', '2817', '中国长久期政府债券ETF', '3199'],
    ['AMC 华夏', '3146', '华夏20年以上美国国债ETF', '3433'],
    ['Premia Partners', '3077', '美国国库浮息票据ETF', '3433'],
    ['恒生投资', '3170', '恒生黃金ETF', '3030'],
    ['State Street', '2840', 'SPDR 金ETF', '3030'],
    ['Global X', '3533', 'Global X 黄金备兑认购期权主动型ETF', '3030'],
    ['Sensible', '3081', '价值黄金ETF', '3030'],
    ['AMC 华夏', '3042', '华夏比特币ETF', '3066'],
    ['AMC 华夏', '3046', '华夏以太币ETF', '3068']
  ];

  /* ────────────── 1. 工具 ────────────── */
  function hash(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return h;
  }
  function rnd(key, lo, hi) { return lo + (hash(key) % 1000) / 1000 * (hi - lo); }
  function pick(arr, key) { return arr[hash(key) % arr.length]; }
  function pickN(arr, key, n) {
    var pool = arr.slice(), out = [];
    for (var i = 0; i < n && pool.length; i++) out.push(pool.splice(hash(key + i) % pool.length, 1)[0]);
    return out;
  }
  function parse(s) { var a = s.split('-'); return new Date(Date.UTC(+a[0], +a[1] - 1, +a[2])); }
  function dstr(d) { return d.toISOString().slice(0, 10); }
  function addDays(s, n) { var d = parse(s); d.setUTCDate(d.getUTCDate() + n); return dstr(d); }
  function diffDays(a, b) { return Math.round((parse(b) - parse(a)) / 86400000); }
  var DOW = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  function dowOf(s) { return DOW[parse(s).getUTCDay()]; }
  function md(s) { return s.slice(5); }

  function rgba(hex, a) {
    var h = hex.replace('#', '');
    return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' + parseInt(h.slice(2, 4), 16) + ',' + parseInt(h.slice(4, 6), 16) + ',' + a + ')';
  }
  function num(v) { return v == null ? '数据暂不可用' : v.toLocaleString('en-US'); }

  /* 把整数 total 按权重摊到各时间桶：最大余数法，保证各桶之和等于 total。
     小时粒度下每桶均值常小于 1，逐桶独立取整会把整条热力条压成 0。 */
  function spread(total, weights) {
    var n = weights.length;
    if (!n) return [];
    var wSum = weights.reduce(function (a, w) { return a + w; }, 0) || 1;
    var out = [], rem = [], used = 0;
    for (var i = 0; i < n; i++) {
      var exact = total * weights[i] / wSum;
      var base = Math.floor(exact);
      out.push(base); used += base;
      rem.push({ i: i, r: exact - base });
    }
    rem.sort(function (a, b) { return b.r - a.r || a.i - b.i; });
    for (var k = 0; k < total - used; k++) out[rem[k % n].i]++;
    return out;
  }
  function pct1(v) { return (Math.round(v * 10) / 10).toFixed(1) + '%'; }

  /* 第 3.6 节：绝对差 + 百分比；基准为 0 时不计算百分比 */
  function delta(cur, base) {
    if (cur == null || base == null) return { text: '数据暂不可用', short: '暂不可用', abs: null, pct: null, dir: 0 };
    var d = cur - base;
    if (base === 0) return d > 0
      ? { text: '新增 ' + d, short: '新增', abs: d, pct: null, dir: 1 }
      : { text: '—', short: '—', abs: 0, pct: null, dir: 0 };
    var p = d / base * 100;
    return {
      text: (d >= 0 ? '+' : '') + d + '（' + (p >= 0 ? '+' : '') + p.toFixed(1) + '%）',
      short: (p >= 0 ? '+' : '') + p.toFixed(0) + '%',
      abs: d, pct: p, dir: d > 0 ? 1 : (d < 0 ? -1 : 0)
    };
  }

  /* ────────────── 2. 日期区间与时间桶粒度（第 3.7 节） ────────────── */
  var PRESETS = [
    { k: 'd1', days: 1, label: '昨日' },
    { k: 'd2', days: 2, label: '近 2 天' },
    { k: 'd7', days: 7, label: '近 7 天' },
    { k: 'd14', days: 14, label: '近 14 天' },
    { k: 'd30', days: 30, label: '近 30 天' }
  ];
  var rangeCache = {};
  function buildRange(key) {
    if (rangeCache[key]) return rangeCache[key];
    var pr = PRESETS.filter(function (x) { return x.k === key; })[0] || PRESETS[2];
    var days = pr.days;
    var to = ANCHOR, from = addDays(ANCHOR, -(days - 1));
    var bTo = addDays(from, -1), bFrom = addDays(bTo, -(days - 1));
    var gran = days <= 2 ? 'hour' : (days <= 14 ? 'day' : 'week');
    var buckets = [];
    if (gran === 'hour') {
      for (var di = 0; di < days; di++) {
        var day = addDays(from, di);
        for (var h = 0; h < 24; h++) {
          buckets.push({
            i: buckets.length, span: 1 / 24, hour: h, day: day,
            label: h % 6 === 0 ? String(h).padStart(2, '0') : '',
            tip: md(day) + ' ' + String(h).padStart(2, '0') + ':00–' + String((h + 1) % 24).padStart(2, '0') + ':00'
          });
        }
      }
    } else if (gran === 'day') {
      for (var d2 = 0; d2 < days; d2++) {
        var dd = addDays(from, d2);
        buckets.push({
          i: buckets.length, span: 1, day: dd, dow: parse(dd).getUTCDay(),
          label: md(dd), tip: md(dd) + '（' + dowOf(dd) + '）全天'
        });
      }
    } else {
      for (var w = 0; w * 7 < days; w++) {
        var ws = addDays(from, w * 7);
        var wLen = Math.min(7, days - w * 7);
        var we = addDays(ws, wLen - 1);
        buckets.push({
          i: buckets.length, span: wLen, day: ws,
          label: 'W' + (w + 1), tip: md(ws) + ' ～ ' + md(we) + '（' + wLen + ' 天）'
        });
      }
    }
    var r = {
      key: key, days: days, from: from, to: to, label: pr.label,
      text: from + ' ～ ' + to,
      gran: gran,
      granLabel: gran === 'hour' ? '按小时' : (gran === 'day' ? '按自然日' : '按自然周'),
      buckets: buckets,
      benchFrom: bFrom, benchTo: bTo,
      benchText: bFrom + ' ～ ' + bTo,
      benchLabel: days <= 2 ? '较昨日同期' : '较上一等长区间',
      trendTitle: days <= 2 ? '日内舆情与价格趋势' : '区间舆情与价格趋势'
    };
    rangeCache[key] = r;
    return r;
  }

  /* ────────────── 3. 产品主数据（自家 + 竞品，统一结构） ────────────── */
  function structOf(name) {
    if (/槓桿|杠杆|反向/.test(name)) return '杠反';
    if (/備兌|备兑|高息|紅利|红利/.test(name)) return '备兑高息';
    if (/貨幣|货币/.test(name)) return '货币';
    if (/主題|主题|新經濟|新经济|太陽能|5G|半導體|半导体/.test(name)) return '主题';
    return 'ETF';
  }
  /* 上市未满 30 天的演示新品（含一只竞品） */
  var NEW_LISTING = { '3535': '2026-08-14', '3443': '2026-08-21', '3101': '2026-08-07', '3473': '2026-08-26', '3196': '2026-08-19' };

  var MASTER = {}, ORDER = [];
  P.forEach(function (r) {
    MASTER[r[0]] = {
      code: r[0], name: r[1], sector: r[2], struct: r[3], w: r[4], south: !!r[5],
      issuer: 'CSOP 南方东英', ownership: 'own'
    };
    ORDER.push(r[0]);
  });
  CMAP.forEach(function (c) {
    if (MASTER[c[1]]) return;
    var own = MASTER[c[3]];
    MASTER[c[1]] = {
      code: c[1], name: c[2], sector: own ? own.sector : 'hk', struct: structOf(c[2]),
      w: Math.max(1.2, (own ? own.w : 4) * rnd('cw' + c[1], 0.35, 0.85)),
      south: false, issuer: c[0], ownership: 'peer', ownCode: c[3]
    };
    ORDER.push(c[1]);
  });
  ORDER.forEach(function (code) {
    var m = MASTER[code];
    m.listingDate = NEW_LISTING[code] || dstr(new Date(Date.UTC(2015, 0, 1 + Math.floor(rnd('ld' + code, 0, 3800)))));
    m.isNew = diffDays(m.listingDate, NOW_DATE) < NEW_DAYS;
    m.sectorName = SECTOR_NAME[m.sector];
  });

  /* 讨论量幂律分布：头部少数产品占掉大部分评论量，长尾快速衰减。
     按主数据权重定一次稳定名次，刷新不跳动。 */
  ORDER.slice().sort(function (a, b) {
    return MASTER[b].w - MASTER[a].w || (a < b ? -1 : 1);
  }).forEach(function (code, i) {
    MASTER[code].power = Math.pow(i + 1, -1.05);
  });

  /* ────────────── 4. 时间桶与产品观测 ────────────── */
  function hourShape(h) { return (h >= 9 && h <= 16) ? 1.9 : ((h >= 21 || h <= 1) ? 1.1 : 0.35); }
  function dayShape(dw) { return (dw === 0 || dw === 6) ? 0.44 : 1.22; }

  function bucketsFor(code, range, salt) {
    var m = MASTER[code]; if (!m) return [];
    var perDay = 200 * m.power * rnd(code + 'pd' + salt, 0.78, 1.28);
    var neuBase = rnd(code + 'nu', 0.30, 0.52);
    var posBase = rnd(code + 'po', 0.28, 0.84);
    return range.buckets.map(function (b) {
      var shape = range.gran === 'hour' ? hourShape(b.hour) : (range.gran === 'day' ? dayShape(b.dow) : 1);
      var noise = rnd(code + salt + 'b' + b.i, 0.5, 1.55);
      var mentions = Math.max(0, Math.round(perDay * b.span * shape * noise));
      var comments = Math.round(mentions * rnd(code + salt + 'c' + b.i, 1.5, 3.3));
      /* 热度口径：帖子点赞与评论点赞合记 likes，转发单列 shares */
      var likes = Math.round(comments * rnd(code + salt + 'lk' + b.i, 4.2, 11.5));
      var shares = Math.round(comments * rnd(code + salt + 'sh' + b.i, 0.22, 0.85));
      var inter = likes + shares;
      var neutral = Math.round(mentions * neuBase * rnd(code + salt + 'n' + b.i, 0.8, 1.2));
      if (neutral > mentions) neutral = mentions;
      var rest = mentions - neutral;
      var positive = Math.round(rest * posBase * rnd(code + salt + 'p' + b.i, 0.85, 1.15));
      if (positive > rest) positive = rest;
      return {
        start: b.day, label: b.label, tip: b.tip, i: b.i,
        mentions: mentions, comments: comments, interactions: inter,
        likes: likes, shares: shares,
        active: comments === 0 ? 0 : Math.max(1, Math.round(comments * rnd(code + salt + 'ac' + b.i, 0.3, 0.55))),
        positive: positive, negative: rest - positive, neutral: neutral
      };
    });
  }

  function sumField(list, f) { return list.reduce(function (t, x) { return t + (x[f] || 0); }, 0); }

  var obsCache = {};
  function observe(code, rangeKey, salt) {
    var ck = code + '|' + rangeKey + '|' + (salt || '');
    if (obsCache[ck]) return obsCache[ck];
    var m = MASTER[code];
    if (!m) return null;
    var range = buildRange(rangeKey);
    var bk = bucketsFor(code, range, salt || '');
    var mentions = sumField(bk, 'mentions'), comments = sumField(bk, 'comments'), inter = sumField(bk, 'interactions');
    var likes = sumField(bk, 'likes'), shares = sumField(bk, 'shares');
    var pos = sumField(bk, 'positive'), neg = sumField(bk, 'negative'), neu = sumField(bk, 'neutral');
    var o = {
      code: code, name: m.name, sector: m.sector, sectorName: m.sectorName,
      struct: m.struct, issuer: m.issuer, ownership: m.ownership, ownCode: m.ownCode,
      listingDate: m.listingDate, isNew: m.isNew, south: m.south,
      buckets: bk,
      mentions: mentions, comments: comments, interactions: inter,
      likes: likes, shares: shares,
      discussionHeat: heatOf(comments, likes, shares),
      activeAccounts: m.accountsUnavailable ? null : Math.max(2, Math.round(mentions * rnd(code + 'aa', 0.3, 0.62))),
      activeByBucket: m.accountsUnavailable ? null : bk.map(function (x) { return x.active; }),
      attitude: { positive: pos, negative: neg, neutral: neu, sampleSufficient: (pos + neg) >= LOW_SAMPLE },
      maxBucket: Math.max.apply(null, bk.map(function (x) { return x.mentions; }).concat([0])),
      updatedAt: NOW_DATE + ' ' + NOW_TIME
    };
    obsCache[ck] = o;
    return o;
  }

  /* 全市场排名（第 3.8 节）：完整活跃池，板块筛选不重算 */
  var rankCache = {};
  function ranks(rangeKey) {
    if (rankCache[rangeKey]) return rankCache[rangeKey];
    var list = ORDER.map(function (c) { return observe(c, rangeKey); })
      .sort(function (a, b) { return b.comments - a.comments || (a.code < b.code ? -1 : 1); });
    var map = {};
    list.forEach(function (o, i) { map[o.code] = i + 1; });
    var r = { map: map, total: list.length, list: list };
    rankCache[rangeKey] = r;
    return r;
  }

  /* 全池聚合：按区间缓存一次，避免页面每次重绘都重算 120 只产品 */
  var poolCache = {};
  function pool(rangeKey) {
    if (poolCache[rangeKey]) return poolCache[rangeKey];
    var rankMap = ranks(rangeKey).map;
    var globalMax = 1, list = ORDER.map(function (c) {
      var o = observe(c, rangeKey);
      if (o.maxBucket > globalMax) globalMax = o.maxBucket;
      return o;
    });
    var alerts = {}, negMentions = {}, baseMentions = {}, baseComments = {}, baseHeat = {}, complianceCount = {};
    list.forEach(function (o) {
      var cats = negCatsFor(o.code, rangeKey);
      var bo = observe(o.code, rangeKey, 'bench');
      var cr = complianceFor(o.code, rangeKey);
      complianceCount[o.code] = cr.status === 'ok' ? cr.list.length : (cr.status === 'empty' ? 0 : null);
      alerts[o.code] = cats.filter(function (c) { return c.severity === 'high'; }).length;
      negMentions[o.code] = cats.reduce(function (t, c) { return t + c.mentions; }, 0);
      baseMentions[o.code] = bo.mentions;
      baseComments[o.code] = bo.comments;
      baseHeat[o.code] = bo.discussionHeat;
    });
    var p = {
      list: list, globalMax: globalMax, rankMap: rankMap, alerts: alerts, negMentions: negMentions,
      baseMentions: baseMentions, baseComments: baseComments, baseHeat: baseHeat, complianceCount: complianceCount
    };
    poolCache[rangeKey] = p;
    return p;
  }

  function benchmark(code, rangeKey) {
    var cur = observe(code, rangeKey), base = observe(code, rangeKey, 'bench');
    return {
      mentions: delta(cur.mentions, base.mentions),
      comments: delta(cur.comments, base.comments),
      interactions: delta(cur.interactions, base.interactions),
      likes: delta(cur.likes, base.likes),
      shares: delta(cur.shares, base.shares),
      heat: delta(cur.discussionHeat, base.discussionHeat),
      positive: delta(cur.attitude.positive, base.attitude.positive),
      negative: delta(cur.attitude.negative, base.attitude.negative),
      neutral: delta(cur.attitude.neutral, base.attitude.neutral),
      accounts: delta(cur.activeAccounts, base.activeAccounts),
      base: base
    };
  }

  /* ────────────── 5. 观点主题（第 5.6 节） ────────────── */
  var POS = {
    'ETF': [
      ['费率具备比较优势', '讨论把经常性开支与同类产品逐项对比，费率差被当作长期持有的主要理由。'],
      ['跟踪偏差控制稳定', '讨论提到区间内净值与指数的差距在预期范围，没有出现集中质疑。'],
      ['盘口流动性充足', '讨论提到成交活跃、买卖价差稳定，分批建仓没有明显冲击成本。'],
      ['港股通渠道可直接买入', '内地投资者讨论提到无需换汇与额外开户，交易便利性被反复提及。'],
      ['作为定投核心仓位', '讨论以月度定投复盘为主，强调持仓周期长、操作频率低。'],
      ['规模与做市深度被认可', '讨论以基金规模与做市商报价档位作为选择依据。']
    ],
    '杠反': [
      ['替代融资的日内工具', '讨论把产品当作日内方向性交易的替代方案，强调不涉及保证金与强平安排。'],
      ['溢价折价近期收敛', '讨论提到市价与参考净值的偏离幅度较上期收窄，入场成本更可控。'],
      ['盘中报价连续', '讨论提到报价档位稳定，当日进出的成交量足够。'],
      ['倍数关系清楚可自行推算', '讨论认为每日重置的倍数规则明确，当日盈亏可以自行核对。']
    ],
    '备兑高息': [
      ['月度派息节奏可预期', '讨论以到账记录为依据，认为分派时间与金额稳定。'],
      ['震荡行情下收益相对稳', '讨论认为指数横盘阶段期权金收入弥补了价格波动。'],
      ['分派水平高于同类', '讨论把年化分派率与其他高息产品对比作为买入理由。']
    ],
    '货币': [
      ['闲置资金的停泊工具', '讨论提到申赎便利，把产品当作交易间隙的现金管理选择。'],
      ['收益率披露口径清楚', '讨论认为七日年化与到期收益率同时披露，便于横向比较。'],
      ['净值曲线平滑', '讨论强调价格波动小，短期资金持有体验稳定。']
    ],
    '主题': [
      ['成分股与主题定义一致', '讨论认为持仓名单能对应主题描述，便于判断行业敞口。'],
      ['一篮子替代个股买入', '讨论提到用一只产品替代分散买入，降低选股难度。']
    ]
  };
  var NEG = {
    'ETF': [
      ['跟踪偏差被质疑扩大', '讨论提到区间内净值与指数的差距超出预期，要求披露偏差来源。'],
      ['费率高于同类产品', '讨论以同类产品的经常性开支作对比，质疑价差没有反映在跟踪表现上。'],
      ['盘口偏薄、价差扩大', '讨论提到挂单成交慢、买卖价差吃掉短线收益。'],
      ['除权与分红处理不清楚', '讨论对除权后单位净值与派息金额的对应关系存在疑问。'],
      ['规模偏小引发流动性担忧', '讨论认为规模不足会在成交清淡时放大价差。']
    ],
    '杠反': [
      ['隔夜与复利耗损', '讨论提到连续波动后累计收益低于倍数预期，认为复利效应披露不足。'],
      ['溢价过高抬升入场成本', '讨论提到市价高于参考净值，质疑买入即承担回归风险。'],
      ['每日再平衡机制难理解', '讨论要求在产品页面直接说明再平衡与倍数重置的计算方式。'],
      ['缺少持有期示例', '讨论认为风险提示集中在开户环节，产品页面没有多日持有的演算。']
    ],
    '备兑高息': [
      ['上行封顶影响总回报', '讨论提到指数上涨时收益跑输普通指数产品。'],
      ['派息金额较上期下降', '讨论以到账金额对比上一期，质疑分派可持续性。'],
      ['收益来源披露不足', '讨论要求区分期权金收入与本金返还部分。']
    ],
    '货币': [
      ['收益率下行后费用占比变大', '讨论提到管理费在收益率走低时对净收益的侵蚀明显。'],
      ['申赎到账时间不确定', '讨论提到赎回到账时间与页面说明不一致。'],
      ['收益率口径容易误读', '讨论对七日年化与实际持有收益的差异提出疑问。']
    ],
    '主题': [
      ['成分股集中度偏高', '讨论提到前几大权重占比过高，与主题分散的预期不符。'],
      ['主题降温后成交清淡', '讨论提到热度回落后盘口变薄，退出成本上升。']
    ]
  };

  var memo = {};
  function cached(key, fn) { if (!(key in memo)) memo[key] = fn(); return memo[key]; }

  function themesFor(code, rangeKey, polarity) { return cached('th|' + code + '|' + rangeKey + '|' + polarity, function () { return themesCalc(code, rangeKey, polarity); }); }
  function themesCalc(code, rangeKey, polarity) {
    var m = MASTER[code], range = buildRange(rangeKey);
    var bank = (polarity === 'positive' ? POS : NEG)[m.struct] || POS['ETF'];
    var o = observe(code, rangeKey);
    var total = polarity === 'positive' ? o.attitude.positive : o.attitude.negative;
    if (!total) return [];
    var n = Math.min(bank.length, Math.max(2, 3 + (hash(code + polarity + rangeKey) % 2)));
    var chosen = pickN(bank, code + polarity + rangeKey, n);
    var weights = chosen.map(function (t, i) { return rnd(code + polarity + t[0] + 'w', 0.5, 1) / (i * 0.45 + 1); });
    var wSum = weights.reduce(function (a, b) { return a + b; }, 0);
    var used = 0;
    return chosen.map(function (t, i) {
      var mn = i === chosen.length - 1 ? total - used : Math.max(1, Math.round(total * weights[i] / wSum));
      if (mn < 0) mn = 0;
      used += mn;
      var baseMn = Math.max(0, Math.round(mn * rnd(code + t[0] + 'bm', 0.55, 1.5)));
      var tw = range.buckets.map(function (b) {
        var shape = range.gran === 'hour' ? hourShape(b.hour) : (range.gran === 'day' ? dayShape(b.dow) : 1);
        return shape * rnd(code + t[0] + b.i, 0.2, 2.1);
      });
      var tv = spread(mn, tw);
      var bk = range.buckets.map(function (b, bi) {
        return { label: b.label, tip: b.tip, mentions: tv[bi] };
      });
      return {
        id: code + '-' + polarity + '-' + i, polarity: polarity,
        title: t[0], summary: t[1],
        mentions: mn,
        share: total ? mn / total * 100 : 0,
        delta: delta(mn, baseMn),
        confidence: Math.round(rnd(code + t[0] + 'cf', 62, 94)),
        buckets: bk,
        evidenceCount: Math.max(1, Math.round(mn * rnd(code + t[0] + 'ev', 0.35, 0.8)))
      };
    }).filter(function (x) { return x.mentions > 0; })
      .sort(function (a, b) { return b.mentions - a.mentions; });
  }

  /* ────────────── 6. 动态负面舆情类别（第 5.8 节） ────────────── */
  var NEGCAT = {
    'ETF': [
      ['跟踪偏差', '区间内多条内容对净值与指数的差距提出疑问，要求给出偏差构成说明。'],
      ['价差扩大', '内容集中反映盘口档位变薄，挂单成交时间拉长。'],
      ['费用理解', '投资者把经常性开支拆到年化收益上比较，认为披露口径不够直观。'],
      ['分红处理', '除权日前后关于派息金额与单位净值对应关系的疑问集中出现。'],
      ['申赎体验', '内容提到申购与赎回的到账时间与页面说明存在落差。'],
      ['披露完整性', '投资者要求在产品页面补充持仓明细与偏差归因的更新频率。']
    ],
    '杠反': [
      ['机制误解', '多条内容把每日重置理解为区间累计倍数，出现对收益的错误预期。'],
      ['溢价折价', '内容反映市价明显高于参考净值，质疑入场时点被抬高。'],
      ['隔夜风险披露', '投资者要求在下单环节而非说明书中提示跳空与持有期耗损。'],
      ['价差扩大', '内容提到非交投时段报价档位不足，退出成本上升。'],
      ['再平衡透明度', '投资者要求公布每日再平衡的时点与规模。']
    ],
    '备兑高息': [
      ['分红处理', '内容对本期分派金额低于上期提出疑问，要求说明收益构成。'],
      ['机制误解', '部分内容把期权金收入等同于股息，出现对分派来源的误读。'],
      ['费用理解', '投资者要求把期权交易成本单独列示。'],
      ['上行参与度', '内容反映指数上涨阶段的收益落后，要求量化封顶幅度。']
    ],
    '货币': [
      ['费用理解', '收益率下行阶段，投资者对管理费占净收益的比例提出疑问。'],
      ['申赎体验', '内容提到赎回到账时间与预期不一致，影响资金安排。'],
      ['收益率口径', '七日年化与到期收益率被混用，投资者要求统一展示口径。'],
      ['披露完整性', '投资者要求公布组合久期与持仓类别的更新频率。']
    ],
    '主题': [
      ['成分集中度', '内容对前几大权重占比提出疑问，认为与主题分散的预期不符。'],
      ['流动性', '主题降温后成交清淡，内容反映退出成本上升。'],
      ['费用理解', '投资者把主题产品与宽基产品的费率直接比较。'],
      ['指数编制', '内容要求说明成分调整的规则与频率。']
    ]
  };
  var LIFE = { 'new': '新增', 'continuing': '持续', 'fading': '消退' };

  function negCatsFor(code, rangeKey) { return cached('nc|' + code + '|' + rangeKey, function () { return negCatsCalc(code, rangeKey); }); }
  function negCatsCalc(code, rangeKey) {
    var m = MASTER[code], o = observe(code, rangeKey), range = buildRange(rangeKey);
    var negTotal = o.attitude.negative;
    if (!negTotal) return [];
    var bank = NEGCAT[m.struct] || NEGCAT['ETF'];
    var n = Math.min(bank.length, 3 + (hash(code + rangeKey + 'nc') % 4));   // 3–6 个
    var chosen = pickN(bank, code + rangeKey + 'ncat', n);
    /* 负面舆情监控只覆盖可归类、可行动的部分，不等于全部消极观点 */
    var pool = Math.max(1, Math.round(negTotal * rnd(code + 'pool', 0.45, 0.78)));
    var ws = chosen.map(function (c, i) { return rnd(code + c[0] + 'nw', 0.5, 1) / (i * 0.4 + 1); });
    var wSum = ws.reduce(function (a, b) { return a + b; }, 0);
    var used = 0;
    return chosen.map(function (c, i) {
      var mn = i === chosen.length - 1 ? Math.max(1, pool - used) : Math.max(1, Math.round(pool * ws[i] / wSum));
      used += mn;
      var baseMn = Math.round(mn * rnd(code + c[0] + 'nb', 0.3, 1.7));
      var lifeKey = baseMn === 0 ? 'new' : (mn > baseMn * 1.15 ? 'continuing' : (mn < baseMn * 0.7 ? 'fading' : 'continuing'));
      if (hash(code + c[0] + 'lf') % 7 === 0) lifeKey = 'new';
      var share = mn / negTotal * 100;
      var sev = (share >= 25 && lifeKey !== 'fading') ? 'high' : (share >= 12 ? 'medium' : 'low');
      var firstOff = Math.floor(rnd(code + c[0] + 'fs', 0, range.days));
      var lastOff = Math.floor(rnd(code + c[0] + 'ls', 0, Math.max(1, range.days - firstOff)));
      return {
        id: code + '-nc-' + i,
        label: c[0], summary: c[1],
        lifecycle: lifeKey, lifecycleLabel: LIFE[lifeKey],
        mentions: mn, shareOfNegative: share,
        delta: delta(mn, baseMn),
        firstSeenAt: md(addDays(range.from, firstOff)) + ' ' + String(9 + hash(code + c[0]) % 9).padStart(2, '0') + ':00',
        lastSeenAt: md(addDays(range.from, Math.min(range.days - 1, firstOff + lastOff))) + ' ' + String(10 + hash(code + c[0] + 'x') % 8).padStart(2, '0') + ':00',
        severity: sev,
        severityLabel: sev === 'high' ? '高' : (sev === 'medium' ? '中' : '低'),
        evidenceCount: Math.max(1, Math.round(mn * rnd(code + c[0] + 'ec', 0.4, 0.9)))
      };
    }).sort(function (a, b) { return b.mentions - a.mentions; });
  }

  /* ────────────── 7. 产品话题情绪（第 5.9 节） ────────────── */
  var TOPIC_BANK = {
    hk: [
      ['恒生科技指数反弹持续性', '讨论围绕反弹能否延续，多空判断并存，与产品机制无关。'],
      ['港股通资金流向', '内容以南向净流入数据为依据推断后续方向。'],
      ['恒指关键点位争夺', '讨论集中在整数关口的支撑与阻力。']
    ],
    a: [
      ['A股成交量能否放大', '讨论以两市成交额判断行情延续性。'],
      ['政策预期与板块轮动', '内容围绕政策窗口对指数结构的影响。'],
      ['A500 与沪深300 的选择', '讨论比较两条宽基路线的覆盖差异。']
    ],
    us: [
      ['纳指财报窗口的波动', '讨论集中在财报密集期的仓位安排。'],
      ['美联储议息预期', '内容以利率路径推断风险资产表现。'],
      ['美股估值分歧', '讨论对当前估值水平存在明显分歧。']
    ],
    sgl: [
      ['英伟达财报前的仓位安排', '讨论围绕财报前后是否降低敞口。'],
      ['海力士 HBM 产能进展', '内容以供应链消息推断股价方向。'],
      ['单一股票杠杆的持有周期', '讨论对合适的持有天数存在分歧。']
    ],
    apac: [
      ['日经与越南的区域轮动', '讨论比较两个市场的配置顺序。'],
      ['日元汇率对回报的影响', '内容关注汇率变动对以港元计价收益的影响。']
    ],
    fi: [
      ['美元利率下行路径', '讨论以利率预期判断货币与债券产品的相对吸引力。'],
      ['长久期国债的久期风险', '内容围绕利率反弹时的回撤幅度。']
    ],
    cm: [
      ['黄金避险与美元流向', '讨论把美元指数与金价走势对照。'],
      ['金价关键位争夺', '内容集中在整数关口附近的多空判断。']
    ],
    va: [
      ['比特币现货与期货基差', '讨论以基差变化判断展期成本。'],
      ['虚拟资产监管进展', '内容围绕政策变化对交易需求的影响。']
    ]
  };
  var TOPIC_COMMON = [
    ['与同类产品的选择比较', '讨论在多只同类产品之间比较，尚未形成一致结论。'],
    ['指数与市场方向判断', '内容为对指数或市场方向的预测，不构成对产品本身的态度。']
  ];

  function topicsFor(code, rangeKey) { return cached('tp|' + code + '|' + rangeKey, function () { return topicsCalc(code, rangeKey); }); }
  function topicsCalc(code, rangeKey) {
    var m = MASTER[code], o = observe(code, rangeKey), range = buildRange(rangeKey);
    if (!o.mentions) return [];
    var bank = (TOPIC_BANK[m.sector] || []).concat(TOPIC_COMMON);
    var n = Math.min(bank.length, 3 + (hash(code + rangeKey + 'tp') % 2));
    var chosen = pickN(bank, code + rangeKey + 'topic', n);
    return chosen.map(function (t, i) {
      var mn = Math.max(1, Math.round(o.mentions * rnd(code + t[0] + 'tm', 0.08, 0.3) / (i * 0.3 + 1)));
      var neu = Math.round(mn * rnd(code + t[0] + 'tn', 0.3, 0.6));
      var pos = Math.round((mn - neu) * rnd(code + t[0] + 'tp2', 0.25, 0.75));
      var tw = range.buckets.map(function (b) { return rnd(code + t[0] + 'tb' + b.i, 0.15, 2.2); });
      var tv = spread(mn, tw);
      var bk = range.buckets.map(function (b, bi) {
        return { label: b.label, tip: b.tip, mentions: tv[bi] };
      });
      return {
        id: code + '-tp-' + i, title: t[0], summary: t[1],
        mentions: mn, positive: pos, negative: mn - neu - pos, neutral: neu,
        buckets: bk,
        delta: delta(mn, Math.round(mn * rnd(code + t[0] + 'td', 0.5, 1.6))),
        evidenceCount: Math.max(1, Math.round(mn * rnd(code + t[0] + 'te', 0.4, 0.85))),
        peak: pick(range.buckets, code + t[0] + 'pk').tip,
        split: pick([
          '讨论存在明显分歧，多空双方均给出量化依据。',
          '讨论方向较一致，反对意见集中在时间点而非方向。',
          '讨论以观望为主，明确表态的内容占比不高。'
        ], code + t[0] + 'sp')
      };
    }).sort(function (a, b) { return b.mentions - a.mentions; });
  }

  /* ────────────── 8. 关联竞品（第 5.10 节，双向） ────────────── */
  function competitorsFor(code, rangeKey) { return cached('cp|' + code + '|' + rangeKey, function () { return competitorsCalc(code, rangeKey); }); }
  function competitorsCalc(code, rangeKey) {
    var m = MASTER[code];
    if (m.relationUnavailable) return { status: 'unavailable', list: [] };
    var rows = [];
    if (m.ownership === 'own') {
      CMAP.filter(function (c) { return c[3] === code; }).forEach(function (c) {
        rows.push({ code: c[1], relation: 'confirmed', reason: '客户维护的固定对位映射 · ' + c[0] });
      });
      var siblings = ORDER.filter(function (x) {
        var t = MASTER[x];
        return t.ownership === 'peer' && t.sector === m.sector && t.struct === m.struct && !rows.some(function (r) { return r.code === x; });
      });
      pickN(siblings, code + 'auto', Math.min(2, siblings.length)).forEach(function (x) {
        rows.push({ code: x, relation: 'auto_candidate', reason: 'AI 依据同板块同结构与共同提及识别，尚未人工确认' });
      });
    } else {
      if (m.ownCode && MASTER[m.ownCode]) {
        rows.push({ code: m.ownCode, relation: 'confirmed', reason: '映射表中的对位自家产品 · CSOP 南方东英' });
      }
      CMAP.filter(function (c) { return c[3] === m.ownCode && c[1] !== code; }).forEach(function (c) {
        rows.push({ code: c[1], relation: 'auto_candidate', reason: '与同一自家产品对位的同类竞品，关系由映射推导' });
      });
    }
    var list = rows.map(function (r) {
      var o = observe(r.code, rangeKey), t = MASTER[r.code];
      var pos = themesFor(r.code, rangeKey, 'positive').slice(0, 2);
      var neg = themesFor(r.code, rangeKey, 'negative').slice(0, 2);
      return {
        code: r.code, name: t.name, issuer: t.issuer, ownership: t.ownership,
        relation: r.relation, reason: r.reason,
        mentions: o.mentions, comments: o.comments, delta: benchmark(r.code, rangeKey).comments,
        positiveThemes: pos, negativeThemes: neg,
        positiveCount: o.attitude.positive, negativeCount: o.attitude.negative,
        evidencePos: pos.reduce(function (a, b) { return a + b.evidenceCount; }, 0),
        evidenceNeg: neg.reduce(function (a, b) { return a + b.evidenceCount; }, 0)
      };
    }).sort(function (a, b) { return b.mentions - a.mentions; });
    return { status: list.length ? 'ok' : 'empty', list: list };
  }

  /* ────────────── 9. 证据与单帖详情（第 5.11 节） ────────────── */
  var RETAIL_NAMES = ['港岛小散', '牛牛_8842', '定投老陈', '中环打工人', '深水埗阿明', '恒指观察员', '半仓过夜',
    '每周复盘', '慢慢变富', '数据派小林', '南向老张', '不加杠杆', '看盘不下单', '沪港通阿May'];
  var KOL_NAMES = ['深夜学堂', '价值投资叶荣添', '陈达美股投资', '财富种植园', '嘉哥爱自由', '京城z先生', 'Zona投資隨筆'];
  var OFFICIAL_NAMES = ['牛牛課堂', '今日熱門', '富途寰球私享匯'];

  var EX_POS = [
    '费率差 0.4 个点，长期下来不是小数目，先放着不动。',
    '这两周价差挺稳，几笔大单进出都没什么冲击。',
    '港股通里直接能买，省了换汇那一步，方便。',
    '月定投第 14 期，波动能受得住，继续。',
    '规模摆在那里，挂单基本是秒成交。',
    '当日做方向用它，不用管保证金，收盘平掉就行。',
    '这次到账金额和上次差不多，节奏是稳的。'
  ];
  var EX_NEG = [
    '净值和指数差出来的这一截，希望说明一下是从哪来的。',
    '盘口就两三档，挂了半小时不成交，价差都吃掉收益了。',
    '同样跟这个指数，别家开支更低，为什么价差没反映在跟踪上？',
    '连着跌了三天，按两倍算应该更多，结果差了一截，复利这块说明书写得太含糊。',
    '现在溢价这么高买进去，等于替别人接盘。',
    '除权那天单位净值怎么对上派息金额，页面上看不出来。',
    '赎回到账比页面写的慢了一天，资金安排被打乱。',
    '收益率下来以后，管理费占的比例就明显了。'
  ];
  var EX_NEU = [
    '指数这个位置还是先观望，等靴子落地。',
    '把几只同类的规模和费率拉了个表，回头再发。',
    '这周成交量比上周小，先不动。',
    '财报前后波动会放大，仓位先减一半。'
  ];

  function evidenceFor(code, ctxKey, polarity, count) {
    var range = buildRange(ctxKey.split('|')[0] || 'd7');
    var bank = polarity === 'positive' ? EX_POS : (polarity === 'negative' ? EX_NEG : EX_NEU);
    var n = Math.max(1, Math.min(12, count || 6));
    var m = MASTER[code];
    var out = [];
    for (var i = 0; i < n; i++) {
      var key = code + ctxKey + i;
      var h = hash(key);
      var typeRoll = h % 10;
      var authorType = typeRoll < 7 ? '普通散户' : (typeRoll < 9 ? '合作 KOL' : '官方账号');
      var pool = authorType === '普通散户' ? RETAIL_NAMES : (authorType === '合作 KOL' ? KOL_NAMES : OFFICIAL_NAMES);
      var dayOff = h % range.days;
      var others = ORDER.filter(function (x) { return MASTER[x].sector === m.sector && x !== code; });
      var extra = (h % 4 === 0 && others.length) ? [pick(others, key + 'o')] : [];
      out.push({
        id: key,
        publishedAt: addDays(range.from, dayOff) + ' ' + String(9 + (h % 12)).padStart(2, '0') + ':' + String(h % 60).padStart(2, '0'),
        authorName: pick(pool, key + 'a'),
        authorType: authorType,
        excerpt: pick(bank, key + 'e'),
        productCodes: [code].concat(extra),
        comments: 2 + (h % 68),
        interactions: 12 + (h % 940),
        sourceUrl: 'https://www.futunn.com/post/' + (6000000 + (h % 2500000))
      });
    }
    return out.sort(function (a, b) { return a.publishedAt < b.publishedAt ? 1 : -1; });
  }

  /* ────────────── 10. 当前舆情总结（第 5.5 节） ────────────── */
  function summaryFor(code, rangeKey) { return cached('sm|' + code + '|' + rangeKey, function () { return summaryCalc(code, rangeKey); }); }
  function summaryCalc(code, rangeKey) {
    var o = observe(code, rangeKey), range = buildRange(rangeKey), b = benchmark(code, rangeKey);
    var a = o.attitude, valid = a.positive + a.negative;
    var peak = o.buckets.slice().sort(function (x, y) { return y.mentions - x.mentions; })[0];
    var s = [];
    s.push('数据显示，' + o.name + '（' + o.code + '）在 ' + range.from + ' 至 ' + range.to + ' 共识别到 ' +
      o.mentions + ' 条去重提及，讨论最集中的时间桶为 ' + (peak ? peak.tip : '—') + '。');
    if (!o.mentions) {
      return { text: '暂无相关内容 — 在所选区间内已完成检查，该产品没有识别到提及内容。', sample: 0, low: true };
    }
    if (!a.sampleSufficient) {
      s.push('针对产品本身的有效态度提及为 ' + valid + ' 条，低于 ' + LOW_SAMPLE + ' 条的判定阈值，本区间不输出整体倾向结论。');
      s.push('原始数量为积极 ' + a.positive + ' 条、消极 ' + a.negative + ' 条、中性 ' + a.neutral + ' 条。');
    } else {
      var diff = a.positive - a.negative;
      s.push('产品态度分类中积极 ' + a.positive + ' 条、消极 ' + a.negative + ' 条、中性 ' + a.neutral + ' 条，' +
        (diff === 0 ? '积极与消极条数持平' : (diff > 0 ? '积极比消极多 ' + diff + ' 条' : '消极比积极多 ' + (-diff) + ' 条')) + '。');
      var pt = themesFor(code, rangeKey, 'positive'), nt = themesFor(code, rangeKey, 'negative');
      if (pt.length || nt.length) {
        s.push('积极讨论主要集中于' + (pt.length ? pt.slice(0, 2).map(function (x) { return x.title; }).join('、') : '暂无可归类主题') +
          '；消极讨论主要集中于' + (nt.length ? nt.slice(0, 2).map(function (x) { return x.title; }).join('、') : '暂无可归类主题') + '。');
      }
    }
    s.push(range.benchLabel + '（' + range.benchText + '），提及 ' + b.mentions.text + '，讨论热度 ' + b.heat.text + '。');
    return { text: s.join(''), sample: valid, low: !a.sampleSufficient };
  }

  /* ────────────── 11. 静态聚合（账号域沿用） ────────────── */
  var DEFAULT_KEY = 'd7';
  var PRODUCTS = P.map(function (r) {
    var o = observe(r[0], DEFAULT_KEY);
    return {
      code: o.code, name: o.name, sector: o.sector, struct: o.struct, w: MASTER[o.code].w, south: o.south,
      mentions: o.mentions, comments: o.comments, inter: o.interactions,
      posters: o.activeAccounts, heat: o.discussionHeat
    };
  });
  var BY_CODE = {}; PRODUCTS.forEach(function (p) { BY_CODE[p.code] = p; });

  function sectorAgg(rangeKey, includeCompetitors) {
    return SECTORS.map(function (s) {
      var list = ORDER.filter(function (c) {
        var m = MASTER[c];
        return m.sector === s.k && (includeCompetitors || m.ownership === 'own');
      }).map(function (c) { return observe(c, rangeKey); });
      var sum = function (f) { return list.reduce(function (t, p) { return t + (p[f] || 0); }, 0); };
      return {
        k: s.k, name: s.name, hue: s.hue, tint: s.tint,
        list: list, n: list.length,
        mentions: sum('mentions'), comments: sum('comments'), inter: sum('interactions'),
        heat: sum('discussionHeat')
      };
    });
  }
  var SECTOR_AGG = sectorAgg(DEFAULT_KEY, false);
  var ACTIVE_ACCOUNTS = 1167;

  /* 42 天评论量与活跃账号趋势（第 4.5 节保留模块） */
  var SERIES = (function () {
    var out = [], d = new Date(Date.UTC(2026, 6, 22));
    for (var i = 0; i < 42; i++) {
      var key = d.toISOString().slice(5, 10);
      var dow = d.getUTCDay();
      var weekend = (dow === 0 || dow === 6) ? 0.34 : 1;
      var wave = 1 + 0.55 * Math.sin(i / 4.4) + 0.3 * Math.sin(i / 1.7);
      var decay = 1.55 - i / 42 * 0.85;
      out.push({
        date: key, iso: d.toISOString().slice(0, 10), w: weekend,
        comments: Math.max(30, Math.round(1150 * wave * decay * weekend * rnd('s' + i, 0.82, 1.18))),
        active: Math.max(12, Math.round(118 * (1.3 - i / 42 * 0.55) * weekend * rnd('a' + i, 0.86, 1.14))),
        px: 0, o: 0, h: 0, l: 0, c: 0
      });
      d.setUTCDate(d.getUTCDate() + 1);
    }
    var px = 68;
    out.forEach(function (r, i) {
      var o = px, drift = Math.sin(i / 5.2) * 2.6 + rnd('px' + i, -1, 1) * 2.2;
      var c = Math.max(38, o + drift);
      out[i].o = o; out[i].c = c;
      out[i].h = Math.max(o, c) + rnd('h' + i, 0.2, 1.9);
      out[i].l = Math.min(o, c) - rnd('l' + i, 0.2, 1.9);
      out[i].px = c; px = c;
    });
    return out;
  })();

  /* 单只 ETF 的日度评论量与活跃账号序列（4.5 模块下拉选择用；mock，种子固定，刷新不变） */
  var DAILY = {};
  function dailyFor(code) {
    if (DAILY[code]) return DAILY[code];
    var out;
    if (code === 'ALL') {
      out = SERIES.map(function (r) {
        return { date: r.date, iso: r.iso, comments: r.comments, active: r.active, px: r.px, o: r.o, h: r.h, l: r.l, c: r.c };
      });
    } else {
      var o = observe(code, 'd7');
      var base = Math.max(1.6, o.comments / 7);
      var ar = rnd(code + 'ar', 0.30, 0.56), ph = rnd(code + 'ph', 0, 6.283);
      var amp = rnd(code + 'am', 0.30, 0.68), tr = rnd(code + 'tr', -0.42, 0.5);
      var p = 16 + rnd(code + 'p0', 0, 96);
      out = SERIES.map(function (r, i) {
        var shape = Math.max(0.12, 1 + amp * Math.sin(i / 5.1 + ph) + 0.2 * Math.sin(i / 1.9 + ph * 2));
        var slope = Math.max(0.25, 1 + tr * (i / 41 - 0.5) * 2);
        var c = Math.max(0, Math.round(base * shape * slope * r.w * rnd(code + 'c' + i, 0.72, 1.3)));
        var a = c === 0 ? 0 : Math.max(1, Math.round(c * ar * rnd(code + 'a' + i, 0.78, 1.22)));
        var op = p;
        p = Math.max(6, p + Math.sin(i / 4.6 + ph) * p * 0.012 + rnd(code + 'px' + i, -1, 1) * p * 0.021);
        var cl = Math.round(p * 100) / 100;
        var hi = Math.round((Math.max(op, cl) + rnd(code + 'h' + i, 0.05, 0.9) * op * 0.012) * 100) / 100;
        var lo = Math.round((Math.min(op, cl) - rnd(code + 'l' + i, 0.05, 0.9) * op * 0.012) * 100) / 100;
        return {
          date: r.date, iso: r.iso, comments: c, active: a, px: cl,
          o: Math.round(op * 100) / 100, c: cl, h: hi, l: lo
        };
      });
    }
    DAILY[code] = out;
    return out;
  }

  /* 账号域主数据：官号名单 [简称, 全称, 7 日发布篮数基准, 7 日互动基准, 对位产品数]；KOL 名单 [名称, 标签, 是否合作中] */
  var OFFICIAL = [
    ['恒生投资', '恒生投資管理有限公司', 12, 3420, 8], ['华夏', '華夏基金香港', 17, 5140, 11],
    ['Global X', 'Global X ETFs', 21, 6280, 14], ['平安', '平安資產管理（香港）', 6, 1180, 4],
    ['博时', '博时国际', 4, 760, 2], ['易方达', '易方达香港', 9, 2010, 6],
    ['三星', '三星ETF', 14, 4360, 9], ['招证', '招證資管香港', 3, 520, 2],
    ['潘渡', 'Pando Finance潘渡', 5, 890, 3], ['嘉实', '嘉實國際資產管理', 7, 1340, 4],
    ['富邦', '富邦基金', 4, 680, 3], ['LeverageShares', 'LeverageShares', 8, 1620, 5],
    ['富途寰球私享', '富途寰球私享匯', 26, 9840, 0], ['牛牛新股君', '牛牛新股君', 31, 12400, 0],
    ['牛牛课堂', '牛牛課堂', 19, 7260, 0], ['牛牛有料到', '牛牛有料到', 23, 8910, 0],
    ['今日热门', '今日熱門', 42, 18600, 0], ['搞活动的牛牛', '搞活动的牛牛', 15, 5480, 0],
    ['玩赚牛牛', '玩賺牛牛', 11, 3920, 0], ['牛友有料到', '牛友有料到', 13, 4610, 0]
  ];
  var KOLS = [
    ['孫子的末代傳人', '技术分析,晒单', 1], ['中戶變大戶小牛變大牛', '技术分析,晒单', 1],
    ['股海中的G妹', '技术分析,晒单', 1], ['年頭旺到年尾', '技术分析,晒单', 1],
    ['摩帥', '技术分析,晒单', 1], ['Zona投資隨筆', '技术分析', 1],
    ['港美猎人', '技术分析,晒单', 1], ['財媽價值輪回', '技术分析,晒单', 1],
    ['价值投资叶荣添', 'ETF投教,市场洞察', 1], ['守猪逮兔估值模型', '估值模型', 1],
    ['圣者为王', '技术分析', 1], ['一颗财丸', '市场洞察', 1],
    ['知常公子', '晒单', 1], ['丹尼斯王子', '市场洞察,技术分析', 0],
    ['陈达美股投资', '市场洞察', 1], ['深夜学堂', 'ETF投教,市场洞察,技术分析', 1],
    ['耀文港古', '市场洞察', 0], ['潘驴邓晓闲缺一', '晒单,技术分析', 0],
    ['中环杰哥投资探秘', '晒单,技术分析', 1], ['财富种植园', 'ETF投教', 1],
    ['嘉哥爱自由', 'ETF投教,市场洞察', 1], ['杨杨得亿', '晒单', 1],
    ['京城z先生', '晒单,ETF投教,市场洞察', 1], ['六爷漫谈', '晒单,打新', 0],
    ['站在Ju人肩上', '晒单,打新', 0], ['搏盡佢啦', '技术分析,晒单', 0],
    ['股市魔术师', '晒单', 1], ['不知名选手', '晒单', 1],
    ['Henry 財自了', '市场洞察', 1], ['只做主升不做调整', '晒单,技术分析', 1],
    ['牛头法师', '晒单', 1], ['逍遥游', '晒单', 1]
  ];
  var RETAIL_TIERS = [
    ['高频（≥20 条）', 46, 1820, '#2361AD'],
    ['中频（5–19 条）', 213, 2140, '#4A4E8C'],
    ['低频（1–4 条）', 908, 1180, '#8B95A3']
  ];
  var RETAIL_NEW = [
    ['08-26', 41, 118], ['08-27', 63, 141], ['08-28', 58, 133],
    ['08-29', 72, 149], ['08-30', 49, 127], ['08-31', 22, 71], ['09-01', 14, 52]
  ];

  /* ────────────── 12. KOL 发帖内容与阵地分布（帖子级 AI 摘要 / 类型 / 阵营，演示数据） ──────────────
     2026-09-06 改版：评论区逐条内容不可得，删除每小时评论序列、好差评、全站增量与洛伦兹分层，
     分析对象改为帖子本身。summary / postType / typeEvidence 模拟 AI 输出（演示文案，简体中文）；
     mentioned / camp 由「挂载标的 ∪ 正文提及」对照产品池（61 自家 + 59 竞品）规则匹配，不经模型。 */
  var POST_TYPES = [
    { k: 'showcase', label: '晒单', group: 'op', bar: '#C9A961', def: '贴出实际成交记录 / 持仓截图' },
    { k: 'action', label: '操作宣言', group: 'op', bar: '#A8853C', def: '宣布买入 / 卖出 / 调仓意向，无凭证' },
    { k: 'market', label: '行情解读', group: 'view', bar: '#2361AD', def: '对市场或标的走势的分析观点' },
    { k: 'promo', label: '产品推介', group: 'promo', bar: '#1F8A5B', def: '介绍或安利某只 ETF 的特点' },
    { k: 'edu', label: '教学科普', group: 'view', bar: '#7DA5DC', def: '知识型内容，不针对特定操作' },
    { k: 'event', label: '活动/福利', group: 'event', bar: '#7A5C9E', def: '转发平台或发行商活动、抽奖' },
    { k: 'qa', label: '问答/互动', group: 'event', bar: '#B39DC9', def: '向粉丝提问、征集观点、回应评论' },
    { k: 'other', label: '其他', group: 'other', bar: '#B7BFC9', def: '无法归入以上' }
  ];
  var TYPE_BY_KEY = {}; POST_TYPES.forEach(function (t) { TYPE_BY_KEY[t.k] = t; });
  /* 标签配色：操作类琥珀、观点类蓝、推介类绿、活动类紫、其他灰 */
  var TYPE_GROUP = {
    op: { bg: 'var(--warning-100)', fg: 'var(--warning-700)' },
    view: { bg: 'var(--csop-blue-50)', fg: 'var(--csop-blue-700)' },
    promo: { bg: 'var(--positive-100)', fg: 'var(--positive-700)' },
    event: { bg: '#F1ECF7', fg: '#5E4480' },
    other: { bg: 'var(--ink-100)', fg: 'var(--ink-600)' }
  };
  function typeStyle(k) {
    var t = TYPE_BY_KEY[k] || TYPE_BY_KEY.other, g = TYPE_GROUP[t.group];
    return { k: t.k, label: t.label, bg: g.bg, fg: g.fg, bar: t.bar, def: t.def };
  }
  /* 操作方向（双标签第二维，2026-09-08）：加仓／建仓 绿、减仓／清仓 红、持有观望 灰；判不出方向的操作类帖子标「待确认」 */
  var DIRECTIONS = [
    { k: 'add', label: '加仓', tone: 'pos' }, { k: 'open', label: '建仓', tone: 'pos' },
    { k: 'reduce', label: '减仓', tone: 'neg' }, { k: 'close', label: '清仓', tone: 'neg' },
    { k: 'hold', label: '持有观望', tone: 'neu' }
  ];
  var DIR_BY_KEY = {}; DIRECTIONS.forEach(function (d) { DIR_BY_KEY[d.k] = d; });
  var DIR_TONE = {
    pos: { bg: 'var(--positive-100)', fg: 'var(--positive-700)' },
    neg: { bg: 'var(--negative-100)', fg: 'var(--negative-700)' },
    neu: { bg: 'var(--ink-100)', fg: 'var(--ink-600)' },
    pending: { bg: 'var(--warning-100)', fg: 'var(--warning-700)' }
  };
  function dirStyle(k, pending) {
    if (pending || !DIR_BY_KEY[k]) return { k: 'pending', label: '方向待确认', bg: DIR_TONE.pending.bg, fg: DIR_TONE.pending.fg, tone: 'pending' };
    var d = DIR_BY_KEY[k], t = DIR_TONE[d.tone];
    return { k: d.k, label: d.label, bg: t.bg, fg: t.fg, tone: d.tone };
  }
  var TYPE_RULE = '类型为双标签：内容形式（晒单／操作宣言／行情解读／产品推介／教学科普／活动福利／问答互动／其他）必有一枚；操作方向（加仓／减仓／建仓／清仓／持有观望）只在帖子表达了明确操作时出现，判不出方向的操作类帖子标「方向待确认」。';
  var CAMP = {
    own: { k: 'own', label: '自家', bg: 'var(--csop-blue-600)', fg: '#fff' },
    competitor: { k: 'competitor', label: '竞品', bg: 'var(--csop-silver-200)', fg: 'var(--ink-700)' },
    both: { k: 'both', label: '双方', bg: 'var(--csop-blue-100)', fg: 'var(--csop-blue-800)' },
    none: { k: 'none', label: '未提及', bg: 'var(--ink-100)', fg: 'var(--ink-500)' }
  };
  var CAMP_RULE = '阵营口径：帖子挂载的标的 ∪ 正文中出现的产品代码 / 名称，对照客户维护的产品池（61 自家 + 59 竞品）规则匹配，不经模型判断。';
  var COUNT_NOTE = '赞 / 评论数 / 转发 / 浏览为平台计数字段，取发布后约 24 小时的值；不含评论区内容层面的分析。';
  var SUMMARY_NOTE = '摘要与类型随采集批次更新（60 分钟一批），摘要 ≤60 字；图片内容第一期不覆盖，置信度低于阈值的类型标「待确认」。';
  function campOf(codes) {
    var own = false, peer = false;
    codes.forEach(function (c) { var m = MASTER[c]; if (!m) return; if (m.ownership === 'own') own = true; else peer = true; });
    return own && peer ? 'both' : (own ? 'own' : (peer ? 'competitor' : 'none'));
  }

  /* KOL 标签 → 类型倾向 */
  var TAG_BIAS = {
    '晒单': { showcase: 3.2, action: 1.8 },
    '技术分析': { market: 3, action: 0.4 },
    'ETF投教': { edu: 3, promo: 1.4 },
    '市场洞察': { market: 2.2, qa: 0.5 },
    '估值模型': { market: 1.8, edu: 1 },
    '打新': { event: 1.6, other: 0.6 }
  };
  var BASE_BIAS = { showcase: 0.7, action: 0.9, market: 1.6, promo: 0.9, edu: 0.8, event: 0.45, qa: 0.6, other: 0.4 };
  function typeWeights(tags) {
    var w = Object.assign({}, BASE_BIAS);
    String(tags || '').split(',').forEach(function (t) {
      var b = TAG_BIAS[t.trim()];
      if (b) Object.keys(b).forEach(function (k) { w[k] += b[k]; });
    });
    return w;
  }
  function pickType(w, h) {
    var keys = POST_TYPES.map(function (t) { return t.k; }).filter(function (k) { return w[k] > 0; });
    var tot = keys.reduce(function (a, k) { return a + w[k]; }, 0);
    var r = (h % 10000) / 10000 * tot;
    for (var i = 0; i < keys.length; i++) { r -= w[keys[i]]; if (r <= 0) return keys[i]; }
    return keys[keys.length - 1] || 'other';
  }

  /* 摘要 / 原文文案库：{tag} 主产品带代码，{peerTag} 第二提及产品，{name} / {peer} 仅名称。ev 为判定依据句下标，-1 表示无 */
  var KOL_BANK = {
    showcase: [
      { dir: 'open', s: '晒出 {name} 当日两笔成交截图，分批建仓，均价略低于昨日收盘，计划回踩前低再补。',
        f: ['{tag} 今天分两笔进了，均价比昨天收盘低一点，截图在下面。', '先建三成仓，剩下的等回到前低再补。', '这只费率在同类里算低的，长期拿着不心慌。', '以上只是个人操作记录，不构成建议。'], ev: 0 },
      { dir: 'hold', s: '贴出 {name} 持仓页面，浮盈约一成，表示继续持有不加仓，等下一次调仓公告再看。',
        f: ['{tag} 持仓截图更新一下，浮盈一成左右。', '这波先不加，等调仓公告出来再看成分变化。', '有人问要不要止盈，我的答案是拿着，长期逻辑没变。'], ev: 0 },
      { dir: 'open', s: '晒出同日买入 {name}、卖出 {peer} 的成交记录，理由是前者盘口更厚、价差更小。', peer: true,
        f: ['今天把 {peerTag} 清了，换成 {tag}，两张成交单都贴出来。', '换的理由很简单：盘口厚、价差小，几笔大单进出基本没冲击。', '仓位不变，只是换了标的。'], ev: 0 },
      { dir: 'reduce', s: '晒出 {name} 止盈三成仓位的成交单，剩余仓位继续持有，等下一个位置再看。',
        f: ['{tag} 今天止盈了三成，成交单贴在下面。', '剩下的仓位不动，等下一个位置再决定。', '个人操作记录，不构成建议。'], ev: 0 }
    ],
    action: [
      { dir: 'add', s: '宣布下周起分批加仓 {name}，理由是回调已接近前低支撑位，未附成交凭证。',
        f: ['{tag} 这个位置差不多了，下周开始分批加。', '理由是回调接近前低，量也缩下来了。', '具体几笔、什么价位到时候再说，先把想法记下。'], ev: 0 },
      { dir: 'close', s: '表示准备清仓 {name}，认为价格已到明显阻力位，先离场观望等下一轮。',
        f: ['{tag} 打算在这两天清掉，前面就是压力位，不想硬扛。', '清完先空着，等回到均线附近再看要不要接。', '不是不看好，是节奏问题。'], ev: 0 },
      { dir: 'open', s: '计划把 {peer} 换成 {name}，看中费率与盘口深度，表示尚未执行、等价差收敛。', peer: true,
        f: ['考虑把 {peerTag} 换到 {tag}，费率差一截，盘口也更厚。', '现在溢价还没收敛，等收敛了再动。', '有同样想法的朋友可以一起盯一下。'], ev: 0 },
      { dir: 'reduce', s: '表示已把 {name} 仓位减到一半，理由是财报周波动放大，等回落后再补回，未附成交凭证。',
        f: ['{tag} 今天减到一半，财报周不想满仓扛。', '不是看空，是控波动，回落了再补回来。', '剩下的仓位继续拿。'], ev: 0 }
    ],
    market: [
      { s: '复盘恒指昨日走势，认为 25400 附近仍有支撑，{name} 短线以震荡为主，不宜追高。',
        f: ['昨日低开后一度冲高，午后回落，收在支持位附近。', '25400 这个位置守了几次，短线还是震荡看待。', '{tag} 持有人不用急着加，等方向出来。'], ev: 1 },
      { s: '分析财报季的波动节奏，提示 {name} 持有人注意隔夜跳空与连续持有的耗损。',
        f: ['财报密集期，隔夜跳空会比平时频繁。', '{tag} 这类每日重置的产品，连着拿几天要算清楚耗损。', '我的做法是日内做方向，收盘前平掉。'], ev: 1 },
      { s: '对比 {name} 与 {peer} 的近期溢价折价走势，认为前者更贴近参考净值，入场成本更可控。', peer: true,
        f: ['把 {tag} 和 {peerTag} 最近两周的溢价折价拉了个图。', '前者基本贴着参考净值走，后者波动明显大一些。', '同样的方向判断，入场成本差不少。'], ev: 1 }
    ],
    promo: [
      { s: '介绍 {name} 的费率、跟踪指数与规模，认为适合作为长期核心仓位分批定投。',
        f: ['今天讲一只我一直拿着的：{tag}。', '费率在同类里偏低，跟踪的指数覆盖面广，规模也够。', '适合当核心仓位，每月定投，别看短期波动。'], ev: 1 },
      { s: '推荐 {name} 作为日内做方向的工具，强调倍数规则清楚、不涉及保证金与强平。',
        f: ['想做日内方向又不想碰融资的，看看 {tag}。', '倍数关系每天重置，规则写得清楚，当日盈亏自己能算。', '不用保证金，也就没有强平这回事。'], ev: 1 },
      { s: '对比 {name} 与 {peer} 的经常性开支与盘口深度，结论偏向前者，建议关注。', peer: true,
        f: ['很多人问 {tag} 和 {peerTag} 选哪只。', '经常性开支差了零点几个点，盘口深度也是前者好一些。', '如果只选一只，我会选前者。'], ev: 1 }
    ],
    edu: [
      { s: '科普杠反产品的每日重置机制，以 {name} 为例解释连续持有时收益偏离倍数的原因。',
        f: ['很多人以为两倍产品拿一周就是指数涨幅的两倍，不是的。', '每日重置的意思是倍数只对当天成立，连着拿几天会有复利偏差。', '拿 {tag} 举个例子，连续震荡三天后的结果和直觉差很多。'], ev: 1 },
      { s: '讲解 ETF 溢价折价的成因，用 {name} 近期盘口数据举例说明如何看参考净值。',
        f: ['先讲清楚什么是溢价、什么是折价，再讲怎么看参考净值。', '{tag} 最近盘口就是个好例子，价格和净值的差在盘中会变。', '买之前把这个差看一眼，能省不少冤枉钱。'], ev: 0 },
      { s: '用 {name} 与 {peer} 说明跟踪偏差与跟踪误差的区别，强调看长期而非单日。', peer: true,
        f: ['跟踪偏差和跟踪误差不是一回事，今天用两只产品说明。', '{tag} 和 {peerTag} 跟同一个指数，单日差异看不出什么，要看长期累计。', '知识帖，不涉及操作。'], ev: 0 }
    ],
    event: [
      { s: '转发平台关于 {name} 的交易返现活动，附报名链接与截止时间，提醒粉丝尽早参与。',
        f: ['平台在做 {tag} 的交易返现，链接放评论区。', '截止日期就在这周末，想参加的别拖。', '活动规则以官方页面为准。'], ev: 0 },
      { s: '发布抽奖帖，参与条件为关注并在 {name} 话题下评论，奖品为交易券。',
        f: ['抽奖来了：关注 + 在 {tag} 话题下留言就能参加。', '奖品是交易券，下周一开奖。', '中奖名单会在这条帖下面公布。'], ev: 0 },
      { s: '转发发行商直播预告，主题为 {name} 与 {peer} 的季度调仓解读，附预约入口。', peer: true,
        f: ['发行商周四晚上有直播，讲 {tag} 和 {peerTag} 的季度调仓。', '预约入口在这里，到时候会提醒大家。', '有问题可以先在评论区留下。'], ev: 0 }
    ],
    qa: [
      { s: '向粉丝征集对 {name} 后市的看法，发起看多 / 看空投票，表示下周复盘时公布结果。',
        f: ['问个问题：{tag} 你们现在是看多还是看空？', '评论区投个票，理由也写一下。', '下周复盘的时候把结果整理出来。'], ev: 0 },
      { s: '回应评论区关于 {name} 分派到账时间的提问，给出预计日期并说明查询方式。',
        f: ['好几位问 {tag} 的分派什么时候到账。', '按公告是月中前后，具体日期以券商通知为准。', '账户里的现金流水能看到，不用等短信。'], ev: 0 },
      { s: '提问粉丝当前持有 {name} 还是 {peer}，征集各自理由，准备整理成对比帖。', peer: true,
        f: ['今天不讲观点，想听大家的：你拿的是 {tag} 还是 {peerTag}？', '理由写在评论区，我整理成一篇对比帖。', '不管哪只，都别上杠杆硬扛。'], ev: 0 }
    ],
    other: [
      { s: '分享交易心态随笔，文末顺带提到 {name} 持仓，无观点与操作。',
        f: ['周末写点心态的东西，最近市场情绪起伏挺大。', '少看盘、多复盘，仓位管理比方向判断更重要。', '顺带一提，{tag} 还拿着，没动。'], ev: -1 },
      { s: '转发一条市场新闻并挂上 {name} 标签，没有附加评论。',
        f: ['转发。', '{tag}'], ev: -1 },
      { s: '发布周末读书笔记，仅以话题标签关联 {name} 与 {peer}，与产品无实质关联。', peer: true,
        f: ['读完一本讲指数基金历史的书，记几条笔记。', '被动投资的胜率来自成本和纪律，不是预测。', '标签：{tag} {peerTag}'], ev: -1 }
    ]
  };
  var OFFICIAL_BANK = {
    promo: [
      { s: '发布 {name} 产品要点：跟踪指数、费率与最新规模，附产品页链接。',
        f: ['{tag} 产品要点更新：跟踪指数、经常性开支与最新规模见图。', '详细资料请参阅产品页面与发售章程。', '投资涉及风险，价格可升可跌。'], ev: 0 },
      { s: '介绍 {name} 最新一期分派安排与除净日，提示派息不代表回报。',
        f: ['{tag} 本期分派安排与除净日已公布。', '分派金额不代表基金回报，部分分派可能来自资本。', '详情见公告。'], ev: 0 },
      { s: '并列介绍 {name} 与 {peer} 的适用场景，强调两者跟踪同一指数但结构不同。', peer: true,
        f: ['{tag} 与 {peerTag} 跟踪同一指数，结构与费率不同，适用场景见图。', '请根据自身风险承受能力选择。', '投资涉及风险。'], ev: 0 }
    ],
    edu: [
      { s: '发布投教内容，以 {name} 为例讲解杠反产品每日重置机制与持有期风险。',
        f: ['投教专栏：什么是每日重置？', '以 {tag} 为例，倍数只对单日有效，长期持有的回报可能偏离目标倍数。', '请在交易前了解产品特性。'], ev: 1 },
      { s: '科普货币市场 ETF 的七日年化收益率口径，说明 {name} 的披露方式。',
        f: ['七日年化收益率是什么？', '{tag} 每个交易日公布该指标，反映过去七日的年化水平，不代表未来。', '更多请参阅产品页面。'], ev: 1 },
      { s: '用 {name} 与 {peer} 讲解跟踪偏差的来源，说明为何同指数产品表现有差异。', peer: true,
        f: ['同一指数，为什么不同 ETF 的表现会有差异？', '{tag} 与 {peerTag} 的差异主要来自费率、复制方法与现金拖累。', '本内容仅供教育用途。'], ev: 1 }
    ],
    event: [
      { s: '发布 {name} 交易优惠活动，说明参与条件与截止日期。',
        f: ['{tag} 限时交易优惠开始。', '活动期间达成条件即可获得奖赏，截止日期见活动页。', '受条款及细则约束。'], ev: 0 },
      { s: '预告线上直播，主题为 {name} 季度调仓与市场展望，附预约入口。',
        f: ['本周四晚八点直播，主题：{tag} 季度调仓与市场展望。', '点击预约，开播前会有提醒。'], ev: 0 },
      { s: '发布 ETF 主题投资周活动，涉及 {name} 与 {peer} 等多只产品的交易奖赏。', peer: true,
        f: ['ETF 主题投资周开启，{tag}、{peerTag} 等产品参与活动。', '交易达标即可获得奖赏，详情见活动页。', '受条款及细则约束。'], ev: 0 }
    ],
    market: [
      { s: '发布周度市场回顾，点评 {name} 底层指数走势与资金流向。',
        f: ['周度回顾：本周指数震荡收高，南向资金持续净流入。', '{tag} 跟踪指数的成分表现分化，科技权重股贡献主要涨幅。', '以上不构成投资建议。'], ev: 1 },
      { s: '解读宏观数据对 {name} 相关资产的影响，提示短线波动加大。',
        f: ['数据公布后市场波动加大。', '{tag} 相关资产短线波动可能持续，请留意风险。'], ev: 1 },
      { s: '比较 {name} 与 {peer} 近一月的溢价折价与成交额变化，作为市场观察。', peer: true,
        f: ['市场观察：近一月 {tag} 与 {peerTag} 的溢价折价均有收敛，成交额回升。', '数据来源见图注，不构成投资建议。'], ev: 0 }
    ],
    qa: [
      { s: '整理投资者关于 {name} 的常见问题并逐条答复，涉及申赎与分派。',
        f: ['{tag} 常见问题汇总：申赎流程、分派时间、税务处理。', '如有其他问题，欢迎在评论区留言。'], ev: 0 },
      { s: '发起话题互动，征集用户对 {name} 后市的看法。',
        f: ['话题互动：你怎么看 {tag} 接下来的走势？', '留言区见，精选留言将在下周专栏展示。'], ev: 0 },
      { s: '发起投票：{name} 与 {peer} 你更关注哪一只，并说明原因。', peer: true,
        f: ['投票：{tag} 还是 {peerTag}，你更关注哪一只？', '留下你的理由，我们下周整理成专题。'], ev: 0 }
    ],
    other: [
      { s: '发布节日问候，附 {name} 话题标签。', f: ['祝各位投资者节日快乐。', '{tag}'], ev: -1 },
      { s: '转发平台公告，附 {name} 与 {peer} 话题标签。', peer: true, f: ['转发平台公告。', '{tag} {peerTag}'], ev: -1 }
    ]
  };
  var NO_TEXT = ['（图片帖：仅含图片或截图，无文字正文；第一期摘要不覆盖图片内容）'];

  function tagOf(code) { var m = MASTER[code]; return '$' + m.name + '（' + code + '.HK）$'; }
  function fill(str, codes) {
    var a = codes[0], b = codes[1] || codes[0];
    return str.split('{peerTag}').join(tagOf(b)).split('{tag}').join(tagOf(a))
      .split('{peer}').join(MASTER[b].name).split('{name}').join(MASTER[a].name).split('{code}').join(a + '.HK');
  }
  function compose(bank, type, codes, h) {
    var all = bank[type] || bank.other;
    var list = all.filter(function (v) { return codes.length > 1 ? !!v.peer : !v.peer; });
    if (!list.length) list = all;
    var v = list[h % list.length];
    return { summary: fill(v.s, codes), fullText: v.f.map(function (x) { return fill(x, codes); }), evidenceIdx: v.ev, direction: v.dir || null };
  }
  function mentionRows(codes) {
    return codes.map(function (c) { var x = MASTER[c]; return { code: c, name: x.name, issuer: x.issuer, ownership: x.ownership }; });
  }
  var ownPoolCache = null, peerPoolCache = null;
  function ownPool() {
    if (!ownPoolCache) ownPoolCache = PRODUCTS.slice().sort(function (a, b) { return b.heat - a.heat; }).slice(0, 16).map(function (p) { return p.code; });
    return ownPoolCache;
  }
  function peerPool() {
    if (!peerPoolCache) peerPoolCache = ORDER.filter(function (c) { return MASTER[c].ownership === 'peer'; })
      .sort(function (a, b) { return MASTER[b].w - MASTER[a].w || (a < b ? -1 : 1); }).slice(0, 14);
    return peerPoolCache;
  }
  /* 第二个提及产品：自家帖按概率提到对位竞品，竞品帖按概率提到对位自家产品 */
  function withPeer(primary, roll, h) {
    var m = MASTER[primary], codes = [primary];
    if (m.ownership === 'own') {
      var comps = CMAP.filter(function (c) { return c[3] === primary; });
      if (roll && comps.length) codes.push(comps[h % comps.length][1]);
    } else if (roll && m.ownCode && MASTER[m.ownCode]) codes.push(m.ownCode);
    return codes;
  }
  /* 一篇帖子的 AI 标注块：类型 / 置信度 / 摘要 / 原文 / 判定依据 / 提及产品 / 阵营 */
  function annotate(bank, type, codes, key, noText) {
    var t = noText ? 'other' : type;
    var low = (hash(key + 'lo') % 100) < 15;
    var conf = noText ? 0.4 : Math.round(rnd(key + 'cf', low ? 0.52 : 0.72, low ? 0.69 : 0.98) * 100) / 100;
    var txt = noText ? { summary: '', fullText: NO_TEXT, evidenceIdx: -1 } : compose(bank, t, codes, hash(key + 'tx'));
    /* 操作方向：只在操作类（晒单／操作宣言）帖子出现；置信度低于阈值的操作类帖子方向标「待确认」 */
    var isOp = !noText && TYPE_BY_KEY[t].group === 'op';
    var dirPending = isOp && low, dirK = isOp && !low ? (txt.direction || null) : null;
    return {
      postType: t, typeLabel: TYPE_BY_KEY[t].label, confidence: conf,
      direction: dirK, directionLabel: dirK ? DIR_BY_KEY[dirK].label : (dirPending ? '待确认' : ''),
      directionPending: dirPending, hasDir: !!(dirK || dirPending), dir: (dirK || dirPending) ? dirStyle(dirK, dirPending) : null,
      hasSummary: !noText, summary: txt.summary, fullText: txt.fullText, evidenceIdx: txt.evidenceIdx,
      typeEvidence: txt.evidenceIdx >= 0 ? txt.fullText[txt.evidenceIdx] : '',
      mentioned: mentionRows(codes), camp: campOf(codes), campPrimary: campOf([codes[0]])
    };
  }

  function kolImpact(rangeKey) {
    var k = rangeKey || DEFAULT_KEY;
    return cached('kolimpact|' + k, function () { return kolImpactCalc(buildRange(k)); });
  }
  function kolImpactCalc(range) {
    var DAYS = range.days, FROM = range.from;
    var actives = KOLS.filter(function (k) { return k[2] === 1; });
    var own = ownPool(), peer = peerPool();
    var slots = [9, 10, 11, 12, 13, 14, 15, 16, 20, 21, 22];
    var posts = [];
    actives.forEach(function (k) {
      var w = typeWeights(k[1]);
      for (var dd = 0; dd < DAYS; dd++) {
        var key = k[0] + '#' + dd, hv = hash(key + '|' + range.key);
        if (hv % 100 >= 12) continue;
        var hr = slots[Math.floor(hv / 8) % slots.length];
        var day = addDays(FROM, dd);
        var primary = ((hv >>> 4) % 100) < 62 ? own[(hv >>> 6) % own.length] : peer[(hv >>> 6) % peer.length];
        var m = MASTER[primary];
        var codes = withPeer(primary, ((hv >>> 10) % 100) < (m.ownership === 'own' ? 38 : 48), hv >>> 14);
        var a = annotate(KOL_BANK, pickType(w, hash(key + 'ty')), codes, key, ((hv >>> 16) % 100) < 5);
        var likes = 240 + (hv % 5400), comments = 20 + ((hv >>> 3) % 640), shares = 4 + ((hv >>> 5) % 210);
        posts.push(Object.assign({
          id: key, kol: k[0], tags: k[1],
          t: dd * 24 + hr, day: day, hour: hr, dateText: md(day),
          time: md(day) + ' ' + String(hr).padStart(2, '0') + ':' + String(hv % 60).padStart(2, '0'),
          code: primary, name: m.name, sector: m.sector, ownership: m.ownership, issuer: m.issuer,
          likes: likes, comments: comments, shares: shares, views: Math.round(likes * rnd(key + 'vw', 9, 22)),
          engagement: likes + comments + shares,
          url: 'https://www.futunn.com/post/' + (7000000 + (hv % 2000000))
        }, a));
      }
    });
    posts.sort(function (a, b) { return a.t - b.t || (a.id < b.id ? -1 : 1); });
    var byKol = {};
    posts.forEach(function (p) { (byKol[p.kol] = byKol[p.kol] || []).push(p); });
    var leaders = Object.keys(byKol).map(function (k) { return kolProfile(k, byKol[k]); })
      .sort(function (a, b) { return b.n - a.n || b.engagement - a.engagement || (a.kol < b.kol ? -1 : 1); });
    return {
      posts: posts, leaders: leaders, kolActive: actives.length,
      range: { from: FROM, to: range.to, text: range.text, days: DAYS },
      updated: NOW_DATE + ' ' + NOW_TIME
    };
  }
  /* 一位 KOL 的区间画像：篇数、自家 / 竞品 / 双方篇数、互动合计、类型分布与主要类型。campFn 可换判定口径 */
  function kolProfile(name, ps, campFn) {
    var cf = campFn || function (p) { return p.camp; };
    var tc = {}; POST_TYPES.forEach(function (t) { tc[t.k] = 0; });
    var own = 0, peer = 0, both = 0, eng = 0, cm = 0;
    ps.forEach(function (p) {
      tc[p.postType]++; eng += p.engagement; cm += p.comments;
      var c = cf(p);
      if (c === 'own') own++; else if (c === 'competitor') peer++; else if (c === 'both') both++;
    });
    var order = POST_TYPES.map(function (t) { return t.k; }).sort(function (a, b) { return tc[b] - tc[a]; });
    var n = ps.length;
    var top = ps.slice().sort(function (a, b) { return b.engagement - a.engagement; })[0] || null;
    var second = tc[order[1]] > 0 && tc[order[1]] >= n * 0.2 ? ' · 兼' + TYPE_BY_KEY[order[1]].label : '';
    return {
      kol: name, n: n, own: own, peer: peer, both: both, ownAny: own + both, peerAny: peer + both,
      engagement: eng, comments: cm, typeCounts: tc, typeOrder: order,
      topType: order[0], topTypeLabel: TYPE_BY_KEY[order[0]].label,
      styleTag: n ? TYPE_BY_KEY[order[0]].label + '为主' + second : '',
      top: top, posts: ps
    };
  }

  /* 官号帖子级内容流（演示数据）：发行商官号偏向发自身产品（即我们的竞品），平台运营账号自家 / 竞品混合 */
  var OFFICIAL_ISSUER = { '恒生投资': '恒生投资', '华夏': 'AMC 华夏', 'Global X': 'Global X', '平安': 'Ping An', '博时': 'Bosera 博时' };
  var OFFICIAL_TYPES = {
    issuer: { promo: 2.4, edu: 1.6, event: 1.4, market: 1.2, qa: 0.5, other: 0.3 },
    platform: { event: 2.2, edu: 1.8, market: 1.6, qa: 1.4, promo: 0.6, other: 0.4 }
  };
  function officialPosts(rangeKey) {
    var key = rangeKey || DEFAULT_KEY;
    return cached('offposts|' + key, function () {
      var range = buildRange(key), DAYS = range.days;
      var own = ownPool(), peer = peerPool(), out = [];
      OFFICIAL.forEach(function (o) {
        var short = o[0], isIssuer = o[4] > 0, iss = OFFICIAL_ISSUER[short];
        var pool = isIssuer
          ? (iss ? CMAP.filter(function (c) { return c[0] === iss; }).map(function (c) { return c[1]; }) : pickN(peer, short + 'pool', 3))
          : null;
        var n = Math.round(o[2] * DAYS / 7 * rnd(short + key + 'n', 0.8, 1.2));
        var w = OFFICIAL_TYPES[isIssuer ? 'issuer' : 'platform'];
        for (var i = 0; i < n; i++) {
          var k2 = short + '@' + i + '|' + key, h = hash(k2);
          var dOff = h % DAYS, hr = 8 + ((h >>> 3) % 13), day = addDays(range.from, dOff);
          var primary, roll;
          if (isIssuer) { primary = pool[(h >>> 5) % pool.length]; roll = ((h >>> 9) % 100) < 25; }
          else { primary = ((h >>> 5) % 100) < 55 ? own[(h >>> 7) % own.length] : peer[(h >>> 7) % peer.length]; roll = ((h >>> 11) % 100) < 30; }
          var m = MASTER[primary];
          var codes = withPeer(primary, roll, h >>> 13);
          var a = annotate(OFFICIAL_BANK, pickType(w, hash(k2 + 'ty')), codes, k2, ((h >>> 15) % 100) < 4);
          var likes = 60 + (h % 1800), comments = 3 + ((h >>> 2) % 140), shares = 1 + ((h >>> 4) % 90);
          out.push(Object.assign({
            id: k2, account: short, accountFull: o[1], accountType: isIssuer ? '发行商官号' : '平台运营', isIssuer: isIssuer,
            t: dOff * 24 + hr, day: day, dateText: md(day),
            time: md(day) + ' ' + String(hr).padStart(2, '0') + ':' + String((h >>> 6) % 60).padStart(2, '0'),
            code: primary, name: m.name, sector: m.sector, ownership: m.ownership, issuer: m.issuer,
            likes: likes, comments: comments, shares: shares, engagement: likes + comments + shares,
            url: 'https://www.futunn.com/post/' + (9000000 + (h % 900000))
          }, a));
        }
      });
      return out.sort(function (a, b) { return b.t - a.t || (a.id < b.id ? -1 : 1); });
    });
  }

  /* ────────────── 12.5 KOL 对其他产品的观点与操作（竞品 ＋ 南方东英其他产品，演示数据）
     口径：从该账号在区间内的评论与转发中识别出的产品观点，一条内容一行；
     「观点」为 AI 一句话概括，原文可点「原帖」查看；「操作」为识别出的持仓动作。 */
  var ACTIONS = [
    { k: '加仓', tone: 'pos' }, { k: '建仓', tone: 'pos' },
    { k: '减仓', tone: 'neg' }, { k: '清仓', tone: 'neg' },
    { k: '转投其他产品', tone: 'neg' }, { k: '持有不动', tone: 'neu' },
    { k: '观望', tone: 'neu' }, { k: '未提及操作', tone: 'neu' }
  ];
  /* 「操作」并入统一类型枚举：有买卖动作的归操作类（操作宣告 / 晒单），持有 / 观望归行情解读，未提操作按内容归观点类 */
  var OP_TYPE = {
    '加仓': ['action', 'showcase', 'action'], '建仓': ['action', 'showcase', 'action'],
    '减仓': ['action', 'showcase', 'action'], '清仓': ['action', 'showcase'],
    '转投其他产品': ['action', 'action'], '持有不动': ['market', 'qa'], '观望': ['market', 'market'],
    '未提及操作': ['market', 'edu', 'promo']
  };
  var OP_LINES = {
    '加仓': ['认为回调已经到位，在支持位附近继续买入', '费率与流动性都满意，趁震荡加了仓', '看好底层指数的估值修复，加仓摊低成本'],
    '建仓': ['第一次买入，理由是一篮子替代个股更省心', '把它当作新的核心仓位开始定投', '认为当前点位适合分批建仓'],
    '减仓': ['觉得涨幅已经反映预期，先减一部分', '担心波动放大，把仓位降到一半', '分派水平不及预期，减少了配置'],
    '清仓': ['认为已经到压力位，全部卖出等下一轮', '溢价拉得太高，直接清掉', '跟踪偏差不满意，清仓换标的'],
    '转投其他产品': ['换到费率更低的同类产品', '觉得同类的盘口更好，转过去了', '改用杠杆产品做同一方向'],
    '持有不动': ['维持原有仓位，等更明确的信号', '认为长期逻辑没变，不做调整', '持仓不动，只把分派再投入'],
    '观望': ['暂时不参与，等回到均线附近再看', '认为方向不明，先空仓观察', '等下一次财报或调仓公告再决定'],
    '未提及操作': ['只做点位与阻力位分析，没有提到操作', '讨论了跟踪指数的构成，未提持仓', '解释了每日重置的机制，未给出操作']
  };
  var OP_RAW = [
    '昨日低开152点，开盘后一度冲高持续涨到中午，最高25519，正正又在25520的阻力位置遇到压力回落，再次下跌接近200点，最后收盘跌幅微微收窄，收25396，又是收在25400的支持/阻力位置',
    '早前文章分享提到近日出现上落市待出方向，上周五曾经跌至接近二万风关口，本周一高开高走，上升超过三百点，上回文章提到假如是超跌反弹的话，一般升幅都会在300点之内',
    '昨日高开187点，开盘后持续上升，最高25578，直到下午近收盘时段回吐，升幅收窄，全日升336点，报25453，上回文章提到近日这波要回一回气，问题只是究竟回到哪个位置',
    '昨日高开61点，开盘后先一度冲高，最高26060，刚刚好在26050附近的阻力位置回落，很久之前，还记得这个位置的阻力参考也经常用，压力比较大',
    '之前文章提到上周一至三，连续三个交易日抗跌，之后升机会比较大，而上周四周五的确连续两天上升了去，而上周五收报26009，当时在走势的整个形态上偏强，快要攻上更高位置'
  ];

  function kolOpinions(kolName, rangeKey) {
    var key = rangeKey || DEFAULT_KEY;
    return cached('kolop|' + kolName + '|' + key, function () {
      var M = kolImpact(key);
      var ownCodes = {};
      M.posts.forEach(function (p) { if (p.kol === kolName) ownCodes[p.code] = 1; });
      /* 范围：竞品 ＋ 南方东英其他产品；已在「发帖记录」出现的产品不重复列出 */
      var candidates = ORDER.filter(function (c) { return !ownCodes[c]; });
      var n = 7 + (hash(kolName + '|op|' + key) % 8);
      var rows = [];
      for (var i = 0; i < n; i++) {
        var h = hash(kolName + '#op#' + i + '|' + key) >>> 0;
        var code = candidates[h % candidates.length];
        if (rows.filter(function (r) { return r.code === code; }).length) continue;
        var m = MASTER[code], o = observe(code, key);
        var act = ACTIONS[h % ACTIONS.length];
        var opCands = OP_TYPE[act.k] || ['market'], opType = opCands[(h >>> 7) % opCands.length];
        var lines = OP_LINES[act.k];
        var dayOff = h % Math.max(1, buildRange(key).days);
        var day = addDays(buildRange(key).from, dayOff);
        rows.push({
          code: code, name: m.name, issuer: m.issuer,
          own: m.ownership === 'own',
          sector: m.sector, sectorName: m.sectorName,
          summary: lines[(h >>> 3) % lines.length],
          excerpt: '$' + m.name + '（' + code + '.HK）$' + OP_RAW[(h >>> 5) % OP_RAW.length],
          action: act.k, actionTone: act.tone, direction: act.k === '未提及操作' ? '' : act.k,
          postType: opType, typeLabel: TYPE_BY_KEY[opType].label,
          confidence: Math.round(rnd(kolName + '#op#' + i + 'cf', (h % 100) < 15 ? 0.55 : 0.72, (h % 100) < 15 ? 0.69 : 0.97) * 100) / 100,
          dateText: day.replace(/-0?/g, '/').replace(/^\//, ''),
          timeText: String(6 + (h % 12)).padStart(2, '0') + ':' + String(h % 60).padStart(2, '0')
            + ':' + String((h >>> 4) % 60).padStart(2, '0'),
          engagement: 18 + (h % 160),
          url: 'https://www.futunn.com/post/' + (5000000 + (h % 3000000)),
          net: o.attitude.sampleSufficient
            ? (o.attitude.positive - o.attitude.negative) / Math.max(1, o.attitude.positive + o.attitude.negative) * 100
            : null
        });
      }
      return rows.sort(function (a, b) { return b.engagement - a.engagement; });
    });
  }

  /* ────────────── 13. 本轮增量（客户新增需求 2026-09-04） ──────────────
     13.1 重点舆情（需合规关注）：只识别风险信号并给出原文与命中依据，不判定言论真伪或产品是否违规；
     13.2 产品相关 KOL：在该产品相关帖子评论区中实际提及该产品的已识别 KOL（范围：合作 KOL 名单）；
     13.3 价格 K 线：演示行情，60 分钟 OHLC 与舆情时间桶对齐，非交易时段与休市日不补造。 */
  var RISK_TAG = {
    regulatory_complaint: '监管举报',
    serious_allegation: '严重指控',
    unverified_claim: '疑似未经证实指控',
    mobilization: '煽动扩散',
    compliance_concern: '合规质疑'
  };
  var RISK_BANK = [
    { tags: ['regulatory_complaint'], src: '评论',
      text: '交易记录我已经整理好了，下周直接去证监会投诉{name}，净值和指数差这么多没人出来解释。',
      why: '出现明确的监管投诉意图（“去证监会投诉”），且直接指向当前产品。' },
    { tags: ['serious_allegation', 'unverified_claim'], src: '评论',
      text: '做市商和发行人之间肯定有利益输送，不然{name}的价差不会天天这样，大家自己想。',
      why: '对利益输送作出严重指控，未附任何依据，属疑似未经证实的重大指控。' },
    { tags: ['mobilization', 'regulatory_complaint'], src: '帖子',
      text: '买了{name}受影响的都来留名，凑够 50 个人一起去 SFC 和消委会集体投诉。',
      why: '号召集体投诉并点名监管机构，同时含煽动他人参与和扩散的语义。' },
    { tags: ['compliance_concern'], src: '评论',
      text: '{name}宣传页写的是“稳健收益”，风险提示要点进三层才看到，这种销售表述合规吗？',
      why: '对销售宣传表述与风险披露的呈现方式提出合规质疑。' },
    { tags: ['serious_allegation'], src: '评论',
      text: '{name}溢价拉到这个位置，明显是有人在操纵盘口，散户进去就是接盘。',
      why: '出现“操纵盘口”的严重指控，并与当前产品的交易表现直接关联。' },
    { tags: ['unverified_claim', 'mobilization'], src: '帖子',
      text: '听说{name}下季度要清盘，还没卖的赶紧走，也转发提醒身边人别再买。',
      why: '传播未经证实的清盘说法并号召转发扩散，可能造成声誉影响。' },
    { tags: ['compliance_concern'], src: '评论', only: '杠反',
      text: '开户问卷是保守型，却能直接买到{name}这种两倍杠杆产品，适当性评估是怎么过的？',
      why: '对投资者适当性安排提出质疑，涉及销售环节的合规要求。' },
    { tags: ['regulatory_complaint', 'compliance_concern'], src: '评论',
      text: '{name}的派息公告和实际到账金额对不上，已经向证监会提交了书面投诉，等回复。',
      why: '陈述已提交监管投诉，并涉及信息披露与实际执行的一致性问题。' },
    { tags: ['serious_allegation', 'unverified_claim'], src: '评论',
      text: '{name}所谓的跟踪偏差范围就是虚假宣传，招股书里写的根本做不到。',
      why: '以“虚假宣传”定性招股书披露内容，属于未附依据的严重指控。' },
    { tags: ['mobilization'], src: '帖子',
      text: '建议大家一起去各个平台给{name}刷一星，让他们知道割韭菜是有代价的。',
      why: '煽动集体行动并扩散负面评价，且明确指向当前产品。' }
  ];
  var RISK_UNAVAILABLE = { '3153': 1 };                       // 演示：风险识别数据不可用
  var RISK_FORCE = { '3033': 3, '7226': 2, '3454': 1, '3096': 1 };   // 演示：稳定有命中的产品
  function riskLabels(tags) { return tags.map(function (t) { return RISK_TAG[t] || t; }); }
  function complianceFor(code, rangeKey) {
    return cached('cr|' + code + '|' + rangeKey, function () {
      var m = MASTER[code]; if (!m) return { status: 'empty', list: [] };
      if (m.ownership !== 'own') return { status: 'na', list: [] };   // 同业产品不纳入需合规关注识别，字段不适用
      if (RISK_UNAVAILABLE[code]) return { status: 'unavailable', list: [] };
      var o = observe(code, rangeKey), range = buildRange(rangeKey);
      var neg = o.attitude.negative;
      var n = RISK_FORCE[code] != null ? RISK_FORCE[code]
        : ((neg >= 6 && hash(code + rangeKey + 'cr') % 100 < 26) ? 1 + hash(code + rangeKey + 'crn') % 3 : 0);
      if (!n) return { status: 'empty', list: [] };
      var bank = RISK_BANK.filter(function (t) { return !t.only || t.only === m.struct; });
      var picks = pickN(bank, code + rangeKey + 'crp', Math.min(n, bank.length));
      var list = picks.map(function (t, i) {
        var key = code + rangeKey + 'cr' + i, h = hash(key);
        var isKol = h % 6 === 0;
        return {
          id: code + '-cr-' + i, productCodes: [code],
          publishedAt: addDays(range.from, h % range.days) + ' ' + String(8 + (h % 14)).padStart(2, '0') + ':' + String((h >>> 3) % 60).padStart(2, '0'),
          authorName: pick(isKol ? KOL_NAMES : RETAIL_NAMES, key + 'a'),
          authorType: isKol ? '合作 KOL' : '普通散户', isKnownKol: isKol,
          excerpt: t.text.split('{name}').join(m.name),
          riskTags: t.tags, riskLabels: riskLabels(t.tags),
          detectionRationale: t.why,
          reviewState: 'ai_pending', reviewLabel: 'AI 识别 · 待人工确认',
          sourceKind: t.src,
          sourceUrl: 'https://www.futunn.com/post/' + (8000000 + (h % 1500000)),
          comments: 1 + (h % 40), interactions: 6 + (h % 520)
        };
      }).sort(function (a, b) { return a.publishedAt < b.publishedAt ? 1 : -1; });
      return { status: 'ok', list: list };
    });
  }

  var KOL_MAP_UNAVAILABLE = { '2848': 1, '3007': 1 };        // 演示：KOL 身份映射不可用
  var KOL_LINES = {
    positive: [
      '{tag} 这个位置分批接回来，费率在同类里算低的，长期拿着问题不大。',
      '{tag} 盘口比想象中厚，今天几笔大单进出价差基本没动。',
      '{tag} 定投第 20 期，回撤比指数小一点，继续按计划走。',
      '{tag} 港股通直接能买，省了换汇那步，内地朋友可以关注。'
    ],
    negative: [
      '{tag} 溢价又拉开了，这时候进去不划算，等收敛再说。',
      '{tag} 跟踪偏差这周偏大，希望官方出个说明再决定加不加。',
      '{tag} 分派金额比上期少了，收益来源要拆开看清楚。',
      '{tag} 连续持有三天以上的耗损不小，只适合日内做方向。'
    ],
    neutral: [
      '{tag} 先看成交量，量不放大不加仓。',
      '{tag} 把同类几只的规模、费率和价差列了个表，各有取舍。',
      '{tag} 财报周波动会放大，仓位先减到一半观察。',
      '{tag} 关注下周的调仓公告，成分变化再决定。'
    ]
  };
  var ATT_LABEL = { positive: '积极', negative: '消极', neutral: '中性' };
  function kolMentionsFor(code, rangeKey) {
    return cached('km|' + code + '|' + rangeKey, function () {
      var scope = '合作 KOL 名单';
      var m = MASTER[code]; if (!m) return { status: 'empty', scope: scope, list: [] };
      if (KOL_MAP_UNAVAILABLE[code]) return { status: 'unavailable', scope: scope, list: [] };
      var o = observe(code, rangeKey), range = buildRange(rangeKey);
      var partners = KOLS.filter(function (k) { return k[2] === 1; });
      var n = o.comments < 30 ? 0 : Math.min(9, Math.floor(o.comments / 45) + (hash(code + rangeKey + 'kn') % 3));
      if (!n) return { status: 'empty', scope: scope, list: [] };
      var chosen = pickN(partners, code + rangeKey + 'kp', n);
      var tag = '$' + m.name + '（' + code + '.HK）$';
      var TONES = ['positive', 'neutral', 'negative'];
      var list = chosen.map(function (k, i) {
        var nm = k[0], key = code + rangeKey + nm, h = hash(key);
        var cnt = Math.max(1, Math.round((n + 1 - i) * rnd(key + 'c', 0.55, 1.35)));
        var roll = h % 10;
        var att = cnt < 3 ? null : (roll < 5 ? 'positive' : (roll < 8 ? 'neutral' : 'negative'));
        var lean = att || TONES[h % 3];
        var ev = [];
        for (var j = 0; j < cnt; j++) {
          var ek = key + '#' + j, eh = hash(ek);
          var tone = (eh % 10 < 7) ? lean : TONES[eh % 3];
          ev.push({
            id: code + '-km-' + i + '-' + j, productCodes: [code],
            publishedAt: addDays(range.from, eh % range.days) + ' ' + String(7 + (eh % 15)).padStart(2, '0') + ':' + String((eh >>> 3) % 60).padStart(2, '0'),
            authorName: nm, authorType: '合作 KOL', isKnownKol: true, kolType: 'partner',
            excerpt: pick(KOL_LINES[tone], ek + 'x').split('{tag}').join(tag),
            attitude: tone, attitudeLabel: ATT_LABEL[tone],
            comments: 3 + (eh % 55), interactions: 20 + (eh % 900),
            sourceKind: '评论',
            sourceUrl: 'https://www.futunn.com/post/' + (7500000 + (eh % 1200000))
          });
        }
        ev.sort(function (a, b) { return a.publishedAt < b.publishedAt ? 1 : -1; });
        return {
          productCode: code, kolAccountId: 'kol-' + (hash(nm) % 100000), kolName: nm, kolTags: k[1],
          kolType: 'partner', kolTypeLabel: '合作 KOL',
          mentionCommentCount: cnt, lastMentionedAt: ev[0].publishedAt,
          dominantAttitude: att, dominantLabel: att ? ATT_LABEL[att] : null,
          representativeExcerpt: ev[0].excerpt,
          evidenceCount: cnt, evidence: ev
        };
      }).sort(function (a, b) { return b.mentionCommentCount - a.mentionCommentCount || (a.kolName < b.kolName ? -1 : 1); });
      return { status: 'ok', scope: scope, list: list };
    });
  }

  var PRICE_UNAVAILABLE = { '3406': 1, '3087': 1 };          // 演示：价格数据不可用
  var TRADE_HOURS = [9, 10, 11, 13, 14, 15];                   // 港交所 09:30–12:00、13:00–16:00，按 60 分钟桶取整
  var PX_DAYS = 45;
  var pxCache = {};
  function currencyOf(m) { return /美元/.test(m.name) ? 'USD' : (/人民幣|人民币/.test(m.name) ? 'CNH' : 'HKD'); }
  function roundPx(v) { var d = v < 10 ? 3 : 2, k = Math.pow(10, d); return Math.round(v * k) / k; }
  function pxSeries(code) {
    if (pxCache[code]) return pxCache[code];
    var m = MASTER[code];
    var lev = m.struct === '杠反' ? (/2x/.test(m.name) ? 2 : 1) : (m.struct === '货币' ? 0.02 : 1);
    var p = m.struct === '货币' ? rnd(code + 'p0', 98, 1120) : (m.struct === '杠反' ? 3 + rnd(code + 'p0', 0, 12) : 4 + rnd(code + 'p0', 0, 90));
    var baseVol = 0.0028 * lev;                                   // 每小时基础波动
    var hourW = { 9: 1.45, 10: 1.0, 11: 0.8, 13: 0.75, 14: 0.9, 15: 1.3 };   // 开盘、收盘前波动更大
    var start = addDays(ANCHOR, -(PX_DAYS - 1)), hours = {}, days = {};
    var seg = 0, segLeft = 0, drift = 0, vol = baseVol;
    for (var di = 0; di < PX_DAYS; di++) {
      var day = addDays(start, di), dw = parse(day).getUTCDay();
      if (dw === 0 || dw === 6) continue;
      /* 走势段：每段 3–8 个交易日，上升／下跌／震荡交替，各段波动率不同 */
      if (segLeft <= 0) {
        seg++;
        segLeft = 3 + (hash(code + 'seg' + seg) % 6);
        var kind = hash(code + 'kind' + seg) % 4;
        drift = kind === 0 ? 0.0075 * lev : (kind === 1 ? -0.0075 * lev : 0);   // 趋势段的日均漂移
        vol = baseVol * rnd(code + 'vol' + seg, 0.7, 1.6);
      }
      segLeft--;
      var gap = (rnd(code + day + 'g1', -1, 1) + rnd(code + day + 'g2', -1, 1)) * vol * 1.2 + drift * 0.25;
      var o = p * (1 + gap);
      var dO = o, dH = -Infinity, dL = Infinity, dC = o;
      /* 当日收益 = 走势段漂移 + 当日随机新闻，再按日内权重拆到各小时；小时噪声只做局部扰动，
         这样日 K 多数有明确实体，小时 K 保留合理影线，不会退化成一串十字星或整根光头光脚 */
      var dayRet = drift + (rnd(code + day + 'd1', -1, 1) + rnd(code + day + 'd2', -1, 1)) * vol * 2.2;
      var wSum = 0; TRADE_HOURS.forEach(function (h) { wSum += hourW[h]; });
      TRADE_HOURS.forEach(function (h) {
        var key = code + day + 'h' + h, v = vol * hourW[h];
        var shock = (hash(key + 'sk') % 23 === 0) ? rnd(key + 'sv', -1, 1) * v * 2.5 : 0;   // 偶发大阳／大阴线
        var r = dayRet * hourW[h] / wSum + (rnd(key + 'r1', -1, 1) + rnd(key + 'r2', -1, 1)) * v * 0.55 + shock;
        var c = o * (1 + r);
        var bar = {
          open: roundPx(o), close: roundPx(c),
          high: roundPx(Math.max(o, c) * (1 + rnd(key + 'hi', 0.05, 1) * v * 0.8)),
          low: roundPx(Math.min(o, c) * (1 - rnd(key + 'lo', 0.05, 1) * v * 0.8))
        };
        hours[day + '|' + h] = bar;
        if (bar.high > dH) dH = bar.high;
        if (bar.low < dL) dL = bar.low;
        dC = bar.close; o = c;
      });
      days[day] = { open: roundPx(dO), high: dH, low: dL, close: dC };
      p = o;
    }
    pxCache[code] = { hours: hours, days: days, currency: currencyOf(m) };
    return pxCache[code];
  }
  /* 与 range.buckets 逐桶对齐；小时桶只在交易时段有 K 线，自然日桶跳过休市日，自然周桶由日 K 合成 */
  function candlesFor(code, rangeKey) {
    return cached('px|' + code + '|' + rangeKey, function () {
      var range = buildRange(rangeKey), m = MASTER[code];
      var granularity = range.gran === 'hour' ? '60m' : '1d';
      var granLabel = range.gran === 'hour' ? '60 分钟 K' : (range.gran === 'day' ? '日 K' : '周 K（由日 K 合成）');
      var empty = function (b, why) { return { bucket: b.tip, open: null, high: null, low: null, close: null, note: why }; };
      if (!m || PRICE_UNAVAILABLE[code]) {
        return { status: 'unavailable', granularity: granularity, granLabel: granLabel, currency: null,
          list: range.buckets.map(function (b) { return empty(b, '价格数据暂不可用'); }), missing: range.buckets.length };
      }
      var S = pxSeries(code), missing = 0;
      var list = range.buckets.map(function (b) {
        var bar = null, why = '';
        if (range.gran === 'hour') {
          var dw = parse(b.day).getUTCDay();
          bar = S.hours[b.day + '|' + b.hour] || null;
          if (!bar) why = (dw === 0 || dw === 6) ? '休市日' : (b.hour === 12 ? '午间休市' : '非交易时段');
        } else if (range.gran === 'day') {
          bar = S.days[b.day] || null;
          if (!bar) why = '休市日';
        } else {
          var o = null, h = -Infinity, l = Infinity, c = null;
          for (var k = 0; k < b.span; k++) {
            var d = S.days[addDays(b.day, k)];
            if (!d) continue;
            if (o == null) o = d.open;
            if (d.high > h) h = d.high;
            if (d.low < l) l = d.low;
            c = d.close;
          }
          if (o != null) bar = { open: o, high: h, low: l, close: c }; else why = '整周休市';
        }
        if (!bar) { missing++; return empty(b, why); }
        return { bucket: b.tip, open: bar.open, high: bar.high, low: bar.low, close: bar.close, note: '' };
      });
      return { status: 'ok', granularity: granularity, granLabel: granLabel, currency: S.currency, list: list, missing: missing };
    });
  }

  /* ────────────── 14. 第三轮增量（客户修改需求 2026-09-08） ──────────────
     14.1 官号 × ETF 提及：只对照 ETF 产品池（61 自家 + 59 竞品）匹配，个股代码／名称不在词表内、不计入；
          次数按出现次数（一帖内出现 3 次计 3，挂载标的至少计 1），另给涉及帖数；
     14.2 帖子类型双标签：内容形式（postType）× 操作方向（direction），见 DIRECTIONS 与 annotate()；
     14.3 热议总结：AI 一句话归纳区间内该 ETF 的主流具体观点（演示文案），样本不足不输出；
     14.4 热度变化与阶段观点：热度序列（口径与全站一致）+ 分时段／分阶段主流观点，随产品与区间同步。 */
  function shortName(name) {
    var s = String(name || '').replace(/^南方[東东]英/, '').replace(/(指數|指数)?ETF$/, '');
    if (!s) s = String(name || '');
    return s.length > 10 ? s.slice(0, 10) + '…' : s;
  }
  function textOccurrences(post, code) {
    var needle = '（' + code + '.HK）', n = 0;
    (post.fullText || []).forEach(function (line) { n += line.split(needle).length - 1; });
    return n;
  }
  var ETF_MENTION_RULE = '提及 ETF 口径：帖子挂载标的 ∪ 正文出现的产品代码／名称，对照 ETF 产品池（61 自家 + 59 竞品）匹配；个股代码、个股名称不在词表内，不计入。次数按出现次数，一帖内出现 3 次计 3，挂载标的至少计 1。';
  function etfMentionsFor(account, rangeKey) {
    var key = rangeKey || DEFAULT_KEY;
    return cached('etfm|' + account + '|' + key, function () {
      var map = {}, postSet = {};
      officialPosts(key).forEach(function (p) {
        if (p.account !== account) return;
        p.mentioned.forEach(function (x) {
          var e = map[x.code] || (map[x.code] = { code: x.code, name: x.name, short: shortName(x.name), issuer: x.issuer, ownership: x.ownership, count: 0, posts: 0 });
          e.posts++;
          e.count += Math.max(1, textOccurrences(p, x.code));
          postSet[p.id] = 1;
        });
      });
      var list = Object.keys(map).map(function (c) { return map[c]; }).sort(function (a, b) {
        var ao = a.ownership === 'own' ? 0 : 1, bo = b.ownership === 'own' ? 0 : 1;
        return ao - bo || b.count - a.count || (a.code < b.code ? -1 : 1);
      });
      return {
        list: list,
        own: list.filter(function (e) { return e.ownership === 'own'; }),
        peer: list.filter(function (e) { return e.ownership !== 'own'; }),
        etfCount: list.length,
        total: list.reduce(function (t, e) { return t + e.count; }, 0),
        postCount: Object.keys(postSet).length
      };
    });
  }

  var HOT_UNAVAILABLE = { '3087': 1 };                        // 演示：热议总结暂不可用
  var HOT_BANK = {
    'ETF': {
      pos: ['回撤已接近支撑位，主流观点倾向分批加仓', '费率与盘口深度获认可，讨论集中在定投节奏', '港股通可直接买入被反复提及，倾向长期持有'],
      neg: ['溢价再度拉开，多数建议等收敛后再进场', '跟踪偏差偏大引发质疑，观望声音占多', '分派低于预期，减仓讨论增多'],
      neu: ['成交清淡，主流观点等方向明朗后再操作', '多空分歧加大，讨论集中在支撑位是否有效']
    },
    '杠反': {
      pos: ['溢价收敛，多数认为日内做方向的成本更可控', '倍数规则清楚获认可，短线参与意愿上升'],
      neg: ['连续持有损耗讨论增多，多数建议只做日内', '溢价拉高引发接盘担忧，主流观点等待回落'],
      neu: ['财报周波动放大，主流观点减半仓观察']
    },
    '备兑高息': {
      pos: ['分派到账稳定获认可，倾向继续持有收息', '震荡行情下期权金收入被视为优势'],
      neg: ['分派环比减少引发质疑，部分讨论转向减配', '上涨行情跑输指数被反复提及'],
      neu: ['讨论集中在除净日前是否加仓，分歧明显']
    },
    '货币': {
      pos: ['七日年化回升，作为闲置资金停泊的讨论增多'],
      neg: ['收益率下行，转投其他现金工具的讨论增多'],
      neu: ['申赎便利被反复提及，观点以持有为主']
    },
    '主题': {
      pos: ['成分股调仓获认可，主流观点倾向一篮子建仓'],
      neg: ['主题定义与持仓不符的质疑增多，观望为主'],
      neu: ['等待调仓公告，讨论以持仓不动为主']
    }
  };
  var HOT_RULE = '热议总结由 AI 归纳当前日期范围内该 ETF 最主流的具体观点，须为观点而非正负面判断；有效态度样本低于 ' + LOW_SAMPLE + ' 条不输出。';
  function hotSummaryFor(code, rangeKey) {
    return cached('hot|' + code + '|' + rangeKey, function () {
      var m = MASTER[code];
      if (!m || HOT_UNAVAILABLE[code]) return { status: 'unavailable', text: '数据暂不可用', sample: 0, ok: false };
      var o = observe(code, rangeKey), a = o.attitude, valid = a.positive + a.negative;
      if (!o.mentions) return { status: 'empty', text: '暂无相关内容', sample: 0, ok: false };
      if (!a.sampleSufficient) return { status: 'low_sample', text: '样本不足，暂无主流观点', sample: valid, ok: false };
      var net = (a.positive - a.negative) / valid;
      var tone = net > 0.15 ? 'pos' : (net < -0.15 ? 'neg' : 'neu');
      var bank = HOT_BANK[m.struct] || HOT_BANK.ETF;
      return { status: 'ok', text: pick(bank[tone], code + rangeKey + 'hot'), sample: valid, tone: tone, ok: true };
    });
  }

  var STAGE_CATS = [
    { k: 'add_opportunity', label: '加仓机会', tone: 'positive' },
    { k: 'pullback_done', label: '回撤到位', tone: 'positive' },
    { k: 'wait', label: '观望等待', tone: 'neutral' },
    { k: 'divergence', label: '分歧加大', tone: 'neutral' },
    { k: 'event', label: '事件驱动', tone: 'neutral', hue: 'event' },
    { k: 'reduce', label: '减仓离场', tone: 'negative' },
    { k: 'product_issue', label: '产品问题', tone: 'negative' }
  ];
  var STAGE_BY_KEY = {}; STAGE_CATS.forEach(function (c) { STAGE_BY_KEY[c.k] = c; });
  var STAGE_STYLE = {
    positive: { bg: 'var(--positive-100)', fg: 'var(--positive-700)', band: 'rgba(31,138,91,0.09)' },
    negative: { bg: 'var(--negative-100)', fg: 'var(--negative-700)', band: 'rgba(197,48,48,0.08)' },
    neutral: { bg: 'var(--ink-100)', fg: 'var(--ink-600)', band: 'rgba(110,122,138,0.10)' },
    event: { bg: 'var(--csop-blue-50)', fg: 'var(--csop-blue-700)', band: 'rgba(35,97,173,0.09)' },
    low: { bg: 'var(--ink-100)', fg: 'var(--ink-500)', band: 'transparent' }
  };
  var CAT_BY_TONE = { positive: ['add_opportunity', 'pullback_done', 'event'], negative: ['reduce', 'product_issue'], neutral: ['wait', 'divergence', 'event'] };
  var STAGE_LINES = {
    add_opportunity: ['市场认为回撤提供加仓机会，讨论集中在分批买入的价位', '多数观点把当前位置视作加仓窗口，费率优势被反复提及', '回撤至支撑位附近，主流观点倾向分批加仓'],
    pullback_done: ['主流观点认为回撤基本到位，等待放量确认', '支撑位已多次确认，讨论倾向持有等待反弹', '多数认为下跌空间有限，止跌信号成为讨论焦点'],
    wait: ['成交清淡，主流观点等待财报与政策方向明朗', '讨论以观望为主，多数选择等回到均线附近再决定', '方向不明，主流观点先减少操作等待信号'],
    divergence: ['多空分歧明显，支撑位是否有效成为争论焦点', '看多与看空观点数量接近，溢价是否合理成为争论点', '对后市判断分歧加大，讨论集中在仓位控制'],
    event: ['季度调仓公告带动讨论，成分变化成为主要话题', '发行商直播与活动带动讨论量上升，观点以解读为主', '分派公告引发集中讨论，焦点在到账时间与金额'],
    reduce: ['涨幅被认为已反映预期，减仓与止盈讨论增多', '波动放大引发担忧，主流观点建议降低仓位', '多数观点倾向先离场观望，等待更好的再入场位置'],
    product_issue: ['跟踪偏差与溢价成为讨论焦点，要求官方说明的声音增多', '分派金额低于预期引发质疑，部分讨论转向同类产品', '盘口价差偏大被反复提及，主流观点建议暂缓进场']
  };
  var DAY_LINES = {
    add_opportunity: ['回撤被视为加仓机会', '分批买入讨论增多', '费率优势支持加仓观点'],
    pullback_done: ['多数认为回撤已到位', '支撑位再次获确认', '止跌信号成讨论焦点'],
    wait: ['观望为主，等待方向', '成交清淡，讨论减少', '等财报／公告再决定'],
    divergence: ['多空分歧明显', '支撑位有效性成争论点', '仓位控制成为讨论重点'],
    event: ['调仓公告带动讨论', '活动／直播带动讨论量', '分派公告引发讨论'],
    reduce: ['止盈与减仓讨论增多', '建议降低仓位的观点占多', '离场观望声音上升'],
    product_issue: ['溢价与跟踪偏差被质疑', '分派低于预期引发质疑', '盘口价差偏大被提及']
  };
  var STAGE_UNAVAILABLE = { '3007': 1 };                      // 演示：阶段观点尚未生成
  var PERIODS = [
    { k: 'am', label: '上午', from: 0, to: 12, text: '00:00–12:00' },
    { k: 'pm', label: '下午', from: 12, to: 17, text: '12:00–17:00' },
    { k: 'post', label: '盘后', from: 17, to: 24, text: '17:00–24:00' }
  ];
  var STAGE_RULE = '阶段观点：当日按上午（00:00–12:00）／下午（12:00–17:00）／盘后（17:00–24:00）三段各归纳一条主流观点；多日先逐日归纳主流观点与情绪，再由 AI 把观点相近的连续日期合并为同一阶段，14 天及以上视图下单日孤立观点并入相邻阶段。样本不足的时段不参与合并，只在折线下方以灰点标记。阶段总结描述讨论区观点，不表述与价格的因果关系。';

  /* 热度序列：小时／日粒度直接取观测桶；30 天按自然日重算后校准到区间总热度 */
  function heatSeriesFor(code, rangeKey) {
    return cached('heat|' + code + '|' + rangeKey, function () {
      var o = observe(code, rangeKey), range = buildRange(rangeKey);
      if (!o) return [];
      var toPoint = function (b, i, k, hour) {
        return {
          i: i, day: b.start, hour: hour, label: b.label, tip: b.tip,
          heat: Math.round(heatOf(b.comments, b.likes, b.shares) * k),
          mentions: Math.round(b.mentions * k), comments: Math.round(b.comments * k),
          positive: Math.round(b.positive * k), negative: Math.round(b.negative * k), neutral: Math.round(b.neutral * k)
        };
      };
      if (range.gran !== 'week') return o.buckets.map(function (b, i) { return toPoint(b, i, 1, range.gran === 'hour' ? range.buckets[i].hour : null); });
      var buckets = [];
      for (var d = 0; d < range.days; d++) {
        var dd = addDays(range.from, d);
        buckets.push({ i: d, span: 1, day: dd, dow: parse(dd).getUTCDay(), label: md(dd), tip: md(dd) + '（' + dowOf(dd) + '）全天' });
      }
      var bk = bucketsFor(code, { gran: 'day', buckets: buckets }, 'daily');
      var raw = bk.reduce(function (t, b) { return t + heatOf(b.comments, b.likes, b.shares); }, 0);
      var k = raw ? o.discussionHeat / raw : 1;
      return bk.map(function (b, i) { return toPoint(b, i, k, null); });
    });
  }
  function stagesFor(code, rangeKey) {
    return cached('stg|' + code + '|' + rangeKey, function () {
      var m = MASTER[code], range = buildRange(rangeKey), series = heatSeriesFor(code, rangeKey);
      var half = range.gran === 'hour';
      var base = { code: code, granularity: half ? 'half_day' : 'day', granLabel: half ? '按上午／下午／盘后归纳' : '按自然日归纳，相近观点合并为阶段', series: series, stages: [], rule: STAGE_RULE };
      if (!m || STAGE_UNAVAILABLE[code]) return Object.assign(base, { status: 'unavailable' });
      var agg = function (pts, label, sub, day) {
        var u = { day: day, label: label, sub: sub, idxFrom: pts[0].i, idxTo: pts[pts.length - 1].i, mentions: 0, heat: 0, positive: 0, negative: 0, neutral: 0 };
        pts.forEach(function (p) { u.mentions += p.mentions; u.heat += p.heat; u.positive += p.positive; u.negative += p.negative; u.neutral += p.neutral; });
        return u;
      };
      /* 1) 切时段：小时粒度 → 每天三段；日粒度 → 每天一段 */
      var units = [];
      if (half) {
        var byDay = {};
        series.forEach(function (p) { (byDay[p.day] = byDay[p.day] || []).push(p); });
        Object.keys(byDay).sort().forEach(function (day) {
          PERIODS.forEach(function (pr) {
            var pts = byDay[day].filter(function (p) { return p.hour >= pr.from && p.hour < pr.to; });
            if (pts.length) units.push(agg(pts, md(day) + ' ' + pr.label, pr.text, day));
          });
        });
      } else series.forEach(function (p) { units.push(agg([p], p.label, dowOf(p.day), p.day)); });
      /* 2) 各时段情绪与观点分类：相邻时段的观点有延续性（74% 概率延续上一时段分类，前提是与本时段情绪相容） */
      var threshold = half ? Math.ceil(LOW_SAMPLE / 2) : LOW_SAMPLE, prevCat = null;
      units.forEach(function (u, i) {
        var valid = u.positive + u.negative;
        u.sufficient = valid >= threshold;
        var net = valid ? (u.positive - u.negative) / valid : 0;
        u.tone = u.sufficient ? (net > 0.12 ? 'positive' : (net < -0.12 ? 'negative' : 'neutral')) : null;
        if (u.tone) {
          var h = hash(code + rangeKey + 'cat' + i), cands = CAT_BY_TONE[u.tone];
          u.cat = (prevCat && cands.indexOf(prevCat) >= 0 && h % 100 < 74) ? prevCat : cands[(h >>> 8) % cands.length];
          prevCat = u.cat;
          u.digest = pick(DAY_LINES[u.cat], code + rangeKey + 'dg' + i);
        } else { u.cat = null; u.digest = u.mentions ? '样本不足，暂无主流观点' : '尚无讨论'; }
      });
      /* 3) 合并：相邻且分类相同的时段归为同一阶段；样本不足的时段并入相邻阶段；14 天及以上单日孤立观点并入前一阶段 */
      var stages = [];
      units.forEach(function (u) {
        var last = stages[stages.length - 1];
        if (!u.cat) { if (last) { last.units.push(u); last.low++; } else stages.push({ cat: null, units: [u], low: 1 }); return; }
        if (last && (last.cat === u.cat || last.cat === null)) { last.cat = u.cat; last.units.push(u); return; }
        stages.push({ cat: u.cat, units: [u], low: 0 });
      });
      if (!half && range.days >= 14) {
        var merged = [];
        stages.forEach(function (s) {
          var prev = merged[merged.length - 1], viewDays = s.units.length - s.low;
          if (prev && prev.cat && viewDays <= 1) { prev.units = prev.units.concat(s.units); prev.low += s.low; prev.absorbed = (prev.absorbed || 0) + viewDays; }
          else merged.push(s);
        });
        stages = merged;
      }
      /* 4) 输出 */
      var out = stages.map(function (s, i) {
        var f = s.units[0], l = s.units[s.units.length - 1], cat = s.cat ? STAGE_BY_KEY[s.cat] : null;
        var st = cat ? STAGE_STYLE[cat.hue || cat.tone] : STAGE_STYLE.low;
        var mentions = 0, heat = 0, pos = 0, neg = 0;
        s.units.forEach(function (u) { mentions += u.mentions; heat += u.heat; pos += u.positive; neg += u.negative; });
        var sample = pos + neg;
        return {
          n: i + 1, from: f.day, to: l.day, idxFrom: f.idxFrom, idxTo: l.idxTo,
          label: f.label === l.label ? f.label : f.label + ' — ' + l.label,
          sub: half ? (f.sub === l.sub && f.day === l.day ? f.sub : f.sub.split('–')[0] + '–' + l.sub.split('–')[1]) : s.units.length + ' 天',
          category: s.cat, categoryLabel: cat ? cat.label : '样本不足', sentiment: cat ? cat.tone : null,
          sentimentLabel: cat ? ATT_LABEL[cat.tone] : '—', bg: st.bg, fg: st.fg, band: st.band,
          summary: cat ? pick(STAGE_LINES[s.cat], code + rangeKey + 'st' + i) : '样本不足，暂无主流观点',
          mentions: mentions, heat: heat, sample: sample,
          evidenceCount: cat ? Math.min(sample, 5 + hash(code + rangeKey + 'ev' + i) % 10) : 0,
          unitCount: s.units.length, lowUnits: s.low, absorbed: s.absorbed || 0,
          digests: s.units.map(function (u) { return { label: u.label, sub: u.sub, tone: u.tone, toneLabel: u.tone ? ATT_LABEL[u.tone] : '样本不足', digest: u.digest, mentions: u.mentions, heat: u.heat, sufficient: u.sufficient }; })
        };
      });
      var anyView = out.some(function (s) { return s.category; });
      var anyMention = series.some(function (p) { return p.mentions > 0; });
      return Object.assign(base, { status: anyView ? 'ok' : (anyMention ? 'low_sample' : 'empty'), stages: out, unitCount: units.length, threshold: threshold });
    });
  }

  var NAV = [
    { k: 'portfolio', name: '市场', items: [
      { k: 'sector', name: '板块总览', href: 'sector-overview.dc.html' },
      { k: 'product', name: '产品监控', href: 'product-monitor.dc.html' } ] },
    { k: 'accounts', name: '账号', items: [
      { k: 'kol', name: 'KOL 影响力', href: 'kol-activity.dc.html' },
      { k: 'official', name: '官号动态', href: 'official-activity.dc.html' } ] }
  ];

  var STATUS_LEGEND = [
    { k: '0', v: '已取得数据，且统计值确实为零。', bg: 'var(--ink-100)', fg: 'var(--ink-700)' },
    { k: '—', v: '字段不适用于该产品或该口径。', bg: 'var(--ink-100)', fg: 'var(--ink-700)' },
    { k: '暂不可用', v: '字段应有值，但当前数据源未提供或尚未核验。', bg: 'var(--warning-100)', fg: 'var(--warning-700)' },
    { k: '暂无内容', v: '范围内已完成检查，没有符合条件的内容。', bg: 'var(--csop-blue-50)', fg: 'var(--csop-blue-700)' },
    { k: '样本不足', v: '有效产品态度评论少于 ' + LOW_SAMPLE + ' 条，不输出倾向结论。', bg: 'var(--ink-100)', fg: 'var(--ink-700)' },
    { k: '待确认', v: 'AI 自动识别的竞品关系、动态分类或重点舆情（需合规关注）信号，尚未人工确认。', bg: 'var(--warning-100)', fg: 'var(--warning-700)' }
  ];

  /* 双层导航（浅色）：一级为域（市场／账号）分段标签，二级为当前域的页面标签 */
  function navGroups(domainKey, subKey) {
    return NAV.map(function (d) {
      var on = d.k === domainKey;
      return {
        name: d.name, href: d.items[0].href,
        fw: on ? 600 : 500,
        fg: on ? 'var(--csop-navy-900)' : 'var(--ink-500)',
        bg: on ? '#fff' : 'transparent',
        sh: on ? '0 1px 2px rgba(14,42,82,0.10), 0 0 0 1px var(--border-1)' : 'none',
        subDisplay: on ? 'flex' : 'none',
        items: d.items.map(function (it) {
          var hit = on && it.k === subKey;
          return {
            name: it.name, href: it.href, fw: hit ? 600 : 400,
            fg: hit ? 'var(--csop-blue-700)' : 'var(--ink-600)',
            bc: hit ? 'var(--csop-blue-600)' : 'transparent'
          };
        })
      };
    });
  }

  /* 账号域沿用的公共外壳 */
  function shell(domainKey, subKey, st, go) {
    var range = buildRange(st.rangeKey || DEFAULT_KEY);
    return {
      navGroups: navGroups(domainKey, subKey),
      domains: NAV.map(function (d) {
        var on = d.k === domainKey;
        return {
          name: d.name, href: d.items[0].href,
          fw: on ? 600 : 400,
          fg: on ? '#fff' : 'rgba(255,255,255,0.66)',
          bg: on ? 'rgba(255,255,255,0.10)' : 'transparent',
          bc: on ? '#fff' : 'transparent'
        };
      }),
      subItems: NAV.filter(function (d) { return d.k === domainKey; })[0].items.map(function (it) {
        var on = it.k === subKey;
        return {
          name: it.name, href: it.href,
          fw: on ? 600 : 400,
          fg: on ? 'var(--csop-blue-600)' : 'var(--ink-600)',
          bc: on ? 'var(--csop-blue-600)' : 'transparent'
        };
      }),
      presets: PRESETS.map(function (p) {
        var on = p.k === range.key;
        return {
          label: p.label, go: function () { go({ rangeKey: p.k }); },
          fw: on ? 600 : 400,
          fg: on ? 'var(--csop-blue-700)' : 'var(--ink-600)',
          bg: on ? 'var(--csop-blue-50)' : '#fff'
        };
      }),
      chips: [{ k: 'all', name: '全部', hue: '#909AAA' }].concat(SECTOR_AGG).map(function (c) {
        var on = c.k === st.sector;
        return {
          name: c.name, go: function () { go({ sector: c.k, sel: null }); },
          dot: c.hue,
          fw: on ? 600 : 400,
          fg: on ? '#fff' : 'var(--ink-700)',
          bg: on ? 'var(--csop-navy-900)' : '#fff',
          bc: on ? 'var(--csop-navy-900)' : 'var(--border-2)'
        };
      }),
      rangeText: range.text, rangeFrom: range.from, rangeTo: range.to, updated: NOW_DATE + ' ' + NOW_TIME + ' HKT',
      statusLegend: STATUS_LEGEND
    };
  }

  window.RADAR = {
    /* 工具 */
    rgba: rgba, num: num, pct1: pct1, delta: delta, hash: hash, rnd: rnd, pick: pick, pickN: pickN,
    addDays: addDays, md: md, dowOf: dowOf, shell: shell, navGroups: navGroups,
    /* 口径常量 */
    LOW_SAMPLE: LOW_SAMPLE, NEW_DAYS: NEW_DAYS, NOW_DATE: NOW_DATE, NOW_TIME: NOW_TIME,
    UPDATED: NOW_DATE + ' ' + NOW_TIME + ' HKT',
    PRESETS: PRESETS, DEFAULT_RANGE: DEFAULT_KEY, STATUS_LEGEND: STATUS_LEGEND, NAV: NAV,
    SECTORS: SECTORS, SECTOR_NAME: SECTOR_NAME,
    /* 主数据与查询 */
    MASTER: MASTER, ORDER: ORDER, CMAP: CMAP,
    buildRange: buildRange, observe: observe, ranks: ranks, benchmark: benchmark, pool: pool,
    themesFor: themesFor, negCatsFor: negCatsFor, topicsFor: topicsFor,
    competitorsFor: competitorsFor, evidenceFor: evidenceFor, summaryFor: summaryFor,
    complianceFor: complianceFor, kolMentionsFor: kolMentionsFor, candlesFor: candlesFor, RISK_TAG: RISK_TAG,
    sectorAgg: sectorAgg,
    /* 账号域沿用 */
    PRODUCTS: PRODUCTS, BY_CODE: BY_CODE, SECTOR_AGG: SECTOR_AGG, SERIES: SERIES, dailyFor: dailyFor,
    OFFICIAL: OFFICIAL, KOLS: KOLS,
    RETAIL_TIERS: RETAIL_TIERS, RETAIL_NEW: RETAIL_NEW,
    kolImpact: kolImpact, kolProfile: kolProfile, officialPosts: officialPosts,
    POST_TYPES: POST_TYPES, TYPE_BY_KEY: TYPE_BY_KEY, typeStyle: typeStyle, CAMP: CAMP, campOf: campOf,
    CAMP_RULE: CAMP_RULE, COUNT_NOTE: COUNT_NOTE, SUMMARY_NOTE: SUMMARY_NOTE,
    kolOpinions: kolOpinions, ACTIONS: ACTIONS,
    /* 第三轮增量 2026-09-08 */
    DIRECTIONS: DIRECTIONS, DIR_BY_KEY: DIR_BY_KEY, dirStyle: dirStyle, TYPE_RULE: TYPE_RULE,
    etfMentionsFor: etfMentionsFor, shortName: shortName, ETF_MENTION_RULE: ETF_MENTION_RULE,
    hotSummaryFor: hotSummaryFor, HOT_RULE: HOT_RULE,
    heatSeriesFor: heatSeriesFor, stagesFor: stagesFor, STAGE_CATS: STAGE_CATS, STAGE_RULE: STAGE_RULE, PERIODS: PERIODS,
    HEAT_W: HEAT_W, HEAT_FORMULA: HEAT_FORMULA, HEAT_NOTE: HEAT_NOTE, heatOf: heatOf,
    ACTIVE_ACCOUNTS: ACTIVE_ACCOUNTS,
    RANGE: { from: addDays(ANCHOR, -6), to: ANCHOR, label: '近 7 天' },
    CHART_RANGE: { from: '2026-07-22', to: ANCHOR }
  };
})();
