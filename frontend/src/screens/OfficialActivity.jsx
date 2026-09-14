/* Port of design/official-activity.dc.html (官号动态).

   The .dc.html logic class is already a React class component minus `render()`, so
   everything from `state` through `renderVals()` below is the design source verbatim.
   Two deliberate deviations, both forced by the move off the dc runtime:
     - `window.RADAR` is a synchronous import here, so the componentDidMount poll that
       waited for the runtime's async <script> to land is gone.
     - `{{ }}` holes became JSX; `style="…"` strings are parsed by `s()` and
       `style-hover` / `style-focus` by `hover()` / `focus()`. */
import React from 'react'
import R from '../data/radar'
import { num, conf2, sumN, descN, typeStyle, shell, rgba, CAMP, POST_TYPES, TYPE_BY_KEY, reviewBadge, needsReview } from '../lib/view'
import { s, hover, focus } from '../lib/dc'
import Shell from '../components/Shell'
import DcLink from '../components/DcLink'

export default class OfficialActivity extends React.Component {
  /* From the .dc.html `data-props` block: editor-tweakable knobs and their defaults. */
  static defaultProps = {
    lowConfidence: 0.7,
    feedLayout: '双列',
    campRule: '挂载标的 ∪ 正文提及',
  }

  state = {
    rangeKey: 'd7', tab: '全部', acct: null, prod: 'ALL', prodMenu: false, feedSort: 'time', open: {}, shown: 30,
    /* 官号清单表内筛选 */ lq: '', onlyActive: false, listSort: 'posts',
    /* 发帖流表内筛选 */ fq: '', acctMenu: false, prodQ: '', types: [], typeMenu: false, camp: 'ALL',
    /* 来自官号清单 ETF 芯片的联动来源（官号 × ETF），其他方式改动官号/产品筛选时清空 */ src: null,
    /* 每行 ETF 芯片条的溢出测量（key＝官号简称）：{ hidden, fade }，只读 DOM，经 state 回到模板，不直接写 DOM */ strip: {}
  };
  componentDidMount() {
    this._onResize = () => this.syncStrips();
    window.addEventListener('resize', this._onResize);
    this.syncStrips();
  }
  componentDidUpdate() { this.syncStrips(); }
  componentWillUnmount() { window.removeEventListener('resize', this._onResize); }
  /* 任何筛选变化都把发帖流折回首屏；官号/产品被其他方式改动时清掉「来自官号清单」来源 */
  go(patch) {
    if (!('shown' in patch)) patch.shown = 30;
    if (!('src' in patch) && (('acct' in patch) || ('prod' in patch))) patch.src = null;
    this.setState(patch);
  }
  /* ETF 芯片点击：发帖流只剩 该官号 × 该 ETF × 当前日期范围，并滚到发帖流 */
  pickEtf(acct, code, name) {
    this.go({ acct: acct, prod: code, prodQ: '', types: [], camp: 'ALL', fq: '', acctMenu: false, prodMenu: false, typeMenu: false, src: { acct: acct, code: code, name: name } });
    setTimeout(() => {
      var el = this.feedEl; if (!el) return;
      window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: 'smooth' });
    }, 40);
  }
  feedRef = (el) => { this.feedEl = el; };
  /* 芯片条横向溢出：右侧渐隐 + 尾部「还有 N 只」，随滚动实时更新。提交后统一量一遍所有芯片条（只读 DOM），
     整体有变化才 setState 一次，由模板渲染；不直接写 DOM，也不在 ref 回调里 setState（两者都会与宿主形成重渲染循环） */
  syncStrips() {
    var cur = this.state.strip || {}, next = {}, changed = false, n = 0;
    var keys = this._stripKeys || [];
    document.querySelectorAll('[data-etf-strip]').forEach(function (el, idx) {
      var key = keys[idx]; if (!key) return;
      var right = el.scrollLeft + el.clientWidth, hidden = 0, kids = el.children;
      for (var i = 0; i < kids.length; i++) { if (kids[i].getAttribute('data-chip') && kids[i].offsetLeft + kids[i].offsetWidth > right + 2) hidden++; }
      var over = el.scrollWidth > el.clientWidth + 2, atEnd = right >= el.scrollWidth - 2;
      next[key] = { hidden: hidden, fade: over && !atEnd };
      n++;
      var c = cur[key]; if (!c || c.hidden !== hidden || c.fade !== next[key].fade) changed = true;
    });
    if (Object.keys(cur).length !== n) changed = true;
    if (!changed) { this._stripDepth = 0; return; }
    /* 防止极端情况下的测量振荡：连续变化超过 6 轮就停下，等下一次滚动／缩放再量 */
    this._stripDepth = (this._stripDepth || 0) + 1;
    if (this._stripDepth > 6) return;
    this.setState({ strip: next });
  }
  primaryOnly() { return this.props.campRule === '仅挂载标的'; }
  camp(p) { return this.primaryOnly() ? p.campPrimary : p.camp; }
  codesOf(p) { return this.primaryOnly() ? [p.code] : p.mentioned.map(function (m) { return m.code; }); }

  renderVals() {
    var s = this.state, self = this;
    var norm = function (x) { return String(x == null ? '' : x).toLowerCase().trim(); };
    /* 阵营三分互斥：仅提自家 / 仅提竞品 / 双方都提 */
    var campOk = function (c, want) { return want === 'ALL' || c === want; };
    var segOf = function (items, cur, key) {
      return items.map(function (x) {
        var on = cur === x[0], patch = {}; patch[key] = x[0];
        return { label: x[1], go: function () { self.go(patch); }, fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff' };
      });
    };
    /* `lowConfidence` 这一屏不再读：徽章改由 `review_state` 驱动（ADR-0019 §2），
       而这一屏没有任何一处把阈值本身印出来。另外两屏的脚注还印着它，所以它们留着。 */
    var cols = this.props.feedLayout === '单列' ? 1 : 2;
    var out = shell('accounts', 'official', s, function (x) { self.go(x); });
    var feedAll = R.officialPosts(s.rangeKey);
    var range = R.buildRange(s.rangeKey);
    /* 三态。判据是 `reviewState`（模型自己有没有举手），不是置信度 —— ADR-0019 §2：
       `calibrated_confidence` 整列为 NULL，`lowConfidence` 在校准概率存在之前不生效；
       而 `null < 0.7` 在 JS 里是 true（null 当 0 用），照置信度写会让 sql provider 下
       **每一篇**官号帖都挂上「待确认」—— 那说的是「模型给了个低分」，不是「模型没跑」。 */
    var pending = function (p) { return needsReview(p.reviewState); };
    /* 这一条结论的如实声明（ADR-0019 §2／§4）。指不到原文就不说「可追溯原文」。 */
    var review = function (p) { return reviewBadge(p.reviewState, p.evidenceIdx != null && p.evidenceIdx >= 0); };
    /* AI 标注整块未生成（ADR-0017 §4） */
    var annNa = function (p) { return p.postType == null; };
    var campsOf = function (p) { var c = self.camp(p); return c === 'both' ? [CAMP.own, CAMP.competitor] : [CAMP[c] || CAMP.none]; };

    /* 账号级汇总由帖子流聚合而来，随区间变化 */
    var acc = R.OFFICIAL.map(function (o) {
      var ps = feedAll.filter(function (p) { return p.account === o.short; });
      return {
        short: o.short, full: o.full, comps: o.comps, type: o.comps > 0 ? '发行商官号' : '平台运营',
        url: o.url,
        /* `engagement` ＝ 赞＋评论＋转发，而转发数在源库里有约 0.03% 的行是坏的
           （raw_json 被 TEXT 列截断，ADR-0008），那些帖子的 engagement 是 null。
           设计源这里是 `reduce(t + p.engagement, 0)` —— 镜像里的数永远齐全，所以没事；
           接真库之后它会把「这一篇不知道」算成「这一篇是 0」，账号的互动合计悄悄少一截。 */
        posts: ps.length, inter: sumN(ps, function (p) { return p.engagement; })
      };
    });
    out.tabs = ['全部', '发行商官号', '平台运营'].map(function (t) {
      var on = s.tab === t;
      return { label: t, go: function () { self.go({ tab: t, acct: null, prod: 'ALL' }); }, fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff' };
    });
    /* 官号清单表内筛选：名称 × 类型（与顶部三态同一状态）× 仅有发布，排序可切篇数 / 互动 */
    var lqq = norm(s.lq);
    var emOf = function (o) { return R.etfMentionsFor(o.short, s.rangeKey); };
    var list = acc.filter(function (o) {
      return (s.tab === '全部' || o.type === s.tab) && (!lqq || norm(o.short + ' ' + o.full).indexOf(lqq) >= 0) && (!s.onlyActive || o.posts > 0);
    }).sort(s.listSort === 'inter'
      ? function (a, b) { return descN(a.inter, b.inter) || b.posts - a.posts; }
      : (s.listSort === 'etf'
        ? function (a, b) { var ea = emOf(a), eb = emOf(b); return eb.etfCount - ea.etfCount || eb.total - ea.total || b.posts - a.posts; }
        : function (a, b) { return b.posts - a.posts || descN(a.inter, b.inter); }));
    out.listSorts = segOf([['posts', '按篇数'], ['inter', '按互动'], ['etf', '按提及 ETF']], s.listSort, 'listSort');
    out.etfRule = R.ETF_MENTION_RULE;
    /* 芯片条按 DOM 顺序对应 list 顺序（每行一条），供 syncStrips 取 key；不能用 data-*="{{ }}" 传 key——运行时会卡死 */
    self._stripKeys = list.map(function (o) { return o.short; });
    out.lq = s.lq; out.lqBc = lqq ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.setLq = function (e) { self.setState({ lq: e.target.value }); };
    out.activeToggle = function () { self.setState({ onlyActive: !s.onlyActive }); };
    out.activeBc = s.onlyActive ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.activeBg = s.onlyActive ? 'var(--csop-blue-600)' : '#fff';
    out.activeFg = s.onlyActive ? '#fff' : 'var(--ink-600)';
    out.listCount = String(list.length);
    out.hasLf = !!lqq || s.onlyActive || s.tab !== '全部';
    out.clearLf = function () { self.go({ lq: '', onlyActive: false, tab: '全部' }); };
    var mx = Math.max.apply(null, acc.map(function (o) { return o.posts; }).concat([1]));
    out.listNote = (s.tab === '全部' ? '发行商官号与平台运营账号' : s.tab) + ' · ' + list.length + ' 个 · 按区间发布篇数';
    out.rows = list.map(function (o, i) {
      var iss = o.type === '发行商官号', on = s.acct === o.short;
      var em = emOf(o);
      var sm = s.strip[o.short] || { hidden: 0, fade: false };
      var rowBg = on ? 'var(--csop-blue-50)' : (i % 2 ? 'var(--canvas)' : '#fff');
      /* 芯片条用 React.createElement 直接构建：模板里的嵌套 sc-for 一旦累计上百个子项，模板引擎会卡死；
         这里每行渲染该官号提及的全部 ETF（自家在前、竞品在后，组间细分隔线），横向滚动查看，点击芯片联动发帖流 */
      var h = React.createElement;
      var chipEl = function (e) {
        var own = e.ownership === 'own', hit = on && s.prod === e.code;
        return h('span', {
          key: e.code, 'data-chip': '1',
          title: e.name + '（' + e.code + '.HK）· ' + (own ? '南方东英 · 自家' : '竞品 · ' + e.issuer) + ' · 出现 ' + e.count + ' 次 · 涉及 ' + e.posts + ' 帖 · 点击只看该官号关于此 ETF 的帖子',
          onClick: function (ev) { ev.stopPropagation(); self.pickEtf(o.short, e.code, e.short); },
          style: { flex: 'none', display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 9px', borderRadius: 9999, boxSizing: 'border-box',
            background: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', color: own ? '#fff' : 'var(--ink-700)',
            boxShadow: hit ? '0 0 0 2px var(--warning-600)' : 'none', font: '600 12px/1 var(--font-mono)', cursor: 'pointer', whiteSpace: 'nowrap' }
        }, e.code + ' ' + (e.short.length > 5 ? e.short.slice(0, 5) + '…' : e.short) + ' ×' + e.count);   /* 单个文本节点：整页上百枚芯片，不再嵌套 span，控制 DOM 节点数 */
      };
      var kids = em.own.map(chipEl);
      if (em.own.length && em.peer.length) kids.push(h('span', { key: '__div', style: { flex: 'none', width: 1, height: 16, background: 'var(--border-2)', margin: '0 2px' } }));
      kids = kids.concat(em.peer.map(chipEl));
      /* onScroll 走 React props，每次渲染重建，始终指向当前实例 */
      var stripEl = h('div', {
        'data-etf-strip': '1',
        onScroll: function () { if (self._stripRaf) return; self._stripRaf = requestAnimationFrame(function () { self._stripRaf = 0; self.syncStrips(); }); },
        style: { display: 'flex', alignItems: 'center', gap: 5, overflowX: 'auto', overflowY: 'hidden', padding: '1px 0 3px', scrollbarWidth: 'thin', scrollbarColor: 'var(--csop-silver-400) transparent' }
      }, kids);
      return {
        key: o.short,
        short: o.short, full: o.full, type: o.type, fw: on ? 600 : 500,
        posts: String(o.posts), inter: num(o.inter),
        hasEtf: em.list.length > 0, noEtf: em.list.length === 0,
        etfHead: em.list.length ? em.etfCount + ' 只 · ' + em.total + ' 次' : '— 区间内未提及 ETF',
        etfHeadTitle: '自家 ' + em.own.length + ' 只 · 竞品 ' + em.peer.length + ' 只 · 涉及 ' + em.postCount + ' 帖 · 仅 ETF，不含个股',
        etfStrip: stripEl,
        fadeDisplay: sm.fade ? 'block' : 'none', fadeBg: 'linear-gradient(to right, rgba(255,255,255,0), ' + rowBg + ' 28%)', tailBg: rowBg,
        tailDisplay: sm.hidden ? 'inline-flex' : 'none', tailText: sm.hidden ? '→ 还有 ' + sm.hidden + ' 只' : '',
        pct: String(Math.round(o.posts / mx * 100)),
        color: iss ? '#2361AD' : '#7A5C9E',
        tbg: iss ? 'var(--csop-blue-50)' : '#F1ECF7',
        tfg: iss ? 'var(--csop-blue-700)' : '#5E4480',
        url: o.url,
        bg: on ? 'var(--csop-blue-50)' : (i % 2 ? 'var(--canvas)' : '#fff'),
        stop: function (e) { e.stopPropagation(); },
        go: function () { self.go({ acct: on ? null : o.short }); }
      };
    });

    var isr = acc.filter(function (o) { return o.comps > 0; }), plt = acc.filter(function (o) { return o.comps === 0; });
    /* 同上：一个账号的互动合计不知道，全站的互动合计就不知道。
       原来那句 `|| 1` 是设计源的遗留（曾经当过百分比分母，后来那处没了）：合计真为 0
       时它会显示成「1」，合计为 null 时它会把「不知道」显示成「1」。两种都是编数字。 */
    var sumI = function (a) { return sumN(a, function (o) { return o.inter; }); };
    var tot = sumI(acc);
    /* `hasSummary` 三态：true 有、false 查过了没有（图片帖）、null 还没生成。
       原来的 `filter(p => p.hasSummary)` 把后两者并成一堆，于是 sql provider 下
       「已生成摘要 0 篇 · 全部是图片帖」—— 两个数都是编的。 */
    var withSummary = feedAll.filter(function (p) { return p.hasSummary === true; }).length;
    var noSummaryN = feedAll.filter(function (p) { return p.hasSummary === false; }).length;
    var summaryNaN = feedAll.filter(function (p) { return p.hasSummary == null; }).length;
    var pendingN = feedAll.filter(pending).length;
    var annNaN = feedAll.filter(annNa).length;
    out.kpis = [
      { label: '重点官号数', value: String(acc.length), unit: '个', sub: '客户提供的名单 · 发行商 ' + isr.length + ' · 平台运营 ' + plt.length },
      { label: '区间发布篇数', value: String(feedAll.length), unit: '篇', sub: range.from.slice(5) + ' ～ ' + range.to.slice(5) + ' · 互动合计 ' + num(tot) },
      { label: '提及自家产品', value: String(feedAll.filter(function (p) { var c = self.camp(p); return c === 'own' || c === 'both'; }).length), unit: '篇', sub: '官号帖子里提到南方东英产品的篇数（含同时提及竞品）' },
      {
        label: '已生成摘要',
        value: summaryNaN === feedAll.length && feedAll.length ? '数据暂不可用' : String(withSummary),
        unit: summaryNaN === feedAll.length && feedAll.length ? '' : '篇',
        vfg: summaryNaN === feedAll.length && feedAll.length ? 'var(--ink-400)' : 'var(--ink-900)',
        sub: summaryNaN === feedAll.length && feedAll.length
          ? 'AI 摘要与类型标注尚未生成 · ' + feedAll.length + ' 篇待标注'
          : noSummaryN + ' 篇图片帖无摘要 · ' + pendingN + ' 篇类型待确认'
            + (summaryNaN ? ' · 另有 ' + summaryNaN + ' 篇摘要尚未生成' : '')
      }
    ].map(function (x) { return { label: x.label, value: x.value, unit: x.unit, sub: x.sub, vfg: x.vfg || 'var(--ink-900)' }; });
    var top = isr.slice().sort(function (a, b) { return b.posts - a.posts || descN(a.inter, b.inter); }).slice(0, 8);
    var tmx = top.length ? Math.max(1, top[0].posts) : 1;
    out.topIssuers = top.map(function (o, i) {
      return {
        name: o.short, posts: String(o.posts), inter: num(o.inter), pct: String(Math.round(o.posts / tmx * 100)),
        color: rgba('#2361AD', 1 - i * 0.08),
        go: function () { self.go({ acct: s.acct === o.short ? null : o.short, tab: '全部' }); }
      };
    });

    /* 发帖流：官号三态 × 单个官号 × 产品 × 类型 × 阵营 × 关键词 */
    var tabPosts = feedAll.filter(function (p) { return s.tab === '全部' || p.accountType === s.tab; });
    var base = tabPosts.filter(function (p) { return !s.acct || p.account === s.acct; });

    /* 官号下拉：只列当前三态下的账号，计数为区间篮数；与清单点行是同一个 acct */
    var accts = acc.filter(function (o) { return s.tab === '全部' || o.type === s.tab; }).sort(function (a, b) { return b.posts - a.posts || descN(a.inter, b.inter); });
    out.acctLabel = s.acct || '全部';
    out.acctCaret = s.acctMenu ? '▲' : '▼';
    out.acctOpen = !!s.acctMenu;
    out.acctBc = (s.acctMenu || s.acct) ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.acctBg = s.acctMenu ? 'var(--csop-blue-50)' : '#fff';
    out.acctToggle = function () { self.setState({ acctMenu: !s.acctMenu, prodMenu: false, typeMenu: false }); };
    out.acctClose = function () { self.setState({ acctMenu: false }); };
    out.acctMenuNote = accts.length + ' 个官号 · 按区间发布篇数';
    out.acctMenu = [{ k: null, name: '全部官号', type: '', n: tabPosts.length, tbg: 'transparent', tfg: 'transparent' }].concat(
      accts.map(function (o) {
        var iss = o.type === '发行商官号';
        return { k: o.short, name: o.short + ' · ' + o.full, type: iss ? '发行商' : '平台运营', n: o.posts, tbg: iss ? 'var(--csop-blue-50)' : '#F1ECF7', tfg: iss ? 'var(--csop-blue-700)' : '#5E4480' };
      })).map(function (o) {
        var on = (s.acct || null) === o.k;
        return {
          key: o.k || '__all', name: o.name, type: o.type, n: String(o.n), tick: on ? '✓' : '', tbg: o.tbg, tfg: o.tfg,
          bg: on ? 'var(--csop-blue-50)' : '#fff',
          go: function () { self.go({ acct: o.k, acctMenu: false }); }
        };
      });

    /* 产品下拉：菜单内即时过滤（代码 / 名称 / 发行商） */
    var byCode = {};
    base.forEach(function (p) { self.codesOf(p).forEach(function (c) { byCode[c] = (byCode[c] || 0) + 1; }); });
    var pqq = norm(s.prodQ);
    out.prodQ = s.prodQ;
    out.setProdQ = function (e) { self.setState({ prodQ: e.target.value }); };
    out.prodLabel = s.prod === 'ALL' ? '全部' : s.prod;
    out.prodCaret = s.prodMenu ? '▲' : '▼';
    out.prodOpen = !!s.prodMenu;
    out.prodBc = (s.prodMenu || s.prod !== 'ALL') ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.prodBg = s.prodMenu ? 'var(--csop-blue-50)' : '#fff';
    out.prodToggle = function () { self.setState({ prodMenu: !s.prodMenu, acctMenu: false, typeMenu: false }); };
    out.prodClose = function () { self.setState({ prodMenu: false }); };
    var prodItems = [{ k: 'ALL', code: '全部', name: '全部产品', n: base.length, own: '', obg: 'transparent', ofg: 'transparent' }].concat(
      Object.keys(byCode).sort(function (a, b) { return byCode[b] - byCode[a] || (a < b ? -1 : 1); }).map(function (c) {
        var m = R.MASTER[c], own = m && m.ownership === 'own';
        return {
          k: c, code: c, name: m ? (own ? m.name : m.issuer + ' · ' + m.name) : c, n: byCode[c],
          own: own ? '自家' : '竞品', obg: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', ofg: own ? '#fff' : 'var(--ink-700)'
        };
      })).filter(function (o) { return o.k === s.prod || !pqq || norm(o.code + ' ' + o.name + ' ' + o.own).indexOf(pqq) >= 0; });
    out.prodMenuNote = '发帖流中被提及的 ' + Object.keys(byCode).length + ' 个产品（自家 + 竞品）' + (pqq ? ' · 匹配 ' + Math.max(0, prodItems.length - 1) + ' 个' : '');
    out.prodMenuEmpty = !!pqq && prodItems.length <= 1;
    out.prodMenu = prodItems.map(function (o) {
      var on = o.k === s.prod;
      return {
        key: o.k, code: o.code, name: o.name, n: String(o.n), tick: on ? '✓' : '', own: o.own, obg: o.obg, ofg: o.ofg,
        bg: on ? 'var(--csop-blue-50)' : '#fff',
        go: function () { self.go({ prod: o.k, prodMenu: false, prodQ: '' }); }
      };
    });
    var prodPosts = base.filter(function (p) { return s.prod === 'ALL' || self.codesOf(p).indexOf(s.prod) >= 0; });

    /* 类型多选：计数按 三态 × 官号 × 产品 的范围算 */
    /* 没标注的不进任何一类：`typeCounts[null]` 会开一个键名为字符串 "null" 的格子，
       八类的计数相加对不上总篇数，而页面上看不出少的那批去哪了。明说。 */
    var typeCounts = {}, typeNaN = 0;
    prodPosts.forEach(function (p) {
      if (p.postType == null) { typeNaN++; return; }
      typeCounts[p.postType] = (typeCounts[p.postType] || 0) + 1;
    });
    out.typeNaNote = typeNaN ? '另有 ' + typeNaN + ' 篇内容形式尚未标注，不计入下表' : '';
    out.typeLabel = !s.types.length ? '全部' : (s.types.length === 1 ? TYPE_BY_KEY[s.types[0]].label : s.types.length + ' 类');
    out.typeCaret = s.typeMenu ? '▲' : '▼';
    out.typeOpen = !!s.typeMenu;
    out.typeBc = (s.typeMenu || s.types.length) ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.typeBg = s.typeMenu ? 'var(--csop-blue-50)' : '#fff';
    out.typeToggle = function () { self.setState({ typeMenu: !s.typeMenu, acctMenu: false, prodMenu: false }); };
    out.typeClose = function () { self.setState({ typeMenu: false }); };
    out.typeAll = function () { self.go({ types: [] }); };
    out.typeMenu = POST_TYPES.map(function (t) {
      var on = s.types.indexOf(t.k) >= 0, st = typeStyle(t.k);
      return {
        key: t.k, label: t.label, def: t.def, n: String(typeCounts[t.k] || 0), tick: on ? '✓' : '',
        boxBc: on ? 'var(--csop-blue-600)' : 'var(--border-2)', boxBg: on ? 'var(--csop-blue-600)' : '#fff',
        tbg: st.bg, tfg: st.fg, bg: on ? 'var(--csop-blue-50)' : '#fff',
        go: function () { self.go({ types: on ? s.types.filter(function (k) { return k !== t.k; }) : s.types.concat([t.k]) }); }
      };
    });
    out.camps = segOf([['ALL', '全部'], ['own', '仅提自家'], ['competitor', '仅提竞品'], ['both', '双方都提']], s.camp, 'camp');

    /* 关键词：官号、产品代码 / 名称 / 发行商、摘要与原文 */
    var fqq = norm(s.fq);
    var fhay = function (p) {
      return norm([p.account, p.accountFull, p.summary]
        .concat(p.mentioned.map(function (m) { return m.code + ' ' + m.name + ' ' + m.issuer; }), p.fullText || []).join(' '));
    };
    var feed = prodPosts.filter(function (p) {
      return (!s.types.length || s.types.indexOf(p.postType) >= 0) && campOk(self.camp(p), s.camp) && (!fqq || fhay(p).indexOf(fqq) >= 0);
    });
    out.fq = s.fq; out.fqBc = fqq ? 'var(--csop-blue-600)' : 'var(--border-2)';
    out.setFq = function (e) { self.go({ fq: e.target.value }); };
    out.hasFf = !!fqq || !!s.acct || s.prod !== 'ALL' || s.types.length > 0 || s.camp !== 'ALL';
    out.clearFf = function () { self.go({ fq: '', acct: null, prod: 'ALL', prodQ: '', types: [], camp: 'ALL', src: null }); };
    /* 「来自官号清单」来源条：只在官号与产品仍与点击的芯片一致时显示 */
    var src = s.src && s.acct === s.src.acct && s.prod === s.src.code ? s.src : null;
    out.hasSrc = !!src;
    out.srcAcct = src ? src.acct : ''; out.srcCode = src ? src.code : ''; out.srcName = src ? src.name : '';
    out.clearSrc = function () { self.go({ acct: null, prod: 'ALL', prodQ: '', src: null }); };
    out.feedRef = self.feedRef;
    /* 未知的互动排最后，不是当成 0 混进最低那一段（descN，铁律 2）。 */
    if (s.feedSort === 'eng') feed.sort(function (a, b) { return descN(a.engagement, b.engagement); });
    else feed.sort(function (a, b) { return b.t - a.t; });
    out.feedSorts = [['time', '时间倒序'], ['eng', '互动降序']].map(function (x) {
      var on = s.feedSort === x[0];
      return { label: x[1], go: function () { self.go({ feedSort: x[0] }); }, fw: on ? 600 : 400, fg: on ? '#fff' : 'var(--ink-600)', bg: on ? 'var(--csop-blue-600)' : '#fff' };
    });
    out.feedCount = String(feed.length);
    out.feedEmpty = feed.length === 0;
    out.feedCols = String(cols);
    /* 只渲染前 N 张，计数与 KPI 仍按全量 */
    var PAGE = 30, shown = Math.min(feed.length, s.shown || PAGE);
    out.hasMore = feed.length > shown;
    out.shownCount = String(shown); out.pageSize = String(PAGE);
    out.loadMore = function () { self.setState({ shown: shown + PAGE }); };
    out.cards = feed.slice(0, shown).map(function (p) {
      var st = typeStyle(p.postType), open = !!s.open[p.id];
      return {
        key: p.id,
        account: p.account, accountType: p.accountType,
        atBg: p.isIssuer ? 'var(--csop-blue-50)' : '#F1ECF7', atFg: p.isIssuer ? 'var(--csop-blue-700)' : '#5E4480',
        time: p.time,
        type: st.label, tbg: st.bg, tfg: st.fg, pending: pending(p), conf: conf2(p.confidence), review: review(p),
        hasDir: !!p.hasDir, dir: p.dir ? p.dir.label : '', dbg: p.dir ? p.dir.bg : 'transparent', dfg: p.dir ? p.dir.fg : 'transparent',
        camps: campsOf(p),
        /* 「查过了，这篇只有图」和「还没查」共用一句就是替 AI 下了个它没下的判断。 */
        summary: p.hasSummary ? p.summary : '', noSummary: p.hasSummary === false, summaryNa: p.hasSummary == null,
        /* 这句承诺上面那段原文里有一句被标出来了。标注整块没生成时一句都没有，
           而那段原文本身也已经是缺失态 —— 照旧写死这句，两句话当场自相矛盾。
           只分出 null 这一支，`evidenceIdx === -1` 仍按设计源原样说（演示数据里
           真有 -1 的帖子，改它就是逐字比对里的一处分叉）。KolDetail 同一处。

           分隔符「 · 」在串里：设计源那行的「· 高亮句为判定依据」是**一个**文本节点，
           写成 `{c.conf} · {c.evidenceNote}` 会拆成两个，逐字比对报差异。 */
        evidenceNote: p.evidenceIdx == null ? ' · 判定依据尚未生成' : ' · 高亮句为判定依据',
        open: open, hasText: p.fullText != null,
        sentences: open ? (p.fullText || []).map(function (t, i) {
          var hit = i === p.evidenceIdx;
          return { text: t, bg: hit ? 'var(--warning-100)' : 'transparent', sh: hit ? 'inset 0 -2px 0 var(--warning-600)' : 'none' };
        }) : [],
        mentioned: p.mentioned.map(function (m) {
          var own = m.ownership === 'own';
          return {
            code: m.code, short: own ? '自家' : m.issuer, title: m.name + '（' + m.code + '.HK）· ' + (own ? '南方东英' : '竞品 · ' + m.issuer),
            bg: own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', fg: own ? '#fff' : 'var(--ink-700)'
          };
        }),
        likes: num(p.likes), comments: num(p.comments), shares: num(p.shares),
        url: p.url,
        openLabel: open ? '收起原文' : '原文',
        linkBg: open ? 'var(--csop-blue-50)' : '#fff',
        linkBc: open ? 'var(--csop-blue-600)' : 'var(--border-2)',
        linkFg: open ? 'var(--csop-blue-700)' : 'var(--csop-blue-600)',
        toggle: function () {
          var nx = Object.assign({}, self.state.open);
          if (nx[p.id]) delete nx[p.id]; else nx[p.id] = 1;
          self.setState({ open: nx });
        }
      };
    });
    return out;
  }

  render() {
    const v = this.renderVals()

    return (
      <div data-screen-label="官号动态" style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px')}>

        <Shell vals={v}>
          <div style={s('display:flex;align-items:center;gap:12px;min-height:48px;padding:7px 24px;background:var(--canvas-alt);border-bottom:1px solid var(--border-1);flex-wrap:wrap')}>
            <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
              {v.presets.map((p) => (
                <div key={p.label} onClick={p.go} style={s(`padding:6px 14px;font:${p.fw} 14px/1.4 var(--font-cjk);color:${p.fg};background:${p.bg};cursor:pointer;border-right:1px solid var(--border-1);transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>{p.label}</div>
              ))}
            </div>
            <div style={s('display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-mono);color:var(--ink-700);white-space:nowrap')}>
              <span>{v.rangeFrom}</span><span style={s('color:var(--ink-300)')}>→</span><span>{v.rangeTo}</span>
            </div>
            <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
              {v.tabs.map((f) => (
                <div key={f.label} onClick={f.go} style={s(`padding:6px 13px;font:${f.fw} 14px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
              ))}
            </div>
            <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;padding:5px 13px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>演示数据</div>
          </div>
        </Shell>

        <div style={s('padding:26px 24px 64px')}>

          <div style={s('display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding-bottom:20px;margin-bottom:22px;border-bottom:1px solid var(--border-2)')}>
            <div>
              <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.18em;color:var(--csop-blue-600);margin-bottom:10px')}>账号 · 官号动态</div>
              <div style={s('font:600 28px/1.2 var(--font-cjk);letter-spacing:-0.015em')}>重点官号发布与内容摘要</div>
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
                </div>
                <div style={s('margin-top:9px;font:400 13px/1.5 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{k.sub}</div>
              </div>
            ))}
          </div>

          <div style={s('display:grid;grid-template-columns:minmax(0,1fr) 264px;gap:18px;margin-bottom:20px')}>
            <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);display:flex;flex-direction:column;min-width:0')}>
              <div style={s('display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
                <div style={s('display:flex;align-items:baseline;gap:10px;min-width:0')}>
                  <div style={s('font:600 18px/1.3 var(--font-cjk)')}>官号清单</div>
                  <div style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>提及 ETF 按出现次数统计，只含 ETF，不含个股</div>
                </div>
                <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                  <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                  <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden')}>
                    {v.listSorts.map((f) => (
                      <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                    ))}
                  </div>
                </div>
              </div>
              <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
                <input type="text" value={v.lq} onChange={v.setLq} placeholder="搜官号简称 / 全称" style={s(`flex:none;width:190px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.lqBc};border-radius:6px;background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
                <div style={s('flex:none;display:flex;align-items:center;gap:7px')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>类型</span>
                  <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden;background:#fff')}>
                    {v.tabs.map((f) => (
                      <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                    ))}
                  </div>
                </div>
                <div onClick={v.activeToggle} style={s(`flex:none;display:flex;align-items:center;height:30px;box-sizing:border-box;padding:0 12px;border:1px solid ${v.activeBc};border-radius:9999px;background:${v.activeBg};font:500 13px/1.4 var(--font-cjk);color:${v.activeFg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)}>仅区间内有发布</div>
                <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:12px')}>
                  <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}><span style={s('font:600 14px/1 var(--font-mono);color:var(--ink-800)')}>{v.listCount}</span> 个</span>
                  {v.hasLf && (
                    <div onClick={v.clearLf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                  )}
                </div>
              </div>
              <div style={s('flex:1;min-height:0;max-height:392px;overflow-y:auto')}>
                <table style={s('table-layout:fixed')}>
                  <thead>
                    <tr style={s('background:var(--canvas-alt)')}>
                      <th style={s('position:sticky;top:0;z-index:2;width:128px;background:var(--canvas-alt);text-align:left;padding:10px 6px 10px 16px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>官号</th>
                      <th style={s('position:sticky;top:0;z-index:2;width:78px;background:var(--canvas-alt);text-align:left;padding:10px 6px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>类型</th>
                      <th style={s('position:sticky;top:0;z-index:2;width:88px;background:var(--canvas-alt);text-align:left;padding:10px 6px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>发布篇数</th>
                      <th style={s('position:sticky;top:0;z-index:2;width:62px;background:var(--canvas-alt);text-align:right;padding:10px 6px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>互动数</th>
                      <th title={v.etfRule} style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:10px 8px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>
                        <span style={s('display:flex;align-items:baseline;gap:8px;white-space:nowrap;overflow:hidden')}>提及 ETF（自家／竞品）<span style={s('min-width:0;overflow:hidden;text-overflow:ellipsis;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>按出现次数 · 仅 ETF · 点芯片看相关帖子 · 可横向滚动</span></span>
                      </th>
                      <th style={s('position:sticky;top:0;z-index:2;width:70px;background:var(--canvas-alt);text-align:right;padding:10px 16px 10px 6px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>主页</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.rows.map((r) => (
                      <tr key={r.key} onClick={r.go} style={s(`background:${r.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <td style={s('padding:11px 6px 11px 16px;border-bottom:1px solid var(--ink-100)')}>
                          <div style={s(`font:${r.fw} 14px/1.4 var(--font-cjk);color:var(--ink-900);white-space:nowrap;overflow:hidden;text-overflow:ellipsis`)}>{r.short}</div>
                          <div title={r.full} style={s('margin-top:2px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{r.full}</div>
                        </td>
                        <td style={s('padding:11px 6px;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                          <span style={s(`padding:2px 7px;border-radius:9999px;background:${r.tbg};font:600 12px/1.6 var(--font-cjk);color:${r.tfg}`)}>{r.type}</span>
                        </td>
                        <td style={s('padding:11px 6px;border-bottom:1px solid var(--ink-100)')}>
                          <span style={s('display:inline-flex;align-items:center;gap:7px')}>
                            <div style={s('width:44px;height:10px;background:var(--ink-100);border-radius:3px;overflow:hidden')}><div style={s(`width:${r.pct}%;height:100%;background:${r.color}`)}></div></div>
                            <span style={s('font:600 14px/1.4 var(--font-mono);width:24px;text-align:right')}>{r.posts}</span>
                          </span>
                        </td>
                        <td style={s('padding:11px 6px;text-align:right;font:600 14px/1.4 var(--font-mono);border-bottom:1px solid var(--ink-100)')}>{r.inter}</td>
                        <td style={s('padding:7px 8px;border-bottom:1px solid var(--ink-100);vertical-align:middle')}>
                          <div style={s('display:flex;flex-direction:column;gap:3px;min-width:0')}>
                            <span title={r.etfHeadTitle} style={s('font:500 11px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap')}>{r.etfHead}</span>
                            <div style={s('position:relative;min-width:0')}>
                              {r.etfStrip}
                              <span style={s(`display:${r.fadeDisplay};position:absolute;right:0;top:0;bottom:0;width:96px;background:${r.fadeBg};pointer-events:none`)}></span>
                              <span style={s(`display:${r.tailDisplay};position:absolute;right:0;top:2px;height:22px;align-items:center;padding:0 2px 0 8px;background:${r.tailBg};font:600 11px/1 var(--font-cjk);color:var(--ink-500);white-space:nowrap;pointer-events:none`)}>{r.tailText}</span>
                            </div>
                          </div>
                        </td>
                        <td style={s('padding:11px 16px 11px 6px;text-align:right;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                          <a href={r.url} target="_blank" rel="noopener" onClick={r.stop} style={s('display:inline-flex;align-items:center;padding:4px 9px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>主页 ↗</a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div style={s('display:flex;flex-direction:column;gap:18px;min-width:0')}>
              <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);padding:16px 20px 14px;flex:1')}>
                <div style={s('font:600 18px/1.3 var(--font-cjk);margin-bottom:4px')}>发行商官号发布强度 Top 8</div>
                <div style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:10px')}>仅统计发行商官号 · 篇数 / 互动 · 点击筛选发帖流</div>
                {v.topIssuers.map((t) => (
                  <div key={t.name} onClick={t.go} style={s('display:flex;align-items:center;gap:11px;padding:6px 0;border-bottom:1px solid var(--ink-100);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>
                    <span style={s('flex:none;width:80px;font:500 13px/1.4 var(--font-cjk);color:var(--ink-700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{t.name}</span>
                    <div style={s('flex:1;height:12px;background:var(--ink-100);border-radius:3px;overflow:hidden')}><div style={s(`width:${t.pct}%;height:100%;background:${t.color}`)}></div></div>
                    <span style={s('flex:none;width:26px;text-align:right;font:600 13px/1.4 var(--font-mono)')}>{t.posts}</span>
                    <span style={s('flex:none;width:58px;text-align:right;font:400 12px/1.4 var(--font-mono);color:var(--ink-500)')}>{t.inter}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div ref={v.feedRef} style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 20px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('font:600 18px/1.3 var(--font-cjk)')}>官号发帖流</div>
              <div style={s('flex:none;display:flex;align-items:center;gap:8px')}>
                <span style={s('font:600 12px/1.4 var(--font-cjk);color:var(--ink-400)')}>排序</span>
                <div style={s('display:flex;border:1px solid var(--border-2);border-radius:6px;overflow:hidden')}>
                  {v.feedSorts.map((f) => (
                    <div key={f.label} onClick={f.go} style={s(`padding:5px 11px;font:${f.fw} 13px/1.4 var(--font-cjk);color:${f.fg};background:${f.bg};cursor:pointer;border-right:1px solid var(--border-1)`)}>{f.label}</div>
                  ))}
                </div>
              </div>
            </div>
            <div style={s('display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--border-1);background:var(--canvas);flex-wrap:wrap')}>
              <input type="text" value={v.fq} onChange={v.setFq} placeholder="搜产品代码 / 名称 / 官号 / 摘要关键词" style={s(`flex:none;width:290px;height:30px;box-sizing:border-box;padding:0 10px;border:1px solid ${v.fqBc};border-radius:6px;background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none`)} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.acctToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.acctBc};border-radius:6px;background:${v.acctBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>官号</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.acctLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.acctCaret}</span>
                </div>
                {v.acctOpen && (
                  <div onMouseLeave={v.acctClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:340px;max-height:342px;overflow-y:auto;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    <div style={s('padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.acctMenuNote}</div>
                    {v.acctMenu.map((m) => (
                      <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                        <span style={s('flex:1;min-width:0;font:500 13px/1.4 var(--font-cjk);color:var(--ink-800);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                        <span style={s(`flex:none;padding:1px 7px;border-radius:9999px;background:${m.tbg};font:600 11px/1.5 var(--font-cjk);color:${m.tfg}`)}>{m.type}</span>
                        <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 篇</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.prodToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.prodBc};border-radius:6px;background:${v.prodBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>产品</span>
                  <span style={s('font:600 13px/1.4 var(--font-mono);color:var(--csop-blue-700)')}>{v.prodLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.prodCaret}</span>
                </div>
                {v.prodOpen && (
                  <div onMouseLeave={v.prodClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:400px;max-height:400px;display:flex;flex-direction:column;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    <div style={s('flex:none;padding:9px 12px 8px;border-bottom:1px solid var(--border-1);background:var(--canvas)')}>
                      <input type="text" value={v.prodQ} onChange={v.setProdQ} placeholder="输入代码或名称，即时过滤" style={s('width:100%;height:28px;box-sizing:border-box;padding:0 9px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:400 13px/1.4 var(--font-cjk);color:var(--ink-800);outline:none')} className={focus('border-color:var(--csop-blue-600);box-shadow:0 0 0 2px var(--csop-blue-100)')} />
                      <div style={s('margin-top:6px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.prodMenuNote}</div>
                    </div>
                    <div style={s('flex:1;min-height:0;overflow-y:auto')}>
                      {v.prodMenu.map((m) => (
                        <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                          <span style={s('flex:none;width:10px;font:600 12px/1.3 var(--font-cjk);color:var(--csop-blue-600)')}>{m.tick}</span>
                          <span style={s('flex:none;width:46px;font:600 14px/1.3 var(--font-mono);color:var(--ink-900)')}>{m.code}</span>
                          <span style={s(`flex:none;padding:1px 6px;border-radius:9999px;background:${m.obg};font:600 11px/1.5 var(--font-cjk);color:${m.ofg}`)}>{m.own}</span>
                          <span style={s('flex:1;min-width:0;font:400 13px/1.4 var(--font-cjk);color:var(--ink-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.name}</span>
                          <span style={s('flex:none;font:500 12px/1.3 var(--font-cjk);color:var(--ink-500)')}>{m.n} 篇</span>
                        </div>
                      ))}
                      {v.prodMenuEmpty && (
                        <div style={s('padding:16px 12px;font:400 13px/1.4 var(--font-cjk);color:var(--ink-400)')}>没有匹配「{v.prodQ}」的产品</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div style={s('position:relative;flex:none')}>
                <div onClick={v.typeToggle} style={s(`display:flex;align-items:center;gap:8px;height:30px;box-sizing:border-box;padding:0 11px;border:1px solid ${v.typeBc};border-radius:6px;background:${v.typeBg};cursor:pointer;transition:background 120ms cubic-bezier(.4,0,.2,1)`)} className={hover('background:var(--csop-blue-50)')}>
                  <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--ink-400)')}>类型</span>
                  <span style={s('font:600 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>{v.typeLabel}</span>
                  <span style={s('font:400 9px/1 var(--font-cjk);color:var(--ink-400)')}>{v.typeCaret}</span>
                </div>
                {v.typeOpen && (
                  <div onMouseLeave={v.typeClose} style={s('position:absolute;left:0;top:36px;z-index:45;width:372px;background:#fff;border:1px solid var(--border-2);border-radius:8px;box-shadow:0 10px 28px rgba(14,42,82,0.16)')}>
                    <div style={s('display:flex;align-items:center;justify-content:space-between;padding:7px 12px;border-bottom:1px solid var(--border-1);background:var(--canvas);font:400 12px/1.4 var(--font-cjk);color:var(--ink-500)')}>
                      <span>多选 · AI 判定的帖子类型</span>
                      <span onClick={v.typeAll} style={s('font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')}>清除选择</span>
                    </div>
                    {v.typeNaNote && (
                      <div style={s('padding:6px 12px;border-bottom:1px solid var(--border-1);background:var(--warning-100);font:400 12px/1.5 var(--font-cjk);color:var(--warning-700)')}>{v.typeNaNote}</div>
                    )}
                    {v.typeMenu.map((m) => (
                      <div key={m.key} onClick={m.go} style={s(`display:flex;align-items:center;gap:9px;padding:8px 12px;border-bottom:1px solid var(--ink-100);background:${m.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <span style={s(`flex:none;width:14px;height:14px;box-sizing:border-box;border:1px solid ${m.boxBc};border-radius:3px;background:${m.boxBg};display:flex;align-items:center;justify-content:center;font:600 10px/1 var(--font-cjk);color:#fff`)}>{m.tick}</span>
                        <span style={s(`flex:none;width:68px;padding:1px 8px;box-sizing:border-box;border-radius:4px;background:${m.tbg};font:600 12px/1.6 var(--font-cjk);color:${m.tfg};text-align:center;white-space:nowrap`)}>{m.label}</span>
                        <span style={s('flex:1;min-width:0;font:400 12px/1.4 var(--font-cjk);color:var(--ink-500);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{m.def}</span>
                        <span style={s('flex:none;font:500 12px/1.3 var(--font-mono);color:var(--ink-500)')}>{m.n} 篇</span>
                      </div>
                    ))}
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
              <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:12px')}>
                <span style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500)')}><span style={s('font:600 14px/1 var(--font-mono);color:var(--ink-800)')}>{v.feedCount}</span> 篇</span>
                {v.hasFf && (
                  <div onClick={v.clearFf} style={s('display:flex;align-items:center;gap:6px;padding:5px 11px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除筛选<span style={s('font:400 12px/1 var(--font-cjk)')}>✕</span></div>
                )}
              </div>
            </div>

            {v.hasSrc && (
              <div style={s('display:flex;align-items:center;gap:10px;padding:9px 20px;border-bottom:1px solid var(--csop-blue-100);background:var(--csop-blue-50);font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-700)')}>
                <span style={s('font:600 11px/1.4 var(--font-cjk);letter-spacing:0.12em;color:var(--csop-blue-600)')}>来自官号清单</span>
                <span>{v.srcAcct}</span>
                <span style={s('color:var(--csop-blue-300)')}>×</span>
                <span style={s('font:600 13px/1.4 var(--font-mono)')}>{v.srcCode}</span>
                <span>{v.srcName}</span>
                <span style={s('color:var(--ink-500)')}>· 当前日期范围 {v.rangeFrom} → {v.rangeTo} · {v.feedCount} 篇</span>
                <span onClick={v.clearSrc} style={s('margin-left:auto;display:flex;align-items:center;gap:6px;padding:3px 10px;border-radius:9999px;border:1px solid var(--csop-blue-200);background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-700);cursor:pointer')} className={hover('background:var(--csop-blue-100)')}>清除 <span style={s('font:400 11px/1 var(--font-cjk)')}>✕</span></span>
              </div>
            )}
            {v.feedEmpty && (
              <div style={s('padding:40px 20px;text-align:center;font:400 14px/1.6 var(--font-cjk);color:var(--ink-400)')}>当前筛选下没有官号帖子。</div>
            )}
            <div style={s(`display:grid;grid-template-columns:repeat(${v.feedCols},minmax(0,1fr));gap:14px;padding:18px 20px 20px`)}>
              {v.cards.map((c) => (
                <div key={c.key} style={s('border:1px solid var(--border-1);border-radius:8px;background:#fff;display:flex;flex-direction:column;min-width:0')}>
                  <div style={s('display:flex;align-items:center;gap:8px;padding:12px 14px 0;flex-wrap:wrap')}>
                    <span style={s('font:600 14px/1.4 var(--font-cjk);color:var(--ink-900)')}>{c.account}</span>
                    <span style={s(`padding:1px 8px;border-radius:9999px;background:${c.atBg};font:600 11px/1.6 var(--font-cjk);color:${c.atFg};white-space:nowrap`)}>{c.accountType}</span>
                    <span style={s('margin-left:auto;font:500 12px/1.4 var(--font-mono);color:var(--ink-400);white-space:nowrap')}>{c.time}</span>
                  </div>
                  <div style={s('display:flex;align-items:center;gap:6px;padding:9px 14px 0;flex-wrap:wrap')}>
                    <span style={s(`padding:2px 8px;border-radius:4px;background:${c.tbg};font:600 12px/1.6 var(--font-cjk);color:${c.tfg};white-space:nowrap`)}>{c.type}</span>
                    {c.hasDir && (
                      <span title="操作方向 · AI 判定" style={s(`padding:2px 8px;border-radius:4px;background:${c.dbg};font:600 12px/1.6 var(--font-cjk);color:${c.dfg};white-space:nowrap`)}>{c.dir}</span>
                    )}
                    {/* 徽章文案由 `review_state` 决定（ADR-0019 §2），逐字取自 PRD §3.9。
                        置信度不再跟在后面：它是模型自报的数，不是「有没有人看过」的事实。 */}
                    {c.review && (
                      <span style={s('padding:0 6px;border-radius:9999px;background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500)')}>{c.review}</span>
                    )}
                    {c.camps.map((k) => (
                      <span key={k.label} style={s(`padding:2px 8px;border-radius:9999px;background:${k.bg};font:600 12px/1.5 var(--font-cjk);color:${k.fg}`)}>{k.label}</span>
                    ))}
                  </div>
                  <div style={s('padding:10px 14px 0;font:400 15px/1.6 var(--font-cjk);color:var(--ink-900);text-wrap:pretty')}>{c.summary}</div>
                  {c.noSummary && (
                    <div style={s('padding:10px 14px 0;font:400 14px/1.6 var(--font-cjk);color:var(--ink-400)')}>暂无摘要 · 图片帖，第一期不覆盖图片内容</div>
                  )}
                  {c.summaryNa && (
                    <div style={s('padding:10px 14px 0;font:400 14px/1.6 var(--font-cjk);color:var(--ink-400)')}>摘要暂不可用 · AI 标注尚未生成</div>
                  )}
                  {c.open && (
                    <>
                      {c.hasText ? (
                        <div style={s('margin:10px 14px 0;padding:10px 12px;border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);font:400 13px/1.8 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>
                          {c.sentences.map((q, i) => <span key={i} style={s(`background:${q.bg};box-shadow:${q.sh};border-radius:2px`)}>{q.text}</span>)}
                        </div>
                      ) : (
                        <div style={s('margin:10px 14px 0;padding:10px 12px;border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);font:400 13px/1.8 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>原文暂不可用：正文分句尚未生成。</div>
                      )}
                      <div style={s('padding:5px 14px 0;font:400 12px/1.5 var(--font-cjk);color:var(--ink-400)')}>类型置信度 {c.conf}{c.evidenceNote}</div>
                    </>
                  )}
                  <div style={s('display:flex;align-items:center;gap:6px;padding:10px 14px 0;flex-wrap:wrap')}>
                    {c.mentioned.map((m) => (
                      <span key={m.code} title={m.title} style={s(`display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:9999px;background:${m.bg};font:600 12px/1.5 var(--font-mono);color:${m.fg};white-space:nowrap`)}>{m.code}<span style={s('font:400 11px/1.4 var(--font-cjk);opacity:0.85')}>{m.short}</span></span>
                    ))}
                  </div>
                  <div style={s('display:flex;align-items:center;gap:10px;margin:12px 0 0;padding:10px 14px 12px;border-top:1px solid var(--ink-100)')}>
                    <span style={s('font:500 13px/1.4 var(--font-mono);color:var(--ink-600);white-space:nowrap')}>赞 {c.likes} · 评 {c.comments} · 转 {c.shares}</span>
                    <span onClick={c.toggle} style={s(`margin-left:auto;display:inline-flex;align-items:center;padding:3px 10px;border:1px solid ${c.linkBc};border-radius:6px;background:${c.linkBg};font:500 12px/1.4 var(--font-cjk);color:${c.linkFg};cursor:pointer;white-space:nowrap`)} className={hover('background:var(--csop-blue-50)')}>{c.openLabel}</span>
                    <a href={c.url} target="_blank" rel="noopener" style={s('display:inline-flex;align-items:center;padding:3px 10px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>原帖 ↗</a>
                  </div>
                </div>
              ))}
            </div>
            {v.hasMore && (
              <div style={s('display:flex;align-items:center;justify-content:center;gap:14px;padding:4px 20px 20px')}>
                <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-400)')}>已显示 {v.shownCount} / {v.feedCount} 篇</span>
                <div onClick={v.loadMore} style={s('display:flex;align-items:center;padding:8px 18px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:var(--csop-blue-600);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>再看 {v.pageSize} 篇</div>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }
}
