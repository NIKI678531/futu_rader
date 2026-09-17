/* Port of design/sector-overview.dc.html (板块总览).

   The second-largest screen after 产品监控, so the template is split into the section
   components alongside this file; `renderVals()` stays whole because the sections share
   derived values and the design computes them in one pass.

   `state`, the helpers, the lifecycle and `renderVals()` are the design source verbatim,
   with the deviations the move off the dc runtime forces:
     - `window.RADAR` is a synchronous import, so componentDidMount no longer polls for
       the runtime's async <script> and `renderVals()` drops its `if (!R) return {}` guard.
     - `{{ }}` holes became JSX; `style="…"` strings are parsed by `s()` and `style-hover`
       by `hover()`.
     - `key` fields were added to the treemap tiles, theme rows and risk rows: JSX needs a
       key per list item and the dc runtime did not.
     - 加载态由真实取数驱动（withTransition.jsx）：go() 里那个 520ms 的假 loading 定时器
       删掉，`loading`／`bodyOpacity` 改读 transition 的 isPending；切区间／开抽屉时旧内容
       保持可见并变淡。构造函数里把本屏已知端点一次性预取（radar.js `urlsFor`）。 */
import React from 'react'
import R, { prefetchScreen } from '../../data/radar'
import { shortName, navGroups, num, numRaw, stamp, naBox, aiValidationNote, heatLowerBoundNote, staleSuffix, rowsStale, STALE_TITLE } from '../../lib/view'
import { s } from '../../lib/dc'
import Shell from '../../components/Shell'
import withTransition, { split } from '../../components/withTransition'
import FilterBar from './FilterBar'
import Notes from './Notes'
import Kpis from './Kpis'
import Board from './Board'
import Tooltip from './Tooltip'
import Drawer from './Drawer'

/* 热度为 null 时，说清楚缺的是哪一项。

   真库上这不是个别现象：约 0.09% 的源 `raw_json` 被 TEXT 列截断，那些帖子的转发数
   取不到，于是整只产品的转发与热度都是未知（`worker/jobs/etl.py` 模块头第 4 条）。
   d30 区间下有 8 只产品落在这里，其中一只有四万六千条评论 —— 榜单上只给一句
   「数据暂不可用」，看的人会以为是系统坏了，而事实是这一项从源头就没采到。

   读的是字段（`o.shares == null`），不是在前端重算热度公式（铁律 1）：后端已经把
   「转发数未知」当作 `shares: null` 发下来了，这里只是把它念出来。 */
function heatWhy(o) {
  return o.shares == null
    ? '讨论热度暂不可用：这只产品的转发数未采到（源数据被截断），公式里缺了一项，不以 0 代替。'
    : '讨论热度暂不可用。';
}

class SectorOverview extends React.Component {
  state = {
    sel: null, sector: 'all', rangeKey: 'd7', notes: false,
    sort: 'comments', heatMode: 'net', scope: 'all', view: 'sector', q: '', listAll: false,
    onlyNew: false, onlyNeg: false, onlyRisk: false, scrollRisk: false,
    secOpen: {}, secAll: {}, flatAll: false, tip: null, bucket: null
  };
  constructor(props) {
    super(props);
    /* 首次 render 之前把本屏端点并行发出去，理由见 productMonitor 同处。 */
    prefetchScreen('sector', { rangeKey: this.state.rangeKey });
  }
  heatRef = React.createRef();
  drawerBodyRef = React.createRef();
  riskRef = React.createRef();

