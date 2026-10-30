/* Port of design/kol-activity.dc.html (KOL 影响力).
   Logic below (state → renderVals) is the design source verbatim; see OfficialActivity
   for the two standing deviations (synchronous RADAR import, `{{ }}` → JSX). */
import React from 'react'
import R, { prefetchScreen } from '../data/radar'
import {
  num, conf2, descN, typeStyle, displayedPostType, rgba, shell, dirStyle, reviewBadge, needsReview, kolDisplayLabel,
  CAMP, POST_TYPES, TYPE_BY_KEY, DIRECTIONS, DIR_BY_KEY,
} from '../lib/view'
import { kolProfile } from '../lib/profile'
import { compareKolReach } from '../lib/kolMetrics'
import { mentionedEtfsOf, primaryDisplayedEtf } from '../lib/kolEtfs'
import { dataSourceBadge } from '../lib/dataSource'
import { s, hover, focus } from '../lib/dc'
import Shell from '../components/Shell'
import DatePresetTabs from '../components/DatePresetTabs'
import AccountSummaryStrip from '../components/AccountSummaryStrip'
import withTransition from '../components/withTransition'
import DcLink from '../components/DcLink'
import { hrefWithRange, listenForRangeNavigation, rangeFromSearch } from '../lib/customRange'

class KolActivity extends React.Component {
  static defaultProps = {
    typeScheme: '双标签',
    lowConfidence: 0.7,
    summaryLines: 2,
  }

