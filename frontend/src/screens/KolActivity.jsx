/* Port of design/kol-activity.dc.html (KOL 影响力).
   Logic below (state → renderVals) is the design source verbatim; see OfficialActivity
   for the two standing deviations (synchronous RADAR import, `{{ }}` → JSX). */
import React from 'react'
import R from '../data/radar'
import {
  num, conf2, descN, typeStyle, rgba, shell, dirStyle, reviewBadge, needsReview,
  CAMP, POST_TYPES, TYPE_BY_KEY, DIRECTIONS, DIR_BY_KEY,
} from '../lib/view'
import { kolProfile } from '../lib/profile'
import { s, hover, focus } from '../lib/dc'
import Shell from '../components/Shell'
import DcLink from '../components/DcLink'

export default class KolActivity extends React.Component {
  static defaultProps = {
    typeScheme: '双标签',
    lowConfidence: 0.7,
    summaryLines: 2,
    campRule: '挂载标的 ∪ 正文提及',
  }

  constructor(props) {
    super(props);
    this.state = {
      rangeKey: 'd7', sort: 'eng', leaderSort: 'n', sel: null,
      etf: 'ALL', kol: 'ALL', post: 'ALL', types: [], dirs: [],
      etfMenu: false, kolMenu: false, postMenu: false, typeMenu: false,
      /* 表内筛选：发帖记录（q / camp / onlyPending / tTypeMenu）与声量排名（lq / lCamp / lType） */
      q: '', camp: 'ALL', onlyPending: false, tTypeMenu: false,
      lq: '', lCamp: 'ALL', lType: 'ALL', lTypeMenu: false
    };
  }
  data() { return R.kolImpact(this.state.rangeKey); }
  primaryOnly() { return this.props.campRule === '仅挂载标的'; }
  camp(p) { return this.primaryOnly() ? p.campPrimary : p.camp; }
  codesOf(p) { return this.primaryOnly() ? [p.code] : p.mentioned.map(function (m) { return m.code; }); }
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
    if (t.length && t.indexOf(p.postType) < 0) return false;
    if (d.length && !(p.directionPending ? d.indexOf('pending') >= 0 : (p.direction && d.indexOf(p.direction) >= 0))) return false;
    return true;
  }
  /* 导出 CSV：只导当前表格所见（日期范围 × 顶部四级 × 表内筛选 × 当前排序）；UTF-8 带 BOM + CRLF，Excel 直开不乱码；首行为筛选摘要 */
  exportCsv(list, summary, filename) {
    var self = this;
    var esc = function (v) { v = v == null ? '' : String(v); return /[",\r\n]/.test(v) ? '"' + v.split('"').join('""') + '"' : v; };
    var campLabel = function (p) { var c = self.camp(p); return c === 'both' ? '自家+竞品' : (CAMP[c] || CAMP.none).label; };
    var head = ['合作KOL', 'KOL标签', '发帖时间', 'ETF代码', 'ETF名称', '发行商', '内容形式', '操作方向', '类型置信度', '是否待确认', 'AI摘要', '阵营', '提及产品(全部代码)', '赞', '评论数', '转发', '原帖链接'];
    /* 导出的缺失格写「数据暂不可用」，不写空。CSV 里的空单元格在 Excel 里与
       「这里确实没有」长得一模一样，而这份文件会被拿去做判断、甚至再统计一遍。
       页面上分得开的两件事，落到文件里也得分得开。 */
    var na = '数据暂不可用';
    var cell = function (v) { return v == null ? na : v; };
    var lines = [esc('# ' + summary), head.map(esc).join(',')].concat(list.map(function (p) {
      var ann = self.annNa(p), pd = self.pending(p);
      return [p.kol, p.tags.split(',').join(' / '), p.day + ' ' + p.time.slice(6), p.code + '.HK', p.name, p.issuer,
        cell(p.typeLabel), ann ? na : (p.directionPending ? '待确认' : (p.dir ? p.dir.label : '')),
        conf2(p.confidence), pd == null ? na : (pd ? '是' : '否'),
        ann ? na : (p.hasSummary ? p.summary : '（图片帖，无摘要）'), campLabel(p),
        p.mentioned.map(function (m) { return m.code + '.HK'; }).join(' '),
        cell(p.likes), cell(p.comments), cell(p.shares), p.url].map(esc).join(',');
    }));
    var blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
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
    var campOk = function (c, want) { return want === 'ALL' || (want === 'own' ? (c === 'own' || c === 'both') : want === 'competitor' ? (c === 'competitor' || c === 'both') : c === 'both'); };
    var CAMPS = [['ALL', '全部'], ['own', '提自家'], ['competitor', '提竞品'], ['both', '双方都提']];
    var seg = function (cur, key) {
      return CAMPS.map(function (x) {
        var on = cur === x[0], patch = {}; patch[key] = x[0];
        return { label: x[1], go: function () { self.setState(patch); }, fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff' };
      });
    };
    this.LC = this.props.lowConfidence != null ? this.props.lowConfidence : 0.7;
    var clamp = this.props.summaryLines != null ? this.props.summaryLines : 2;
    var dual = this.props.typeScheme !== '合并单标签';
    var range = R.buildRange(s.rangeKey);
    var out = shell('accounts', 'kol', s, function (x) { self.setState(x); });
    var M = this.data();
    var etfPosts = this.postsForEtf(M);
    var sp = this.scoped(M);
    var secOf = function (k) { return R.SECTORS.filter(function (x) { return x.k === k; })[0] || R.SECTORS[0]; };
    var campsOf = function (p) { var c = self.camp(p); return c === 'both' ? [CAMP.own, CAMP.competitor] : [CAMP[c] || CAMP.none]; };
    var issuerShort = function (x) { return x === 'CSOP 南方东英' ? '南方东英' : x; };
    var pct = function (n, d) { return d ? Math.round(n / d * 100) + '%' : '—'; };
    out.clamp = String(clamp); out.clampH = String(clamp * 22);
    out.lcText = this.LC.toFixed(2);
    out.typeRule = R.TYPE_RULE;

    /* ① ETF 菜单：发帖提及的全部产品（自家 + 竞品） */
    var byCode = {};
    M.posts.forEach(function (p) { self.codesOf(p).forEach(function (c) { byCode[c] = (byCode[c] || 0) + 1; }); });
    out.etfLabel = s.etf === 'ALL' ? '全部' : s.etf;
    out.etfCaret = s.etfMenu ? '▲' : '▼';
    out.etfOpen = !!s.etfMenu;
    out.etfBc = (s.etfMenu || s.etf !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.etfBg = s.etfMenu ? 'var(--csop-blue-50)' : '#fff';
    out.etfToggle = function () { self.setState({ etfMenu: !s.etfMenu, kolMenu: false, postMenu: false, typeMenu: false }); };
    out.etfClose = function () { self.setState({ etfMenu: false }); };
    out.etfMenu = [{ k: 'ALL', code: '全部', name: '全部产品', n: M.posts.length, own: '', obg: 'transparent', ofg: 'transparent' }].concat(
      Object.keys(byCode).sort(function (a, b) { return byCode[b] - byCode[a] || (a < b ? -1 : 1); }).map(function (c) {
        var m = R.MASTER[c], own = m && m.ownership === 'own';
        return {
          k: c, code: c, name: m ? (own ? m.name : m.issuer + ' · ' + m.name) : c, n: byCode[c],
          own: own ? '自家' : '竞品', obg: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', ofg: own ? '#fff' : 'var(--ink-700)'
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
      ? kolNames.length + ' 位合作 KOL 在区间内发过帖'
      : '只列提及 ' + s.etf + ' 的 ' + kolNames.length + ' 位';
    out.kolMenu = [{ k: 'ALL', name: '全部合作 KOL', n: etfPosts.length }].concat(
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
      kolPosts.map(function (p) { return { k: p.id, time: p.time, code: p.code, type: typeStyle(p.postType).label }; })).map(function (o) {
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
      if (p.postType == null) { typeBaseNa++; return; }
      typeCounts[p.postType] = (typeCounts[p.postType] || 0) + 1;
      if (p.directionPending) dirCounts.pending = (dirCounts.pending || 0) + 1; else if (p.direction) dirCounts[p.direction] = (dirCounts[p.direction] || 0) + 1;
    });
    out.typeNaNote = typeBaseNa ? '另有 ' + typeBaseNa + ' 篇内容形式尚未标注，不计入下表' : '';
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
    out.scopeText = (s.etf === 'ALL' ? '全部产品' : s.etf + ' ' + (R.MASTER[s.etf] ? R.MASTER[s.etf].name : ''))
      + ' × ' + (s.kol === 'ALL' ? '全部 KOL' : s.kol)
      + (picked.length ? ' × ' + picked.join('、') : '');
    out.closeSel = function () { self.setState({ sel: null }); };

    /* 发帖记录表内筛选：关键词 × 阵营 × 仅待确认，只作用于表格，KPI 仍按上方范围 */
    var qq = norm(s.q);
    var hay = function (p) {
      return norm([p.kol, p.tags, p.code, p.name, p.issuer, p.summary]
        .concat(p.mentioned.map(function (m) { return m.code + ' ' + m.name + ' ' + m.issuer; }), p.fullText || []).join(' '));
    };
    var tbl = sp.filter(function (p) { return (!qq || hay(p).indexOf(qq) >= 0) && campOk(self.camp(p), s.camp) && (!s.onlyPending || self.pending(p)); });
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

    /* KPI：发帖数 / 提及自家 / 提及竞品 / 类型识别（内容形式 × 操作方向） */
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
      { label: '发帖数', value: String(sp.length), unit: '篇', pct: '', pfg: 'transparent', sub: '涉及 ' + Object.keys(spKols).length + ' 位合作 KOL · 最近 ' + M.range.days + ' 天', vfg: 'var(--ink-900)' },
      { label: '提及自家产品', value: String(ownAny), unit: '篇', pct: pct(ownAny, sp.length), pfg: 'var(--csop-blue-700)', sub: '其中 ' + both + ' 篇同时提及竞品 · 按产品池规则匹配', vfg: 'var(--csop-blue-700)' },
      { label: '提及竞品', value: String(peerAny), unit: '篇', pct: pct(peerAny, sp.length), pfg: 'var(--ink-600)', sub: '仅提竞品、未提自家的有 ' + (peerAny - both) + ' 篇', vfg: 'var(--ink-800)' },
      { label: '类型已识别（内容形式 × 操作方向）', value: String(annOk), unit: '篇', pct: '', pfg: 'transparent', sub: typeSub, vfg: 'var(--ink-900)' }
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
      '帖子 ' + (s.post === 'ALL' ? '全部' : s.post), '类型 ' + (picked.length ? picked.join('、') : '全部'), '关键词 ' + (qq || '—'), '阵营 ' + campName,
      '仅待确认 ' + (s.onlyPending ? '是' : '否'), '排序 ' + (s.sort === 'eng' ? '按互动' : '按时间'), '共 ' + list.length + ' 条', '数据更新 ' + M.updated].join(' · ');
    var csvName = 'KOL发帖记录_' + range.from + '_' + range.to + (hasAnyFilter ? '_筛选' : '') + '.csv';
    out.exportCsv = function () { if (list.length) self.exportCsv(list, csvSummary, csvName); };
    out.exportTip = list.length ? '导出当前日期范围与筛选条件下的 ' + list.length + ' 条记录，与表格所见一致 · UTF-8 CSV，Excel 可直接打开' : '当前筛选下没有可导出的记录';
    out.exportBc = list.length ? 'var(--border-2)' : 'var(--border-1)';
    out.exportFg = list.length ? 'var(--csop-blue-600)' : 'var(--ink-400)';
    out.exportCursor = list.length ? 'pointer' : 'not-allowed';
    out.exportOp = list.length ? '1' : '0.6';
    out.rows = list.map(function (p, i) {
      var sec = secOf(p.sector), st = typeStyle(p.postType), dirOn = !!p.hasDir;
      return {
        key: p.id,
        kol: p.kol, tags: p.tags.split(',').join(' · '), time: p.time, code: p.code, issuer: issuerShort(p.issuer),
        pbg: rgba(sec.hue, 0.12), pfg: sec.hue,
        /* 双标签：形式在上、方向在下；合并单标签（Tweaks）：「加仓 · 晒单」一枚 */
        type: dual ? st.label : ((dirOn ? p.dir.label + ' · ' : '') + st.label), tbg: st.bg, tfg: st.fg,
        hasDir: dual && dirOn, dir: dirOn ? p.dir.label : '', dbg: dirOn ? p.dir.bg : 'transparent', dfg: dirOn ? p.dir.fg : 'transparent',
        pending: self.pending(p), conf: conf2(p.confidence), review: self.review(p),
        /* 摘要位有**三**态：有摘要、查过了是图片帖（「暂无内容」侧）、标注还没跑
           （「暂不可用」侧）。原来只有前两态，于是没跑过标注的帖子会被写成
           「暂无摘要 · 图片帖」—— 替一篇可能全是文字的帖子宣布了它只有图片。 */
        summary: p.hasSummary ? p.summary : '',
        noSummary: p.hasSummary === false, summaryNa: self.annNa(p),
        camps: campsOf(p),
        likes: num(p.likes), comments: num(p.comments), shares: num(p.shares),
        url: p.url, stop: function (e) { e.stopPropagation(); },
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
    /* `n`（篇数）永远数得出来；`comments` 是合计，有一篇没采到就整份未知，所以它走
       descN：评论量未知的 KOL 排最后，而不是被 `b.comments - a.comments` 当成 0 排进
       「最不被讨论」那一头 —— 那一头是一个结论，这里要的是「还不知道」。 */
    if (s.leaderSort === 'n') leaders.sort(function (a, b) { return b.n - a.n || descN(a.comments, b.comments); });
    else leaders.sort(function (a, b) { return descN(a.comments, b.comments) || b.n - a.n; });
    out.leaderSorts = [['n', '按篇数'], ['eng', '按评论量']].map(function (x) {
      var on = s.leaderSort === x[0];
      return {
        label: x[1], go: function () { self.setState({ leaderSort: x[0] }); },
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff'
      };
    });
    out.leaderSortNote = s.leaderSort === 'n' ? '按篇数降序' : '按评论量降序';

    /* 排名表内筛选：KOL 名称 / 标签 × 阵营 × 主要类型 */
    var lqq = norm(s.lq);
    var lBase = leaders.filter(function (l) {
      if (lqq && norm(l.kol + ' ' + (l.posts[0] ? l.posts[0].tags : '')).indexOf(lqq) < 0) return false;
      if (s.lCamp === 'own') return l.ownAny > 0;
      if (s.lCamp === 'competitor') return l.peerAny > 0;
      if (s.lCamp === 'both') return l.ownAny > 0 && l.peerAny > 0;
      return true;
    });
    /* 「主要类型」未知的不进任何一格，与顶部④同一条规矩：漏掉的在菜单头上明说，
       不摊进八个类型里冒充计数。 */
    var ltc = {}, lBaseNa = 0;
    lBase.forEach(function (l) { if (l.topType == null) lBaseNa++; else ltc[l.topType] = (ltc[l.topType] || 0) + 1; });
    out.lTypeNaNote = lBaseNa ? '另有 ' + lBaseNa + ' 位的主要类型尚未标注，不计入下表' : '';
    var lRows = lBase.filter(function (l) { return s.lType === 'ALL' || l.topType === s.lType; });
    out.lq = s.lq; out.lqBc = lqq ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.setLq = function (e) { self.setState({ lq: e.target.value }); };
    out.lCamps = seg(s.lCamp, 'lCamp');
    out.lTypeLabel = s.lType === 'ALL' ? '全部' : TYPE_BY_KEY[s.lType].label;
    out.lTypeOpen = !!s.lTypeMenu; out.lTypeCaret = s.lTypeMenu ? '▲' : '▼';
    out.lTypeBc = (s.lTypeMenu || s.lType !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.lTypeBg = s.lTypeMenu ? 'var(--csop-blue-50)' : '#fff';
    out.lTypeToggle = function () { self.setState({ lTypeMenu: !s.lTypeMenu, tTypeMenu: false, etfMenu: false, kolMenu: false, postMenu: false, typeMenu: false }); };
    out.lTypeClose = function () { self.setState({ lTypeMenu: false }); };
    out.lTypeMenu = [{ k: 'ALL', label: '全部类型', def: '', n: lBase.length, tbg: 'transparent', tfg: 'var(--ink-800)' }].concat(
      POST_TYPES.map(function (t) { var st = typeStyle(t.k); return { k: t.k, label: t.label, def: t.def, n: ltc[t.k] || 0, tbg: st.bg, tfg: st.fg }; })).map(function (o) {
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

    var mxN = Math.max.apply(null, lRows.map(function (l) { return l.n; }).concat([1]));
    var p0 = this.selPost();
    var selKol = p0 ? p0.kol : null;
    var leaderRows = lRows.map(function (l, i) {
      var on = s.kol === l.kol || selKol === l.kol, st = typeStyle(l.topType);
      return {
        key: l.kol,
        rank: String(i + 1), rankFg: i < 3 ? 'var(--csop-blue-700)' : 'var(--ink-400)',
        kol: l.kol, n: String(l.n),
        ownW: (l.own / mxN * 100).toFixed(1), bothW: (l.both / mxN * 100).toFixed(1), peerW: (l.peer / mxN * 100).toFixed(1),
        ownAny: String(l.ownAny), peerAny: String(l.peerAny),
        eng: num(l.comments),
        type: st.label, tbg: st.bg, tfg: st.fg,
        fw: on ? 600 : 500,
        dot: on ? 'var(--csop-blue-600)' : 'var(--csop-silver-400)',
        bg: on ? 'var(--csop-blue-50)' : 'transparent',
        href: 'kol-detail.dc.html?kol=' + encodeURIComponent(l.kol) + '&range=' + s.rangeKey,
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
      var sec0 = secOf(p0.sector), st0 = typeStyle(p0.postType), ann0 = self.annNa(p0);
      var sameKol = etfPosts.filter(function (q) { return q.kol === p0.kol; }).sort(function (a, b) { return b.t - a.t; });
      out.sel = {
        kol: p0.kol, kolTags: p0.tags.split(',').join(' · '), time: p0.time, code: p0.code, name: p0.name, url: p0.url,
        pbg: rgba(sec0.hue, 0.12), pfg: sec0.hue,
        type: dual ? st0.label : ((p0.hasDir ? p0.dir.label + ' · ' : '') + st0.label), tbg: st0.bg, tfg: st0.fg, pending: self.pending(p0), confidence: conf2(p0.confidence), review: self.review(p0),
        hasDir: dual && !!p0.hasDir, dir: p0.hasDir ? p0.dir.label : '', dbg: p0.hasDir ? p0.dir.bg : 'transparent', dfg: p0.hasDir ? p0.dir.fg : 'transparent',
        /* `evidenceIdx >= 0` 不能单独判 null：JS 的关系比较会把 null 当 0，`null >= 0`
           是 **true**，于是「判定依据的原句已在下方原文中标出」会挂在一篇根本没标注过的
           帖子上，而下面的原文里一句高亮都没有。三态要分开写。 */
        evidenceNote: p0.evidenceIdx == null ? '判定依据尚未生成'
          : (p0.evidenceIdx >= 0 ? '判定依据的原句已在下方原文中标出' : '本篇无可标注的判定依据句'),
        camps: campsOf(p0),
        campNote: self.primaryOnly() ? '阵营按挂载标的判定（当前口径）' : '阵营按挂载标的 ∪ 正文提及判定',
        hasSwitch: sameKol.length > 1, switchN: String(sameKol.length),
        switch: sameKol.map(function (q) {
          var on = q.id === p0.id;
          return {
            key: q.id,
            time: q.time, code: q.code, type: typeStyle(q.postType).label,
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
        mentioned: p0.mentioned.map(function (m) {
          var own = m.ownership === 'own';
          return {
            code: m.code + '.HK', name: m.name, issuer: issuerShort(m.issuer), tag: own ? '自家' : '竞品',
            tbg: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', tfg: own ? '#fff' : 'var(--ink-700)'
          };
        }),
        counts: [
          { label: '点赞', value: num(p0.likes) },
          { label: '评论数', value: num(p0.comments) },
          { label: '转发', value: num(p0.shares) },
          { label: '浏览', value: num(p0.views) }
        ],
        detailHref: 'kol-detail.dc.html?kol=' + encodeURIComponent(p0.kol) + '&post=' + encodeURIComponent(p0.id) + '&range=' + s.rangeKey
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
            <span style={s(`flex:none;width:68px;padding:1px 8px;box-sizing:border-box;border-radius:4px;background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
            <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.def}</span>
            <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 篇</span>
          </div>
        ))}
        <div style={s('padding:8px 12px 4px;border-top:1px solid var(--border-1);background:var(--canvas);font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>操作方向 · 仅操作类帖子（晒单／操作宣言）</div>
        {v.dirMenu.map((m) => (
          <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
            <span style={s(`flex:none;width:14px;height:14px;box-sizing:border-box;border:1px solid ${m.boxBc};border-radius:3px;background:${m.boxBg};display:flex;align-items:center;justify-content:center;font:600 10px/1 var(--font-cjk);color:#fff`)}>{m.tick}</span>
            <span style={s(`flex:none;min-width:68px;padding:1px 8px;box-sizing:border-box;border-radius:4px;background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
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
          <div style={s('display:flex;align-items:center;gap:12px;min-height:48px;padding:7px 24px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);flex-wrap:wrap')}>
            <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
              {v.presets.map((p) => (
                <div key={p.label} onClick={p.go} style={s(`padding:6px 14px;font:${p.fw} 14px/1.4 var(--font-cjk);color:${p.fg};background:${p.bg};cursor:pointer;border-right:1px solid var(--border-1);transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>{p.label}</div>
              ))}
            </div>

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.etfToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.etfBc};border-radius:6px;background:${v.etfBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>① ETF</span>
                <span style={s('font:600 14px/1.4 var(--font-mono);color:var(--csop-blue-700)')}>{v.etfLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.etfCaret}</span>
              </div>
              {v.etfOpen && (
                <div onMouseLeave={v.etfClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:380px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>发帖提及的产品（自家 + 竞品）</div>
                  {v.etfMenu.map((m) => (
                    <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                      <span style={s('flex:none;width:46px;font:600 14px/1.3 var(--font-mono);color:var(--ink-900)')}>{m.code}</span>
                      <span style={s(`flex:none;padding:1px 6px;border-radius:9999px;background:${m.obg};font:600 11px/1.5 var(--font-cjk);color:${m.ofg}`)}>{m.own}</span>
                      <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                      <span style={s('flex:none;font:500 12px/1.3 var(--font-cjk);color:var(--ink-500)')}>{m.n} 篇</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={s('position:relative;flex:none')}>
              <div onClick={v.kolToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.kolBc};border-radius:6px;background:${v.kolBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>② KOL</span>
                <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.kolLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.kolCaret}</span>
              </div>
              {v.kolOpen && (
                <div onMouseLeave={v.kolClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:320px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
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
              <div onClick={v.postToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.postBc};border-radius:6px;background:${v.postBg};cursor:${v.postCursor};opacity:${v.postOp};transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>③ 帖子</span>
                <span style={s(`font:600 14px/1.4 var(--font-mono);color:${v.postFg}`)}>{v.postLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.postCaret}</span>
              </div>
              {v.postOpen && (
                <div onMouseLeave={v.postClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:360px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
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
              <div onClick={v.typeToggle} style={s(`display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid ${v.typeBc};border-radius:6px;background:${v.typeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>④ 类型</span>
                <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.typeLabel}</span>
                <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.typeCaret}</span>
              </div>
              {v.typeOpen && (
                <div onMouseLeave={v.typeClose} style={s('position:absolute;left:0;top:38px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                  {this.typeMenuBody(v, '多选 · AI 判定的帖子类型 · 内容形式与操作方向可跨组选（交集）')}
                </div>
              )}
            </div>

            {v.hasScope && (
              <div onClick={v.clearScope} style={s('flex:none;display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>恢复全部<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
            )}

            <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;padding:5px 13px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>演示数据</div>
          </div>
        </Shell>

        <div style={s('padding:26px 24px 64px')}>

          <div style={s('display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding-bottom:20px;margin-bottom:22px;border-bottom:1px solid var(--border-2)')}>
            <div>
              <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.18em;color:var(--csop-blue-600);margin-bottom:10px')}>账号 · KOL 影响力</div>
              <div style={s('font:600 28px/1.2 var(--font-cjk);letter-spacing:-0.015em')}>KOL 发帖内容与阵地分布</div>
            </div>
            <DcLink href="sector-overview.dc.html" style={s('flex:none;display:flex;align-items:center;padding:8px 15px;border:1px solid var(--border-2);border-radius:9999px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:var(--ink-700);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>← 返回板块总览</DcLink>
          </div>

          <div style={s('display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px')}>
            {v.kpis.map((k) => (
              <div key={k.label} style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);padding:16px 17px')}>
                <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:11px')}>{k.label}</div>
                <div style={s('display:flex;align-items:baseline;gap:6px')}>
                  <span style={s(`font:600 30px/1 var(--font-cjk);letter-spacing:-0.02em;color:${k.vfg}`)}>{k.value}</span>
                  <span style={s('font:500 14px/1 var(--font-cjk);color:var(--ink-500)')}>{k.unit}</span>
                  <span style={s(`margin-left:auto;font:600 15px/1 var(--font-mono);color:${k.pfg}`)}>{k.pct}</span>
                </div>
                <div style={s('margin-top:9px;font:400 13px/1.5 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{k.sub}</div>
              </div>
            ))}
          </div>

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:18px')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('font:600 18px/1.3 var(--font-cjk)')}>发帖记录</div>
              <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                <div onClick={v.exportCsv} title={v.exportTip} style={s(`display:flex;align-items:center;gap:6px;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.exportBc};border-radius:6px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:${v.exportFg};cursor:${v.exportCursor};opacity:${v.exportOp};white-space:nowrap;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>导出 CSV（{v.postCount} 条）</div>
                <span style={s('width:1px;height:16px;background:var(--border-2);margin:0 4px')}></span>
                <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden')}>
                  {v.sorts.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
            </div>
            <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
              <input type="text" value={v.q} onChange={v.setQ} placeholder="搜 KOL / 产品代码 / 摘要关键词" style={s(`flex:none;width:262px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.qBc};border-radius:6px;background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.tTypeToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.tTypeBc};border-radius:6px;background:${v.tTypeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>类型</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.typeLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.tTypeCaret}</span>
                </div>
                {v.tTypeOpen && (
                  <div onMouseLeave={v.tTypeClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    {this.typeMenuBody(v, '多选 · 与顶部「④ 类型」同步 · 内容形式 × 操作方向')}
                  </div>
                )}
              </div>
              <div style={s('flex:none;display:flex;align-items:center;gap:7px')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>阵营</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
                  {v.camps.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
              <div onClick={v.pendToggle} style={s(`flex:none;display:flex;align-items:center;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.pendBc};border-radius:9999px;background:${v.pendBg};font:500 13px/1.4 var(--font-cjk);color:${v.pendFg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>仅看待确认</div>
              <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:12px')}>
                <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}><span style={s('font:600 14px/1 var(--font-mono);color:var(--ink-800)')}>{v.postCount}</span> 篇</span>
                {v.hasTf && (
                  <div onClick={v.clearTf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                )}
              </div>
            </div>
            <div style={s('max-height:560px;overflow-y:auto')}>
              <table>
                <thead>
                  <tr style={s('background:var(--canvas-alt)')}>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 16px;width:150px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>合作 KOL</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:96px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>发帖时间</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:88px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>ETF</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:118px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>类型</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 10px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>AI 摘要</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 6px;width:96px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>阵营</th>
                    <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:right;padding:10px 6px;width:132px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>赞 / 评 / 转</th>
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
                        <span style={s(`display:inline-block;padding:2px 7px;border-radius:9999px;background:${r.pbg};font:600 12px/1.5 var(--font-mono);color:${r.pfg}`)}>{r.code}</span>
                        <div style={s('margin-top:4px;font:400 11px/1.3 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px')}>{r.issuer}</div>
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                        <div style={s('display:flex;flex-direction:column;align-items:flex-start;gap:4px')}>
                          <span style={s(`padding:2px 8px;border-radius:4px;background:${r.tbg};font:600 12px/1.6 var(--font-cjk);color:${r.tfg};white-space:nowrap`)}>{r.type}</span>
                          {r.hasDir && (
                            <span title="操作方向 · AI 判定" style={s(`padding:2px 8px;border-radius:4px;background:${r.dbg};font:600 12px/1.6 var(--font-cjk);color:${r.dfg};white-space:nowrap`)}>{r.dir}</span>
                          )}
                          {/* 如实声明（ADR-0019 §2／§4）：标注过的帖子都挂一枚，文案由
                              `review_state` 决定，逐字取自 PRD §3.9。置信度不再跟在徽章
                              后面 —— 它是模型自报的数，不是「有没有人看过」的事实，两件事
                              挂在一枚徽章上会被读成一件。数值仍在右侧抽屉与 CSV 里。 */}
                          {r.review && (
                            <span style={s('padding:0 6px;border-radius:9999px;background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500);white-space:nowrap')}>{r.review}</span>
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
                        <div style={s('display:flex;flex-wrap:wrap;gap:3px')}>
                          {r.camps.map((c) => (
                            <span key={c.label} style={s(`padding:2px 8px;border-radius:9999px;background:${c.bg};font:600 12px/1.5 var(--font-cjk);color:${c.fg};white-space:nowrap`)}>{c.label}</span>
                          ))}
                        </div>
                      </td>
                      <td style={s('padding:11px 6px;vertical-align:top;text-align:right;font:500 13px/1.6 var(--font-mono);color:var(--ink-700);border-bottom:1px solid var(--ink-100);white-space:nowrap')}>{r.likes} / {r.comments} / {r.shares}</td>
                      <td style={s('padding:11px 16px 11px 6px;vertical-align:top;text-align:right;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                        <a href={r.url} target="_blank" rel="noopener" onClick={r.stop} style={s('display:inline-flex;align-items:center;padding:4px 11px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>原帖 ↗</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={s('padding:14px 20px;border-top:1px solid var(--border-1);font:400 13px/1.7 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>「阵营」按帖子挂载的标的 ∪ 正文出现的产品代码 / 名称，对照客户维护的产品池（61 自家 + 59 竞品）规则匹配，不经模型判断；同时提及双方的帖子两个标签都挂。「类型」由 AI 判定，置信度低于 {v.lcText} 标「待确认」。{v.typeRule} 赞 / 评 / 转为平台计数字段，取发布后约 24 小时的值。「导出 CSV」只导出当前日期范围与筛选条件下的记录，首行为筛选摘要。</div>
          </div>

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('font:600 18px/1.3 var(--font-cjk)')}>KOL 声量排名</div>
              <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden')}>
                  {v.leaderSorts.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
            </div>
            <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
              <input type="text" value={v.lq} onChange={v.setLq} placeholder="搜 KOL 名称 / 标签" style={s(`flex:none;width:220px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.lqBc};border-radius:6px;background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
              <div style={s('flex:none;display:flex;align-items:center;gap:7px')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>阵营</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
                  {v.lCamps.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.lTypeToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.lTypeBc};border-radius:6px;background:${v.lTypeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>主要类型</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.lTypeLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.lTypeCaret}</span>
                </div>
                {v.lTypeOpen && (
                  <div onMouseLeave={v.lTypeClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>单选 · 该 KOL 区间内最多的一类</div>
                    {v.lTypeNaNote && (
                      <div style={s('padding:6px 12px;border-bottom:1px solid var(--border-1);background:var(--warning-100);font:400 12px/1.5 var(--font-cjk);color:var(--warning-700)')}>{v.lTypeNaNote}</div>
                    )}
                    {v.lTypeMenu.map((m) => (
                      <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                        <span style={s(`flex:none;min-width:68px;padding:1px 8px;box-sizing:border-box;border-radius:4px;background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
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
                  <div onClick={v.clearLf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                )}
              </div>
            </div>
            <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:0 26px;padding:6px 20px 4px')}>
              {v.leaderCols.map((col) => (
                <div key={col.key} style={s('min-width:0')}>
                  <div style={s('display:flex;align-items:center;gap:10px;padding:7px 0 8px;border-bottom:1px solid var(--border-1)')}>
                    <span style={s('flex:none;width:22px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>#</span>
                    <span style={s('flex:1;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>KOL</span>
                    <span style={s('flex:none;width:28px;text-align:right;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>篇</span>
                    <span style={s('flex:none;width:150px;padding-left:12px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>自家 / 竞品</span>
                    <span style={s('flex:none;width:62px;text-align:right;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>评论量</span>
                    <span style={s('flex:none;width:76px;padding-left:10px;font:600 14px/1.4 var(--font-cjk);color:var(--ink-700)')}>主要类型</span>
                    <span style={s('flex:none;width:58px')}></span>
                  </div>
                  {col.rows.map((l) => (
                    <div key={l.key} onClick={l.go} style={s(`display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--ink-100);background:${l.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                      <span style={s(`flex:none;width:22px;font:600 13px/1.4 var(--font-mono);color:${l.rankFg}`)}>{l.rank}</span>
                      <div style={s('flex:1;min-width:0;display:flex;align-items:center;gap:6px')}>
                        <span style={s(`flex:none;width:7px;height:7px;border-radius:9999px;background:${l.dot}`)}></span>
                        <span style={s(`font:${l.fw} 14px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis`)}>{l.kol}</span>
                      </div>
                      <span style={s('flex:none;width:28px;text-align:right;font:500 13px/1.4 var(--font-mono);color:var(--ink-600)')}>{l.n}</span>
                      <div style={s('flex:none;width:150px;display:flex;align-items:center;gap:8px;padding-left:12px')}>
                        <div style={s('flex:1;height:8px;display:flex;background:var(--ink-100);border-radius:3px;overflow:hidden')}>
                          <div style={s(`width:${l.ownW}%;height:100%;background:var(--csop-blue-600)`)}></div>
                          <div style={s(`width:${l.bothW}%;height:100%;background:var(--csop-blue-300)`)}></div>
                          <div style={s(`width:${l.peerW}%;height:100%;background:var(--csop-silver-400)`)}></div>
                        </div>
                        <span style={s('flex:none;width:44px;text-align:right;font:600 12px/1.4 var(--font-mono);color:var(--ink-700)')}>{l.ownAny}/{l.peerAny}</span>
                      </div>
                      <span style={s('flex:none;width:62px;text-align:right;font:600 13px/1.4 var(--font-mono);color:var(--ink-800)')}>{l.eng}</span>
                      <span style={s('flex:none;width:76px;padding-left:10px;box-sizing:border-box')}><span style={s(`display:inline-block;padding:1px 7px;border-radius:4px;background:${l.tbg};font:600 11px/1.6 var(--font-cjk);color:${l.tfg};white-space:nowrap`)}>{l.type}</span></span>
                      <DcLink href={l.href} onClick={l.stop} style={s('flex:none;width:58px;box-sizing:border-box;text-align:center;padding:3px 0;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>详情 →</DcLink>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div style={s('padding:14px 20px;border-top:1px solid var(--border-1);font:400 13px/1.7 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>「自家 / 竞品」是该 KOL 区间内提及自家产品与提及竞品的篇数，深蓝＝只提自家、浅蓝＝双方都提、灰＝只提竞品；同时提及双方的帖子两边都计入，所以两数之和可以大于篇数。「主要类型」取该 KOL 区间内最多的一类。</div>
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
                  <div onClick={v.closeSel} style={s('flex:none;width:30px;height:30px;border:1px solid var(--border-2);border-radius:6px;display:flex;align-items:center;justify-content:center;cursor:pointer;font:400 16px/1 var(--font-cjk);color:var(--ink-600);background:#fff')} className={hover('background:var(--csop-blue-50)')}>×</div>
                </div>
                <div style={s('margin-top:14px;display:flex;align-items:center;gap:6px;flex-wrap:wrap')}>
                  <span style={s(`padding:3px 9px;border-radius:9999px;background:${v.sel.pbg};font:600 13px/1.5 var(--font-mono);color:${v.sel.pfg}`)}>{v.sel.code}</span>
                  <span style={s('font:400 13px/1.5 var(--font-cjk);color:var(--ink-600);max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{v.sel.name}</span>
                  <span style={s(`padding:3px 9px;border-radius:4px;background:${v.sel.tbg};font:600 12px/1.5 var(--font-cjk);color:${v.sel.tfg}`)}>{v.sel.type}</span>
                  {v.sel.hasDir && (
                    <span title="操作方向 · AI 判定" style={s(`padding:3px 9px;border-radius:4px;background:${v.sel.dbg};font:600 12px/1.5 var(--font-cjk);color:${v.sel.dfg}`)}>{v.sel.dir}</span>
                  )}
                  {v.sel.review && (
                    <span style={s('padding:2px 7px;border-radius:9999px;background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500)')}>{v.sel.review}</span>
                  )}
                  {v.sel.camps.map((c) => (
                    <span key={c.label} style={s(`padding:3px 9px;border-radius:9999px;background:${c.bg};font:600 12px/1.5 var(--font-cjk);color:${c.fg}`)}>{c.label}</span>
                  ))}
                </div>
                {v.sel.hasSwitch && (
                  <div style={s('margin-top:14px;padding-top:13px;border-top:1px solid var(--border-1)')}>
                    <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>这位 KOL 在区间内的 {v.sel.switchN} 篇</div>
                    <div style={s('display:flex;flex-wrap:wrap;gap:6px')}>
                      {v.sel.switch.map((w) => (
                        <div key={w.key} onClick={w.go} style={s(`display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid ${w.bc};border-radius:9999px;background:${w.bg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
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
                <div style={s('padding:16px 18px;border-radius:8px;background:var(--csop-blue-50);font:500 17px/1.6 var(--font-cjk);color:var(--csop-navy-900);text-wrap:pretty')}>{v.sel.summary}</div>
                <div style={s('margin-top:8px;font:400 12px/1.6 var(--font-cjk);color:var(--ink-400)')}>类型「{v.sel.type}」置信度 {v.sel.confidence} · {v.sel.evidenceNote}</div>

                <div style={s('margin-top:22px;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>原文</div>
                {v.sel.hasText ? (
                  <div style={s('padding:14px 16px;border:1px solid var(--border-1);border-radius:8px;background:var(--canvas);font:400 14px/1.9 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>
                    {v.sel.sentences.map((q, i) => <span key={i} style={s(`background:${q.bg};box-shadow:${q.sh};border-radius:2px`)}>{q.text}</span>)}
                  </div>
                ) : (
                  <div style={s('padding:14px 16px;border:1px solid var(--border-1);border-radius:8px;background:var(--canvas);font:400 14px/1.9 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>原文暂不可用：正文分句尚未生成。</div>
                )}

                <div style={s('margin-top:22px;display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px')}>
                  <div style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>提及产品</div>
                  <div style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{v.sel.campNote}</div>
                </div>
                <div style={s('border:1px solid var(--border-1);border-radius:8px;overflow:hidden')}>
                  {v.sel.mentioned.map((m) => (
                    <div key={m.code} style={s('display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--ink-100);background:#fff')}>
                      <span style={s('flex:none;font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{m.code}</span>
                      <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                      <span style={s('flex:none;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>{m.issuer}</span>
                      <span style={s(`flex:none;padding:2px 8px;border-radius:9999px;background:${m.tbg};font:600 12px/1.5 var(--font-cjk);color:${m.tfg}`)}>{m.tag}</span>
                    </div>
                  ))}
                </div>

                <div style={s('margin-top:22px;font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400);margin-bottom:8px')}>互动计数</div>
                <div style={s('display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border-1);border:1px solid var(--border-1);border-radius:6px;overflow:hidden')}>
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
                <DcLink href={v.sel.detailHref} style={s('flex:1;display:flex;align-items:center;justify-content:center;gap:8px;padding:11px 16px;border-radius:6px;background:var(--csop-blue-600);font:600 14px/1.4 var(--font-cjk);color:#fff;text-decoration:none')} className={hover('background:var(--csop-blue-800);text-decoration:none')}>查看 {v.sel.kol} 的完整详情 →</DcLink>
                <a href={v.sel.url} target="_blank" rel="noopener" style={s('flex:none;display:flex;align-items:center;justify-content:center;padding:11px 16px;border-radius:6px;background:#fff;border:1px solid var(--border-2);font:600 14px/1.4 var(--font-cjk);color:var(--ink-700);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>在 Futu 查看原帖 ↗</a>
              </div>
            </div>
          </>
        )}
      </div>
    )
  }
}
