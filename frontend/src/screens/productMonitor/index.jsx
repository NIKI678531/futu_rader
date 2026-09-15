/* Port of design/product-monitor.dc.html (产品监控).

   The biggest of the four screens (1793 lines of source), so the template is split into
   the section components alongside this file; `renderVals()` stays whole because the
   sections share derived values and the design computes them in one pass.

   Everything from `state` through `renderVals()` is the design source verbatim, with
   three deviations forced by the move off the dc runtime:
     - `window.RADAR` is a synchronous import, so componentDidMount no longer polls for
       the runtime's async <script>; the deep-link parse runs once, inline.
     - `go()` rewrites the address to `/product?code=…&range=…` instead of
       `product-monitor.dc.html?…`. It still uses history.replaceState, which the router
       does not observe — the URL updates silently, exactly as in the design.
     - `{{ }}` holes became JSX; `style="…"` strings are parsed by `s()` and
       `style-hover` by `hover()`.
     - 加载态由真实取数驱动（withTransition.jsx）：设计源 go() 里那个 520ms 的假 loading
       定时器删掉，`loading`／`bodyOpacity` 改读 transition 的 isPending；切产品／切区间／
       开抽屉时旧内容保持可见并变淡，不再退回整屏 fallback。构造函数里把本屏已知端点
       一次性预取（radar.js `urlsFor`），首绘不再是十几次串行往返。 */
import React from 'react'
import R, { prefetchScreen } from '../../data/radar'
import { rgba, num, numRaw, navGroups, stamp, naBox, aiValidationNote, heatLowerBoundNote, staleSuffix, STALE_TITLE } from '../../lib/view'
import { s } from '../../lib/dc'
import Shell from '../../components/Shell'
import withTransition, { split } from '../../components/withTransition'
import FilterBar from './FilterBar'
import ProductHeader from './ProductHeader'
import Overview from './Overview'
import KolList from './KolList'
import Attitude from './Attitude'
import Risk from './Risk'
import Trend from './Trend'
import Stages from './Stages'
import Topics from './Topics'
import Competitors from './Competitors'
import EvidenceDrawer from './EvidenceDrawer'

export default class ProductMonitor extends React.Component {
  static defaultProps = {
    candleColor: 'greenUp',
  }

  state = {
    code: '3033', rangeKey: 'd7', selOpen: false, q: '', pq: '', secF: 'all', newF: 'all',
    posMore: false, negMore: false, panel: null, post: null, hover: null, loading: false,
    legend: { comments: 1, inter: 1, active: 1, pos: 1, neg: 1, px: 1 }, kolMore: false,
    /* 热度变化与阶段观点：折线悬停桶 / 展开的阶段 / 悬停的阶段 */ heatHover: null, stageOpen: null, stageHover: null,
    /* 两块图表的绘图宽度：按面板实际宽度测量，随窗口变化重算，不再固定 1344 */ trendW: 1344
  };
  trendBoxRef = (el) => { this._trendBox = el; this.measureTrend(); };
  measureTrend() {
    var el = this._trendBox; if (!el) return;
    var w = Math.max(720, Math.round(el.clientWidth - 40));
    if (w && Math.abs(w - this.state.trendW) >= 2) this.setState({ trendW: w });
  }

  constructor(props) { super(props); this.trendRef = React.createRef(); }