  /* 板块、范围、搜索与开关只改变可见范围；排名与色阶标尺始终来自全市场 */
  visibleFor(st) {
    var P = R.pool(st.rangeKey);
    var q = (st.q || '').trim().toLowerCase();
    return P.list.filter(function (o) {
      if (st.scope === 'own' && o.ownership !== 'own') return false;
      if (st.scope === 'peer' && o.ownership !== 'peer') return false;
      if (st.sector !== 'all' && o.sector !== st.sector) return false;
      if (st.onlyNew && !o.isNew) return false;
      /* `alerts[code]` 是关注程度为高的负面类别数：没标过 null、标过 0 或正数。筛选按 `> 0`
         判：null 与 0 都不算「有舆情」（没查过不能报警），但两者在舆情列上要分开渲染（rows 里）。 */
      if (st.onlyNeg && !(P.alerts[o.code] > 0)) return false;
      if (st.onlyRisk && !(P.complianceCount[o.code] > 0)) return false;
      if (q && o.code.toLowerCase().indexOf(q) < 0 && o.name.toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }

  /* 榜单行与热力图色块共用同一张悬浮卡；榜单行跟随光标横向定位，色块居中于自身 */
  productHover(o, hr, poolN, atMouse) {
    return (e) => {
      var t = this.state.tip;
      if (t && t.code === o.code && t.i === -1) return;
      var r = e.currentTarget.getBoundingClientRect();
      var rc = R.pool(this.state.rangeKey).complianceCount[o.code];
      this.setState({
        tip: {
          code: o.code, i: -1,
          x: atMouse ? e.clientX : r.left + r.width / 2,
          y: r.top - 8, title: o.code + ' · ' + o.name,
          rows: [
            { k: '评论量', v: num(o.comments), fg: '#fff' },
            /* `heatUnknownPosts > 0` 时热度是下限，悬浮卡里说出来；demo 下没有这个键，一个字不多。 */
            { k: '讨论热度', v: num(o.discussionHeat) + ' · 全市场第 ' + hr + ' ／ ' + poolN + ' 名' + heatLowerBoundNote(o.heatUnknownPosts), fg: '#fff' },
            { k: '点赞 ／ 转发', v: num(o.likes) + ' ／ ' + num(o.shares), fg: 'rgba(255,255,255,0.88)' },
            /* attitude 整块可能为 null（AI 标注未建，ADR-0017）。三态同出一块标注，
               要缺一起缺，所以整行一句长文案，不写三遍。 */
            { k: '积极 ／ 消极 ／ 中性', v: o.attitude == null ? '数据暂不可用' : o.attitude.positive + ' / ' + o.attitude.negative + ' / ' + o.attitude.neutral, fg: 'rgba(255,255,255,0.88)' },
            { k: '情绪净值', v: this.netText(o), fg: 'rgba(255,255,255,0.88)' }
          ].concat(rc > 0 ? [{ k: '需合规关注', v: rc + ' 条 · AI 识别待确认', fg: '#F3A6A6' }] : [])
        }
      });
    };
  }
  netText(o) {
    var a = o.attitude;
    /* 没这块标注 ≠ 样本不足。前者是没算过，后者是算过但样本太少 —— 六态里两态。 */
    if (a == null) return '数据暂不可用';
    var v = a.positive + a.negative;
    if (!a.sampleSufficient || !v) return '样本不足';
    var n = (a.positive - a.negative) / v * 100;
    return (n > 0 ? '+' : (n < 0 ? '−' : '')) + Math.abs(n).toFixed(0);
  }

  /* 方形化 treemap（squarify）：面积正比 value，尽量把每块压成接近正方形 */
  treemap(items, x0, y0, w0, h0) {
    var res = [];
    var list = items.filter(function (it) { return it.value > 0; })
      .sort(function (a, b) { return b.value - a.value; });
    var total = list.reduce(function (t, it) { return t + it.value; }, 0);
    if (!list.length || total <= 0 || w0 <= 0 || h0 <= 0) return res;
    var scale = w0 * h0 / total;
    var rect = { x: x0, y: y0, w: w0, h: h0 };
    var worst = function (vals, len) {
      var sum = 0, mx = 0, mn = Infinity;
      vals.forEach(function (v) { sum += v; if (v > mx) mx = v; if (v < mn) mn = v; });
      sum *= scale; mx *= scale; mn *= scale;
      if (sum <= 0 || len <= 0) return Infinity;
      var s2 = sum * sum, l2 = len * len;
      return Math.max(l2 * mx / s2, s2 / (l2 * mn));
    };
    var i = 0, guard = 0;
    while (i < list.length && guard++ < 500) {
      if (!(rect.w > 0.5) || !(rect.h > 0.5)) break;
      var row = [], vals = [], best = Infinity;
      var len = Math.min(rect.w, rect.h);
      while (i < list.length) {
        var w2 = worst(vals.concat([list[i].value]), len);
        if (vals.length && w2 > best) break;
        vals.push(list[i].value); row.push(list[i]); best = w2; i++;
      }
      var sum = vals.reduce(function (t, v) { return t + v; }, 0) * scale;
      if (!(sum > 0)) break;
      if (rect.w >= rect.h) {
        var cw = Math.min(rect.w, Math.max(0.5, sum / rect.h)), yy = rect.y;
        row.forEach(function (it, k) {
          var hh = k === row.length - 1 ? rect.y + rect.h - yy : vals[k] * scale / cw;
          res.push({ item: it.item || it, x: rect.x, y: yy, w: cw, h: hh });
          yy += hh;
        });
        rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
      } else {
        var ch = Math.min(rect.h, Math.max(0.5, sum / rect.w)), xx = rect.x;
        row.forEach(function (it, k) {
          var ww = k === row.length - 1 ? rect.x + rect.w - xx : vals[k] * scale / ch;
          res.push({ item: it.item || it, x: xx, y: rect.y, w: ww, h: ch });
          xx += ww;
        });
        rect = { x: rect.x, y: rect.y + ch, w: rect.w, h: rect.h - ch };
      }
      if (rect.w < 0.5 || rect.h < 0.5) break;
    }
    return res;
  }

  componentDidMount() {
    var el = this.heatRef.current;
    if (el) {
      var measure = () => {
        var w = el.getBoundingClientRect().width;
        if (w > 50 && Math.abs(w - (this.state.heatW || 0)) > 1) this.setState({ heatW: w });
      };
      measure();
      if (window.ResizeObserver) { this._ro = new ResizeObserver(measure); this._ro.observe(el); }
      else window.addEventListener('resize', measure);
    }
  }
  go(patch) {
    var s = this.state;
    if (patch.rangeKey || ('sel' in patch)) patch = Object.assign({ bucket: null }, patch);
    var changed = (patch.rangeKey && patch.rangeKey !== s.rangeKey)
      || (patch.sector && patch.sector !== s.sector)
      || (patch.scope && patch.scope !== s.scope)
      || (patch.view && patch.view !== s.view);
    /* 会触发新取数的两件事：切区间（整池换一份）、打开抽屉（摘要／主题／负面归类／竞品／
       合规五块现取）。它们走 transition：旧内容留着变淡，数据到了再换，不退回骨架。
       板块／范围／视图切换只是池上的子集运算，设计源也给它们演 loading，一并进 transition
       （isPending 只会亮一帧）。搜索框的字紧急提交，理由见 withTransition.jsx。 */
    var opening = patch.sel != null && patch.sel !== s.sel;
    if (changed || opening) {
      patch = Object.assign({}, patch, { tip: null });
      var parts = split(patch, ['q']);
      if (parts.hasUrgent) this.setState(parts.urgent);
      this.props.startTransition(() => this.setState(parts.deferred));
      return;
    }
    this.setState(patch);
  }
  componentWillUnmount() { if (this._ro) this._ro.disconnect(); }

  seg(active, label, patch) {
    return {
      label: label, go: () => this.go(patch),
      fw: active ? 600 : 400,
      fg: active ? '#fff' : 'var(--ink-600)',
      bg: active ? 'var(--csop-blue-600)' : '#fff'
    };
  }
  dfg(d) { return d.dir > 0 ? 'var(--positive-700)' : (d.dir < 0 ? 'var(--negative-600)' : 'var(--ink-400)'); }

  barMove(o) {
    return (e) => {
      var r = e.currentTarget.getBoundingClientRect();
      var n = o.buckets.length;
      var i = Math.max(0, Math.min(n - 1, Math.floor((e.clientX - r.left) / r.width * n)));
      var t = this.state.tip;
      if (t && t.code === o.code && t.i === i) return;
      var b = o.buckets[i];
      this.setState({
        tip: {
          code: o.code, i: i,
          x: r.left + r.width / 2, y: r.top - 8, title: o.code + ' · ' + b.tip,
          /* 三行全部过 numRaw：
             - `shares` 会因源库 raw_json 截断而为 null（ADR-0008），设计源的 `|| 0`
               把它读成「零次转发」；
             - 三态要 AI 标注（ADR-0017），设计源这里连 `|| 0` 都没有，直接字符串拼接 ——
               接真库之后悬浮卡上会写着 `null / null / null`。
             三态同出一块标注，要缺一起缺，所以合并成一句，不写三遍长文案。 */
          rows: [
            { k: '评论数', v: numRaw(b.comments), fg: '#fff' },
            { k: '点赞 ／ 转发', v: numRaw(b.likes) + ' ／ ' + numRaw(b.shares), fg: 'rgba(255,255,255,0.88)' },
            {
              k: '积极 ／ 消极 ／ 中性',
              v: b.positive == null && b.negative == null && b.neutral == null
                ? '数据暂不可用'
                : numRaw(b.positive) + ' / ' + numRaw(b.negative) + ' / ' + numRaw(b.neutral),
              fg: 'rgba(255,255,255,0.88)'
            }
          ]
        }
      });
    };
  }

  /* 从榜单红标进入抽屉时，把抽屉滚到重点舆情区块 */
  componentDidUpdate() {
    if (this.state.scrollRisk && this.state.sel) {
      var body = this.drawerBodyRef.current, el = this.riskRef.current;
      if (body && el) body.scrollTop = el.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 14;
      this.setState({ scrollRisk: false });
    }
  }

  renderVals() {
    var s = this.state, self = this;
    var range = R.buildRange(s.rangeKey);
    var rk = R.ranks(s.rangeKey);

    var presets = R.PRESETS.map(function (p) {
      var on = p.k === s.rangeKey;
      return {
        label: p.label, go: () => self.go({ rangeKey: p.k, sel: null, tip: null }),
        fw: on ? 600 : 400, fg: on ? 'var(--csop-blue-700)' : 'var(--ink-600)',
        bg: on ? 'var(--csop-blue-50)' : '#fff'
      };
    });
    var chips = [{ k: 'all', name: '全部', hue: 'var(--ink-400)' }].concat(R.SECTORS).map(function (c) {
      var on = c.k === s.sector;
      return {
        name: c.name, go: () => self.go({ sector: c.k, sel: null, tip: null }), dot: c.hue,
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-700)',
        bg: on ? 'var(--csop-navy-900)' : '#fff', bc: on ? 'var(--csop-navy-900)' : 'var(--border-2)'
      };
    });

    /* 可见产品：搜索与筛选只改变可见范围，排名与色阶标尺仍来自全市场 */
    var P = R.pool(s.rangeKey);
    var visible = self.visibleFor(s);
    /* 需合规关注：命中产品数按其余筛选条件计算，供快捷筛选显示计数；同业产品该字段不适用 */
    var riskVisibleN = self.visibleFor(Object.assign({}, s, { onlyRisk: false })).filter(function (o) { return P.complianceCount[o.code] > 0; }).length;
    /* 时间桶色阶：6 级离散蓝阶，按该产品区间峰值归一，相邻级别肉眼可分 */
    var STEP = ['#F1F3F6', '#D7E6F8', '#A8CAEF', '#6D9FD9', '#3574BC', '#0E4A8C'];
    var cellBg = function (v, peak) {
      return v <= 0 ? STEP[0] : STEP[Math.min(5, Math.max(1, Math.ceil(v / Math.max(1, peak) * 5)))];
    };

    /* 底色 = 全市场讨论热度「排名分段」。用排名而非数值分位：卡片默认只显示每板块前几名，
       数值分位会把它们全部推到最深一级（此前所有卡片看起来一样深）。分段对所有板块同一标尺。 */
    var byHeat = P.list.slice().sort(function (a, b) { return b.discussionHeat - a.discussionHeat; });
    var heatRank = {}; byHeat.forEach(function (o, i) { heatRank[o.code] = i + 1; });
    var poolN = P.list.length;
    var heatRankOf = function (o) { return heatRank[o.code] || poolN; };
    var hasQ = !!(s.q || '').trim();
    var visSet = {}; visible.forEach(function (o) { visSet[o.code] = 1; });

    /* 榜单：单表，点表头切换排序口径；序号即当前排序下的名次 */
    var growthPct = function (o) {
      var b = P.baseComments[o.code];
      return b >= 5 ? (o.comments - b) / b * 100 : null;
    };
    var SORTS = {
      comments: { label: '评论量', note: '按区间评论量降序', get: function (o) { return o.comments; } },
      growth: { label: '环比', note: '按' + range.benchLabel + '评论量增速降序 · 基准期不足 5 条不参与', get: function (o) { var g = growthPct(o); return g == null ? -1e9 : g; } },
      heat: { label: '讨论热度', note: '按区间讨论热度降序', get: function (o) { return o.discussionHeat; } },
      /* null 用与态度列同一个哨兵排到最末：`null - 5` 在 JS 里是 -5，不挡就会把「没标过」
         混进「0 条」那一段里排。 */
      neg: { label: '舆情', note: '按舆情条数（可归类为需关注问题的内容）降序', get: function (o) { var n = P.negMentions[o.code]; return n == null ? -1e9 : n; } },
      /* `-1e9` 是「不参与排序」的哨兵，排到最末。标注整块缺失与样本不足在**排序**上
         同样处理（都排不出名次），但在**显示**上必须分开 —— 分开的地方在下面 rows 里。 */
      att: { label: '正面／负面', note: '按正面占比（正面÷(正面＋负面)）降序 · 样本不足不参与', get: function (o) { var a = o.attitude; if (a == null) return -1e9; var v = a.positive + a.negative; return (a.sampleSufficient && v) ? a.positive / v : -1e9; } }
    };
    var sortKey = SORTS[s.sort] ? s.sort : 'comments';
    var asc = !!s.sortAsc;
    var mkSort = function (k) {
      var on = k === sortKey;
      return {
        go: () => self.go({ sort: k, sortAsc: on ? !asc : false, tip: null }),
        fw: on ? 600 : 600,
        fg: on ? 'var(--csop-blue-700)' : 'var(--ink-500)',
        caret: on ? (asc ? '▲' : '▼') : ''
      };
    };
    var cmp = function (a, b) {
      var g = SORTS[sortKey].get;
      var d = g(b) - g(a);
      if (!d) d = b.comments - a.comments;
      return asc ? -d : d;
    };
    var sorted = visible.slice().sort(cmp);
    var rows = (s.listAll ? sorted : sorted.slice(0, 50)).map(function (o, i) {
      /* `attitude` **整块**可能是 null（三态要 AI 标注，ADR-0017：标注表没建起来之前
         观测上就没有这个块）。设计源直接 `att.positive`，接真库时第一次渲染就 TypeError。
         注意不能退化成 `sampleSufficient: false` 去走「样本不足」那条分支 ——
         样本不足是「数过了，样本太少不下结论」，暂不可用是「根本没数过」，
         六态里是两态，混用等于把一次采集故障说成一个结论（PRD §3.6）。 */
      var att = o.attitude, valid = att == null ? 0 : att.positive + att.negative;
      var g = growthPct(o), negN = P.negMentions[o.code];
      var on = s.sel === o.code;
      /* 热议总结整块可能是 null：它要 AI 归纳（`providers/sql.py::hot_summaries`），
         接真库时**整池一份**都没有。设计源直接读 `hs.text`／`hs.ok`／`hs.sample`，
         第一次渲染就 TypeError，整页白屏。
         文案逐字取「数据暂不可用」—— 演示数据里本来就有一只产品是这个值
         （hot_summaries.json 里 42 条「样本不足，暂无主流观点」＋ 1 条「数据暂不可用」），
         所以这不是新造的一句话，是同一态的同一句。 */
      var hs = R.hotSummaryFor(o.code, s.rangeKey);
      var hsOk = hs != null && hs.ok;
      var hsText = hs == null ? '数据暂不可用' : hs.text;
      return {
        idx: String(i + 1), code: o.code, name: o.name,
        comments: num(o.comments),
        growth: g == null ? '—' : (g > 0 ? '+' : '') + g.toFixed(0) + '%',
        gfg: g == null ? 'var(--ink-300)' : (g > 2 ? 'var(--positive-700)' : (g < -2 ? 'var(--negative-600)' : 'var(--ink-500)')),
        heat: (o.heatUnknownPosts > 0 ? '≥ ' : '') + num(o.discussionHeat),
        /* undefined ⇒ React 干脆不写这个属性，demo 下与设计源一模一样。
           有值但 `heatUnknownPosts > 0` 时是下限，title 里说出来（demo 下没有这个键）。 */
        heatWhy: o.discussionHeat == null ? heatWhy(o)
          : (o.heatUnknownPosts > 0 ? '讨论热度 ' + num(o.discussionHeat) + heatLowerBoundNote(o.heatUnknownPosts) : undefined),
        hasAttitude: att != null && att.sampleSufficient,
        lowSample: att != null && !att.sampleSufficient,
        attNa: att == null,
        pos: att == null ? '' : String(att.positive), neg: att == null ? '' : String(att.negative),
        posW: valid ? (att.positive / valid * 100).toFixed(1) : '0',
        negW: valid ? (att.negative / valid * 100).toFixed(1) : '0',
        /* `negMentions` 也要 AI（舆情条数来自负面类别）。`null > 0` 为假，红标不出现 ——
           这是对的：没扫过就不该报警。但 `alertN` 不能是 `String(null)`。
           三态分开渲染：正数红标、0 留空（扫过了、没有需关注的内容）、null 灰字「暂不可用」
           （这只产品还没做负面类别标注）。null 与 0 在这一格长得一样，就是把「没查」说成
           「查了没有」（PRD §3.6）。demo 里没有 null，逐字比对不受影响。 */
        alert: negN > 0, alertN: numRaw(negN), alertNa: negN == null,
        /* 热议总结：AI 一句话归纳区间内该 ETF 的主流具体观点；样本不足／不可用按统一状态文案灰字显示 */
        hot: hsText, hotFg: hsOk ? 'var(--ink-800)' : 'var(--ink-400)',
        /* `stale === true`：底层标注已更新、这句总结还没重新生成。title 说出来，格内加一枚
           小徽章；demo 下没有这个键，两处都不出现。 */
        hotStale: hs != null && hs.stale === true, staleTitle: STALE_TITLE,
        hotTitle: hsOk
          ? (hs.stale === true ? STALE_TITLE + ' — ' : '') + hs.text + '（AI 生成' + staleSuffix(hs.stale) + ' · 基于 ' + hs.sample + ' 条有效态度样本）'
          : hsText,
        bg: on ? 'var(--csop-blue-50)' : (i % 2 ? 'var(--canvas)' : '#fff'),
        codeFg: on ? 'var(--csop-blue-700)' : 'var(--ink-900)',
        hoverIn: self.productHover(o, heatRankOf(o), poolN, true),
        hoverOut: () => { if (self.state.tip) self.setState({ tip: null }); },
        go: () => self.go({ sel: o.code, tip: null })
      };
    });

    /* 热力图：始终铺开全市场活跃 ETF，面积＝讨论热度，颜色＝情绪净值或热度环比；
       筛选只决定哪些色块保留颜色，其余置灰，以便在全市场背景下看位置 */
    var heatMode = s.heatMode === 'growth' ? 'growth' : 'net';
    var netOf = function (o) {
      var a = o.attitude;
      if (a == null) return null;
      var v = a.positive + a.negative;
      return (a.sampleSufficient && v) ? (a.positive - a.negative) / v * 100 : null;
    };
    var heatGrowth = function (o) {
      var b = P.baseComments[o.code];
      return b >= 20 ? (o.comments - b) / b * 100 : null;
    };
    var tileVal = function (o) { return heatMode === 'net' ? netOf(o) : heatGrowth(o); };
    var subLabel = heatMode === 'net' ? '情绪净值' : '评论环比';
    var fmtVal = function (v) {
      if (v == null) return '';
      return (v > 0 ? '+' : (v < 0 ? '−' : '')) + Math.abs(v).toFixed(0) + (heatMode === 'net' ? '' : '%');
    };
    var VBANDS = [-30, -15, -5, 5, 15, 30];
    var NET_BG = ['#C53030', '#DC7B7B', '#F2C7C7', '#EEF2F7', '#C2E2D1', '#67B994', '#1F8A5B'];
    var NET_FG = ['#fff', '#7C2323', '#7C2323', '#5C6678', '#1B5E3F', '#0D3E28', '#fff'];
    var GRW_BG = ['#8B95A3', '#B7BFC9', '#DDE2E8', '#EEF2F7', '#D2E0F4', '#5288CF', '#163E73'];
    var GRW_FG = ['#fff', '#2A3140', '#4A5568', '#5C6678', '#1B4F8E', '#fff', '#fff'];
    var bandIdx = function (v) {
      for (var i2 = 0; i2 < VBANDS.length; i2++) if (v < VBANDS[i2]) return i2;
      return VBANDS.length;
    };
    /* 热力图：只画讨论热度前 20 只，长尾合并为一格「其他 N 只」（点开看完整榜单）；
       面积＝讨论热度，颜色＝情绪净值或评论量环比；筛选外的产品置灰 */
    var HEAT_TOP = 20;
    var heatSorted = P.list.slice()
      /* `> 0` 顺带把热度未知的产品也挡在外面（null > 0 为假），这是有意的：格子面积就是热度，
         「不知道多大」画不出来，硬给个尺寸才是撒谎。它照样出现在下方榜单里，那里有位置说
         「数据暂不可用」。 */
      .filter(function (o) { return o.discussionHeat > 0; })
      .sort(function (a, b) { return b.discussionHeat - a.discussionHeat; });
    /* 挡掉是对的，**不吭声地**挡掉不是。少画几个格子在界面上看不出来，于是「热度分布」
       看着像是全市场的全貌，而它漏掉了其中活跃度最高的那一只。数一数，说出来。 */
    var heatNa = P.list.filter(function (o) { return o.discussionHeat == null; });
    var headList = heatSorted.slice(0, HEAT_TOP);
    var tailList = heatSorted.slice(HEAT_TOP);
    var tailHeat = tailList.reduce(function (t, o) { return t + o.discussionHeat; }, 0);
    var tiles = [];
    var flat = headList.map(function (o) { return { item: o, value: o.discussionHeat }; });
    var heatW = s.heatW || 420;
    self.treemap(flat, 0, 0, heatW, 440).forEach(function (t) {
      var o = t.item, tw = t.w - 1.3, th = t.h - 1.3;
      if (tw < 3 || th < 3) return;
      var v = tileVal(o), on = s.sel === o.code, dim = !visSet[o.code];
      var bi = v == null ? 3 : bandIdx(v);
      tiles.push({
        key: o.code,
        x: t.x.toFixed(1), y: t.y.toFixed(1), w: tw.toFixed(1), h: th.toFixed(1),
        bg: dim ? '#F1F3F7' : (heatMode === 'net' ? NET_BG[bi] : GRW_BG[bi]),
        fg: dim ? '#C2C8D2' : (heatMode === 'net' ? NET_FG[bi] : GRW_FG[bi]),
        code: (tw >= 30 && th >= 16) ? o.code : '',
        codeFont: '600 ' + (tw >= 56 && th >= 30 ? 13 : 11) + 'px/1.1 var(--font-mono)',
        /* 格内数字＝面积口径（讨论热度）；颜色口径带明确标签，避免把颜色值误读成面积 */
        val: (tw >= 42 && th >= 30) ? '热度 ' + o.discussionHeat.toLocaleString('en-US') : '',
        valFont: '600 ' + (tw >= 64 ? 11 : 9) + 'px/1.2 var(--font-mono)',
        sub: (tw >= 82 && th >= 50) ? (v == null ? '样本不足' : subLabel + ' ' + fmtVal(v)) : '',
        subFont: '500 10px/1.2 var(--font-mono)',
        ring: on ? 'inset 0 0 0 2px var(--csop-navy-900)' : 'inset 0 0 0 0.5px rgba(255,255,255,0.5)',
        risk: !dim && P.complianceCount[o.code] > 0,
        hoverIn: self.productHover(o, heatRankOf(o), poolN, false),
        hoverOut: () => { if (self.state.tip) self.setState({ tip: null }); },
        go: () => self.go({ sel: o.code, tip: null })
      });
    });
    var heatLegend = (heatMode === 'net'
      ? ['≤−30', '−15', '−5', '0', '+5', '+15', '≥+30']
      : ['≤−30%', '−15%', '−5%', '0', '+5%', '+15%', '≥+30%']
    ).map(function (lb, i2) {
      return {
        label: lb,
        bg: heatMode === 'net' ? NET_BG[i2] : GRW_BG[i2],
        fg: heatMode === 'net' ? NET_FG[i2] : GRW_FG[i2],
        radius: i2 === 0 ? '3px 0 0 3px' : (i2 === 6 ? '0 3px 3px 0' : '0')
      };
    });
    var heatColorText = heatMode === 'net'
      ? '情绪净值＝(积极−消极)÷(积极＋消极)×100，绿色为积极多、红色为消极多，样本不足不着色'
      : '评论量' + range.benchLabel + '的变化百分比';

    /* 顶部 4 卡（第三轮）：CSOP产品讨论热度 → 舆情管理 → 近期热议飙升的CSOP产品 → 近期热议提升的其他产品；
       前两卡口径收窄至 CSOP 自家（ownership = own），不随榜单筛选变化；基准期为顶部日期预设的前一等长区间 */
    /* 汇总与三个环比全部由后端算好（P.own），屏幕不再自己遍历 61 只求和。设计源那段
       循环里有三处 `|| 0`：JS 的 `sum + null === sum + 0`，少加的那一只悄悄消失，合计
       看着还挺像样（比 NaN 难发现得多）。合计里有一个不知道，合计就是不知道（铁律 2）。
       环比同理 —— delta 是 PRD 第 3 章的全局口径，只在后端实现一份（铁律 1）。 */
    var own = P.own;
    var dOH = own.dHeat, dON = own.dNeg, dOP = own.dPos;
    /* 自家合计里有几帖转发数未知（`own.heatUnknownPosts > 0`）⇒ 合计是下限，备注里追加一句；demo 下没有这个键。 */
    var k1 = { value: (own.heatUnknownPosts > 0 ? '≥ ' : '') + num(own.heat), delta: dOH.short, dfg: self.dfg(dOH), sub: '仅统计 CSOP 自家 ' + own.count + ' 只 · ' + range.benchLabel + ' · ' + R.HEAT_FORMULA.replace('讨论热度 ＝ ', '热度＝').split(' ').join('') + heatLowerBoundNote(own.heatUnknownPosts) + (own.baseHeatUnknownPosts > 0 ? ' · 基准期' + heatLowerBoundNote(own.baseHeatUnknownPosts) : '') };
    var pnTot = own.neg == null || own.pos == null ? null : own.neg + own.pos;
    var k2 = {
      neg: num(own.neg), negDelta: dON.short, negDfg: dON.dir > 0 ? 'var(--negative-600)' : (dON.dir < 0 ? 'var(--positive-700)' : 'var(--ink-400)'),
      pos: num(own.pos), posDelta: dOP.short, posDfg: self.dfg(dOP),
      negW: pnTot ? (own.neg / pnTot * 100).toFixed(1) : '0', posW: pnTot ? (own.pos / pnTot * 100).toFixed(1) : '0',
      barTitle: '负面 ' + num(own.neg) + ' ： 正面 ' + num(own.pos) + (pnTot ? ' · 负面占 ' + (own.neg / pnTot * 100).toFixed(0) + '%' : ''),
      /* 「N 条」整体替换成长文案，而不是「需合规关注 数据暂不可用 条」—— 量词得跟着数字走。
         演示数据里 3153 的合规扫描是 unavailable，所以这里真会走到 null 分支：设计源那句
         `|| 0` 把「没扫过」读成「零条」，于是这张卡少数了一只还显得言之凿凿。 */
      sub: '正面＝AI 判定积极态度；负面＝可归类为需关注问题 · 其中需合规关注 ' + (own.risk == null ? '数据暂不可用' : own.risk + ' 条') + ' · ' + range.benchLabel
    };
    /* 两张 Top3 卡：按评论量环比增长率降序，只取正增长；基准期不足 5 条不参与（与榜单环比同口径）；点行打开右侧快速详情抽屉 */
    var topOf = function (own) {
      return P.list.filter(function (o) { return (o.ownership === 'own') === own; })
        .map(function (o) { return { o: o, g: growthPct(o) }; })
        .filter(function (x) { return x.g != null && x.g > 0; })
        .sort(function (a, b) { return b.g - a.g || b.o.comments - a.o.comments; })
        .slice(0, 3)
        .map(function (x, i) {
          var o = x.o;
          return {
            rank: String(i + 1), code: o.code, name: shortName(o.name), growth: '+' + x.g.toFixed(0) + '%', comments: o.comments.toLocaleString('en-US'),
            title: o.name + '（' + o.code + '.HK）· 评论量 ' + o.comments + '，' + range.benchLabel + ' ' + '+' + x.g.toFixed(0) + '%（基准期 ' + P.baseComments[o.code] + ' 条）· 点击打开快速详情',
            go: () => self.go({ sel: o.code, tip: null })
          };
        });
    };
    var topOwn = topOf(true), topPeer = topOf(false);

    var out = {
      navGroups: navGroups('portfolio', 'sector'), presets: presets, chips: chips,
      rangeText: range.text, rangeFrom: range.from, rangeTo: range.to,
      granLabel: range.granLabel, updated: stamp(R.UPDATED),
      /* 真实的「还在等」：transition 提交前为 true（withTransition.jsx），不再是 520ms 定时器。 */
      loading: !!self.props.isPending, bodyOpacity: self.props.isPending ? '0.45' : '1',
      visibleCount: String(visible.length),
      k1: k1, k2: k2, topOwn: topOwn, topPeer: topPeer, topOwnEmpty: topOwn.length === 0, topPeerEmpty: topPeer.length === 0,
      topNote: '评论量' + range.benchLabel + ' · 前 3',
      hotRule: R.HOT_RULE,
      rows: rows, rowsEmpty: sorted.length === 0,
      rowsEmptyText: (s.scope === 'peer' && s.onlyRisk) ? '暂无相关内容 — 同业产品不纳入需合规关注识别，请切换范围或关闭该筛选。'
        : (s.scope === 'peer' ? '暂无相关内容 — 当前条件下没有匹配的同业产品。'
        : (s.onlyRisk ? '暂无相关内容 — 当前条件下没有需合规关注的产品。' : '暂无相关内容 — 当前搜索与筛选下没有匹配的产品。')),
      riskRef: self.riskRef, drawerBodyRef: self.drawerBodyRef,
      listMore: sorted.length > 50,
      listAll: !!s.listAll,
      listMoreLabel: s.listAll ? '收起，只看前 50 只' : '展开全部 ' + sorted.length + ' 只（当前显示前 50）',
      listToggle: () => self.setState({ listAll: !s.listAll, tip: null }),
      boardNote: SORTS[sortKey].note,
      sortComments: mkSort('comments'), sortGrowth: mkSort('growth'),
      sortHeat: mkSort('heat'), sortNeg: mkSort('neg'), sortAtt: mkSort('att'),
      heatRef: self.heatRef,
      heatTopN: String(headList.length), heatTailN: String(tailList.length),
      /* demo 下恒为 0 ⇒ 整块不渲染，逐字比对照旧一致。 */
      heatNaShow: heatNa.length > 0,
      heatNaText: '另有 ' + heatNa.length + ' 只讨论热度暂不可用，未参与面积分配；它们在下方榜单里。',
      /* 只有在**每一只**都缺转发时才归因于转发；混着别的原因就只说结果，不猜。 */
      heatNaWhy: heatNa.length > 0 && heatNa.every(function (o) { return o.shares == null; })
        ? heatWhy(heatNa[0]) : '讨论热度暂不可用。',
      tailHeat: tailHeat.toLocaleString('en-US'),
      tailShare: (tailHeat / Math.max(1, tailHeat + headList.reduce(function (t, o) { return t + o.discussionHeat; }, 0)) * 100).toFixed(0) + '%',
      tailGo: () => self.go({ listAll: true, sort: 'heat', sortAsc: false, sel: null, tip: null }),
      heatFormula: R.HEAT_FORMULA, heatNote: R.HEAT_NOTE,
      tiles: tiles,
      heatModes: [
        self.seg(heatMode === 'net', '情绪净值', { heatMode: 'net', tip: null }),
        self.seg(heatMode === 'growth', '评论量环比', { heatMode: 'growth', tip: null })
      ],
      heatLegend: heatLegend, heatColorText: heatColorText, heatLegendTitle: subLabel,
      heatMetricName: heatMode === 'growth' ? '评论量环比' : '情绪净值',
      heatMetricNote: heatMode === 'growth'
        ? '环比＝评论量较上期增减 %'
        : '净值＝积极占比 − 消极占比',
      benchText: range.benchText, benchLabel: range.benchLabel,
      q: s.q, hasQ: hasQ, qBc: hasQ ? 'var(--csop-blue-600)' : 'var(--border-2)',
      onSearch: (e) => {
        var v = e.target.value;
        var res = self.visibleFor(Object.assign({}, s, { q: v }));
        self.go({ q: v, sel: res.length === 1 ? res[0].code : null, tip: null, flatAll: false });
      },
      clearQ: () => self.go({ q: '', sel: null, tip: null }),
      poolCount: String(poolN),
      bucketLegend: STEP.map(function (c) { return { bg: c }; }),
      toggles: [
        { on: s.onlyNew, label: '仅新品', go: () => self.go({ onlyNew: !s.onlyNew, sel: null, tip: null }) },
        { on: s.onlyNeg, label: '仅有舆情', go: () => self.go({ onlyNeg: !s.onlyNeg, sel: null, tip: null }) },
        { on: s.onlyRisk, label: '仅看需合规关注', n: riskVisibleN, go: () => self.go({ onlyRisk: !s.onlyRisk, sel: null, tip: null }) }
      ].map(function (t) {
        return {
          label: t.label, go: t.go, fw: t.on ? 600 : 400,
          hasN: t.n > 0, n: String(t.n || 0),
          fg: t.on ? 'var(--csop-blue-700)' : 'var(--ink-700)',
          bg: t.on ? 'var(--csop-blue-50)' : '#fff',
          bc: t.on ? 'var(--csop-blue-600)' : 'var(--border-2)',
          dot: t.on ? 'var(--csop-blue-600)' : 'var(--ink-300)'
        };
      }),
      /* 范围三项互斥：同业产品＝产品池中已标记为非自家（ownership: peer）的产品，不由 AI 临时推断 */
      scopeOptions: [
        self.seg(s.scope === 'all', '全部', { scope: 'all', sel: null }),
        self.seg(s.scope === 'own', '仅看自家', { scope: 'own', sel: null }),
        self.seg(s.scope === 'peer', '仅看同业产品', { scope: 'peer', sel: null })
      ],
      notesOpen: s.notes, toggleNotes: () => this.go({ notes: !s.notes }),
      notesCaret: s.notes ? '▲' : '▼',
      notesBc: s.notes ? 'var(--csop-blue-600)' : 'var(--border-2)',
      notesBg: s.notes ? 'var(--csop-blue-50)' : '#fff',
      notesFg: s.notes ? 'var(--csop-blue-700)' : 'var(--ink-700)',
      statusLegend: R.STATUS_LEGEND,
      notesBlocks: [
        { title: '评论量与讨论热度', body: '评论量为区间内被识别为讨论该 ETF 的评论条数，同一账号同一条评论只计一次，一条评论可分别计入多只 ETF。' + R.HEAT_FORMULA + '；' + R.HEAT_NOTE + ' 热度只用于排序与快速扫描，不作为绝对水平解读。' },
        { title: '积极、消极与中性', body: '「正面／负面」为 AI 对内容中产品态度的分类，不是 Futu 平台的点赞／点踩行为。只判断针对产品本身的态度（费用、流动性、跟踪表现、机制、分红、使用体验）；单纯预测指数或价格涨跌归入产品话题情绪。正负面比分母为正面＋负面，中性单独计数。有效态度评论少于 ' + R.LOW_SAMPLE + ' 条时显示样本不足。「舆情」列为可归类为需关注问题的内容条数。' },
        { title: '热议总结（AI 生成）', body: R.HOT_RULE + ' 总结为「现象 + 主流观点／动作倾向」一句话，与产品监控页的舆情总结出自同一次生成；字段缺失显示「数据暂不可用」。' },
        { title: '排名、基准与热力粒度', body: '卡片名次为全市场评论量排名，基于完整活跃 ETF 池计算，板块筛选不重算。基准区间为 ' + range.benchText + '（' + range.benchLabel + '）。热力粒度随日期筛选自适应：1–2 天按小时、3–14 天按自然日、15 天及以上按自然周；当前为' + range.granLabel + '。' },
        /* AI 结论的验证程度（ADR-0019 §4）。放进这个面板而不是某个角标，是因为它管的
           不是某一格而是整页：上面几块每一块都写着「AI 判定」「AI 生成」，读的人默认
           这些结论上线前被人看过。本期没有人看过，这句得自己说出来。
           文案由 /meta 的 `aiValidation` 决定，不在这里写死 —— 见 lib/view.js。 */
        { title: 'AI 结论的验证程度', body: aiValidationNote(R.AI_VALIDATION, R.AI_VALIDATION_DETAIL) + (R.AI_VALIDATION === 'spot_check' && R.AI_VALIDATION_DETAIL
          ? '抽检是样本统计，不是对页面上每一条结论的逐条确认 —— 对不对请点开证据以原文为准。'
          : '本期未安排人工复核，也没有金标集 —— 页面上任何一条 AI 结论都没有经过人工确认，对不对请点开证据以原文为准。') },
        { title: '重点舆情（需合规关注）与同业产品', body: '重点舆情为 AI 识别的高风险言论信号，标签包括监管举报、严重指控、疑似未经证实指控、煽动扩散、合规质疑；系统只识别信号并保留原文与命中依据，不判定言论真伪或产品是否违规，状态统一为「AI 识别 · 待人工确认」。同一条评论可同时属于消极观点与重点舆情。识别范围为自家产品，同业产品不适用。「仅看同业产品」对应产品池中已标记为非自家的产品，不由 AI 临时推断关系。' }
      ],
      tipOpen: !!s.tip,
      tipX: s.tip ? s.tip.x : 0, tipY: s.tip ? s.tip.y : 0,
      tipTitle: s.tip ? s.tip.title : '', tipRows: s.tip ? s.tip.rows : [],
      drawerOpen: !!s.sel, drawerX: s.sel ? '0' : '100%',
      closeDrawer: () => this.go({ sel: null })
    };

    if (s.sel && R.MASTER[s.sel]) {
      var code = s.sel;
      var o = R.observe(code, s.rangeKey), m = R.MASTER[code];
      var b = R.benchmark(code, s.rangeKey);
      /* 抽屉里的这几块全要 AI（摘要／主题／负面归类），接真库时整块 null。
         设计源直接 `.text`／`.slice(0,3)`，第一次开抽屉就 TypeError。null 与空数组
         在这里是两句不同的话，见 productMonitor 同处注释。 */
      var sum = R.summaryFor(code, s.rangeKey);
      var sumNa = sum == null;
      if (sumNa) sum = { text: '数据暂不可用 — 舆情总结尚未生成或数据源未提供。', sample: null };
      /* 同 rows 那处：attitude 整块可能为 null（ADR-0017）。`attNa` 单独一条，
         别并进 `sampleSufficient`。 */
      var att = o.attitude, attNa = att == null;
      var valid = attNa ? 0 : att.positive + att.negative;
      var all = attNa ? 0 : att.positive + att.negative + att.neutral;
      var link = function (extra) {
        return 'product-monitor.dc.html?code=' + code + '&range=' + s.rangeKey + (extra || '');
      };
      var themeRow = function (t) {
        return {
          id: t.id,
          title: t.title, mentions: String(t.mentions), delta: t.delta.short,
          dfg: self.dfg(t.delta), evidence: String(t.evidenceCount),
          href: link('&ev=' + t.id)
        };
      };
      var posRaw = R.themesFor(code, s.rangeKey, 'positive');
      var negRaw = R.themesFor(code, s.rangeKey, 'negative');
      var catsRaw = R.negCatsFor(code, s.rangeKey);
      /* 负面类别仍是数组，`stale` 逐行挂在每个元素上（后端契约），整块判定见 lib/view.js rowsStale。 */
      var catsStale = rowsStale(catsRaw);
      var posNa = posRaw == null, negNa = negRaw == null, catsNa = catsRaw == null;
      var pos = (posNa ? [] : posRaw).slice(0, 3).map(themeRow);
      var neg = (negNa ? [] : negRaw).slice(0, 3).map(themeRow);
      var cats = (catsNa ? [] : catsRaw).slice(0, 3);
      var comps = naBox(R.competitorsFor(code, s.rangeKey));
      var cr = naBox(R.complianceFor(code, s.rangeKey));
      var lifeStyle = { '新增': ['var(--negative-100)', 'var(--negative-700)'], '持续': ['var(--warning-100)', 'var(--warning-700)'], '消退': ['var(--ink-100)', 'var(--ink-600)'] };

      /* 分时段热力条：桶随顶部日期粒度自适应，色阶按该产品区间峰值归一 */
      var bks = o.buckets, stride = bks.length > 16 ? Math.ceil(bks.length / 12) : (bks.length > 8 ? 2 : 1);
      var peak = bks.reduce(function (a, b) { return b.comments > a.comments ? b : a; }, bks[0]);
      var peakN = peak ? peak.comments : 0;
      var peakIdx = peak ? bks.indexOf(peak) : -1;
      var readIdx = (s.bucket != null && bks[s.bucket]) ? s.bucket : peakIdx;
      var read = bks[readIdx] || null;
      /* 小时／自然日等时间元数据只在 range.buckets 上（观测桶只带数值），按索引对齐取用 */
      var rbk = range.buckets;
      var dayBands = [];
      if (range.gran === 'hour') {
        rbk.forEach(function (b) {
          var last = dayBands[dayBands.length - 1];
          if (!last || last.day !== b.day) dayBands.push({ day: b.day, n: 1 });
          else last.n++;
        });
        dayBands = dayBands.map(function (d) {
          return { label: String(d.day).slice(5), flex: d.n };
        });
      }

      out.sel = {
        code: code, name: o.name, sector: o.sectorName, struct: o.struct, issuer: o.issuer,
        listing: m.listingDate, isNew: m.isNew,
        cells: bks.map(function (b, i) {
          var hot = s.tip && s.tip.code === code && s.tip.i === i;
          var on = s.bucket === i;
          return {
            bg: cellBg(b.comments, peakN),
            ring: on ? 'inset 0 0 0 2px var(--csop-navy-900)'
              : (hot ? 'inset 0 0 0 2px var(--csop-blue-600)' : 'inset 0 0 0 1px rgba(14,42,82,0.10)'),
            pick: () => self.setState({ bucket: on ? null : i })
          };
        }),
        /* 时间轴：小时粒度只标 0/6/12/18 点，自然日单独一行；日／周粒度按 stride 稀释 */
        axis: rbk.map(function (b, i) {
          var head = range.gran === 'hour' && b.hour === 0;
          var show = range.gran === 'hour' ? (b.hour % 6 === 0) : (i % stride === 0);
          return {
            v: show ? (range.gran === 'hour' ? String(b.hour).padStart(2, '0') : b.label) : '',
            fw: head ? 600 : 500,
            fg: head ? 'var(--ink-800)' : 'var(--ink-500)',
            tick: show ? (head ? 11 : 6) : 3,
            tickC: head ? 'var(--ink-600)' : 'var(--border-2)'
          };
        }),
        hasDayBands: dayBands.length > 1,
        dayBands: dayBands,
        cellMove: self.barMove(o), cellLeave: () => { if (self.state.tip) self.setState({ tip: null }); },
        hasPick: readIdx !== peakIdx && read != null,
        clearPick: () => self.setState({ bucket: null }),
        readTime: read ? read.tip : '数据暂不可用',
        readBg: readIdx === peakIdx ? 'var(--csop-navy-900)' : 'var(--csop-blue-700)',
        readHint: read == null ? '' : (readIdx === peakIdx ? '区间峰值时段 · 点击任一色块查看该时段' : '已选时段 · 点击其他色块切换'),
        /* 同上：转发可能坏、三态要 AI。`占区间评论量` 的分子分母任一未知就整格未知 ——
           设计源的 `o.comments ? … : '—'` 只挡住了分母为 0，分子为 null 时会算出 `NaN%`。 */
        readRows: read ? [
          { k: '评论', v: numRaw(read.comments), fg: 'var(--ink-900)' },
          { k: '点赞', v: numRaw(read.likes), fg: 'var(--ink-800)' },
          { k: '转发', v: numRaw(read.shares) + heatLowerBoundNote(read.heatUnknownPosts), fg: 'var(--ink-800)' },
          { k: '积极', v: numRaw(read.positive), fg: 'var(--positive-700)' },
          { k: '消极', v: numRaw(read.negative), fg: 'var(--negative-700)' },
          {
            k: '占区间评论量',
            /* 公式逐字照抄设计源（直接 toFixed(1)，**不要**换成 view.js 的 pct1 ——
               那个先 round 再 toFixed，在 .05 边界上结果不同，逐字比对会红）。 */
            v: read.comments == null || o.comments == null
              ? '数据暂不可用'
              : (o.comments ? (read.comments / o.comments * 100).toFixed(1) + '%' : '—'),
            fg: 'var(--ink-700)'
          }
        ] : [],
        own: o.ownership === 'own' ? '自家产品' : '同业产品',
        obg: o.ownership === 'own' ? 'var(--csop-blue-50)' : 'var(--ink-100)',
        ofg: o.ownership === 'own' ? 'var(--csop-blue-700)' : 'var(--ink-600)',
        summary: sum.text, sample: sumNa ? '数据暂不可用' : String(sum.sample), sampleOk: !sumNa, rangeText: range.text,
        /* 徽章：`aiStatus === 'unavailable'`（只有计数句、AI 要点没生成）不许写「AI 生成」；
           `stale === true` 追加「 · 待更新」。两个键 demo 下都不存在。 */
        sumAiLabel: (sum.aiStatus === 'unavailable' ? '计数句 · AI 要点待生成' : 'AI 生成 · 可追溯原文') + staleSuffix(sum.stale),
        sumStale: sum.stale === true, staleTitle: STALE_TITLE,
        posThemes: pos, negThemes: neg,
        noPos: !posNa && pos.length === 0, noNeg: !negNa && neg.length === 0,
        posUnavailable: posNa, negUnavailable: negNa,
        posTotal: attNa ? '数据暂不可用' : String(att.positive),
        negTotal: attNa ? '数据暂不可用' : String(att.negative),
        neuTotal: attNa ? '数据暂不可用' : String(att.neutral),
        neuShare: attNa ? '数据暂不可用' : (all ? (att.neutral / all * 100).toFixed(0) + '% 全部有效内容' : '—'),
        posPct: valid ? (att.positive / valid * 100).toFixed(1) : '0',
        negPct: valid ? (att.negative / valid * 100).toFixed(1) : '0',
        /* 三态未标注时既不是「持平」也不是「样本不足」—— 两句都是结论，而这里没有结论。 */
        net: attNa
          ? '数据暂不可用 · 不输出倾向结论'
          : (att.sampleSufficient
            ? (att.positive === att.negative ? '积极与消极持平' : (att.positive > att.negative ? '积极比消极多 ' + (att.positive - att.negative) + ' 条' : '消极比积极多 ' + (att.negative - att.positive) + ' 条'))
            : '样本不足 · 不输出倾向结论'),
        netFg: !attNa && att.sampleSufficient ? (att.positive >= att.negative ? 'var(--positive-700)' : 'var(--negative-700)') : 'var(--ink-500)',
        heat: (o.heatUnknownPosts > 0 ? '≥ ' : '') + num(o.discussionHeat),
        heatWhy: o.discussionHeat == null ? heatWhy(o)
          : (o.heatUnknownPosts > 0 ? '讨论热度 ' + num(o.discussionHeat) + heatLowerBoundNote(o.heatUnknownPosts) : undefined),
        heatDelta: b.heat.short, heatDfg: self.dfg(b.heat),
        heatDeltaTitle: b.heatUnknownPosts && (b.heatUnknownPosts.current > 0 || b.heatUnknownPosts.base > 0)
          ? '环比按下限计算：当期 ' + num(b.heatUnknownPosts.current) + ' 帖、基准期 ' + num(b.heatUnknownPosts.base) + ' 帖转发数未知'
          : undefined,
        comments: num(o.comments),
        likes: num(o.likes),
        shares: num(o.shares) + heatLowerBoundNote(o.heatUnknownPosts),
        interactions: num(o.interactions) + heatLowerBoundNote(o.heatUnknownPosts),
        rank: String(rk.map[code]), rankTotal: String(rk.total),
        hasNegCats: cats.length > 0, noNegCats: !catsNa && cats.length === 0, negCatsUnavailable: catsNa,
        negCatsStale: catsStale, themesStale: R.themesStale(code, s.rangeKey) === true, compsStale: comps.stale === true,
        negCats: cats.map(function (c) {
          /* 基准期没标注时后端给 null（生命周期未知），不是「持续」——徽章写「暂不可用」（PRD §3.6 短文案）。 */
          var st = lifeStyle[c.lifecycleLabel] || lifeStyle['持续'];
          return {
            label: c.label, summary: c.summary, life: c.lifecycleLabel == null ? '暂不可用' : c.lifecycleLabel,
            lbg: st[0], lfg: st[1],
            mentions: String(c.mentions), share: '占负面 ' + c.shareOfNegative.toFixed(0) + '%'
          };
        }),
        hasComps: comps.status === 'ok', noComps: comps.status !== 'ok',
        compsEmptyText: comps.status === 'unavailable'
          ? '数据暂不可用 — 该产品的竞品关系尚未核验，映射字段未提供。'
          : '暂无相关内容 — 映射表与自动识别均未给出关联产品。',
        comps: comps.list.slice(0, 3).map(function (c) {
          return {
            code: c.code, name: c.name, issuer: c.issuer,
            relation: c.relation === 'confirmed' ? '已确认' : 'AI 生成 · 待确认',
            rbg: c.relation === 'confirmed' ? 'var(--csop-blue-50)' : 'var(--warning-100)',
            rfg: c.relation === 'confirmed' ? 'var(--csop-blue-700)' : 'var(--warning-700)',
            mentions: String(c.comments), delta: c.delta.short, dfg: self.dfg(c.delta),
            pos: c.positiveThemes.length ? c.positiveThemes.map(function (t) { return t.title; }).join('、') : '暂无相关内容',
            neg: c.negativeThemes.length ? c.negativeThemes.map(function (t) { return t.title; }).join('、') : '暂无相关内容'
          };
        }),
        monitorHref: link(''),
        riskOk: cr.status === 'ok', riskEmpty: cr.status === 'empty', riskNa: cr.status === 'na', riskUnavailable: cr.status === 'unavailable',
        riskN: String(cr.list.length), riskHref: link('&risk=1'),
        riskHasMore: cr.list.length > 3, riskMore: cr.list.length > 3 ? '另有 ' + (cr.list.length - 3) + ' 条，见完整产品监控' : '',
        riskRows: cr.list.slice(0, 3).map(function (r) {
          return {
            id: r.id,
            tags: r.riskLabels.map(function (l) { return { label: l }; }),
            time: r.publishedAt, excerpt: r.excerpt, why: r.detectionRationale,
            author: r.authorName, type: r.authorType, source: r.sourceKind,
            tbg: r.isKnownKol ? 'var(--csop-blue-50)' : 'var(--canvas-alt)',
            tfg: r.isKnownKol ? 'var(--csop-blue-700)' : 'var(--ink-700)',
            href: link('&risk=' + r.id)
          };
        })
      };
    }
    return out;
  }

  render() {
    const v = this.renderVals()

    return (
      <div data-screen-label="板块总览" style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px')}>

        <Shell vals={v}>
          <FilterBar v={v} />
        </Shell>

        {v.loading && (
          <div style={s('position:sticky;top:144px;z-index:30;display:flex;align-items:center;gap:10px;margin:14px 24px 0;padding:9px 15px;border:1px solid var(--csop-blue-200);border-radius:6px;background:var(--csop-blue-50)')}>
            <span style={s('width:8px;height:8px;border-radius:9999px;background:var(--csop-blue-600)')}></span>
            <span style={s('font:500 14px/1.4 var(--font-cjk);color:var(--csop-blue-800)')}>数据加载中 — 正在按新的日期范围与板块筛选重新聚合</span>
          </div>
        )}

        <div style={s(`padding:24px 24px 64px;opacity:${v.bodyOpacity};transition:opacity 200ms cubic-bezier(.4,0,.2,1)`)}>

          <div style={s('display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding-bottom:18px;margin-bottom:20px;border-bottom:1px solid var(--border-2)')}>
            <div>
              <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.18em;color:var(--csop-blue-600);margin-bottom:10px')}>市场 · 板块总览</div>
              <div style={s('font:600 28px/1.2 var(--font-cjk);letter-spacing:-0.015em')}>全市场活跃 ETF 舆情分布</div>
            </div>
            <div style={s('flex:none;text-align:right')}>
              <div style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>数据范围 <span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800)')}>{v.rangeText}</span></div>
              <div style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-500)')}>最近更新 <span style={s('font:600 13px/1.6 var(--font-mono);color:var(--ink-800)')}>{v.updated}</span></div>
            </div>
          </div>

          {v.notesOpen && <Notes v={v} />}

          <Kpis v={v} />
          <Board v={v} />

        </div>

        {v.tipOpen && <Tooltip v={v} />}
        <Drawer v={v} />
      </div>
    )
  }
}

export default withTransition(SectorOverview)