  constructor(props) {
    super(props);
    this.state = {
      rangeKey: rangeFromSearch(window.location.search, R.PRESETS), sort: 'time', leaderSort: 'views', sel: null,
      etf: 'ALL', kol: 'ALL', post: 'ALL', types: [], dirs: [],
      etfMenu: false, kolMenu: false, postMenu: false, typeMenu: false,
      /* 表内筛选：发帖记录（q / camp / onlyPending / tTypeMenu）与声量排名（lq / lCamp / lType） */
      q: '', camp: 'ALL', onlyPending: false, tTypeMenu: false,
      lq: '', lCamp: 'ALL', lType: 'ALL', lTypeMenu: false,
      shown: 50
    };
    /* 首次 render 之前把本屏端点并行发出去（radar.js `urlsFor`），首绘不再是串行往返。 */
    prefetchScreen('kol', { rangeKey: this.state.rangeKey });
  }
  componentDidMount() {
    var p = {};
    try {
      var u = new URLSearchParams(window.location.search);
      var etf = u.get('etf'), kol = u.get('kol');
      if (etf) p.etf = etf;
      if (kol && R.KOLS.some(function (x) { return x.name === kol && x.active; })) p.kol = kol;
    } catch (e) { /* 深链参数不可用时保留默认筛选 */ }
    if (Object.keys(p).length) this.setState(p);
    this._stopRangeNavigation = listenForRangeNavigation(R.PRESETS, (rangeKey) => {
      prefetchScreen('kol', { rangeKey });
      this.setState({ rangeKey, sel: null, shown: 50 });
    });
  }
  componentWillUnmount() { if (this._stopRangeNavigation) this._stopRangeNavigation(); }
  data() { return R.kolImpact(this.state.rangeKey); }
  camp(p) { return p.camp; }
  codesOf(p) { return mentionedEtfsOf(p).map(function (m) { return m.code; }); }
  /* 「这条标签模型自己举手了吗」有**三**个答案：举了（true）、没举（false）、
     不知道（null）。判据是 `reviewState`，不是置信度（ADR-0019 §2：校准概率存在
     之前 lowConfidence 不生效，而 `calibrated_confidence` 整列是 NULL）。
     返回 null 的那一支在 `if (...)` 里是假值，所以徽章不挂；「还没标注」由类型徽章
     自己那枚「暂不可用」说，不跟「判了但存疑」共用一个徽章。 */
  pending(p) { return needsReview(p.reviewState); }
  /* 这一条结论的如实声明（ADR-0019 §2／§4）。没有可定位的判定依据句时退为「AI 生成」
     —— 「可追溯原文」是一句承诺，指不到原文就不能这么说。 */
  review(p) { return reviewBadge(p.reviewState, p.evidenceIdx != null && p.evidenceIdx >= 0); }
  /* 整块 AI 标注是一起下发、一起缺的（sql provider 的 `_UNANNOTATED`）。以 postType
     为准判整块，免得屏内十几处各判各的字段、日后漂成十几种说法。 */
  annNa(p) { return p.postType == null; }
  typeOk(p) {
    var t = this.state.types, d = this.state.dirs;
    if (!t.length && !d.length) return true;
    var key = displayedPostType(p).filterKey;
    return t.some(function (k) { return key === 'content:' + k; })
      || d.some(function (k) { return key === 'direction:' + k; });
  }
  /* 导出 CSV：只导当前表格所见（日期范围 × 顶部四级 × 表内筛选 × 当前排序）；UTF-8 带 BOM + CRLF，Excel 直开不乱码；首行为筛选摘要 */
  exportCsv(list, summary, filename) {
    var self = this;
    var esc = function (v) { v = v == null ? '' : String(v); return /[",\r\n]/.test(v) ? '"' + v.split('"').join('""') + '"' : v; };
    var ownershipLabel = function (p) { return !p.code ? (mentionedEtfsOf(p).length ? '其他公司产品' : '未提及') : (p.ownership === 'own' ? '自家' : (p.ownership === 'peer' ? '其他公司产品' : '数据暂不可用')); };
    var head = ['重点KOL', 'KOL标签', '发帖时间', 'ETF代码', 'ETF名称', '发行商', '内容形式', '操作方向', '类型置信度', '是否待确认', 'AI摘要', 'ETF归属', '正文提及ETF(全部ticker)', '赞', '评论数', '转发', '浏览量', '原帖链接'];
    /* 导出的缺失格写「数据暂不可用」，不写空。CSV 里的空单元格在 Excel 里与
       「这里确实没有」长得一模一样，而这份文件会被拿去做判断、甚至再统计一遍。
       页面上分得开的两件事，落到文件里也得分得开。 */
    var na = '数据暂不可用';
    var cell = function (v) { return v == null ? na : v; };
    var lines = [esc('# ' + summary), head.map(esc).join(',')].concat(list.map(function (p) {
      var ann = self.annNa(p), pd = self.pending(p);
      var etfs = mentionedEtfsOf(p), primary = primaryDisplayedEtf(p);
      var mentioned = etfs.map(function (m) { return m.ticker; }).join(' ') || '未提及';
      return [p.kol, p.tags.split(',').join(' / '), p.day + ' ' + p.time.slice(6), primary ? primary.ticker : '未提及', primary ? (primary.name || na) : '未提及', primary ? (primary.issuer || (primary.inPool ? na : '其他公司产品')) : '未提及',
        cell(p.typeLabel), ann ? na : (p.directionPending ? '待确认' : (p.dir ? p.dir.label : '')),
        conf2(p.confidence), pd == null ? na : (pd ? '是' : '否'),
        ann ? na : (p.hasSummary ? p.summary : '（图片帖，无摘要）'), ownershipLabel(p),
        mentioned,
        cell(p.likes), cell(p.comments), cell(p.shares), cell(p.views), p.url].map(esc).join(',');
    }));
    this.downloadCsv(lines, filename);
  }
  downloadCsv(lines, filename) {
    var blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  }
  exportLeaderCsv(list, summary, filename, rangeKey) {
    var esc = function (v) { v = v == null ? '' : String(v); return /[",\r\n]/.test(v) ? '"' + v.split('"').join('""') + '"' : v; };
    var cell = function (v) { return v == null ? '数据暂不可用' : v; };
    var head = ['排名', 'KOL', 'KOL类型', '发帖篇数', '仅提自家', '双方都提', '仅提其他公司', '提及自家篇数', '提及其他公司篇数', '浏览量', '评论量', '详情链接'];
    var lines = [esc('# ' + summary), head.map(esc).join(',')].concat(list.map(function (leader, index) {
      var detail = hrefWithRange('kol-detail.dc.html?kol=' + encodeURIComponent(leader.kol), rangeKey);
      return [index + 1, leader.kol, leader.rosterTags.join(' / '), leader.n, leader.own, leader.both, leader.peer,
        leader.ownAny, leader.peerAny, cell(leader.views), cell(leader.comments), detail].map(esc).join(',');
    }));
    this.downloadCsv(lines, filename);
  }

  /* 四级联动：ETF → KOL → 帖子，类型独立多选。上一级变化时清掉下一级 */
  postsForEtf(M) {
    var s = this.state, self = this;
    return M.posts.filter(function (p) { return s.etf === 'ALL' || self.codesOf(p).indexOf(s.etf) >= 0; });
  }
  scoped(M) {
    var s = this.state, self = this;
    return this.postsForEtf(M).filter(function (p) {
      if (s.kol !== 'ALL' && p.kol !== s.kol) return false;
      if (s.post !== 'ALL' && p.id !== s.post) return false;
      return self.typeOk(p);
    });
  }
  selPost() {
    var M = this.data();
    if (!M || !this.state.sel) return null;
    var id = this.state.sel;
    return M.posts.filter(function (p) { return p.id === id; })[0] || null;
  }

  renderVals() {
    var s = this.state, self = this;
    var norm = function (x) { return String(x == null ? '' : x).toLowerCase().trim(); };
    var campOk = function (p, want) { var c = self.camp(p); return want === 'ALL' || (want === 'own' ? (c === 'own' || c === 'both') : want === 'competitor' ? (c === 'competitor' || c === 'both' || (c === 'none' && mentionedEtfsOf(p).length > 0)) : c === want); };
    var CAMPS = [['ALL', '全部'], ['own', '提自家'], ['competitor', '提竞品'], ['both', '双方都提']];
    var seg = function (cur, key) {
      return CAMPS.map(function (x) {
        var on = cur === x[0], patch = {}; patch[key] = x[0];
        return { label: x[1], go: function () { self.setState(patch); }, fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff' };
      });
    };
    this.LC = this.props.lowConfidence != null ? this.props.lowConfidence : 0.7;
    var clamp = this.props.summaryLines != null ? this.props.summaryLines : 2;
    var range = R.buildRange(s.rangeKey);
    /* 切区间会触发新的 kolImpact 取数；进 transition 让旧内容留着，不退回骨架（withTransition.jsx）。 */
    var out = shell('accounts', 'kol', s, function (x) { self.props.startTransition(function () { self.setState(x); }); });
    var M = this.data();
    var etfPosts = this.postsForEtf(M);
    var sp = this.scoped(M);
    var secOf = function (k) { return R.SECTORS.filter(function (x) { return x.k === k; })[0] || R.SECTORS[0]; };
    /* 这枚标签描述左侧主 ETF，不描述整篇正文还提过什么产品。 */
    var ownershipOf = function (p) {
      if (!p.code && mentionedEtfsOf(p).length) return { k: 'outside', label: '其他公司产品', bg: 'var(--csop-silver-200)', fg: 'var(--ink-700)' };
      if (!p.code) return { k: 'none', label: '未提及', bg: 'var(--ink-100)', fg: 'var(--ink-500)' };
      if (p.ownership === 'own') return CAMP.own;
      if (p.ownership === 'peer') return Object.assign({}, CAMP.competitor, { label: '其他公司' });
      return { k: 'unknown', label: '暂不可用', bg: 'var(--ink-100)', fg: 'var(--ink-500)' };
    };
    var mentionBadgesOf = function (p) {
      var c = self.camp(p);
      if (c === 'none' && mentionedEtfsOf(p).length) return [{ k: 'outside', label: '其他公司产品', bg: 'var(--csop-silver-200)', fg: 'var(--ink-700)' }];
      return c === 'both' ? [CAMP.own, CAMP.competitor] : [CAMP[c] || CAMP.none];
    };
    var issuerShort = function (x) { return x === 'CSOP 南方东英' ? '南方东英' : x; };
    var pct = function (n, d) { return d ? Math.round(n / d * 100) + '%' : '—'; };
    out.clamp = String(clamp); out.clampH = String(clamp * 22);

    /* ① ETF 菜单：发帖提及的全部产品（自家 + 竞品） */
    var byCode = {}, etfMeta = {};
    M.posts.forEach(function (p) { mentionedEtfsOf(p).forEach(function (m) { byCode[m.code] = (byCode[m.code] || 0) + 1; etfMeta[m.code] = m; }); });
    out.etfLabel = s.etf === 'ALL' ? '全部' : ((etfMeta[s.etf] || {}).ticker || s.etf);
    out.etfCaret = s.etfMenu ? '▲' : '▼';
    out.etfOpen = !!s.etfMenu;
    out.etfBc = (s.etfMenu || s.etf !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.etfBg = s.etfMenu ? 'var(--csop-blue-50)' : '#fff';
    out.etfToggle = function () { self.setState({ etfMenu: !s.etfMenu, kolMenu: false, postMenu: false, typeMenu: false }); };
    out.etfClose = function () { self.setState({ etfMenu: false }); };
    out.etfMenu = [{ k: 'ALL', code: '全部', name: '全部产品', n: M.posts.length, own: '', obg: 'transparent', ofg: 'transparent' }].concat(
      Object.keys(byCode).sort(function (a, b) { return byCode[b] - byCode[a] || (a < b ? -1 : 1); }).map(function (c) {
        var m = R.MASTER[c] || etfMeta[c], own = m && m.ownership === 'own', outside = m && m.inPool === false;
        return {
          k: c, code: (m && m.ticker) || c, name: m ? (outside ? (m.name || '其他公司产品') : (own ? m.name : m.issuer + ' · ' + m.name)) : c, n: byCode[c],
          own: outside ? '其他公司产品' : (own ? '自家' : '竞品'), obg: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', ofg: own ? '#fff' : 'var(--ink-700)'
        };
      })).map(function (o) {
        var on = o.k === s.etf;
        return {
          key: o.k, code: o.code, name: o.name, n: String(o.n), tick: on ? '✓' : '', own: o.own, obg: o.obg, ofg: o.ofg,
          bg: on ? 'var(--csop-blue-50)' : '#fff',
          go: function () { self.setState({ etf: o.k, kol: 'ALL', post: 'ALL', sel: null, etfMenu: false }); }
        };
      });

    /* ② KOL 菜单：只列在所选 ETF 下发过帖的人 */
    var byKolN = {};
    etfPosts.forEach(function (p) { byKolN[p.kol] = (byKolN[p.kol] || 0) + 1; });
    var kolNames = Object.keys(byKolN).sort(function (a, b) { return byKolN[b] - byKolN[a] || (a < b ? -1 : 1); });
    out.kolLabel = s.kol === 'ALL' ? '全部' : s.kol;
    out.kolCaret = s.kolMenu ? '▲' : '▼';
    out.kolOpen = !!s.kolMenu;
    out.kolBc = (s.kolMenu || s.kol !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.kolBg = s.kolMenu ? 'var(--csop-blue-50)' : '#fff';
    out.kolToggle = function () { self.setState({ kolMenu: !s.kolMenu, etfMenu: false, postMenu: false, typeMenu: false }); };
    out.kolClose = function () { self.setState({ kolMenu: false }); };
    out.kolMenuNote = s.etf === 'ALL'
      ? kolNames.length + ' 位重点KOL在区间内发过帖'
      : '只列提及 ' + s.etf + ' 的 ' + kolNames.length + ' 位';
    out.kolMenu = [{ k: 'ALL', name: '全部重点KOL', n: etfPosts.length }].concat(
      kolNames.map(function (k) { return { k: k, name: k, n: byKolN[k] }; })).map(function (o) {
        var on = o.k === s.kol;
        return {
          key: o.k, name: o.name, n: String(o.n), tick: on ? '✓' : '',
          bg: on ? 'var(--csop-blue-50)' : '#fff',
          go: function () { self.setState({ kol: o.k, post: 'ALL', sel: null, kolMenu: false }); }
        };
      });

    /* ③ 帖子菜单：所选 KOL 在所选 ETF 下的每一篇 */
    var kolPosts = etfPosts.filter(function (p) { return s.kol !== 'ALL' && p.kol === s.kol; })
      .sort(function (a, b) { return b.t - a.t; });
    var postEnabled = s.kol !== 'ALL' && kolPosts.length > 1;
    out.postOpen = !!s.postMenu && postEnabled;
    out.postCaret = postEnabled ? (s.postMenu ? '▲' : '▼') : '';
    out.postOp = postEnabled ? '1' : '0.5';
    out.postCursor = postEnabled ? 'pointer' : 'default';
    out.postBc = (s.postMenu || s.post !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.postBg = s.postMenu ? 'var(--csop-blue-50)' : '#fff';
    out.postFg = postEnabled ? 'var(--csop-blue-700)' : 'var(--ink-400)';
    out.postLabel = s.kol === 'ALL' ? '先选 KOL'
      : (kolPosts.length <= 1 ? '仅 1 篇'
        : (s.post === 'ALL' ? '全部 ' + kolPosts.length + ' 篇'
          : (kolPosts.filter(function (p) { return p.id === s.post; })[0] || {}).time || '全部'));
    out.postToggle = function () { if (postEnabled) self.setState({ postMenu: !s.postMenu, etfMenu: false, kolMenu: false, typeMenu: false }); };
    out.postClose = function () { self.setState({ postMenu: false }); };
    out.postMenu = [{ k: 'ALL', time: '全部 ' + kolPosts.length + ' 篇', code: '', type: '' }].concat(
      kolPosts.map(function (p) { var primary = primaryDisplayedEtf(p); return { k: p.id, time: p.time, code: primary ? primary.ticker : '—', type: displayedPostType(p).label }; })).map(function (o) {
        var on = o.k === s.post;
        return {
          key: o.k, time: o.time, code: o.code, type: o.type, tick: on ? '✓' : '',
          bg: on ? 'var(--csop-blue-50)' : '#fff',
          go: function () { self.setState({ post: o.k, sel: o.k === 'ALL' ? null : o.k, postMenu: false }); }
        };
      });

    /* ④ 类型多选：计数按 ETF × KOL × 帖子 的范围算，与表格一致 */
    var typeBase = etfPosts.filter(function (p) { return (s.kol === 'ALL' || p.kol === s.kol) && (s.post === 'ALL' || p.id === s.post); });
    var typeCounts = {}, dirCounts = {}, typeBaseNa = 0;
    typeBase.forEach(function (p) {
      /* 没标注过的不进任何一格。放进去会得到键名 "null" 的一格（谁都不会去读），
         同时让八格之和悄悄小于总篇数 —— 菜单看起来数得清清楚楚，其实漏了一批。
         漏掉的那批改在菜单头上明说（typeNaNote），不摊进八个类型里。 */
      var shown = displayedPostType(p);
      if (shown.filterKey == null) { typeBaseNa++; return; }
      if (shown.source === 'direction') dirCounts[shown.k] = (dirCounts[shown.k] || 0) + 1;
      else typeCounts[shown.k] = (typeCounts[shown.k] || 0) + 1;
    });
    out.typeNaNote = typeBaseNa ? '另有 ' + typeBaseNa + ' 篇展示类型尚未标注，不计入下表' : '';
    var DIR_DEF = { add: '在已有持仓上继续买入', open: '首次买入建立仓位', reduce: '部分卖出降低仓位', close: '全部卖出离场', hold: '明确表示暂不操作', pending: '操作类帖子但方向置信度不足' };
    var dirLabel = function (k) { return k === 'pending' ? '方向待确认' : DIR_BY_KEY[k].label; };
    var picked = s.types.map(function (k) { return TYPE_BY_KEY[k].label; }).concat(s.dirs.map(dirLabel));
    out.typeLabel = !picked.length ? '全部' : (picked.length <= 2 ? picked.join(' · ') : picked.length + ' 项');
    out.typeCaret = s.typeMenu ? '▲' : '▼';
    out.typeOpen = !!s.typeMenu;
    out.typeBc = (s.typeMenu || s.types.length || s.dirs.length) ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.typeBg = s.typeMenu ? 'var(--csop-blue-50)' : '#fff';
    out.typeToggle = function () { self.setState({ typeMenu: !s.typeMenu, etfMenu: false, kolMenu: false, postMenu: false }); };
    out.typeClose = function () { self.setState({ typeMenu: false }); };
    out.typeAll = function () { self.setState({ types: [], dirs: [], sel: null }); };
    /* 表内的「类型」下拉与顶部 ④ 共用 types / dirs，只是各自开合 */
    out.tTypeOpen = !!s.tTypeMenu; out.tTypeCaret = s.tTypeMenu ? '▲' : '▼';
    out.tTypeBc = (s.tTypeMenu || s.types.length || s.dirs.length) ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.tTypeBg = s.tTypeMenu ? 'var(--csop-blue-50)' : '#fff';
    out.tTypeToggle = function () { self.setState({ tTypeMenu: !s.tTypeMenu, lTypeMenu: false, etfMenu: false, kolMenu: false, postMenu: false, typeMenu: false }); };
    out.tTypeClose = function () { self.setState({ tTypeMenu: false }); };
    out.typeMenu = POST_TYPES.map(function (t) {
      var on = s.types.indexOf(t.k) >= 0, st = typeStyle(t.k);
      return {
        key: t.k, label: t.label, def: t.def, n: String(typeCounts[t.k] || 0), tick: on ? '✓' : '',
        boxBc: on ? 'var(--csop-blue-600)' : 'var(--border-2)', boxBg: on ? 'var(--csop-blue-600)' : '#fff',
        tbg: st.bg, tfg: st.fg, bg: on ? 'var(--csop-blue-50)' : '#fff',
        go: function () {
          var nx = on ? s.types.filter(function (k) { return k !== t.k; }) : s.types.concat([t.k]);
          self.setState({ types: nx, sel: null });
        }
      };
    });
    out.dirMenu = DIRECTIONS.map(function (d) { return { k: d.k, label: d.label }; }).concat([{ k: 'pending', label: '方向待确认' }]).map(function (d) {
      var on = s.dirs.indexOf(d.k) >= 0, st = dirStyle(d.k === 'pending' ? null : d.k, d.k === 'pending');
      return {
        key: d.k, label: d.label, def: DIR_DEF[d.k], n: String(dirCounts[d.k] || 0), tick: on ? '✓' : '',
        boxBc: on ? 'var(--csop-blue-600)' : 'var(--border-2)', boxBg: on ? 'var(--csop-blue-600)' : '#fff',
        tbg: st.bg, tfg: st.fg, bg: on ? 'var(--csop-blue-50)' : '#fff',
        go: function () {
          var nx = on ? s.dirs.filter(function (k) { return k !== d.k; }) : s.dirs.concat([d.k]);
          self.setState({ dirs: nx, sel: null });
        }
      };
    });

    out.hasScope = s.etf !== 'ALL' || s.kol !== 'ALL' || s.post !== 'ALL' || s.types.length > 0 || s.dirs.length > 0;
    out.clearScope = function () { self.setState({ etf: 'ALL', kol: 'ALL', post: 'ALL', types: [], dirs: [], sel: null }); };
    out.scopeText = (s.etf === 'ALL' ? '全部 ETF' : (((etfMeta[s.etf] || {}).ticker || s.etf) + ' ' + ((R.MASTER[s.etf] || etfMeta[s.etf] || {}).name || '')))
      + ' × ' + (s.kol === 'ALL' ? '全部 KOL' : s.kol)
      + (picked.length ? ' × ' + picked.join('、') : '');
    out.closeSel = function () { self.setState({ sel: null }); };

    /* 发帖记录表内筛选：关键词 × 提及范围（camp）× 仅待确认，只作用于表格，KPI 仍按上方范围 */
    var qq = norm(s.q);
    var hay = function (p) {
      return norm([p.kol, p.tags, p.code, p.name, p.issuer, p.summary]
        .concat(mentionedEtfsOf(p).map(function (m) { return m.code + ' ' + m.ticker + ' ' + m.name + ' ' + (m.issuer || ''); }), p.fullText || []).join(' '));
    };
    var tbl = sp.filter(function (p) { return (!qq || hay(p).indexOf(qq) >= 0) && campOk(p, s.camp) && (!s.onlyPending || self.pending(p)); });
    out.postCount = String(tbl.length);
    out.q = s.q; out.qBc = qq ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.setQ = function (e) { self.setState({ q: e.target.value }); };
    out.camps = seg(s.camp, 'camp');
    out.pendToggle = function () { self.setState({ onlyPending: !s.onlyPending }); };
    out.pendBc = s.onlyPending ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.pendBg = s.onlyPending ? 'var(--csop-blue-600)' : '#fff';
    out.pendFg = s.onlyPending ? '#fff' : 'var(--ink-600)';
    out.hasTf = !!qq || s.camp !== 'ALL' || s.onlyPending || s.types.length > 0 || s.dirs.length > 0;
    out.clearTf = function () { self.setState({ q: '', camp: 'ALL', onlyPending: false, types: [], dirs: [] }); };

    /* KPI：底层仍保留内容形式与操作方向，页面展示单一优先类型。 */
    var spKols = {}; sp.forEach(function (p) { spKols[p.kol] = 1; });
    var ownAny = 0, peerAny = 0, both = 0, pendingN = 0, noSum = 0, tc = {}, opN = 0, addN = 0, redN = 0, dirPend = 0, annNa = 0;
    sp.forEach(function (p) {
      var c = self.camp(p);
      if (c === 'own' || c === 'both') ownAny++;
      if (c === 'competitor' || c === 'both') peerAny++;
      if (c === 'both') both++;
      /* 整块标注没生成的，类型／方向／摘要三项一个都不算 —— 它们不是「没有」，是「没跑」。
         原来这一段会把它们全部算进「形式待确认」「图片帖」「非操作类」，于是一屏
         从没被标注过的帖子，会在 KPI 上读成一屏已经判完、只是判得没把握的帖子。 */
      if (self.annNa(p)) { annNa++; return; }
      if (self.pending(p)) pendingN++;
      /* `=== false` 而不是 `!p.hasSummary`：null 也是假值，但它的意思是「不知道有没有
         摘要」，不是「查过了是图片帖」。走到这里的都不是 null，写死语义留给下一个人。 */
      if (p.hasSummary === false) noSum++;
      tc[p.postType] = (tc[p.postType] || 0) + 1;
      if (TYPE_BY_KEY[p.postType].group === 'op') opN++;
      if (p.direction === 'add') addN++;
      if (p.direction === 'reduce') redN++;
      if (p.directionPending) dirPend++;
    });
    /* 「已识别」就是字面意思：标注跑过的那几篇。全都没跑时 `0` 是真零 —— 我们确实查了
       标注表，确实一条都没有 —— 所以它不是「暂不可用」，但那行小字得换句话说，
       否则读到的是「操作类 0 篇 · 加仓 0 · 减仓 0 …」一排看着像结论的零。 */
    var annOk = sp.length - annNa;
    var typeSub = '操作类 ' + opN + ' 篇 · 加仓 ' + addN + ' · 减仓 ' + redN + ' · 方向待确认 ' + dirPend + ' · 形式待确认 ' + pendingN + ' 篇' + (noSum ? ' · 图片帖 ' + noSum + ' 篇' : '');
    if (sp.length && !annOk) typeSub = '内容形式与操作方向标注尚未生成 · ' + sp.length + ' 篇待标注';
    else if (annNa) typeSub += ' · 另有 ' + annNa + ' 篇标注尚未生成';
    out.kpis = [
      { label: '发帖数', value: String(sp.length), unit: '篇', pct: '', pfg: 'transparent', sub: '涉及 ' + Object.keys(spKols).length + ' 位重点KOL · 最近 ' + M.range.days + ' 天', vfg: 'var(--ink-900)' },
      { label: '提及自家产品', value: String(ownAny), unit: '篇', pct: pct(ownAny, sp.length), pfg: 'var(--csop-blue-700)', sub: '其中 ' + both + ' 篇同时提及竞品 · 按产品池规则匹配', vfg: 'var(--csop-blue-700)' },
      { label: '提及竞品', value: String(peerAny), unit: '篇', pct: pct(peerAny, sp.length), pfg: 'var(--ink-600)', sub: '仅提竞品、未提自家的有 ' + (peerAny - both) + ' 篇', vfg: 'var(--ink-800)' },
      { label: '展示类型已识别', value: String(annOk), unit: '篇', pct: '', pfg: 'transparent', sub: typeSub, vfg: 'var(--ink-900)' }
    ];

    /* 发帖记录 */
    out.sorts = [['eng', '按互动'], ['time', '按时间']].map(function (x) {
      var on = s.sort === x[0];
      return {
        label: x[1], go: function () { self.setState({ sort: x[0] }); },
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff'
      };
    });
    var list = tbl.slice();
    /* 互动未知的排最后。`b.engagement - a.engagement` 会把 null 当 0，于是一条
       「互动量没采到」的帖子被排进「互动最少」那一段，与一条真的没人理的帖子混在一起。
       `t`（距区间起点的小时数）由后端按 posted_at 算，不会缺，照旧直接相减。 */
    if (s.sort === 'eng') list.sort(function (a, b) { return descN(a.engagement, b.engagement); });
    else list.sort(function (a, b) { return b.t - a.t; });
    /* 导出 CSV：与表格所见完全一致，首行为筛选摘要 */
    var campName = CAMPS.filter(function (x) { return x[0] === s.camp; })[0][1];
    var hasAnyFilter = out.hasScope || out.hasTf;
    var csvSummary = ['日期范围 ' + range.from + ' ～ ' + range.to + '（' + range.label + '）', 'ETF ' + (s.etf === 'ALL' ? '全部' : s.etf), 'KOL ' + (s.kol === 'ALL' ? '全部' : s.kol),
      '帖子 ' + (s.post === 'ALL' ? '全部' : s.post), '类型 ' + (picked.length ? picked.join('、') : '全部'), '关键词 ' + (qq || '—'), '提及范围 ' + campName,
      '仅待确认 ' + (s.onlyPending ? '是' : '否'), '排序 ' + (s.sort === 'eng' ? '按互动' : '按时间'), '共 ' + list.length + ' 条', '数据更新 ' + M.updated].join(' · ');
    var csvName = 'KOL发帖记录_' + range.from + '_' + range.to + (hasAnyFilter ? '_筛选' : '') + '.csv';
    out.exportCsv = function () { if (list.length) self.exportCsv(list, csvSummary, csvName); };
    out.exportTip = list.length ? '导出当前日期范围与筛选条件下的 ' + list.length + ' 条记录，与表格所见一致 · UTF-8 CSV，Excel 可直接打开' : '当前筛选下没有可导出的记录';
    out.exportBc = list.length ? 'var(--border-2)' : 'var(--border-1)';
    out.exportFg = list.length ? 'var(--csop-blue-600)' : 'var(--ink-400)';
    out.exportCursor = list.length ? 'pointer' : 'not-allowed';
    out.exportOp = list.length ? '1' : '0.6';
    var pageSize = 50, shown = Math.min(list.length, s.shown || pageSize);
    out.shownCount = String(shown);
    out.hasMoreRows = shown < list.length;
    out.moreCount = String(Math.min(pageSize, list.length - shown));
    out.loadMoreRows = function () { self.setState({ shown: shown + pageSize }); };
    out.rows = list.slice(0, shown).map(function (p, i) {
      var sec = secOf(p.sector), st = displayedPostType(p);
      var displayedEtfs = mentionedEtfsOf(p);
      return {
        key: p.id,
        kol: p.kol, tags: p.tags.split(',').join(' · '), time: p.time,
        etfs: displayedEtfs.map(function (m) { return {
          ticker: m.ticker, name: m.name, outside: m.inPool === false,
          bg: m.inPool === false ? 'var(--warning-100)' : rgba(sec.hue, 0.12),
          fg: m.inPool === false ? 'var(--warning-700)' : sec.hue
        }; }),
        issuer: displayedEtfs.length > 1 ? displayedEtfs.length + ' 只 ETF'
          : (p.code ? issuerShort(p.issuer) : (displayedEtfs.length ? '其他公司产品' : '正文未提及 ETF')),
        pbg: p.code ? rgba(sec.hue, 0.12) : 'var(--ink-100)', pfg: p.code ? sec.hue : 'var(--ink-500)',
        type: st.label, tbg: st.bg, tfg: st.fg,
        pending: self.pending(p), conf: conf2(p.confidence), review: self.review(p),
        /* 摘要位有**三**态：有摘要、查过了是图片帖（「暂无内容」侧）、标注还没跑
           （「暂不可用」侧）。原来只有前两态，于是没跑过标注的帖子会被写成
           「暂无摘要 · 图片帖」—— 替一篇可能全是文字的帖子宣布了它只有图片。 */
        summary: p.hasSummary ? p.summary : '',
        noSummary: p.hasSummary === false, summaryNa: self.annNa(p),
        ownership: ownershipOf(p),
        likes: num(p.likes), comments: num(p.comments), shares: num(p.shares), views: num(p.views),
        url: p.url || null, hasUrl: !!p.url, stop: function (e) { e.stopPropagation(); },
        bg: p.id === s.sel ? 'var(--csop-blue-50)' : (i % 2 ? 'var(--canvas)' : '#fff'),
        /* 只打开抽屉，不改动上方筛选 */
        go: function () { self.setState({ sel: p.id }); }
      };
    });

    /* 声量排名：随 ETF 与类型筛选变化，不随 KOL 筛选变化 */
    var basePosts = etfPosts.filter(function (p) { return self.typeOk(p); });
    var byK = {};
    basePosts.forEach(function (p) { (byK[p.kol] = byK[p.kol] || []).push(p); });
    var leaders = Object.keys(byK).map(function (k) { return kolProfile(k, byK[k], function (p) { return self.camp(p); }); });
    /* `n`（篇数）永远数得出来；`views` / `comments` 是合计，有一篇没采到就整份未知。
       浏览量由 compareKolReach、评论量由 descN 把未知 KOL 排最后，而不是当成 0 排进
       「触达／讨论最少」那一头 —— 那一头是一个结论，这里要的是「还不知道」。 */
    if (s.leaderSort === 'views') leaders.sort(compareKolReach);
    else if (s.leaderSort === 'n') leaders.sort(function (a, b) { return b.n - a.n || descN(a.comments, b.comments) || compareKolReach(a, b); });
    else leaders.sort(function (a, b) { return descN(a.comments, b.comments) || b.n - a.n || compareKolReach(a, b); });
    out.leaderSorts = [['views', '按浏览量'], ['n', '按篇数'], ['eng', '按评论量']].map(function (x) {
      var on = s.leaderSort === x[0];
      return {
        label: x[1], go: function () { self.setState({ leaderSort: x[0] }); },
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff'
      };
    });
    out.leaderSortNote = s.leaderSort === 'views' ? '按浏览量降序' : (s.leaderSort === 'n' ? '按篇数降序' : '按评论量降序');

    /* 排名表内筛选：KOL 名称 / 名单标签 × 提及范围 × KOL 类型。 */
    var rosterTags = function (name, posts) {
      var meta = R.KOLS.filter(function (k) { return k.name === name; })[0];
      var raw = meta && meta.tags != null ? meta.tags : (posts[0] ? posts[0].tags : '');
      return (Array.isArray(raw) ? raw : String(raw || '').split(','))
        .map(function (x) { return kolDisplayLabel(String(x).trim()); }).filter(Boolean);
    };
    var lqq = norm(s.lq);
    var lBase = leaders.filter(function (l) {
      if (lqq && norm(l.kol + ' ' + (l.posts[0] ? l.posts[0].tags : '')).indexOf(lqq) < 0) return false;
      if (s.lCamp === 'own') return l.ownAny > 0;
      if (s.lCamp === 'competitor') return l.peerAny > 0;
      if (s.lCamp === 'both') return l.ownAny > 0 && l.peerAny > 0;
      return true;
    });
    var ltc = {}, allRosterTags = {};
    lBase.forEach(function (l) {
      l.rosterTags = rosterTags(l.kol, l.posts);
      l.rosterTags.forEach(function (tag) { allRosterTags[tag] = 1; ltc[tag] = (ltc[tag] || 0) + 1; });
    });
    out.lTypeNaNote = '';
    var lRows = lBase.filter(function (l) { return s.lType === 'ALL' || l.rosterTags.indexOf(s.lType) >= 0; });
    out.lq = s.lq; out.lqBc = lqq ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.setLq = function (e) { self.setState({ lq: e.target.value }); };
    out.lCamps = seg(s.lCamp, 'lCamp');
    out.lTypeLabel = s.lType === 'ALL' ? '全部' : s.lType;
    out.lTypeOpen = !!s.lTypeMenu; out.lTypeCaret = s.lTypeMenu ? '▲' : '▼';
    out.lTypeBc = (s.lTypeMenu || s.lType !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.lTypeBg = s.lTypeMenu ? 'var(--csop-blue-50)' : '#fff';
    out.lTypeToggle = function () { self.setState({ lTypeMenu: !s.lTypeMenu, tTypeMenu: false, etfMenu: false, kolMenu: false, postMenu: false, typeMenu: false }); };
    out.lTypeClose = function () { self.setState({ lTypeMenu: false }); };
    out.lTypeMenu = [{ k: 'ALL', label: '全部类型', def: '名单中的全部 KOL', n: lBase.length, tbg: 'transparent', tfg: 'var(--ink-800)' }].concat(
      Object.keys(allRosterTags).sort().map(function (tag) { return { k: tag, label: tag, def: '名单维护的 KOL 类型', n: ltc[tag] || 0, tbg: 'var(--csop-blue-50)', tfg: 'var(--csop-blue-700)' }; })).map(function (o) {
        var on = o.k === s.lType;
        return {
          key: o.k, label: o.label, def: o.def, n: String(o.n), tick: on ? '✓' : '', tbg: o.tbg, tfg: o.tfg,
          bg: on ? 'var(--csop-blue-50)' : '#fff',
          go: function () { self.setState({ lType: o.k, lTypeMenu: false }); }
        };
      });
    out.leaderCount = String(lRows.length);
    out.hasLf = !!lqq || s.lCamp !== 'ALL' || s.lType !== 'ALL';
    out.clearLf = function () { self.setState({ lq: '', lCamp: 'ALL', lType: 'ALL' }); };
    var leaderCampName = CAMPS.filter(function (x) { return x[0] === s.lCamp; })[0][1];
    var leaderCsvSummary = ['日期范围 ' + range.from + ' ～ ' + range.to + '（' + range.label + '）', 'ETF ' + (s.etf === 'ALL' ? '全部' : s.etf),
      '展示类型 ' + (picked.length ? picked.join('、') : '全部'), '关键词 ' + (lqq || '—'), '提及范围 ' + leaderCampName,
      'KOL类型 ' + (s.lType === 'ALL' ? '全部' : s.lType), '排序 ' + out.leaderSortNote, '共 ' + lRows.length + ' 位', '数据更新 ' + M.updated].join(' · ');
    var leaderCsvName = 'KOL声量排名_' + range.from + '_' + range.to + (out.hasScope || out.hasLf ? '_筛选' : '') + '.csv';
    out.exportLeaderCsv = function () { if (lRows.length) self.exportLeaderCsv(lRows, leaderCsvSummary, leaderCsvName, s.rangeKey); };
    out.leaderExportTip = lRows.length ? '导出当前日期范围、ETF、展示类型及排名筛选下的 ' + lRows.length + ' 位 KOL，与排名所见一致' : '当前筛选下没有可导出的 KOL';
    out.leaderExportBc = lRows.length ? 'var(--border-2)' : 'var(--border-1)';
    out.leaderExportFg = lRows.length ? 'var(--csop-blue-600)' : 'var(--ink-400)';
    out.leaderExportCursor = lRows.length ? 'pointer' : 'not-allowed';
    out.leaderExportOp = lRows.length ? '1' : '0.6';

    var mxN = Math.max.apply(null, lRows.map(function (l) { return l.n; }).concat([1]));
    var p0 = this.selPost();
    var selKol = p0 ? p0.kol : null;
    var leaderRows = lRows.map(function (l, i) {
      var on = s.kol === l.kol || selKol === l.kol;
      return {
        key: l.kol,
        rank: String(i + 1), rankFg: i < 3 ? 'var(--csop-blue-700)' : 'var(--ink-400)',
        kol: l.kol, n: String(l.n),
        ownW: (l.own / mxN * 100).toFixed(1), bothW: (l.both / mxN * 100).toFixed(1), peerW: (l.peer / mxN * 100).toFixed(1),
        ownAny: String(l.ownAny), peerAny: String(l.peerAny),
        views: num(l.views), eng: num(l.comments),
        tags: l.rosterTags,
        fw: on ? 600 : 500,
        dot: on ? 'var(--csop-blue-600)' : 'var(--csop-silver-400)',
        bg: on ? 'var(--csop-blue-50)' : 'transparent',
        href: hrefWithRange('kol-detail.dc.html?kol=' + encodeURIComponent(l.kol), s.rangeKey),
        stop: function (e) { e.stopPropagation(); },
        /* 点 KOL 行：打开他互动最高的一篇，抽屉里可切换其余篇 */
        go: function () { self.setState({ sel: l.top ? l.top.id : null }); }
      };
    });
    var half = Math.ceil(leaderRows.length / 2);
    out.leaderCols = [{ key: 'a', rows: leaderRows.slice(0, half) }, { key: 'b', rows: leaderRows.slice(half) }];
    out.leaderScope = (s.etf === 'ALL' ? '全部产品' : '提及 ' + s.etf + ' 的发帖') + ' · ' + lRows.length + ' 位 KOL';

    /* 抽屉：帖子内容卡 */
    out.selOn = !!p0;
    if (p0) {
      var sec0 = secOf(p0.sector), st0 = displayedPostType(p0), ann0 = self.annNa(p0);
      var etfs0 = mentionedEtfsOf(p0), primary0 = primaryDisplayedEtf(p0);
      var sameKol = etfPosts.filter(function (q) { return q.kol === p0.kol; }).sort(function (a, b) { return b.t - a.t; });
      out.sel = {
        kol: p0.kol, kolTags: p0.tags.split(',').join(' · '), time: p0.time, code: primary0 ? primary0.ticker : '—', name: primary0 ? (primary0.name || 'ETF 名称暂不可用') : '正文未提及 ETF', url: p0.url || null, hasUrl: !!p0.url,
        pbg: primary0 ? (primary0.inPool === false ? 'var(--warning-100)' : rgba(sec0.hue, 0.12)) : 'var(--ink-100)', pfg: primary0 ? (primary0.inPool === false ? 'var(--warning-700)' : sec0.hue) : 'var(--ink-500)',
        type: st0.label, tbg: st0.bg, tfg: st0.fg, pending: self.pending(p0), confidence: conf2(p0.confidence), review: self.review(p0),
        /* `evidenceIdx >= 0` 不能单独判 null：JS 的关系比较会把 null 当 0，`null >= 0`
           是 **true**，于是「判定依据的原句已在下方原文中标出」会挂在一篇根本没标注过的
           帖子上，而下面的原文里一句高亮都没有。三态要分开写。 */
        evidenceNote: p0.evidenceIdx == null ? '判定依据尚未生成'
          : (p0.evidenceIdx >= 0 ? '判定依据的原句已在下方原文中标出' : '本篇无可标注的判定依据句'),
        camps: mentionBadgesOf(p0),
        campNote: '仅按正文明确提及判定；挂载讨论区不作为产品证据',
        hasSwitch: sameKol.length > 1, switchN: String(sameKol.length),
        switch: sameKol.map(function (q) {
          var on = q.id === p0.id, qPrimary = primaryDisplayedEtf(q);
          return {
            key: q.id,
            time: q.time, code: qPrimary ? qPrimary.ticker : '—', type: displayedPostType(q).label,
            fw: on ? 600 : 500,
            fg: on ? 'var(--csop-blue-700)' : 'var(--ink-700)',
            bg: on ? 'var(--csop-blue-50)' : '#fff',
            bc: on ? 'var(--csop-blue-600)' : 'var(--border-2)',
            go: function () { self.setState({ sel: q.id }); }
          };
        }),
        /* 三态：有摘要／查过了确实没有（图片帖）／根本没生成。中间那句是**结论**，
           落到第三种情况上就是替 AI 说了一句它没说过的话。 */
        summary: p0.hasSummary ? p0.summary
          : (ann0 ? '摘要暂不可用：AI 标注尚未生成。' : '暂无摘要：该帖仅含图片或截图，第一期不覆盖图片内容。'),
        /* 正文分句属于标注块（ADR-0017 §4：evidenceIdx 索引的就是这个数组），sql 下整块为
           null —— `null.map` 直接抛。空数组会让原文面板变成一个没有任何提示的空框，所以
           另给一个显式缺失态。 */
        hasText: p0.fullText != null,
        sentences: (p0.fullText || []).map(function (t, i) {
          var hit = i === p0.evidenceIdx;
          return { text: t, bg: hit ? 'var(--warning-100)' : 'transparent', sh: hit ? 'inset 0 -2px 0 var(--warning-600)' : 'none' };
        }),
        mentioned: etfs0.map(function (m) {
          var own = m.ownership === 'own', outside = m.inPool === false;
          return {
            code: m.ticker, name: m.name || 'ETF 名称暂不可用', issuer: m.issuer ? issuerShort(m.issuer) : '其他公司', tag: outside ? '其他公司产品' : (own ? '自家' : '竞品'),
            tbg: own ? 'var(--csop-blue-600)' : (outside ? 'var(--warning-100)' : 'var(--csop-silver-200)'), tfg: own ? '#fff' : (outside ? 'var(--warning-700)' : 'var(--ink-700)')
          };
        }),
        hasMentioned: etfs0.length > 0,
        counts: [
          { label: '点赞', value: num(p0.likes) },
          { label: '评论数', value: num(p0.comments) },
          { label: '转发', value: num(p0.shares) },
          { label: '浏览', value: num(p0.views) }
        ],
        detailHref: hrefWithRange('kol-detail.dc.html?kol=' + encodeURIComponent(p0.kol) + '&post=' + encodeURIComponent(p0.id), s.rangeKey)
      };
    }
    return out;
  }

  /* 类型下拉的内容：顶部 ④ 与表内「类型」是同一份菜单，只是外框不同 */
  typeMenuBody(v, note) {
    return (
      <>
        <div style={s('display:flex;align-items:center;justify-content:space-between;padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>
          <span>{note}</span>
          <span onClick={v.typeAll} style={s('font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>清除选择</span>
        </div>
        {/* 下面八项的篇数只数得出「已标注」的那批；没标注的既不能摊进任一类，也不能
            悄悄消失（各项相加对不上总篇数会被当成 bug），所以在菜单头上明说。 */}
        {v.typeNaNote && (
          <div style={s('padding:6px 12px;border-bottom:1px solid var(--border-1);background:var(--warning-100);font:400 12px/1.5 var(--font-cjk);color:var(--warning-700)')}>{v.typeNaNote}</div>
        )}
        <div style={s('padding:6px 12px 4px;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>内容形式</div>
        {v.typeMenu.map((m) => (
          <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
            <span style={s(`flex:none;width:14px;height:14px;box-sizing:border-box;border:1px solid ${m.boxBc};border-radius:3px;background:${m.boxBg};display:flex;align-items:center;justify-content:center;font:600 10px/1 var(--font-cjk);color:#fff`)}>{m.tick}</span>
            <span style={s(`flex:none;width:68px;padding:1px 8px;box-sizing:border-box;border-radius:var(--radius-small);background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
            <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.def}</span>
            <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 篇</span>
          </div>
        ))}
        <div style={s('padding:8px 12px 4px;border-top:1px solid var(--border-1);background:var(--canvas);font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>操作方向 · 仅操作类帖子（晒单／操作宣言）</div>
        {v.dirMenu.map((m) => (
          <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
            <span style={s(`flex:none;width:14px;height:14px;box-sizing:border-box;border:1px solid ${m.boxBc};border-radius:3px;background:${m.boxBg};display:flex;align-items:center;justify-content:center;font:600 10px/1 var(--font-cjk);color:#fff`)}>{m.tick}</span>
            <span style={s(`flex:none;min-width:68px;padding:1px 8px;box-sizing:border-box;border-radius:var(--radius-small);background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
            <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.def}</span>
            <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 篇</span>
          </div>
        ))}
      </>
    )
  }

  render() {
    const v = this.renderVals()

    return (
      <div data-screen-label="KOL 影响力" style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px')}>

        <Shell vals={v}>
          <div data-shell-filter style={s('display:flex;align-items:center;gap:9px;min-height:44px;box-sizing:border-box;padding:5px 16px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);flex-wrap:wrap')}>
            <DatePresetTabs presets={v.presets} />

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.etfToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.etfBc};border-radius:var(--radius-control);background:${v.etfBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);color:var(--ink-400)')}>ETF</span>
                <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--csop-blue-700)')}>{v.etfLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.etfCaret}</span>
              </div>
              {v.etfOpen && (
                <div onMouseLeave={v.etfClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:380px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>发帖提及的产品（自家 + 竞品）</div>
                  {v.etfMenu.map((m) => (
                    <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                      <span style={s('flex:none;width:46px;font:600 14px/1.3 var(--font-mono);color:var(--ink-900)')}>{m.code}</span>
                      <span style={s(`flex:none;padding:1px 6px;border-radius:var(--radius-pill);background:${m.obg};font:600 11px/1.5 var(--font-cjk);color:${m.ofg}`)}>{m.own}</span>
                      <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                      <span style={s('flex:none;font:500 12px/1.3 var(--font-cjk);color:var(--ink-500)')}>{m.n} 篇</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.kolToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.kolBc};border-radius:var(--radius-control);background:${v.kolBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);color:var(--ink-400)')}>KOL</span>
                <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.kolLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.kolCaret}</span>
              </div>
              {v.kolOpen && (
                <div onMouseLeave={v.kolClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:320px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.kolMenuNote}</div>
                  {v.kolMenu.map((m) => (
                    <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                      <span style={s('flex:1;min-width:0;font:500 13px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                      <span style={s('flex:none;font:500 12px/1.3 var(--font-cjk);color:var(--ink-500)')}>{m.n} 篇</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.postToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.postBc};border-radius:var(--radius-control);background:${v.postBg};cursor:${v.postCursor};opacity:${v.postOp};transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);color:var(--ink-400)')}>帖子</span>
                <span style={s(`font:600 14px/1.4 var(--font-mono);color:${v.postFg}`)}>{v.postLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.postCaret}</span>
              </div>
              {v.postOpen && (
                <div onMouseLeave={v.postClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:360px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  {v.postMenu.map((m) => (
                    <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                      <span style={s('flex:none;font:500 13px/1.4 var(--font-mono);color:var(--ink-700)')}>{m.time}</span>
                      <span style={s('flex:none;font:600 12px/1.4 var(--font-mono);color:var(--csop-blue-700)')}>{m.code}</span>
                      <span style={s('flex:1;min-width:0;text-align:right;font:500 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.typeToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.typeBc};border-radius:var(--radius-control);background:${v.typeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);color:var(--ink-400)')}>类型</span>
                <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.typeLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.typeCaret}</span>
              </div>
              {v.typeOpen && (
                <div onMouseLeave={v.typeClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  {this.typeMenuBody(v, '多选 · 按表格实际展示类型统计 · 选中项之间为“或”')}
                </div>
              )}
            </div>

            {v.hasScope && (
              <div onClick={v.clearScope} style={s('flex:none;display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:var(--radius-pill);background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>恢复全部<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
            )}

            <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;padding:5px 13px;border-radius:var(--radius-pill);background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>{dataSourceBadge(R.DATA_PROVIDER)}</div>
          </div>
        </Shell>

        <div style={s('padding:12px 16px 64px')}>

          <AccountSummaryStrip eyebrow="账号 · KOL 影响力" title="KOL 发帖内容与阵地分布" kpis={v.kpis} />

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:18px')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('font:600 18px/1.3 var(--font-cjk)')}>发帖记录</div>
              <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                <div onClick={v.exportCsv} title={v.exportTip} style={s(`display:flex;align-items:center;gap:6px;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.exportBc};border-radius:var(--radius-control);background:#fff;font:500 13px/1.4 var(--font-cjk);color:${v.exportFg};cursor:${v.exportCursor};opacity:${v.exportOp};white-space:nowrap;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>导出 CSV（{v.postCount} 条）</div>
                <span style={s('width:1px;height:16px;background:var(--border-2);margin:0 4px')}></span>
                <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden')}>
                  {v.sorts.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
            </div>
            <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
              <input type="text" value={v.q} onChange={v.setQ} placeholder="搜 KOL / 产品代码 / 摘要关键词" style={s(`flex:none;width:262px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.qBc};border-radius:var(--radius-control);background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.tTypeToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.tTypeBc};border-radius:var(--radius-control);background:${v.tTypeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>类型</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.typeLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.tTypeCaret}</span>
                </div>
                {v.tTypeOpen && (
                  <div onMouseLeave={v.tTypeClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    {this.typeMenuBody(v, '多选 · 与顶部「④ 类型」同步 · 操作方向优先')}
                  </div>
                )}
              </div>
              <div style={s('flex:none;display:flex;align-items:center;gap:7px')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>提及范围</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff')}>
                  {v.camps.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
              <div onClick={v.pendToggle} style={s(`flex:none;display:flex;align-items:center;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.pendBc};border-radius:var(--radius-pill);background:${v.pendBg};font:500 13px/1.4 var(--font-cjk);color:${v.pendFg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>仅看待确认</div>
              <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:12px')}>
                <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}><span style={s('font:600 14px/1 var(--font-mono);color:var(--ink-800)')}>{v.postCount}</span> 篇</span>
                {v.hasTf && (
                  <div onClick={v.clearTf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:var(--radius-pill);background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                )}
              </div>
            </div>
            <div style={s('max-height:560px;overflow-y:auto')}>
              <table>
                <thead>
                  <tr style={s('background:var(--canvas-alt)')}>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 16px;width:150px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>重点KOL</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:96px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>发帖时间</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:88px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>ETF</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:118px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>类型</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 10px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>AI 摘要</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:96px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>ETF 归属</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:right;padding:10px 6px;width:132px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>赞 / 评 / 转</th>
                    <th data-testid="kol-post-view-header" data-screen-diff-new="kol-view-column" style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:right;padding:10px 6px;width:88px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>浏览量</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:right;padding:10px 16px 10px 6px;width:84px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>原帖</th>
                  </tr>
                </thead>
                <tbody>
                  {v.rows.map((r) => (
                    <tr key={r.key} onClick={r.go} style={s(`background:${r.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <td style={s('padding:11px 16px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <div style={s('font:500 14px/1.4 var(--font-cjk);color:var(--ink-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{r.kol}</div>
                        <div style={s('margin-top:3px;font:400 12px/1.3 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{r.tags}</div>
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;font:500 13px/1.6 var(--font-mono);color:var(--ink-600);border-bottom:1px solid var(--ink-100);white-space:nowrap')}>{r.time}</td>
                      <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <div style={s('display:flex;flex-direction:column;align-items:flex-start;gap:4px')}>
                          {r.etfs.length ? r.etfs.map((etf) => (
                            <span key={etf.ticker} title={etf.name || etf.ticker} style={s(`display:inline-block;padding:2px 7px;border-radius:var(--radius-pill);background:${etf.bg};font:600 12px/1.5 var(--font-mono);color:${etf.fg};white-space:nowrap`)}>{etf.ticker}</span>
                          )) : <span style={s('display:inline-block;padding:2px 7px;border-radius:var(--radius-pill);background:var(--ink-100);font:600 12px/1.5 var(--font-mono);color:var(--ink-500)')}>—</span>}
                        </div>
                        <div style={s('margin-top:4px;font:400 11px/1.3 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px')}>{r.issuer}</div>
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <div style={s('display:flex;flex-direction:column;align-items:flex-start;gap:4px')}>
                          <span style={s(`padding:2px 8px;border-radius:var(--radius-small);background:${r.tbg};font:600 12px/1.6 var(--font-cjk);color:${r.tfg};white-space:nowrap`)}>{r.type}</span>
                          {/* 如实声明（ADR-0019 §2／§4）：标注过的帖子都挂一枚，文案由
                              `review_state` 决定，逐字取自 PRD §3.9。置信度不再跟在徽章
                              后面 —— 它是模型自报的数，不是「有没有人看过」的事实，两件事
                              挂在一枚徽章上会被读成一件。数值仍在右侧抽屉与 CSV 里。 */}
                          {r.review && (
                            <span style={s('padding:0 6px;border-radius:var(--radius-pill);background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500);white-space:nowrap')}>{r.review}</span>
                          )}
                        </div>
                      </td>
                      <td style={s('padding:11px 10px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <div title={r.summary} style={s(`display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:${v.clamp};max-height:${v.clampH}px;overflow:hidden;font:400 14px/22px var(--font-cjk);color:var(--ink-800);text-wrap:pretty`)}>{r.summary}</div>
                        {r.noSummary && (
                          <div style={s('font:400 13px/22px var(--font-cjk);color:var(--ink-400)')}>暂无摘要 · 图片帖，第一期不覆盖图片内容</div>
                        )}
                        {/* 上一行是「查过了，这篇确实没有」；这一行是「还没查」。共用一句会把
                            未标注的帖子全都说成图片帖。 */}
                        {r.summaryNa && (
                          <div style={s('font:400 13px/22px var(--font-cjk);color:var(--ink-400)')}>摘要暂不可用 · AI 标注尚未生成</div>
                        )}
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <span style={s(`display:inline-block;padding:2px 8px;border-radius:var(--radius-pill);background:${r.ownership.bg};font:600 12px/1.5 var(--font-cjk);color:${r.ownership.fg};white-space:nowrap`)}>{r.ownership.label}</span>
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;text-align:right;font:500 13px/1.6 var(--font-mono);color:var(--ink-700);border-bottom:1px solid var(--ink-100);white-space:nowrap')}>{r.likes} / {r.comments} / {r.shares}</td>
                      <td data-testid="kol-post-view-cell" data-screen-diff-new="kol-view-column" style={s('padding:11px 6px;vertical-align:top;text-align:right;font:500 13px/1.6 var(--font-mono);color:var(--ink-700);border-bottom:1px solid var(--ink-100);white-space:nowrap')}>{r.views}</td>
                      <td style={s('padding:11px 16px 11px 6px;vertical-align:top;text-align:right;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                        {r.hasUrl
                          ? <a href={r.url} target="_blank" rel="noopener noreferrer" onClick={r.stop} style={s('display:inline-flex;align-items:center;padding:4px 11px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>原帖 ↗</a>
                          : <span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>链接暂不可用</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {v.hasMoreRows && (
              <div style={s('display:flex;align-items:center;justify-content:center;gap:12px;padding:12px 16px;border-top:1px solid var(--border-1);background:var(--canvas-alt)')}>
                <span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>已显示 {v.shownCount} / {v.postCount} 篇</span>
                <button type="button" onClick={v.loadMoreRows} style={s('padding:6px 14px;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>再看 {v.moreCount} 篇</button>
              </div>
            )}
          </div>

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-card);box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('font:600 18px/1.3 var(--font-cjk)')}>KOL 声量排名</div>
              <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                <div onClick={v.exportLeaderCsv} title={v.leaderExportTip} style={s(`display:flex;align-items:center;gap:6px;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.leaderExportBc};border-radius:var(--radius-control);background:#fff;font:500 13px/1.4 var(--font-cjk);color:${v.leaderExportFg};cursor:${v.leaderExportCursor};opacity:${v.leaderExportOp};white-space:nowrap;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>导出 CSV（{v.leaderCount} 位）</div>
                <span style={s('width:1px;height:16px;background:var(--border-2);margin:0 4px')}></span>
                <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden')}>
                  {v.leaderSorts.map((f) => (
                    <div key={f.label} data-screen-diff-new={f.label === '按浏览量' ? 'kol-view-sort' : undefined} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
            </div>
            <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
              <input type="text" value={v.lq} onChange={v.setLq} placeholder="搜 KOL 名称 / 标签" style={s(`flex:none;width:220px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.lqBc};border-radius:var(--radius-control);background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
              <div style={s('flex:none;display:flex;align-items:center;gap:7px')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>提及范围</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:var(--radius-control);overflow:hidden;background:#fff')}>
                  {v.lCamps.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.lTypeToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.lTypeBc};border-radius:var(--radius-control);background:${v.lTypeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>KOL 类型</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.lTypeLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.lTypeCaret}</span>
                </div>
                {v.lTypeOpen && (
                  <div onMouseLeave={v.lTypeClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:var(--radius-card);box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>单选 · 来自 KOL 名单 · 多标签按任一标签命中</div>
                    {v.lTypeNaNote && (
                      <div style={s('padding:6px 12px;border-bottom:1px solid var(--border-1);background:var(--warning-100);font:400 12px/1.5 var(--font-cjk);color:var(--warning-700)')}>{v.lTypeNaNote}</div>
                    )}
                    {v.lTypeMenu.map((m) => (
                      <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                        <span style={s(`flex:none;min-width:68px;padding:1px 8px;box-sizing:border-box;border-radius:var(--radius-small);background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
                        <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.def}</span>
                        <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 位</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:12px')}>
                <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}><span style={s('font:600 14px/1 var(--font-mono);color:var(--ink-800)')}>{v.leaderCount}</span> 位</span>
                {v.hasLf && (
                  <div onClick={v.clearLf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:var(--radius-pill);background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                )}
              </div>
            </div>
            <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:0 26px;padding:6px 20px 4px')}>
              {v.leaderCols.map((col) => (
                <div key={col.key} style={s('min-width:0')}>
                  <div style={s('display:flex;align-items:center;gap:8px;padding:7px 0 8px;border-bottom:1px solid var(--border-1)')}>
                    <span style={s('flex:none;width:22px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>#</span>
                    <span style={s('flex:1;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>KOL</span>
                    <span style={s('flex:none;width:28px;text-align:right;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>篇</span>
                    <span style={s('flex:none;width:120px;padding-left:8px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>自家 / 其他</span>
                    <span data-screen-diff-new="kol-view-column" style={s('flex:none;width:68px;text-align:right;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>浏览量</span>
                    <span style={s('flex:none;width:52px;text-align:right;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>评论量</span>
                    <span style={s('flex:none;width:116px;padding-left:6px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>KOL 类型</span>
                    <span style={s('flex:none;width:54px')}></span>
                  </div>
                  {col.rows.map((l) => (
                    <div key={l.key} onClick={l.go} style={s(`display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--ink-100);background:${l.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s(`flex:none;width:22px;font:600 13px/1.4 var(--font-mono);color:${l.rankFg}`)}>{l.rank}</span>
                      <div style={s('flex:1;min-width:0;display:flex;align-items:center;gap:6px')}>
                        <span style={s(`flex:none;width:7px;height:7px;border-radius:var(--radius-pill);background:${l.dot}`)}></span>
                        <span style={s(`font:${l.fw} 14px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis`)}>{l.kol}</span>
                      </div>
                      <span style={s('flex:none;width:28px;text-align:right;font:500 13px/1.4 var(--font-mono);color:var(--ink-600)')}>{l.n}</span>
                      <div style={s('flex:none;width:120px;display:flex;align-items:center;gap:6px;padding-left:8px')}>
                        <div style={s('flex:1;height:8px;display:flex;background:var(--ink-100);border-radius:3px;overflow:hidden')}>
                          <div style={s(`width:${l.ownW}%;height:100%;background:var(--csop-blue-600)`)}></div>
                          <div style={s(`width:${l.bothW}%;height:100%;background:var(--csop-blue-300)`)}></div>
                          <div style={s(`width:${l.peerW}%;height:100%;background:var(--csop-silver-400)`)}></div>
                        </div>
                        <span style={s('flex:none;width:44px;text-align:right;font:600 12px/1.4 var(--font-mono);color:var(--ink-700)')}>{l.ownAny}/{l.peerAny}</span>
                      </div>
                      <span data-testid="kol-leader-views" data-screen-diff-new="kol-view-column" style={s('flex:none;width:68px;text-align:right;font:600 13px/1.4 var(--font-mono);color:var(--ink-800)')}>{l.views}</span>
                      <span style={s('flex:none;width:52px;text-align:right;font:600 13px/1.4 var(--font-mono);color:var(--ink-800)')}>{l.eng}</span>
                      <span style={s('flex:none;width:116px;padding-left:6px;box-sizing:border-box;display:flex;gap:3px;flex-wrap:wrap')}>{l.tags.map((tag) => <span key={tag} style={s('display:inline-block;padding:1px 6px;border-radius:var(--radius-small);background:var(--csop-blue-50);font:600 10px/1.5 var(--font-cjk);color:var(--csop-blue-700);white-space:nowrap')}>{tag}</span>)}</span>
                      <DcLink href={l.href} onClick={l.stop} style={s('flex:none;width:54px;box-sizing:border-box;text-align:center;padding:3px 0;border:1px solid var(--border-2);border-radius:var(--radius-control);background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>详情 →</DcLink>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>

        {v.selOn && (
          <>
            <div onClick={v.closeSel} style={s('position:fixed;inset:0;background:rgba(14,42,82,0.55);z-index:60')}></div>
            <div style={s('position:fixed;top:0;right:0;width:640px;height:100vh;background:#fff;border-left:1px solid var(--border-2);box-shadow:-8px 0 28px rgba(14,42,82,0.14);z-index:61;display:flex;flex-direction:column')}>
              <div style={s('flex:none;padding:20px 24px 18px;background:var(--canvas);border-bottom:1px solid var(--border-1)')}>
                <div style={s('display:flex;align-items:flex-start;justify-content:space-between;gap:14px')}>
                  <div style={s('min-width:0')}>
                    <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.16em;color:var(--csop-blue-600);margin-bottom:8px')}>帖子内容卡</div>
                    <div style={s('font:600 19px/1.3 var(--font-cjk);color:var(--ink-900)')}>{v.sel.kol}</div>
                    <div style={s('margin-top:6px;font:400 13px/1.5 var(--font-cjk);color:var(--ink-500)')}>{v.sel.kolTags} · {v.sel.time} HKT</div>
                  </div>
                  <div onClick={v.closeSel} style={s('flex:none;width:30px;height:30px;border:1px solid var(--border-2);border-radius:var(--radius-control);display:flex;align-items:center;justify-content:center;cursor:pointer;font:400 16px/1 var(--font-cjk);color:var(--ink-600);background:#fff')} className={hover('background:var(--csop-blue-50)')}>×</div>
                </div>
                <div style={s('margin-top:14px;display:flex;align-items:center;gap:6px;flex-wrap:wrap')}>
                  <span style={s(`padding:3px 9px;border-radius:var(--radius-pill);background:${v.sel.pbg};font:600 13px/1.5 var(--font-mono);color:${v.sel.pfg}`)}>{v.sel.code}</span>
                  <span style={s('font:400 13px/1.5 var(--font-cjk);color:var(--ink-600);max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{v.sel.name}</span>
                  <span style={s(`padding:3px 9px;border-radius:var(--radius-small);background:${v.sel.tbg};font:600 12px/1.5 var(--font-cjk);color:${v.sel.tfg}`)}>{v.sel.type}</span>
                  {v.sel.review && (
                    <span style={s('padding:2px 7px;border-radius:var(--radius-pill);background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500)')}>{v.sel.review}</span>
                  )}
                  {v.sel.camps.map((c) => (
                    <span key={c.label} style={s(`padding:3px 9px;border-radius:var(--radius-pill);background:${c.bg};font:600 12px/1.5 var(--font-cjk);color:${c.fg}`)}>{c.label}</span>
                  ))}
                </div>
                {v.sel.hasSwitch && (
                  <div style={s('margin-top:14px;padding-top:13px;border-top:1px solid var(--border-1)')}>
                    <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>这位 KOL 在区间内的 {v.sel.switchN} 篇</div>
                    <div style={s('display:flex;flex-wrap:wrap;gap:6px')}>
                      {v.sel.switch.map((w) => (
                        <div key={w.key} onClick={w.go} style={s(`display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid ${w.bc};border-radius:var(--radius-pill);background:${w.bg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                          <span style={s(`font:${w.fw} 13px/1.4 var(--font-mono);color:${w.fg}`)}>{w.time}</span>
                          <span style={s('font:500 12px/1.4 var(--font-mono);color:var(--ink-500)')}>{w.code}</span>
                          <span style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{w.type}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div style={s('flex:1;min-height:0;overflow-y:auto;padding:20px 24px 28px')}>
                <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>AI 摘要</div>
                <div style={s('padding:16px 18px;border-radius:var(--radius-card);background:var(--csop-blue-50);font:500 17px/1.6 var(--font-cjk);color:var(--csop-navy-900);text-wrap:pretty')}>{v.sel.summary}</div>
                <div style={s('margin-top:8px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-400)')}>类型「{v.sel.type}」置信度 {v.sel.confidence} · {v.sel.evidenceNote}</div>

                <div style={s('margin-top:22px;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>原文</div>
                {v.sel.hasText ? (
                  <div style={s('padding:14px 16px;border:1px solid var(--border-1);border-radius:var(--radius-card);background:var(--canvas);font:400 14px/1.9 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>
                    {v.sel.sentences.map((q, i) => <span key={i} style={s(`background:${q.bg};box-shadow:${q.sh};border-radius:2px`)}>{q.text}</span>)}
                  </div>
                ) : (
                  <div style={s('padding:14px 16px;border:1px solid var(--border-1);border-radius:var(--radius-card);background:var(--canvas);font:400 14px/1.9 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>原文暂不可用：正文分句尚未生成。</div>
                )}

                <div style={s('margin-top:22px;display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px')}>
                  <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>正文提及 ETF</div>
                  <div style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>仅展示 ETF；指数、股票及其他产品不计入 · {v.sel.campNote}</div>
                </div>
                <div style={s('border:1px solid var(--border-1);border-radius:var(--radius-card);overflow:hidden')}>
                  {!v.sel.hasMentioned && (
                    <div style={s('padding:12px 14px;background:var(--canvas);font:400 13px/1.5 var(--font-cjk);color:var(--ink-500)')}>正文未识别到 ETF</div>
                  )}
                  {v.sel.mentioned.map((m) => (
                    <div key={m.code} style={s('display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--ink-100);background:#fff')}>
                      <span style={s('flex:none;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{m.code}</span>
                      <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                      <span style={s('flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{m.issuer}</span>
                      <span style={s(`flex:none;padding:2px 8px;border-radius:var(--radius-pill);background:${m.tbg};font:600 12px/1.5 var(--font-cjk);color:${m.tfg}`)}>{m.tag}</span>
                    </div>
                  ))}
                </div>

                <div style={s('margin-top:22px;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>互动计数</div>
                <div style={s('display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border-1);border:1px solid var(--border-1);border-radius:var(--radius-control);overflow:hidden')}>
                  {v.sel.counts.map((c) => (
                    <div key={c.label} style={s('background:#fff;padding:12px 13px')}>
                      <div style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:7px')}>{c.label}</div>
                      <div style={s('font:600 19px/1 var(--font-mono);color:var(--ink-900)')}>{c.value}</div>
                    </div>
                  ))}
                </div>
                <div style={s('margin-top:8px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-400)')}>平台计数字段，取发布后约 24 小时的值；摘要与类型随采集批次每 60 分钟更新。</div>
              </div>

              <div style={s('flex:none;padding:16px 24px;border-top:1px solid var(--border-1);background:var(--canvas);display:flex;align-items:center;gap:12px')}>
                <DcLink href={v.sel.detailHref} style={s('flex:1;display:flex;align-items:center;justify-content:center;gap:8px;padding:11px 16px;border-radius:var(--radius-control);background:var(--csop-blue-600);font:600 14px/1.4 var(--font-cjk);color:#fff;text-decoration:none')} className={hover('background:var(--csop-blue-800);text-decoration:none')}>查看 {v.sel.kol} 的完整详情 →</DcLink>
                {v.sel.hasUrl
                  ? <a href={v.sel.url} target="_blank" rel="noopener noreferrer" style={s('flex:none;display:flex;align-items:center;justify-content:center;padding:11px 16px;border-radius:var(--radius-control);background:#fff;border:1px solid var(--border-2);font:600 14px/1.4 var(--font-cjk);color:var(--ink-700);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>在 Futu 查看原帖 ↗</a>
                  : <span style={s('flex:none;padding:11px 16px;font:500 13px/1.4 var(--font-cjk);color:var(--ink-400)')}>原帖链接暂不可用</span>}
              </div>
            </div>
          </>
        )}
      </div>
    )
  }
}

export default withTransition(KolActivity)