  componentDidMount() {
    this._onResize = () => this.measureTrend();
    window.addEventListener('resize', this._onResize);
    this.measureTrend();
    /* 字体就绪后强制重画一次：画布文字在 webfont 载入前后度量不同，否则刻度会错位 */
    try {
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => { this._sig = null; this.drawTrend(); });
      }
    } catch (e) { /* 环境不支持 FontFaceSet */ }
    var p = {};
    try {
      var u = new URLSearchParams(window.location.search);
      var c = u.get('code'), rg = u.get('range'), ev = u.get('ev'), risk = u.get('risk');
      if (c && R.MASTER[c]) p.code = c;
      if (rg && R.PRESETS.some(function (x) { return x.k === rg; })) p.rangeKey = rg;
      if (ev) {
        var c2 = p.code || this.state.code, r2 = p.rangeKey || this.state.rangeKey;
        /* 整块 null 时这里本来会抛，被外层 catch 吞成「深链参数不可用」—— 结果是对的，
           但用异常当控制流，下次真有个拼写错误也会被同一个 catch 咽掉。显式跳过。 */
        var evPos = R.themesFor(c2, r2, 'positive'), evNeg = R.themesFor(c2, r2, 'negative');
        var th = (evPos == null || evNeg == null ? [] : evPos.concat(evNeg))
          .filter(function (x) { return x.id === ev; })[0];
        if (th) p.panel = {
          kind: 'theme', id: th.id, polarity: th.polarity, title: th.title,
          filter: (th.polarity === 'positive' ? '积极观点' : '消极观点') + ' · ' + th.title,
          count: th.evidenceCount
        };
      }
      if (risk) {
        var c3 = p.code || this.state.code, r3 = p.rangeKey || this.state.rangeKey;
        var cr0 = naBox(R.complianceFor(c3, r3));
        var it0 = cr0.list.filter(function (x) { return x.id === risk; })[0] || null;
        if (cr0.status === 'ok') p.panel = this.riskPanel(cr0, it0, c3, r3);
      }
    } catch (e) { /* 深链参数不可用时使用默认产品 */ }
    this.setState(p, () => this.drawTrend());
  }
  componentDidUpdate() { this.drawTrend(); }
  componentWillUnmount() { clearTimeout(this._lt); window.removeEventListener('resize', this._onResize); }

  go(patch) {
    var st = this.state;
    if ((patch.code && patch.code !== st.code) || (patch.rangeKey && patch.rangeKey !== st.rangeKey)) {
      patch = Object.assign({}, patch, { loading: true, hover: null, heatHover: null, stageOpen: null, stageHover: null });
      clearTimeout(this._lt);
      this._lt = setTimeout(() => this.setState({ loading: false }), 520);
    }
    this.setState(patch, () => {
      try {
        var u = '/product?code=' + this.state.code + '&range=' + this.state.rangeKey;
        window.history.replaceState(null, '', u);
      } catch (e) { /* 沙箱内可能不允许改写地址 */ }
    });
  }
  seg(active, label, patch) {
    return {
      label: label, go: () => this.go(patch),
      fw: active ? 600 : 400,
      fg: active ? '#fff' : 'var(--ink-600)',
      bg: active ? 'var(--csop-blue-600)' : '#fff'
    };
  }
  dfg(d) { return d.dir > 0 ? 'var(--positive-700)' : (d.dir < 0 ? 'var(--negative-600)' : 'var(--ink-400)'); }

  /* 切换筛选后，若当前产品不在结果中则选中第一只，并同步 URL */
  refilter(patch) {
    var next = Object.assign({}, this.state, patch);
    var list = this.filtered(next);
    if (list.length && list.every(function (o) { return o.code !== next.code; })) patch.code = list[0].code;
    patch.panel = null; patch.post = null;
    this.go(patch);
  }
  /* 候选产品＝整池按当前筛选取子集。设计源写的是 `ORDER.filter(用 MASTER 判).map(observe)`，
     这里直接在 `pool().list` 上筛 —— 同一批对象、同一个顺序（池就是 ORDER.map(observe)），
     而观测本身就带着 sector / isNew / name，不必再回 MASTER 查一遍。
     顺带把「可见集从哪儿来」这件事说死了：候选是池的子集，不是主数据的子集（铁律 3）。 */
  filtered(st) {
    var q = (st.q || '').trim().toLowerCase();
    return R.pool(st.rangeKey).list.filter(function (o) {
      if (st.secF !== 'all' && o.sector !== st.secF) return false;
      if (st.newF === 'new' && !o.isNew) return false;
      if (st.newF === 'existing' && o.isNew) return false;
      if (q && o.code.toLowerCase().indexOf(q) < 0 && o.name.toLowerCase().indexOf(q) < 0) return false;
      return true;
    }).sort(function (a, b) { return b.mentions - a.mentions; });
  }

  openPanel(p) { this.setState({ panel: p, post: p.post || null }); }

  trendGeo() { return { W: this.state.trendW || 1344, H: 340, L: 64, Rr: 136, t1: 28, h1: 152, t2: 224, h2: 104 }; }
  riskPanel(cr, item, code, rangeKey) {
    var range = R.buildRange(rangeKey);
    var seen = {}, all = [];
    cr.list.forEach(function (r) { r.riskLabels.forEach(function (l) { if (!seen[l]) { seen[l] = 1; all.push(l); } }); });
    var tags = item ? item.riskLabels.join('、') : all.join('、');
    return {
      kind: 'risk', id: 'risk', polarity: 'negative', title: '重点舆情（需合规关注）原文',
      filter: '风险标签 · ' + tags, count: cr.list.length, items: cr.list, post: item ? item.id : null,
      extra: [
        { k: '产品', v: code + ' ' + R.MASTER[code].name },
        { k: '时间范围', v: range.text + '（HKT）' },
        { k: '风险标签', v: tags },
        { k: '识别状态', v: 'AI 识别 · 待人工确认 — 系统只标记风险信号并提供原文，不判定言论真伪或产品是否违规' }
      ]
    };
  }
  kolPanel(k, code, rangeKey) {
    var range = R.buildRange(rangeKey);
    return {
      kind: 'kol', id: 'kol-' + k.kolName, polarity: 'neutral', title: k.kolName + ' 在评论区对 ' + code + ' 的提及',
      filter: '产品 ' + code + ' · ' + range.text + ' · KOL ' + k.kolName, count: k.evidenceCount, items: k.evidence,
      extra: [
        { k: '产品', v: code + ' ' + R.MASTER[code].name },
        { k: '时间范围', v: range.text + '（HKT）' },
        { k: 'KOL', v: k.kolName + '（' + k.kolTypeLabel + ' · ' + String(k.kolTags || '').split(',').join(' · ') + '）' },
        { k: '统计口径', v: '只计该 KOL 在相关帖子评论区中实际提及该产品的评论，同一条评论只计一次；每条可跳转 Futu 原文' }
      ]
    };
  }

  drawTrend() {
    var cv = this.trendRef.current; if (!cv) return;
    var s = this.state, lg = s.legend;
    var mode = this.props.candleColor || 'greenUp';
    var upC = mode === 'redUp' ? '#C53030' : (mode === 'neutral' ? '#6E7A8A' : '#1F8A5B');
    var dnC = mode === 'redUp' ? '#1F8A5B' : (mode === 'neutral' ? '#6E7A8A' : '#C53030');
    var sig = s.code + '|' + s.rangeKey + '|' + s.hover + '|' + JSON.stringify(lg) + '|' + mode + '|' + s.trendW + '|' + this.props.dataVersion;
    if (this._sig === sig) return;
    this._sig = sig;
    var range = R.buildRange(s.rangeKey);
    var cur = R.observe(s.code, s.rangeKey).buckets;
    var base = R.observe(s.code, s.rangeKey, 'bench').buckets;
    /* 行情源还没接（ADR-0017）⇒ 整块 null。`px.status` 会直接 TypeError，而这里是
       canvas 绘制路径 —— 崩在这儿连错误边界都接不到一个有意义的位置。 */
    var px = naBox(R.candlesFor(s.code, s.rangeKey));
    var pxOk = !!lg.px && px.status === 'ok';

    var G = this.trendGeo();
    var W = G.W, H = G.H, dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr;
    var g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);

    var L = G.L, pw = W - L - G.Rr, t1 = G.t1, h1 = G.h1, t2 = G.t2, h2 = G.h2;
    var AX_R = L + pw + 9, AX_P = L + pw + 64;   // 互动数右轴 · 独立价格轴（最右）
    this._geo = { L: L, pw: pw, n: cur.length, top: t1, bottom: t2 + h2 };

    /* 取标尺上界时把未知项**剔除**（而不是当成 0）。两者对上界的结果一样，
       但写成 filter 才说得清意图：下面 line() 遇到未知是断线，不是画到轴底。 */
    var maxOf = function (f) {
      var vals = cur.concat(base).map(function (b) { return b[f]; }).filter(function (v) { return v != null; });
      return vals.length ? Math.max.apply(null, vals) : 0;
    };
    /* 讨论轨道：评论数与活跃账号数同为计数且账号数 ≤ 评论数，共用左轴；互动数量级不同，单独右轴 */
    var maxC = Math.max(1, Math.max(maxOf('comments'), maxOf('active')) * 1.15);
    var maxI = Math.max(1, maxOf('interactions') * 1.15);
    var maxS = Math.max(1, Math.max.apply(null, cur.concat(base).map(function (b) { return Math.max(b.positive, b.negative); })) * 1.2);

    var step = pw / cur.length, xAt = function (i) { return L + step * i + step / 2; };

    /* 画布只画网格、刻度数字、K 线与折线；轴标题与时间轴由 DOM 承担，避免 CJK 字体未就绪时出现乱码 */
    function grid(top, h, leftMax, rightMax) {
      g.strokeStyle = '#E4E8EE'; g.lineWidth = 1;
      g.font = '500 10px JetBrains Mono, Consolas, monospace';
      for (var i = 0; i <= 4; i++) {
        var y = top + h - h / 4 * i;
        g.beginPath(); g.moveTo(L, y + 0.5); g.lineTo(L + pw, y + 0.5); g.stroke();
        g.fillStyle = '#909AAA';
        g.textAlign = 'right'; g.fillText(String(Math.round(leftMax / 4 * i)), L - 9, y + 3.5);
        if (rightMax != null) { g.textAlign = 'left'; g.fillText(String(Math.round(rightMax / 4 * i)), AX_R, y + 3.5); }
      }
    }
    /* 未知值处**断线**，不落到坐标轴底部。设计源那句 `b[field] || 0` 在镜像里无害
       （编出来的数永远齐全），接真库之后它会把「这一桶没采到」画成「这一桶是零」——
       图上这两件事长得一模一样，而看图的人会当成结论。断开的线至少能看出来缺了一段。
       （转发坏掉 ⇒ interactions 为 null，三态未标注 ⇒ positive/negative 为 null。） */
    function line(data, field, top, h, max, color, dashed) {
      var trace = function () {
        g.beginPath();
        var pen = false;
        data.forEach(function (b, i) {
          var v = b[field];
          if (v == null) { pen = false; return; }
          var x = xAt(i), y = top + h - (v / max) * h;
          if (pen) g.lineTo(x, y); else { g.moveTo(x, y); pen = true; }
        });
      };
      /* 当前区间折线加白色描边，压在 K 线之上仍清晰可读 */
      if (!dashed) { g.strokeStyle = 'rgba(255,255,255,0.9)'; g.lineWidth = 5; g.setLineDash([]); trace(); g.stroke(); }
      g.strokeStyle = color; g.lineWidth = dashed ? 1.6 : 2.2;
      g.setLineDash(dashed ? [5, 4] : []);
      trace(); g.stroke(); g.setLineDash([]);
      if (!dashed && data.length <= 32) {
        data.forEach(function (b, i) {
          var v = b[field];
          if (v == null) return;   // 同上：没有点，不是点在零位
          var x = xAt(i), y = top + h - (v / max) * h;
          g.beginPath(); g.arc(x, y, 2.8, 0, Math.PI * 2);
          g.fillStyle = '#fff'; g.fill(); g.strokeStyle = color; g.lineWidth = 1.8; g.stroke();
        });
      }
    }

    /* 价格标尺：两条轨道共用同一价格区间，各自映射到自身高度；刻度画在最右侧独立价格轴，不与舆情单位共用 */
    var pxLo = 0, pxHi = 1, dec = 2;
    if (pxOk) {
      var vals = [];
      px.list.forEach(function (c) { if (c.open != null) vals.push(c.high, c.low); });
      var hi = vals.length ? Math.max.apply(null, vals) : 1, lo = vals.length ? Math.min.apply(null, vals) : 0;
      var pad = (hi - lo) * 0.18 || hi * 0.004 || 1;
      pxLo = lo - pad; pxHi = hi + pad; dec = pxHi < 10 ? 3 : 2;
    }
    /* K 线叠加在轨道底层（折线画在其上）；非交易时段／休市日只铺浅底，不补造 K 线 */
    function candles(top, h) {
      if (!pxOk) return;
      var yP = function (v) { return top + h - (v - pxLo) / (pxHi - pxLo) * h; };
      g.font = '500 10px JetBrains Mono, Consolas, monospace';
      g.strokeStyle = '#C7CDD7'; g.lineWidth = 1;
      g.beginPath(); g.moveTo(AX_P - 7 + 0.5, top); g.lineTo(AX_P - 7 + 0.5, top + h); g.stroke();
      for (var i = 0; i <= 4; i++) {
        var y = top + h - h / 4 * i;
        g.beginPath(); g.moveTo(AX_P - 7, y + 0.5); g.lineTo(AX_P - 3, y + 0.5); g.stroke();
        g.fillStyle = '#4A5568'; g.textAlign = 'left';
        g.fillText((pxLo + (pxHi - pxLo) / 4 * i).toFixed(dec), AX_P, y + 3.5);
      }
      px.list.forEach(function (c, i) {
        if (c.open != null) return;
        g.fillStyle = 'rgba(14,42,82,0.035)';
        g.fillRect(L + step * i, top, step, h);
      });
      var bw = Math.max(3, Math.min(34, step * 0.62));
      g.globalAlpha = 0.86;
      px.list.forEach(function (c, i) {
        if (c.open == null) return;
        var x = xAt(i), up = c.close >= c.open, color = up ? upC : dnC;
        g.strokeStyle = color; g.lineWidth = 1.3;
        g.beginPath(); g.moveTo(x, yP(c.high)); g.lineTo(x, yP(c.low)); g.stroke();
        var yo = yP(c.open), yc = yP(c.close);
        var bt = Math.min(yo, yc), bh = Math.max(1.6, Math.abs(yo - yc));
        g.fillStyle = (mode === 'neutral' && up) ? '#fff' : color;
        g.fillRect(x - bw / 2, bt, bw, bh);
        g.strokeRect(x - bw / 2 + 0.5, bt + 0.5, bw - 1, Math.max(0.6, bh - 1));
      });
      g.globalAlpha = 1;
    }

    grid(t1, h1, maxC, maxI);
    grid(t2, h2, maxS, null);
    candles(t1, h1);
    candles(t2, h2);

    var fade = function (hex) { return rgba(hex, 0.42); };
    if (lg.comments) { line(base, 'comments', t1, h1, maxC, fade('#2361AD'), 1); line(cur, 'comments', t1, h1, maxC, '#2361AD', 0); }
    if (lg.active) { line(base, 'active', t1, h1, maxC, fade('#C9A961'), 1); line(cur, 'active', t1, h1, maxC, '#C9A961', 0); }
    if (lg.inter) { line(base, 'interactions', t1, h1, maxI, fade('#4A4E8C'), 1); line(cur, 'interactions', t1, h1, maxI, '#4A4E8C', 0); }
    if (lg.pos) { line(base, 'positive', t2, h2, maxS, fade('#1F8A5B'), 1); line(cur, 'positive', t2, h2, maxS, '#1F8A5B', 0); }
    if (lg.neg) { line(base, 'negative', t2, h2, maxS, fade('#C53030'), 1); line(cur, 'negative', t2, h2, maxS, '#C53030', 0); }

    g.strokeStyle = '#C7CDD7'; g.lineWidth = 1;
    [t1 + h1, t2 + h2].forEach(function (y) {
      g.beginPath(); g.moveTo(L, y + 0.5); g.lineTo(L + pw, y + 0.5); g.stroke();
    });

    /* 自然日分隔线（小时粒度）：日期文本本身由 DOM 时间轴渲染 */
    if (range.gran === 'hour') {
      range.buckets.forEach(function (b, i) {
        if (i === 0 || b.hour !== 0) return;
        var x0 = L + step * i;
        g.strokeStyle = '#D5DBE4'; g.lineWidth = 1; g.setLineDash([3, 3]);
        g.beginPath(); g.moveTo(x0 + 0.5, t1); g.lineTo(x0 + 0.5, t2 + h2); g.stroke(); g.setLineDash([]);
      });
    }

    /* 共享十字线：贯穿两条轨道 */
    if (s.hover != null && cur[s.hover]) {
      var hx = xAt(s.hover);
      g.strokeStyle = 'rgba(14,42,82,0.28)'; g.lineWidth = 1; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(hx, t1); g.lineTo(hx, t2 + h2); g.stroke(); g.setLineDash([]);
    }
  }

  renderVals() {
    var s = this.state, self = this;
    var code = R.MASTER[s.code] ? s.code : '3033';
    var m = R.MASTER[code];
    var range = R.buildRange(s.rangeKey);
    var P = R.pool(s.rangeKey);
    var o = R.observe(code, s.rangeKey);
    var bench = R.benchmark(code, s.rangeKey);
    var rk = R.ranks(s.rangeKey);
    /* `attitude` 整块可能为 null（三态要 AI 标注，ADR-0017）。设计源直接 `att.positive`，
       接真库时第一次渲染就 TypeError，整屏白。「没标注」与「样本不足」是六态里的两态
       （PRD §3.6），不能合并 —— 后者是算过之后的结论，前者根本没算。 */
    var att = o.attitude, attNa = att == null;
    var valid = attNa ? 0 : att.positive + att.negative;
    var allValid = attNa ? 0 : valid + att.neutral;

    var presets = R.PRESETS.map(function (p) {
      var on = p.k === s.rangeKey;
      return {
        label: p.label, go: () => self.go({ rangeKey: p.k, panel: null, post: null, hover: null }),
        fw: on ? 600 : 400, fg: on ? 'var(--csop-blue-700)' : 'var(--ink-600)',
        bg: on ? 'var(--csop-blue-50)' : '#fff'
      };
    });

    /* 产品选择器 */
    var selList = self.filtered(s);
    var secChips = [{ k: 'all', name: '全部', hue: 'var(--ink-400)' }].concat(R.SECTORS).map(function (c) {
      var on = c.k === s.secF;
      return {
        name: c.name, dot: c.hue, go: () => self.refilter({ secF: c.k }),
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-700)',
        bg: on ? 'var(--csop-navy-900)' : '#fff', bc: on ? 'var(--csop-navy-900)' : 'var(--border-2)'
      };
    });
    var newOptions = [['all', '全部'], ['new', '新品'], ['existing', '存量产品']].map(function (x) {
      var on = s.newF === x[0];
      return {
        label: x[1], go: () => self.refilter({ newF: x[0] }),
        fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff'
      };
    });

    /* 观点主题 */
    /* 时间桶色阶：统一 6 级离散蓝阶，绿红只用于态度语义 */
    var STEP = ['#F1F3F6', '#D7E6F8', '#A8CAEF', '#6D9FD9', '#3574BC', '#0E4A8C'];
    var themeCells = function (t) {
      var mx = Math.max(1, Math.max.apply(null, t.buckets.map(function (b) { return b.mentions; })));
      return t.buckets.map(function (b) {
        return {
          bg: b.mentions <= 0 ? STEP[0] : STEP[Math.min(5, Math.max(1, Math.ceil(b.mentions / mx * 5)))],
          title: (b.tip || '') + ' · 内容 ' + b.mentions + ' 条'
        };
      });
    };
    /* 热力条时间轴：起点、峰值、终点三个锚点，让色块能对上具体时段 */
    var themeSpan = function (t) {
      var bk = t.buckets || [], pk = bk.reduce(function (a, b) { return b.mentions > a.mentions ? b : a; }, bk[0]);
      var short = function (b) { return b ? String(b.tip || b.label || '').split(/[（(]/)[0].trim() : '—'; };
      var hasPk = pk && pk.mentions > 0;
      return {
        spanFrom: short(bk[0]), spanTo: short(bk[bk.length - 1]),
        spanPeak: hasPk ? '峰值 ' + short(pk) : '各时段分布均匀'
      };
    };
    /* 消极观点行内合并原「负面舆情监控」的关注程度、生命周期与出现时间 */
    var sevStyle = { '高': ['var(--negative-100)', 'var(--negative-700)'], '中': ['var(--warning-100)', 'var(--warning-700)'], '低': ['var(--ink-100)', 'var(--ink-600)'] };
    var lifeStyleT = { '新增': ['var(--negative-100)', 'var(--negative-700)'], '持续': ['var(--warning-100)', 'var(--warning-700)'], '消退': ['var(--ink-100)', 'var(--ink-600)'] };
    var themeMeta = function (t) {
      if ('hasMeta' in t) {
        var severity = t.severityLabel, lifecycle = t.lifecycleLabel;
        var severityStyle = sevStyle[severity] || ['var(--ink-100)', 'var(--ink-600)'];
        var lifecycleStyle = lifeStyleT[lifecycle] || ['var(--ink-100)', 'var(--ink-600)'];
        return {
          hasMeta: t.hasMeta, severity: severity == null ? '暂不可用' : severity,
          sevBg: severityStyle[0], sevFg: severityStyle[1],
          life: lifecycle == null ? '暂不可用' : lifecycle,
          lifeBg: lifecycleStyle[0], lifeFg: lifecycleStyle[1],
          first: t.firstSeenAt == null ? '数据暂不可用' : t.firstSeenAt,
          last: t.lastSeenAt == null ? '数据暂不可用' : t.lastSeenAt
        };
      }
      var sev = t.share >= 25 ? '高' : (t.share >= 12 ? '中' : '低');
      var isNewCat = String(t.delta.short).indexOf('新增') >= 0;
      var life = isNewCat ? '新增' : (t.delta.dir < 0 ? '消退' : '持续');
      if (life === '消退') sev = sev === '高' ? '中' : sev;
      var hit = t.buckets.filter(function (b) { return b.mentions > 0; });
      var ss = sevStyle[sev], ls = lifeStyleT[life];
      return {
        hasMeta: true, severity: sev, sevBg: ss[0], sevFg: ss[1],
        life: life, lifeBg: ls[0], lifeFg: ls[1],
        first: hit.length ? hit[0].tip : '数据暂不可用',
        last: hit.length ? hit[hit.length - 1].tip : '数据暂不可用'
      };
    };
    var themeRow = function (t) {
      var meta = t.polarity === 'negative' ? themeMeta(t) : { hasMeta: false };
      return Object.assign(meta, themeSpan(t), {
        title: t.title, summary: t.summary, mentions: String(t.mentions),
        share: t.share.toFixed(0) + '%', confidence: t.confidence == null ? '数据暂不可用' : t.confidence + '%',
        delta: t.delta.short, dfg: self.dfg(t.delta), evidence: String(t.evidenceCount),
        cells: themeCells(t),
        go: () => self.openPanel({
          kind: 'theme', id: t.id, polarity: t.polarity,
          title: t.title, filter: (t.polarity === 'positive' ? '积极观点' : '消极观点') + ' · ' + t.title,
          count: t.evidenceCount,
          extra: t.polarity === 'negative' ? [
            { k: '关注程度', v: meta.severity + '（占消极 ' + t.share.toFixed(0) + '%，按明示规则判定）' },
            { k: '生命周期', v: meta.life + '（与基准区间同一主题比较后判定）' },
            { k: '出现时间', v: '首次 ' + meta.first + ' · 最近 ' + meta.last }
          ] : null
        })
      });
    };
    /* 主题聚类要 AI（`providers/sql.py::themes_for`）⇒ 整块 null，`null.slice` 硬崩。
       null 与 `[]` 在这里是两句不同的话：`[]` 是「聚过类了，这一极没有主题」（暂无
       相关内容），null 是「还没聚过」（暂不可用）。合并成一个空列表，页面会言之凿凿
       地说这只产品没有负面主题（铁律 2、PRD §3.6）。 */
    var posAll = R.themesFor(code, s.rangeKey, 'positive');
    var negAll = R.themesFor(code, s.rangeKey, 'negative');
    var posNa = posAll == null, negNa = negAll == null;
    if (posNa) posAll = [];
    if (negNa) negAll = [];
    var posThemes = (s.posMore ? posAll : posAll.slice(0, 3)).map(function (t) { return themeRow(t); });
    var negThemes = (s.negMore ? negAll : negAll.slice(0, 3)).map(function (t) { return themeRow(t); });

    /* 产品话题情绪 */
    var topicsAll = R.topicsFor(code, s.rangeKey);
    /* 顶层 `stale` 旗标（标注已更新、汇总待重新生成）。话题今天下发的是数组，数组上放不了
       旗标；后端若改成 `{list, stale}` 这里就地拆开，数组形状照旧走下面的 map。两种形状
       都认，是为了这条契约落地时前端不用再动一次。 */
    var topicsStale = false;
    if (topicsAll != null && !Array.isArray(topicsAll) && Array.isArray(topicsAll.list)) {
      topicsStale = topicsAll.stale === true; topicsAll = topicsAll.list;
    }
    var topicsNa = topicsAll == null;
    var topics = (topicsNa ? [] : topicsAll).map(function (t) {
      var mx = Math.max(1, Math.max.apply(null, t.buckets.map(function (b) { return b.mentions; })));
      return {
        title: t.title, summary: t.summary, mentions: String(t.mentions),
        pos: String(t.positive), neg: String(t.negative), neu: String(t.neutral),
        posPct: (t.positive / t.mentions * 100).toFixed(1),
        negPct: (t.negative / t.mentions * 100).toFixed(1),
        neuPct: (t.neutral / t.mentions * 100).toFixed(1),
        delta: t.delta.short, dfg: self.dfg(t.delta), evidence: String(t.evidenceCount),
        spark: t.buckets.map(function (b) { return { h: Math.max(2, Math.round(b.mentions / mx * 26)) }; }),
        go: () => self.openPanel({
          kind: 'topic', id: t.id, polarity: 'neutral', title: t.title,
          filter: '产品话题 · ' + t.title, count: t.evidenceCount,
          extra: [
            { k: '讨论规模', v: t.mentions + ' 条（积极 ' + t.positive + ' · 消极 ' + t.negative + ' · 中性 ' + t.neutral + '）' },
            { k: '时段分布', v: '讨论最集中于 ' + t.peak },
            { k: '观点分歧', v: t.split }
          ]
        })
      };
    });

    /* 关联竞品 */
    var compsRes = naBox(R.competitorsFor(code, s.rangeKey));
    var comps = compsRes.list.map(function (c) {
      var mk = function (polarity, themes) {
        return () => self.openPanel({
          kind: 'comp', id: c.code + polarity, polarity: polarity, title: c.code + ' ' + c.name,
          filter: (polarity === 'positive' ? '竞品积极观点' : '竞品消极观点') + ' · ' + c.code,
          count: polarity === 'positive' ? c.evidencePos : c.evidenceNeg,
          evidenceCode: c.code,
          extra: [
            { k: '关联关系', v: (c.relation === 'confirmed' ? '已确认' : 'AI 生成 · 待确认') + ' — ' + c.reason },
            { k: '主要观点', v: (themes.length ? themes.map(function (t) { return t.title + '（' + t.mentions + ' 条）'; }).join('；') : '暂无相关内容') }
          ]
        });
      };
      return {
        code: c.code, name: c.name, issuer: c.issuer, reason: c.reason,
        relation: c.relation === 'confirmed' ? '已确认' : 'AI 生成 · 待确认',
        rbg: c.relation === 'confirmed' ? 'var(--csop-blue-50)' : 'var(--warning-100)',
        rfg: c.relation === 'confirmed' ? 'var(--csop-blue-700)' : 'var(--warning-700)',
        /* `num()` 就是 `toLocaleString('en-US')` 加一道空值关口，逐字等价，多的是
           null 分支 —— 设计源的 `|| 0` 会把「竞品这一项没取到」写成「0 条评论」。 */
        mentions: num(c.comments), delta: c.delta.short, dfg: self.dfg(c.delta),
        pos: c.positiveThemes.length ? c.positiveThemes.map(function (t) { return t.title; }).join('；') : '暂无相关内容',
        neg: c.negativeThemes.length ? c.negativeThemes.map(function (t) { return t.title; }).join('；') : '暂无相关内容',
        posEv: String(c.evidencePos), negEv: String(c.evidenceNeg),
        goPos: mk('positive', c.positiveThemes), goNeg: mk('negative', c.negativeThemes),
        open: () => self.go({ code: c.code, panel: null, post: null, posMore: false, negMore: false })
      };
    });

    /* 本轮新增：价格 K 线、产品相关 KOL、重点舆情（需合规关注）—— 均随 code + rangeKey 同步重算，不残留上一只产品的数据 */
    var px = naBox(R.candlesFor(code, s.rangeKey));
    var pxOn = !!s.legend.px;
    var candleMode = self.props.candleColor || 'greenUp';
    var candleUpTip = candleMode === 'redUp' ? '#F3A6A6' : '#8FE3B8', candleDnTip = candleMode === 'redUp' ? '#8FE3B8' : '#F3A6A6';
    var kolRes = naBox(R.kolMentionsFor(code, s.rangeKey));
    var kolAll = kolRes.list, kolShow = s.kolMore ? kolAll : kolAll.slice(0, 5);
    var ATT_STYLE = {
      positive: ['var(--positive-100)', 'var(--positive-700)'],
      negative: ['var(--negative-100)', 'var(--negative-700)'],
      neutral: ['var(--ink-100)', 'var(--ink-600)']
    };
    var kolRows = kolShow.map(function (k) {
      var st = k.dominantAttitude ? ATT_STYLE[k.dominantAttitude] : ['var(--canvas-alt)', 'var(--ink-500)'];
      return {
        name: k.kolName, type: k.kolTypeLabel, tags: String(k.kolTags || '').split(',').join(' · '),
        count: String(k.mentionCommentCount), last: k.lastMentionedAt,
        /* 有效样本 < 3 条时后端给 null，这里是「暂不可用」——PRD §3.6 六态里的字段级
           null 一律走这一态，设计源那句「样本不足」是六态里的另一态（低于判定阈值但
           仍有结论）。差别不在字数上：一个是「我们不知道」，一个是「我们知道但不下
           结论」。screen-diff 的 WHITELIST 有对应条目记录这处有意偏差。 */
        att: k.dominantLabel == null ? '暂不可用' : k.dominantLabel, abg: st[0], afg: st[1],
        excerpt: k.representativeExcerpt || '暂无相关内容', evidence: String(k.evidenceCount),
        go: () => self.openPanel(self.kolPanel(k, code, s.rangeKey))
      };
    });
    var cr = naBox(R.complianceFor(code, s.rangeKey));
    var riskRows = cr.list.map(function (r) {
      return {
        id: r.id,
        tags: r.riskLabels.map(function (l) { return { label: l }; }),
        time: r.publishedAt, excerpt: r.excerpt, rationale: r.detectionRationale,
        author: r.authorName, type: r.authorType, source: r.sourceKind, code: code, status: r.reviewLabel,
        tbg: r.isKnownKol ? 'var(--csop-blue-50)' : 'var(--canvas-alt)',
        tfg: r.isKnownKol ? 'var(--csop-blue-700)' : 'var(--ink-700)',
        go: () => self.openPanel(self.riskPanel(cr, r, code, s.rangeKey))
      };
    });

    /* DOM 时间轴：与画布的绘图区（左 64 右 76）严格同宽，逐桶等分，标签不会互相压叠 */
    var axisBks = range.buckets;
    var axisSkip = range.gran === 'hour' ? 1 : (axisBks.length > 20 ? Math.ceil(axisBks.length / 16) : 1);
    var axisCells = axisBks.map(function (b, i) {
      var head = range.gran === 'hour' && b.hour === 0;
      var show = range.gran === 'hour' ? (b.hour % 6 === 0) : (i % axisSkip === 0);
      return {
        v: show ? (range.gran === 'hour' ? String(b.hour).padStart(2, '0') : (b.label || String(i + 1))) : '',
        fw: head ? 600 : 500,
        fg: head ? 'var(--ink-800)' : 'var(--ink-500)',
        tick: show ? (head ? 11 : 6) : 3,
        tickC: head ? 'var(--ink-600)' : 'var(--border-2)'
      };
    });
    var dayBands = [];
    if (range.gran === 'hour') {
      axisBks.forEach(function (b) {
        var last = dayBands[dayBands.length - 1];
        if (!last || last.day !== b.day) dayBands.push({ day: b.day, n: 1 });
        else last.n++;
      });
      dayBands = dayBands.map(function (d) { return { label: String(d.day).slice(5), flex: d.n }; });
    }

    /* 顶部产品搜索：命中即出下拉，点击直接切换当前产品 */
    var pqQ = (s.pq || '').trim().toLowerCase();
    var pqHits = pqQ ? P.list.filter(function (o) {
      return o.code.toLowerCase().indexOf(pqQ) >= 0 || o.name.toLowerCase().indexOf(pqQ) >= 0;
    }).sort(function (a, b) { return b.mentions - a.mentions; }) : [];

    /* 总结要 AI 归纳（`providers/sql.py::summary_for`）⇒ 整块 null。
       `String(null.text)` 是硬 TypeError；而退成空字符串又会让右栏「有效样本」写出
       「0 条」—— 那是铁律 2 明令禁止的那种谎。所以这里立旗标，值位统一走长文案。 */
    var sum = R.summaryFor(code, s.rangeKey);
    var sumNa = sum == null;
    if (sumNa) sum = { text: '', sample: null, low: false };
    /* 总结按句拆成要点；过短的片段并入上一条 */
    var sumPts = [], sumBuf = '';
    Array.from(String(sum.text || '')).forEach(function (ch) {
      sumBuf += ch;
      if ('。；！'.indexOf(ch) >= 0) { sumPts.push(sumBuf.trim()); sumBuf = ''; }
    });
    if (sumBuf.trim()) sumPts.push(sumBuf.trim());
    sumPts = sumPts.reduce(function (acc, p) {
      if (p.length < 14 && acc.length) acc[acc.length - 1] += p; else acc.push(p);
      return acc;
    }, []);
    var trendLegend = [
      { key: 'comments', label: '评论数', color: '#2361AD' },
      { key: 'active', label: '活跃账号数', color: '#C9A961' },
      { key: 'inter', label: '互动数', color: '#4A4E8C' },
      { key: 'pos', label: '积极内容数', color: '#1F8A5B' },
      { key: 'neg', label: '消极内容数', color: '#C53030' },
      { key: 'px', label: candleMode === 'redUp' ? 'K 线（红涨绿跌）' : (candleMode === 'neutral' ? 'K 线（空心涨 · 实心跌）' : 'K 线（绿涨红跌）'), color: candleMode === 'redUp' ? '#C53030' : (candleMode === 'neutral' ? '#8B95A3' : '#1F8A5B') }
    ].map(function (l) {
      return {
        label: l.label, color: l.color, op: s.legend[l.key] ? 1 : 0.32,
        go: () => {
          var nx = Object.assign({}, self.state.legend); nx[l.key] = nx[l.key] ? 0 : 1;
          self.setState({ legend: nx });
        }
      };
    });

    var out = {
      navGroups: navGroups('portfolio', 'product'), presets: presets,
      rangeText: range.text, rangeFrom: range.from, rangeTo: range.to,
      granLabel: range.granLabel, benchText: range.benchText, benchLabel: range.benchLabel,
      updated: stamp(R.UPDATED), rangeKey: s.rangeKey,
      loading: s.loading, bodyOpacity: s.loading ? '0.45' : '1',
      code: code, name: o.name, sectorName: o.sectorName, struct: o.struct, issuer: o.issuer,
      listing: m.listingDate,
      ownLabel: o.ownership === 'own' ? '自家产品' : '竞品',
      ownBg: o.ownership === 'own' ? 'var(--csop-blue-50)' : 'var(--ink-100)',
      ownFg: o.ownership === 'own' ? 'var(--csop-blue-700)' : 'var(--ink-600)',
      newLabel: m.isNew ? '新品' : '存量产品',
      newBg: m.isNew ? 'var(--warning-100)' : 'var(--canvas-alt)',
      newFg: m.isNew ? 'var(--warning-700)' : 'var(--ink-700)',

      pq: s.pq, pqHas: !!s.pq, pqOpen: !!(s.pq || '').trim(),
      pqBc: (s.pq || '').trim() ? 'var(--csop-blue-600)' : 'var(--border-2)',
      onPq: (e) => this.setState({ pq: e.target.value }),
      pqClear: () => this.setState({ pq: '' }),
      pqList: pqHits.slice(0, 10).map(function (p) {
        return {
          code: p.code, name: p.name, sector: p.sectorName, mentions: String(p.mentions),
          bg: p.code === code ? 'var(--csop-blue-50)' : '#fff',
          go: () => self.go({ code: p.code, pq: '', panel: null, post: null, posMore: false, negMore: false })
        };
      }),
      pqEmpty: pqHits.length === 0,

      selOpen: s.selOpen, toggleSel: () => this.setState({ selOpen: !s.selOpen }),
      selCaret: s.selOpen ? '▲' : '▼',
      selBc: s.selOpen ? 'var(--csop-blue-600)' : 'var(--border-2)',
      selBg: s.selOpen ? 'var(--csop-blue-50)' : '#fff',
      selFg: s.selOpen ? 'var(--csop-blue-700)' : 'var(--ink-800)',
      q: s.q, onSearch: (e) => this.refilter({ q: e.target.value }),
      secChips: secChips, newOptions: newOptions,
      resultCount: String(selList.length),
      selEmpty: selList.length === 0,
      selList: selList.slice(0, 60).map(function (p) {
        return {
          code: p.code, name: p.name, rank: String(rk.map[p.code]),
          mentions: numRaw(p.mentions), sector: p.sectorName,
          own: p.ownership === 'own' ? '自家' : '竞品',
          obg: p.ownership === 'own' ? 'var(--csop-blue-50)' : 'var(--ink-100)',
          ofg: p.ownership === 'own' ? 'var(--csop-blue-700)' : 'var(--ink-600)',
          newTag: p.isNew ? '新品' : '存量',
          nbg: p.isNew ? 'var(--warning-100)' : 'var(--canvas-alt)',
          nfg: p.isNew ? 'var(--warning-700)' : 'var(--ink-600)',
          bg: p.code === code ? 'var(--csop-blue-50)' : '#fff',
          go: () => self.go({ code: p.code, selOpen: false, panel: null, post: null, posMore: false, negMore: false })
        };
      }),

      kpis: [
        { label: '评论量', value: num(o.comments), d: bench.comments, note: '区间内被识别为讨论该 ETF 的评论条数，同一账号同一条只计一次' },
        /* `heatUnknownPosts > 0` 时热度／转发是**下限**（这些帖子的转发数没采到），备注里
           说出来；环比的悬浮提示带上当期与基准期各有几帖未知（`bench.heatUnknownPosts`）。
           demo 下没有这两个键，`undefined > 0` 为假、title 为 undefined ⇒ 一个字不多。 */
        {
          label: '讨论热度', value: num(o.discussionHeat), d: bench.heat,
          note: R.HEAT_FORMULA + '　·　点赞 ' + num(o.likes) + ' ／ 转发 ' + num(o.shares) + heatLowerBoundNote(o.heatUnknownPosts),
          deltaTitle: bench.heatUnknownPosts && (bench.heatUnknownPosts.current > 0 || bench.heatUnknownPosts.base > 0)
            ? '环比按下限计算：当期 ' + num(bench.heatUnknownPosts.current) + ' 帖、基准期 ' + num(bench.heatUnknownPosts.base) + ' 帖转发数未知'
            : undefined
        },
        { label: '活跃账号数', value: num(o.activeAccounts), d: bench.accounts, note: o.activeAccounts == null ? '该产品的账号口径尚未核验' : '区间内发布或评论过的独立账号' },
        { label: '全市场评论量排名', value: '第 ' + rk.map[code], d: { short: '／ ' + rk.total + ' 只', dir: 0 }, note: '基于完整活跃 ETF 池计算，板块筛选不重算' }
      ].map(function (k) { return { label: k.label, value: k.value, note: k.note, delta: k.d.short, dfg: self.dfg(k.d), deltaTitle: k.deltaTitle }; }),

      summary: sum.text, sampleN: sumNa ? '数据暂不可用' : String(sum.sample), sampleOk: !sumNa,
      summaryNa: sumNa,
      summaryPoints: sumPts.map(function (p, i) { return { n: String(i + 1), text: p }; }),
      summaryCountText: sumNa ? '数据暂不可用' : sumPts.length + ' 条要点 · 基于 ' + sum.sample + ' 条有效样本',
      /* P7 元信息末尾的如实声明（ADR-0019 §4）：徽章说的是这一条怎么来的，这句说的是
         整页的 AI 结论被验证到了什么程度。文案跟 /meta 的 `aiValidation` 走。 */
      aiValidationNote: aiValidationNote(R.AI_VALIDATION, R.AI_VALIDATION_DETAIL),
      /* 徽章三件事按顺序说：整块缺失 → 样本不足 → `aiStatus === 'unavailable'`（后端只给了
         计数句、AI 要点还没生成，这时不许写「AI 生成」）→ 正常；最后若 `stale` 为 true 追加
         「 · 待更新」。后两个键 demo 下不存在，走不到。 */
      aiLabel: sumNa ? '暂不可用' : (sum.low ? '样本不足 · 不输出倾向结论'
        : (sum.aiStatus === 'unavailable' ? '计数句 · AI 要点待生成' : 'AI 生成 · 可追溯原文') + staleSuffix(sum.stale)),
      aiBg: sumNa ? 'var(--ink-100)' : (sum.low ? 'var(--ink-100)' : 'var(--warning-100)'),
      aiFg: sumNa ? 'var(--ink-500)' : (sum.low ? 'var(--ink-700)' : 'var(--warning-700)'),
      hasSummaryEvidence: !sumNa && (sum.evidenceCount != null ? sum.evidenceCount > 0 : o.mentions > 0),
      /* 证据条数用后端的 `evidenceCount`。设计源那句 `Math.round(o.mentions * 0.4)` 是
         演示稿的伪造系数，真库下是编数；没有这个键（demo）时沿用原写法以保持逐字比对。 */
      summaryEvidence: String(sum.evidenceCount != null ? sum.evidenceCount : Math.round(o.mentions * 0.4)),
      openSummaryEvidence: () => self.openPanel({
        kind: 'summary', id: 'sum', polarity: 'neutral', title: '当前舆情总结的支撑原文',
        filter: '全部产品相关内容', count: sum.evidenceCount != null ? sum.evidenceCount : Math.round(o.mentions * 0.4)
      }),

      netText: attNa
        ? '数据暂不可用 · 不输出倾向结论'
        : (att.sampleSufficient
          ? (att.positive === att.negative ? '积极与消极条数持平' : (att.positive > att.negative ? '积极比消极多 ' + (att.positive - att.negative) + ' 条' : '消极比积极多 ' + (att.negative - att.positive) + ' 条'))
          : '样本不足 · 不输出倾向结论'),
      netFg: !attNa && att.sampleSufficient ? (att.positive >= att.negative ? 'var(--positive-700)' : 'var(--negative-700)') : 'var(--ink-500)',
      posTotal: attNa ? '数据暂不可用' : String(att.positive),
      negTotal: attNa ? '数据暂不可用' : String(att.negative),
      neuTotal: attNa ? '数据暂不可用' : String(att.neutral),
      posShare: attNa ? '数据暂不可用' : (valid ? (att.positive / valid * 100).toFixed(1) + '%' : '—'),
      negShare: attNa ? '数据暂不可用' : (valid ? (att.negative / valid * 100).toFixed(1) + '%' : '—'),
      /* 条形宽度是几何量不是数字位：没数据时两边各半，画出来是一条中性的灰条，
         旁边的文字位已经说了「数据暂不可用」。 */
      posBarPct: valid ? (att.positive / valid * 100).toFixed(2) : '50',
      negBarPct: valid ? (att.negative / valid * 100).toFixed(2) : '50',
      posDelta: bench.positive.text, posDfg: self.dfg(bench.positive),
      negDelta: bench.negative.text, negDfg: self.dfg(bench.negative),
      neuShare: attNa ? '数据暂不可用' : (allValid ? (att.neutral / allValid * 100).toFixed(1) + '%' : '—'),
      validN: attNa ? '数据暂不可用' : String(valid),
      lowSample: !attNa && !att.sampleSufficient, attNa: attNa, threshold: String(R.LOW_SAMPLE),

      posSummary: posAll.length ? posAll[0].summary : '',
      negSummary: negAll.length ? negAll[0].summary : '',
      negActionable: negNa ? '数据暂不可用' : String(negAll.filter(function (t) { return 'hasMeta' in t ? t.hasMeta && t.severityLabel !== '低' : t.share >= 12; }).length),
      /* na 时导语留空，缺失态交给下面的 posUnavailable 框 —— 「区间内没有可归类的积极观点」
         是**空态**的话（已聚类、这一极没有），拿它盖 null 就是替没做过的事下结论。 */
      posLead: posNa ? '' : posAll.length
        ? ('hasMeta' in posAll[0] ? '主要集中于' + posAll.slice(0, 2).map(function (t) { return t.title; }).join('、') + '。'
          : '主要集中于' + posAll.slice(0, 2).map(function (t) { return t.title; }).join('、')
          + '，合计 ' + posAll.slice(0, 2).reduce(function (a, t) { return a + t.mentions; }, 0) + ' 条，占积极内容 '
          + Math.round(posAll.slice(0, 2).reduce(function (a, t) { return a + t.share; }, 0)) + '%。')
        : '区间内没有可归类的积极观点。',
      negLead: negNa ? '' : negAll.length
        ? ('hasMeta' in negAll[0] ? '主要集中于' + negAll.slice(0, 2).map(function (t) { return t.title; }).join('、') + '。'
          : '主要集中于' + negAll.slice(0, 2).map(function (t) { return t.title; }).join('、')
          + '，合计 ' + negAll.slice(0, 2).reduce(function (a, t) { return a + t.mentions; }, 0) + ' 条，占消极内容 '
          + Math.round(negAll.slice(0, 2).reduce(function (a, t) { return a + t.share; }, 0)) + '%。')
        : '区间内没有可归类的消极观点。',
      posThemes: posThemes, negThemes: negThemes,
      noPos: !posNa && posThemes.length === 0, noNeg: !negNa && negThemes.length === 0,
      posUnavailable: posNa, negUnavailable: negNa,
      posMoreVisible: posAll.length > 3, negMoreVisible: negAll.length > 3,
      posMoreLabel: s.posMore ? '收起' : '展开全部 ' + posAll.length + ' 条',
      negMoreLabel: s.negMore ? '收起' : '展开全部 ' + negAll.length + ' 条',
      posToggle: () => this.setState({ posMore: !s.posMore }),
      negToggle: () => this.setState({ negMore: !s.negMore }),

      trendTitle: range.trendTitle, trendRef: this.trendRef, trendLegend: trendLegend,
      trendMove: (e) => {
        var geo = self._geo; if (!geo) return;
        var r = e.currentTarget.getBoundingClientRect();
        var x = e.clientX - r.left, y = e.clientY - r.top;
        var i = Math.floor((x - geo.L) / geo.pw * geo.n);
        if (i < 0 || i >= geo.n) { if (s.hover != null) self.setState({ hover: null }); return; }
        if (i !== s.hover) self.setState({ hover: i, hoverPos: { x: x, y: y, w: r.width, h: r.height } });
      },
      trendLeave: () => self.setState({ hover: null }),
      pxOk: pxOn && px.status === 'ok', pxUnavailable: pxOn && px.status !== 'ok',
      pxCurrency: px.currency || '', pxKLabel: candleMode === 'redUp' ? '红涨绿跌' : (candleMode === 'neutral' ? '空心为涨、实心为跌' : '绿涨红跌'),
      pxMeta: px.status === 'ok' ? ('行情粒度 ' + px.granLabel + ' · ' + px.currency + (px.missing ? ' · ' + px.missing + ' 个时间桶无行情' : '')) : '价格数据暂不可用',
      pxNote: px.status === 'ok' ? ('行情粒度为' + px.granLabel + '、价格单位 ' + px.currency + '，非交易时段与休市日不补造 K 线') : '当前产品价格数据暂不可用，不以指数或其他产品价格替代',
      kolScope: kolRes.scope, kolHas: kolRes.status === 'ok', kolEmpty: kolRes.status === 'empty', kolUnavailable: kolRes.status === 'unavailable',
      kolRows: kolRows, kolMoreVisible: kolAll.length > 5,
      kolMoreLabel: s.kolMore ? '收起，只看前 5 位' : '展开全部 ' + kolAll.length + ' 位',
      kolToggle: () => this.setState({ kolMore: !s.kolMore }),
      riskHas: cr.status === 'ok', riskEmpty: cr.status === 'empty', riskUnavailable: cr.status === 'unavailable', riskNa: cr.status === 'na',
      riskN: String(cr.list.length), riskRows: riskRows,

      axisCells: axisCells, dayBands: dayBands, hasDayBands: dayBands.length > 1,
      hasTopics: topics.length > 0, noTopics: !topicsNa && topics.length === 0, topicsUnavailable: topicsNa, topics: topics,
      /* 各块汇总的「待更新」旗标（只认 `=== true`；demo 下没有这个键，全为 false，不渲染）。 */
      topicsStale: topicsStale, themesStale: R.themesStale(code, s.rangeKey) === true,
      compsStale: compsRes.stale === true, staleTitle: STALE_TITLE,
      hasComps: comps.length > 0, noComps: comps.length === 0, comps: comps,
      compCount: String(comps.length),
      compScopeText: o.ownership === 'own' ? '自家产品 · 固定关联竞品与 AI 自动候选' : '竞品产品 · 反向展示对位自家产品与同类竞品',
      compsEmptyText: compsRes.status === 'unavailable'
        ? '数据暂不可用 — 该产品的竞品关系字段尚未核验，不展示推测关联。'
        : '暂无相关内容 — 映射表与自动识别在当前区间内都没有给出关联产品。',

      panelOpen: !!s.panel, panelX: s.panel ? '0' : '100%',
      closePanel: () => this.setState({ panel: null, post: null })
    };

    if (s.hover != null && o.buckets[s.hover]) {
      /* 逐桶环比随 benchmark 一起下发（bench.buckets 与 base.buckets 逐桶对齐）。
         悬停不可能每次打一趟接口，而环比是 PRD 第 3 章的全局口径，屏幕里不能自己算
         那个减法（铁律 1）—— `series` 是后端的序列键，`key` 是图例开关的键，两者不同名。 */
      var cb = o.buckets[s.hover], bb = bench.base.buckets[s.hover], db = bench.buckets[s.hover];
      var rows = [
        { key: 'comments', series: 'comments', label: '评论数', color: '#2361AD', c: cb.comments, b: bb.comments },
        { key: 'active', series: 'active', label: '活跃账号数', color: '#C9A961', c: cb.active, b: bb.active },
        { key: 'inter', series: 'interactions', label: '互动数', color: '#4A4E8C', c: cb.interactions, b: bb.interactions },
        { key: 'pos', series: 'positive', label: '积极内容数', color: '#1F8A5B', c: cb.positive, b: bb.positive },
        { key: 'neg', series: 'negative', label: '消极内容数', color: '#C53030', c: cb.negative, b: bb.negative }
      ].filter(function (r) { return s.legend[r.key]; }).map(function (r) {
        var d = db[r.series];
        /* 这张悬浮卡不打千分位（设计源逐字 String()），所以走 numRaw —— null 仍然
           必须是「数据暂不可用」，而不是字符串 'null'，更不是 0。 */
        return { label: r.label, color: r.color, cur: numRaw(r.c), base: numRaw(r.b), delta: d.text, dfg: d.dir > 0 ? '#8FE3B8' : (d.dir < 0 ? '#F3A6A6' : 'rgba(255,255,255,0.6)') };
      });
      out.hoverOpen = true;
      out.hoverTitle = cb.tip + '　·　基准同位 ' + bb.tip;
      out.hoverRows = rows;
      var hc = px.list[s.hover];
      out.hoverPxOn = pxOn;
      out.hoverPxTitle = code + ' 价格';
      out.hoverPxMeta = px.status === 'ok' ? (px.granLabel + ' · ' + px.currency) : '';
      out.hoverPxHas = !!(pxOn && px.status === 'ok' && hc && hc.open != null);
      out.hoverPxNone = !!(pxOn && !out.hoverPxHas);
      out.hoverPxNote = px.status !== 'ok' ? '价格数据暂不可用' : ('无行情 · ' + ((hc && hc.note) || '非交易时段'));
      out.hoverPx = out.hoverPxHas ? (function () {
        var dec = hc.high < 10 ? 3 : 2, up = hc.close >= hc.open, tip = up ? candleUpTip : candleDnTip;
        var chg = hc.close - hc.open, pct = hc.open ? chg / hc.open * 100 : 0;
        return [
          { k: '开盘', v: hc.open.toFixed(dec), fg: '#fff' },
          { k: '最高', v: hc.high.toFixed(dec), fg: '#fff' },
          { k: '最低', v: hc.low.toFixed(dec), fg: '#fff' },
          { k: '收盘', v: hc.close.toFixed(dec), fg: tip },
          { k: '涨跌', v: (chg >= 0 ? '+' : '') + chg.toFixed(dec) + '（' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%）', fg: tip }
        ];
      })() : [];
      /* 避让：水平翻到光标的另一侧，垂直上靠顶部空白带，不遮住柱体与数据点 */
      var TIPW = 336;
      var hp = s.hoverPos || { x: 40, y: 40, w: 1200, h: 320 };
      var left = hp.x + 26;
      if (left + TIPW > hp.w - 10) left = hp.x - TIPW - 26;
      out.hoverX = Math.max(10, Math.min(left, Math.max(10, hp.w - TIPW - 10)));
      out.hoverY = 10;
    } else {
      out.hoverOpen = false; out.hoverTitle = ''; out.hoverRows = []; out.hoverX = 0; out.hoverY = 0;
      out.hoverPxOn = false; out.hoverPx = []; out.hoverPxHas = false; out.hoverPxNone = false; out.hoverPxTitle = ''; out.hoverPxMeta = ''; out.hoverPxNote = '';
    }

    /* 热度变化与阶段观点（第三轮 D1）：与上方趋势面板共用同一水平几何（左 64 / 右 136），随 code + rangeKey 同步重算，不残留上一只产品的数据 */
    var SG = naBox(R.stagesFor(code, s.rangeKey), ['series', 'stages']);
    var HG = { W: s.trendW || 1344, L: 64, Rr: 136, top: 14, ph: 112 };
    var hpw = HG.W - HG.L - HG.Rr, hsr = SG.series || [];
    var hn = Math.max(1, hsr.length), hstep = hpw / hn;
    var hmax = Math.max(1, Math.max.apply(null, hsr.map(function (p) { return p.heat; }).concat([1])) * 1.15);
    var hx = function (i) { return HG.L + hstep * i + hstep / 2; };
    var hy = function (v) { return HG.top + HG.ph - v / hmax * HG.ph; };
    var isHalf = SG.granularity === 'half_day';
    var NUMS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩', '⑪', '⑫'];
    var numOf = function (n) { return NUMS[n - 1] || String(n); };
    var heatPath = hsr.map(function (p, i) { return (i ? 'L' : 'M') + hx(i).toFixed(1) + ' ' + hy(p.heat).toFixed(1); }).join(' ');
    var stageOf = function (i) { return SG.stages.filter(function (st) { return i >= st.idxFrom && i <= st.idxTo; })[0] || null; };
    var hh = (s.heatHover != null && hsr[s.heatHover]) ? hsr[s.heatHover] : null, hhStage = hh ? stageOf(hh.i) : null;
    var lowTone = ['var(--ink-100)', 'var(--ink-500)'];
    var heatDays = [];
    if (isHalf) {
      var seenDay = {};
      hsr.forEach(function (p) { if (!seenDay[p.day]) { seenDay[p.day] = { day: p.day, from: p.i, to: p.i }; heatDays.push(seenDay[p.day]); } else seenDay[p.day].to = p.i; });
    }
    var every = isHalf ? 6 : (hsr.length > 14 ? 5 : (hsr.length > 7 ? 2 : 1));
    Object.assign(out, {
      trendW: String(HG.W), heatGridW: String(hpw), axRX: String(HG.W - 127), axPX: String(HG.W - 72), trendBoxRef: self.trendBoxRef,
      stageOk: SG.status === 'ok' || SG.status === 'low_sample', stageUnavailable: SG.status === 'unavailable', stageEmpty: SG.status === 'empty',
      stageAiLabel: 'AI 生成 · 可追溯原文' + staleSuffix(SG.stale), stageStale: SG.stale === true,
      stageGranLabel: SG.granLabel, heatGranLabel: isHalf ? '60 分钟' : '自然日', stageRule: SG.rule || R.STAGE_RULE, heatFormulaText: R.HEAT_FORMULA,
      heatPath: heatPath,
      heatArea: hsr.length ? heatPath + ' L' + hx(hsr.length - 1).toFixed(1) + ' ' + (HG.top + HG.ph).toFixed(1) + ' L' + hx(0).toFixed(1) + ' ' + (HG.top + HG.ph).toFixed(1) + ' Z' : '',
      heatGrid: [0, 1, 2, 3, 4].map(function (i) { var v = hmax / 4 * i; return { y: hy(v).toFixed(1), ly: (hy(v) - 4).toFixed(1), v: String(Math.round(v)) }; }),
      /* 样本不足的日子：折线下方灰点，不参与阶段合并（日粒度） */
      heatLowDots: isHalf ? [] : hsr.filter(function (p) { return p.mentions > 0 && (p.positive + p.negative) < (SG.threshold || R.LOW_SAMPLE); }).map(function (p) { return { x: hx(p.i).toFixed(1), tip: p.tip + ' · 样本不足，不参与阶段合并' }; }),
      heatCols: hsr.map(function (p, i) { return { x: (HG.L + hstep * i).toFixed(1), w: hstep.toFixed(1), in: () => self.setState({ heatHover: i }), out: () => { if (self.state.heatHover === i) self.setState({ heatHover: null }); } }; }),
      heatHoverOn: !!hh, heatHoverX: hh ? hx(hh.i).toFixed(1) : '0', heatHoverY: hh ? hy(hh.heat).toFixed(1) : '0',
      heatTipX: hh ? (hx(hh.i) + 12 + 250 > HG.W - HG.Rr + 60 ? hx(hh.i) - 262 : hx(hh.i) + 12).toFixed(1) : '0',
      heatTipTitle: hh ? hh.tip : '',
      heatTipRows: hh ? [
        { k: '讨论热度', v: hh.heat.toLocaleString('en-US') },
        { k: '评论量', v: hh.comments.toLocaleString('en-US') },
        { k: '积极 ／ 消极', v: hh.positive + ' ／ ' + hh.negative },
        { k: '所属阶段', v: hhStage ? numOf(hhStage.n) + ' ' + hhStage.categoryLabel : '—' }
      ] : [],
      heatAxis: hsr.filter(function (p, i) { return isHalf ? !!p.label : (i % every === 0); }).map(function (p) { return { x: hx(p.i).toFixed(1), label: p.label }; }),
      hasHeatDays: heatDays.length > 0,
      heatDays: heatDays.map(function (d) { return { x: (HG.L + hstep * d.from).toFixed(1), w: (hstep * (d.to - d.from + 1)).toFixed(1), label: d.day.slice(5) }; }),
      stageBands: SG.stages.map(function (st) {
        var on = s.stageOpen === st.n || s.stageHover === st.n;
        return {
          n: numOf(st.n), x: (HG.L + hstep * st.idxFrom).toFixed(1), w: Math.max(1, hstep * (st.idxTo - st.idxFrom + 1)).toFixed(1),
          bg: st.band, fg: st.fg, tagBg: st.bg, op: on ? '1' : '0.8', ring: on ? 'inset 0 -2px 0 ' + st.fg : 'none',
          title: numOf(st.n) + ' ' + st.label + ' · ' + st.categoryLabel + ' · ' + st.summary,
          go: () => self.setState({ stageOpen: self.state.stageOpen === st.n ? null : st.n }),
          in: () => self.setState({ stageHover: st.n }), out: () => { if (self.state.stageHover === st.n) self.setState({ stageHover: null }); }
        };
      }),
      stageRows: SG.stages.map(function (st) {
        var open = s.stageOpen === st.n, hot = s.stageHover === st.n;
        var tone = ATT_STYLE[st.sentiment] || lowTone;
        return {
          n: numOf(st.n), label: st.label, sub: st.sub, cat: st.categoryLabel, catBg: st.bg, catFg: st.fg,
          hasSent: !!st.sentiment, sent: st.sentimentLabel, sbg: tone[0], sfg: tone[1],
          summary: st.summary, sumFg: st.category ? 'var(--ink-900)' : 'var(--ink-400)',
          sample: st.sample.toLocaleString('en-US'), hasEv: st.evidenceCount > 0, noEv: st.evidenceCount === 0, evidence: String(st.evidenceCount),
          open: open, caret: open ? '▲' : '▼', rowBg: (open || hot) ? 'var(--csop-blue-50)' : '#fff',
          unitNote: (isHalf ? '按上午／下午／盘后' : '按自然日') + '逐段归纳 · ' + st.unitCount + ' 个时段'
            + (st.lowUnits ? ' · 其中 ' + st.lowUnits + ' 段样本不足，已并入相邻阶段' : '') + (st.absorbed ? ' · 含 ' + st.absorbed + ' 个单日孤立观点并入' : ''),
          digests: st.digests.map(function (d) {
            var dt = ATT_STYLE[d.tone] || lowTone;
            return { label: d.label, sub: d.sub, tone: d.toneLabel, tbg: dt[0], tfg: dt[1], digest: d.digest, dfg: d.sufficient ? 'var(--ink-800)' : 'var(--ink-400)', meta: '提及 ' + d.mentions + ' · 热度 ' + d.heat.toLocaleString('en-US') };
          }),
          toggle: () => self.setState({ stageOpen: open ? null : st.n }),
          in: () => self.setState({ stageHover: st.n }), out: () => { if (self.state.stageHover === st.n) self.setState({ stageHover: null }); },
          openEvidence: (e) => {
            e.stopPropagation();
            self.openPanel({
              kind: 'stage', id: 'stage-' + st.n + '-' + st.from, polarity: st.sentiment || 'neutral',
              title: '阶段观点原文 · ' + st.label, filter: '产品 ' + code + ' · ' + st.from + ' ～ ' + st.to + ' · ' + st.categoryLabel, count: st.evidenceCount,
              extra: [
                { k: '产品', v: code + ' ' + R.MASTER[code].name },
                { k: '阶段区间', v: st.from + ' ～ ' + st.to + (isHalf ? '（' + st.sub + '）' : '') + '（HKT）· 自动带入当前产品与该阶段日期区间' },
                { k: '观点分类', v: st.categoryLabel + ' · ' + st.sentimentLabel },
                { k: '阶段总结', v: st.summary },
                { k: '口径', v: '阶段总结描述讨论区观点，不表述与价格的因果关系；AI 生成，每条可跳转 Futu 原文' }
              ]
            });
          }
        };
      })
    });

    if (s.panel) {
      var p = s.panel;
      var evCode = p.evidenceCode || code;
      /* 条数钳位（不传取 6、上限 12）是口径，实现在后端，这里原样转发 p.count（铁律 1）。 */
      var itemsRes = p.items || R.evidenceFor(evCode, s.rangeKey + '|' + (p.id || p.kind), p.polarity, p.count);
      /* 原文证据要 AI 挑（`providers/sql.py::evidence_for`）⇒ 整块 null。抽屉是覆盖层，
         它抛异常整页跟着白。空列表只是让下面的 `.map` 有东西可遍历，缺失态由
         `panelUnavailable` 单独渲染 —— 不与「已检索、没有符合条件的原文」混为一谈。 */
      var itemsNa = itemsRes == null;
      var items = itemsNa ? [] : itemsRes;
      var typeStyle = {
        '普通散户': ['var(--canvas-alt)', 'var(--ink-700)'],
        '合作 KOL': ['var(--csop-blue-50)', 'var(--csop-blue-700)'],
        '官方账号': ['var(--warning-100)', 'var(--warning-700)']
      };
      out.panelTitle = p.title || '原文证据';
      out.panelFilter = p.filter || '全部内容';
      out.panelCount = itemsNa ? '数据暂不可用' : String(items.length);
      out.panelCountOk = !itemsNa;
      out.panelUnavailable = itemsNa;
      out.panelHasExtra = !!(p.extra && p.extra.length);
      out.panelExtra = p.extra || [];
      out.panelEmpty = !itemsNa && items.length === 0;
      out.evidence = items.map(function (e) {
        var ts = typeStyle[e.authorType] || typeStyle['普通散户'];
        var open = s.post === e.id;
        return {
          id: e.id,
          time: e.publishedAt, author: e.authorName == null ? '数据暂不可用' : e.authorName,
          type: e.authorType, tbg: ts[0], tfg: ts[1],
          excerpt: e.excerpt,
          codes: e.productCodes.map(function (c) { return { code: c }; }),
          comments: e.comments == null ? '暂不可用' : e.comments.toLocaleString('en-US'),
          inter: e.interactions == null ? '暂不可用' : e.interactions.toLocaleString('en-US'),
          url: e.sourceUrl || '#', stop: (ev) => ev.stopPropagation(),
          source: e.sourceKind || '评论',
          hasAtt: !!e.attitudeLabel, att: e.attitudeLabel || '',
          abg: (ATT_STYLE[e.attitude] || ['var(--ink-100)', 'var(--ink-600)'])[0],
          afg: (ATT_STYLE[e.attitude] || ['var(--ink-100)', 'var(--ink-600)'])[1],
          hasRisk: !!(e.riskLabels && e.riskLabels.length),
          riskTags: (e.riskLabels || []).map(function (l) { return { label: l }; }),
          rationale: e.detectionRationale || '',
          open: open, expandLabel: open ? '收起单帖详情' : '展开单帖详情',
          bg: open ? 'var(--canvas)' : '#fff',
          bc: open ? 'var(--csop-blue-400)' : 'var(--border-1)',
          detail: [
            { k: '发布时间', v: e.publishedAt + ' HKT' },
            { k: '账号类型', v: e.authorType },
            { k: '相关 ETF', v: e.productCodes.join(' · ') },
            { k: '评论 ／ 互动', v: (e.comments == null ? '暂不可用' : e.comments) + ' ／ ' + (e.interactions == null ? '暂不可用' : e.interactions) }
          ].concat(e.riskLabels && e.riskLabels.length ? [
            { k: '风险标签', v: e.riskLabels.join('、') },
            { k: '识别状态', v: 'AI 识别 · 待人工确认' }
          ] : []),
          go: () => self.setState({ post: open ? null : e.id })
        };
      });
    } else {
      out.panelTitle = ''; out.panelFilter = ''; out.panelCount = '0'; out.panelCountOk = true;
      out.panelHasExtra = false; out.panelExtra = []; out.panelEmpty = false; out.panelUnavailable = false; out.evidence = [];
    }

    return out;
  }

  render() {
    const v = this.renderVals()

    return (
      <div data-screen-label="产品监控" style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px')}>

        <Shell vals={v}>
          <FilterBar v={v} />
        </Shell>

        {v.loading && (
          <div style={s('position:sticky;top:144px;z-index:30;display:flex;align-items:center;gap:10px;margin:14px 24px 0;padding:9px 15px;border:1px solid var(--csop-blue-200);border-radius:6px;background:var(--csop-blue-50)')}>
            <span style={s('width:8px;height:8px;border-radius:9999px;background:var(--csop-blue-600)')}></span>
            <span style={s('font:500 14px/1.4 var(--font-cjk);color:var(--csop-blue-800)')}>数据加载中 — 正在按新的产品与日期范围重新聚合舆情、KOL、重点舆情与行情数据</span>
          </div>
        )}

        <div style={s(`padding:22px 24px 72px;opacity:${v.bodyOpacity};transition:opacity 200ms cubic-bezier(.4,0,.2,1)`)}>
          <ProductHeader v={v} />
          <Overview v={v} />
          <KolList v={v} />
          <Attitude v={v} />
          <Risk v={v} />
          <Trend v={v} />
          <Stages v={v} />
          <Topics v={v} />
          <Competitors v={v} />
        </div>

        <EvidenceDrawer v={v} />
      </div>
    )
  }
}
