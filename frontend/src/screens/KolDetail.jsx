/* Port of design/kol-detail.dc.html (KOL 详情).
   Reads `?kol=`, `?post=` and `?range=` off the URL in the constructor exactly as the
   design source does — App.jsx keys this route on the full URL so a link from KOL 影响力
   remounts and re-reads. Logic below is the design source verbatim; see OfficialActivity
   for the two standing deviations (synchronous RADAR import, `{{ }}` → JSX), plus one
   local to this screen: the 赞/评/转 合计 propagates null instead of swallowing it
   （见 out.stats 上方，与 lib/profile.js 同一处偏差）。 */
import React from 'react'
import R from '../data/radar'
import { num, conf2, typeStyle, md, shell, rgba, CAMP, POST_TYPES, reviewBadge, needsReview } from '../lib/view'
import { kolProfile } from '../lib/profile'
import { s, hover } from '../lib/dc'
import Shell from '../components/Shell'
import DcLink from '../components/DcLink'

export default class KolDetail extends React.Component {
  static defaultProps = {
    lowConfidence: 0.7,
    campRule: '挂载标的 ∪ 正文提及',
  }

  constructor(props) {
    super(props);
    var q = {};
    try {
      new URLSearchParams(location.search).forEach(function (v, k) { q[k] = v; });
    } catch (e) { q = {}; }
    this.state = {
      rangeKey: q.range || 'd7',
      kol: q.kol || null,
      sel: q.post || null,
      openOps: {}
    };
  }
  data() { return R.kolImpact(this.state.rangeKey); }
  primaryOnly() { return this.props.campRule === '仅挂载标的'; }
  camp(p) { return this.primaryOnly() ? p.campPrimary : p.camp; }
  /* 三态，不是两态：举了手（true）、没举（false）、不知道（null）。判据是
     `reviewState` 而不是置信度 —— ADR-0019 §2：`calibrated_confidence` 整列为
     NULL，`lowConfidence` 在校准概率存在之前不生效；而 `null < 0.7` 在 JS 里是
     **true**，照置信度写会给每一篇没标注过的帖子都挂上「待确认」。 */
  pending(p) { return needsReview(p.reviewState); }
  /* 这一条结论的如实声明（ADR-0019 §2／§4）。指不到原文就不说「可追溯原文」。 */
  review(p) { return reviewBadge(p.reviewState, p.evidenceIdx != null && p.evidenceIdx >= 0); }
  /* AI 标注整块未生成（ADR-0017 §4，sql provider 下恒为真） */
  annNa(p) { return p.postType == null; }

  /* 当前 KOL：URL 未指定时取排名第一位 */
  kolName() {
    var M = this.data();
    if (this.state.kol) return this.state.kol;
    return M.leaders.length ? M.leaders[0].kol : '';
  }
  myPosts() {
    var k = this.kolName();
    return this.data().posts.filter(function (p) { return p.kol === k; })
      .sort(function (a, b) { return b.t - a.t; });
  }
  selPost() {
    var ps = this.myPosts();
    if (!ps.length) return null;
    var id = this.state.sel;
    return ps.filter(function (p) { return p.id === id; })[0] || ps[0];
  }

  exportCsv(rows, kol) {
    var head = ['产品', '产品名称', '发行商', '归属', '观点', '类型', '操作', '置信度', '日期', '时间', '互动量', '原帖链接'];
    var esc = function (v) { return '"' + String(v == null ? '' : v).split('"').join('""') + '"'; };
    var lines = [head.map(esc).join(',')];
    rows.forEach(function (r) {
      lines.push([r.code, r.name, r.issuer, r.own ? '南方东英' : '竞品', r.summary, r.typeLabel, r.direction || '未提及操作', r.confidence, r.dateText, r.timeText, r.engagement, r.url].map(esc).join(','));
    });
    var blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = kol + '-其他产品观点及操作.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  renderVals() {
    var s = this.state, self = this;
    this.LC = this.props.lowConfidence != null ? this.props.lowConfidence : 0.7;
    var out = shell('accounts', 'kol', s, function (x) { self.setState(x); });
    var M = this.data();
    var kol = this.kolName();
    var ps = this.myPosts();
    var p0 = this.selPost();
    var prof = kolProfile(kol, ps, function (p) { return self.camp(p); });
    var secOf = function (k) { return R.SECTORS.filter(function (x) { return x.k === k; })[0] || R.SECTORS[0]; };
    var campsOf = function (p) { var c = self.camp(p); return c === 'both' ? [CAMP.own, CAMP.competitor] : [CAMP[c] || CAMP.none]; };
    var issuerShort = function (x) { return x === 'CSOP 南方东英' ? '南方东英' : x; };
    var pct = function (n, d) { return d ? Math.round(n / d * 100) + '%' : '—'; };
    /* 正文分句在标注块里（ADR-0017 §4），sql 下整块是 null —— `null.map` 直接抛。
       返回空数组会让展开后的原文框变成一个没有任何说明的空框，所以调用点另发 hasText。 */
    var sentencesOf = function (p) {
      return (p.fullText || []).map(function (t, i) {
        var hit = i === p.evidenceIdx;
        return { text: t, bg: hit ? 'var(--warning-100)' : 'transparent', sh: hit ? 'inset 0 -2px 0 var(--warning-600)' : 'none' };
      });
    };

    out.kolName = kol;
    out.rangeText = M.range.text;
    out.postCount = String(ps.length);
    out.lcText = this.LC.toFixed(2);
    out.hasStyle = !!prof.styleTag;
    out.styleTag = prof.styleTag;

    /* /meta 下发的是 {name, tags, active}，不是设计源那个三元组（radar.js 的 KOLS）。 */
    var meta = R.KOLS.filter(function (k) { return k.name === kol; })[0];
    out.tags = (meta ? String(meta.tags).split(',') : ['合作 KOL']).map(function (t) { return { label: t }; });

    /* 上一位 / 下一位：沿声量排名顺序翻页 */
    var order = M.leaders.map(function (l) { return l.kol; });
    var at = order.indexOf(kol);
    var prev = at > 0 ? order[at - 1] : null;
    var next = at >= 0 && at < order.length - 1 ? order[at + 1] : null;
    out.prevFg = prev ? 'var(--ink-700)' : 'var(--ink-300)';
    out.nextFg = next ? 'var(--ink-700)' : 'var(--ink-300)';
    out.prevGo = function () { if (prev) self.setState({ kol: prev, sel: null, openOps: {} }); };
    out.nextGo = function () { if (next) self.setState({ kol: next, sel: null, openOps: {} }); };

    var codes = {}; ps.forEach(function (p) { p.mentioned.forEach(function (m) { codes[m.code] = 1; }); });
    var pendingN = ps.filter(function (p) { return self.pending(p); }).length;
    var annNaN = ps.filter(function (p) { return self.annNa(p); }).length;
    /* 有一篇不知道，合计就是不知道。设计源这里写的是 `likes += p.likes`，而 JS 里
       `sum + null === sum + 0` —— 少加的那一篇悄悄消失，合计看着还挺像样。这是
       lib/profile.js 里同一处偏差，理由同（ADR-0015 末段、铁律 2）。 */
    var add = function (sum, v) { return sum == null || v == null ? null : sum + v; };
    var likes = 0, comments = 0, shares = 0;
    ps.forEach(function (p) {
      likes = add(likes, p.likes); comments = add(comments, p.comments); shares = add(shares, p.shares);
    });
    out.stats = [
      { label: '发帖数', value: String(ps.length), unit: '篇', pct: '', pfg: 'transparent', sub: '提及 ' + Object.keys(codes).length + ' 只产品 · ' + (annNaN === ps.length && ps.length ? '类型标注尚未生成' : pendingN + ' 篇类型待确认' + (annNaN ? ' · 另有 ' + annNaN + ' 篇尚未标注' : '')), fg: 'var(--ink-900)' },
      { label: '提及自家产品', value: String(prof.ownAny), unit: '篇', pct: pct(prof.ownAny, ps.length), pfg: 'var(--csop-blue-700)', sub: '其中 ' + prof.both + ' 篇同时提及竞品', fg: 'var(--csop-blue-700)' },
      { label: '提及竞品', value: String(prof.peerAny), unit: '篇', pct: pct(prof.peerAny, ps.length), pfg: 'var(--ink-600)', sub: '仅提竞品的有 ' + prof.peer + ' 篇', fg: 'var(--ink-800)' },
      { label: '互动合计', value: num(prof.engagement), unit: '', pct: '', pfg: 'transparent', sub: '赞 ' + num(likes) + ' · 评 ' + num(comments) + ' · 转 ' + num(shares) + '（发布后约 24h）', fg: 'var(--ink-900)' }
    ];

    /* 左表：点行 = 选中该帖（右侧高亮那天）+ 展开原文 */
    out.posts = ps.map(function (p, i) {
      var sec = secOf(p.sector), st = typeStyle(p.postType), on = !!(p0 && p.id === p0.id);
      return {
        key: p.id,
        time: p.time, code: p.code,
        pbg: rgba(sec.hue, 0.12), pfg: sec.hue,
        type: st.label, tbg: st.bg, tfg: st.fg, pending: self.pending(p), conf: conf2(p.confidence), review: self.review(p),
        hasDir: !!p.hasDir, dir: p.dir ? p.dir.label : '', dbg: p.dir ? p.dir.bg : 'transparent', dfg: p.dir ? p.dir.fg : 'transparent',
        /* `!p.hasSummary` 把 null 和 false 合成了一句「图片帖」—— 前者是「还没生成」，
           后者是「查过了，这篇确实只有图」。合成的那一刻，未标注的帖子全被说成了图片帖。 */
        summary: p.hasSummary ? p.summary : '', noSummary: p.hasSummary === false, summaryNa: p.hasSummary == null,
        /* 「高亮句为判定依据」是一句**指路**：它承诺上面那段原文里有一句被标出来了。
           标注整块没生成时一句都没有，照旧写死这句，读的人会在原文里找一个不存在的
           高亮 —— 而那段原文本身也已经换成了缺失态，两句话当场自相矛盾。

           这里只分出 null 这一支，`evidenceIdx === -1`（查过了，本篇没有可标注的
           依据句）仍按设计源原样说「高亮句为判定依据」：演示数据里确实有 -1 的帖子，
           改它就是逐字比对里的一处分叉，而那是设计源的文案取舍，不是空值适配。
           kol-activity.dc.html:908 对 -1 另有说法，两份设计源在这一句上本来就不一致
           —— 已记在交付说明里，等裁决，不在这里顺手统一（CLAUDE.md：设计变更从设计源
           重新拷贝，不照着新行为手推）。

           分隔符「 · 」在串里而不是留在 JSX 里：设计源那一行是
           `类型置信度 {{ p.conf }} · 高亮句为判定依据`，「· 高亮句为判定依据」是**一个**
           文本节点。写成 `{p.conf} · {p.evidenceNote}` 会拆成两个，逐字比对当场报差异
           （screen-diff 比的是文本分段，不是拼出来的整句）。 */
        evidenceNote: p.evidenceIdx == null ? ' · 判定依据尚未生成' : ' · 高亮句为判定依据',
        open: on, hasText: p.fullText != null, sentences: on ? sentencesOf(p) : [],
        camps: campsOf(p),
        likes: num(p.likes), comments: num(p.comments), shares: num(p.shares), eng: num(p.engagement),
        url: p.url, stop: function (e) { e.stopPropagation(); },
        bg: on ? 'var(--csop-blue-50)' : (i % 2 ? 'var(--canvas)' : '#fff'),
        go: function () { self.setState({ sel: on ? '' : p.id }); }
      };
    });

    /* 右上：按天双色堆叠柱（每格一篮）
       日历轴由 /ranges/{key} 下发（`dates`）。设计源在下面 addDays(range.from, i) 现算，
       那是演示生成器泄漏进屏幕代码（ADR-0004、静态守卫①）。`buckets` 顶不上它 ——
       桶的粒度随区间在时/日/周之间变（d1 是 24 个小时桶，d30 是 5 个周桶），而这根
       时间线固定按天；backend/tests/test_ranges.py 钉住了这条区别。
       取的是 shell() 已经取过的那份区间（read() 命中缓存），不多发一次请求；
       `M.range` 是 kolImpact 里那份四字段的裁剪副本，没有 dates。 */
    var RANGE = R.buildRange(s.rangeKey);
    var DAYS = RANGE.days;
    var perDay = {};
    ps.forEach(function (p) {
      var e = perDay[p.day] = perDay[p.day] || { own: 0, both: 0, peer: 0 };
      var c = self.camp(p);
      if (c === 'own') e.own++; else if (c === 'both') e.both++; else if (c === 'competitor') e.peer++;
    });
    var mx = 1;
    Object.keys(perDay).forEach(function (d) { var e = perDay[d], t = e.own + e.both + e.peer; if (t > mx) mx = t; });
    var unit = Math.min(40, Math.floor(160 / mx));
    var step = DAYS > 14 ? 5 : (DAYS > 7 ? 2 : 1);
    var selDay = p0 ? p0.day : null;
    out.colGap = DAYS > 14 ? '3' : '8';
    out.colPad = DAYS > 14 ? '1' : (DAYS > 2 ? '6' : '40');
    out.days = [];
    for (var i = 0; i < DAYS; i++) {
      (function (i) {
        var d = RANGE.dates[i], e = perDay[d] || { own: 0, both: 0, peer: 0 }, on = d === selDay;
        out.days.push({
          key: d,
          ownH: String(e.own * unit), bothH: String(e.both * unit), peerH: String(e.peer * unit),
          peerGap: e.peer && (e.both || e.own) ? '1' : '0', bothGap: e.both && e.own ? '1' : '0',
          bg: on ? 'var(--csop-blue-50)' : 'transparent',
          label: (i % step === 0 || on || DAYS <= 7) ? md(d) : '',
          lfg: on ? 'var(--csop-blue-700)' : 'var(--ink-400)', lfw: on ? '600' : '500',
          title: md(d) + ' · 只提自家 ' + e.own + ' · 双方 ' + e.both + ' · 只提竞品 ' + e.peer,
          go: function () { var q = ps.filter(function (p) { return p.day === d; }).sort(function (a, b) { return a.t - b.t; })[0]; if (q) self.setState({ sel: q.id }); }
        });
      })(i);
    }
    out.chartNote = p0 ? '高亮 ' + p0.dateText + '（' + typeStyle(p0.postType).label + ' · ' + p0.code + '）' : '这段时间没有发帖';
    out.campCells = [
      { label: '只提自家', value: String(prof.own), fg: '#2361AD' },
      { label: '双方都提', value: String(prof.both), fg: '#3674C2' },
      { label: '只提竞品', value: String(prof.peer), fg: 'var(--ink-600)' }
    ];

    /* 右下：8 类构成 */
    var tot = ps.length || 1;
    /* typeCounts 只要有一篇没标注就整份是 null（lib/profile.js 偏差二）—— 按 8 类拆的
       构成图在那时没有任何一格是可信的，整块换成缺失态，而不是画一张全 0 的图：
       全 0 的柱状图长得和「这段时间他真没发过这几类」一模一样。 */
    out.typeNa = prof.typeCounts == null;
    out.pendingNote = out.typeNa ? '类型标注尚未生成'
      : (pendingN ? pendingN + ' 篇置信度低于 ' + this.LC.toFixed(2) + ' 已计入但标「待确认」' : '全部类型置信度达标');
    out.typeBar = out.typeNa ? [] : POST_TYPES.map(function (t) {
      var n = prof.typeCounts[t.k];
      return { key: t.k, pct: (n / tot * 100).toFixed(2), color: t.bar, title: t.label + ' ' + n + ' 篇' };
    });
    out.typeRows = out.typeNa ? [] : POST_TYPES.map(function (t) {
      var n = prof.typeCounts[t.k];
      return { key: t.k, label: t.label, color: n ? t.bar : 'var(--ink-200)', n: String(n), pct: n ? Math.round(n / tot * 100) + '%' : '—', fg: n ? 'var(--ink-800)' : 'var(--ink-400)' };
    });

    /* 其他产品观点及操作 */
    var TONE = {
      pos: ['var(--positive-100)', 'var(--positive-700)'],
      neg: ['var(--negative-100)', 'var(--negative-700)'],
      neu: ['var(--ink-100)', 'var(--ink-700)']
    };
    /* `kolOpinions` 整份可以是 null：观点、操作、情绪净值三项全部来自 AI 标注，一条都
       算不出来时后端回 null 而不是 []（sql.py::kol_opinions）—— 空列表是在说「他对别的
       产品没有观点」，那是个结论。这里必须分开：null → 缺失态，[] → 空态。 */
    var opRows = R.kolOpinions(kol, s.rangeKey);
    out.opsNa = opRows == null;
    out.opCount = opRows == null ? '暂不可用' : String(opRows.length);
    out.ops = (opRows || []).map(function (r, i) {
      var open = !!s.openOps[r.code];
      var tn = TONE[r.actionTone] || TONE.neu, st = typeStyle(r.postType);
      return {
        key: r.code,
        code: r.code + '.HK', name: r.name, issuer: issuerShort(r.issuer),
        ownLabel: r.own ? '自家' : '竞品',
        ownBg: r.own ? 'var(--csop-blue-600)' : 'var(--csop-silver-200)', ownFg: r.own ? '#fff' : 'var(--ink-700)',
        summary: r.summary, excerpt: r.excerpt,
        type: st.label, tbg: st.bg, tfg: st.fg,
        hasDir: !!r.direction, direction: r.direction, actBg: tn[0], actFg: tn[1],
        pending: r.confidence == null ? null : r.confidence < self.LC, conf: conf2(r.confidence),
        date: r.dateText, time: r.timeText,
        engagement: num(r.engagement), url: r.url,
        open: open,
        linkLabel: open ? '收起' : '原文',
        linkBg: open ? 'var(--csop-blue-50)' : '#fff',
        linkBc: open ? 'var(--csop-blue-600)' : 'var(--border-2)',
        linkFg: open ? 'var(--csop-blue-700)' : 'var(--csop-blue-600)',
        bg: i % 2 ? 'var(--canvas)' : '#fff',
        toggle: function () {
          var nx = Object.assign({}, self.state.openOps);
          if (nx[r.code]) delete nx[r.code]; else nx[r.code] = 1;
          self.setState({ openOps: nx });
        }
      };
    });
    out.csvGo = function () { self.exportCsv(opRows || [], kol); };
    return out;
  }

  render() {
    const v = this.renderVals()

    return (
      <div data-screen-label="KOL 详情" style={s('width:100%;min-width:1200px;min-height:100vh;box-sizing:border-box;background:var(--canvas);font-family:var(--font-cjk);color:var(--ink-900);font-size:14px')}>

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
            <div style={s('margin-left:auto;flex:none;display:flex;align-items:center;gap:8px;padding:5px 13px;border-radius:9999px;background:#fff;border:1px solid var(--border-2);font:500 13px/1.4 var(--font-cjk);color:var(--ink-600)')}>演示数据</div>
          </div>
        </Shell>

        <div style={s('padding:22px 24px 64px')}>

          <div style={s('display:flex;align-items:center;gap:10px;margin-bottom:18px')}>
            <DcLink href="kol-activity.dc.html" style={s('display:flex;align-items:center;padding:7px 14px;border:1px solid var(--border-2);border-radius:9999px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:var(--ink-700);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>← 返回 KOL 影响力</DcLink>
            <span style={s('font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.rangeText}</span>
          </div>

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);margin-bottom:20px')}>
            <div style={s('display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding:20px 22px 18px')}>
              <div style={s('min-width:0')}>
                <div style={s('font:600 12px/1.2 var(--font-cjk);letter-spacing:0.18em;color:var(--csop-blue-600);margin-bottom:10px')}>合作 KOL</div>
                <div style={s('display:flex;align-items:center;gap:12px;flex-wrap:wrap')}>
                  <div style={s('font:600 28px/1.2 var(--font-cjk);letter-spacing:-0.015em')}>{v.kolName}</div>
                  {v.hasStyle && (
                    <span style={s('display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:9999px;background:var(--csop-navy-900);font:600 13px/1.5 var(--font-cjk);color:#fff')}><span style={s('font:600 10px/1 var(--font-cjk);letter-spacing:0.12em;color:rgba(255,255,255,0.62)')}>AI 画像</span>{v.styleTag}</span>
                  )}
                </div>
                <div style={s('margin-top:11px;display:flex;align-items:center;gap:6px;flex-wrap:wrap')}>
                  {v.tags.map((t) => (
                    <span key={t.label} style={s('padding:3px 10px;border-radius:9999px;background:var(--csop-blue-50);font:500 13px/1.5 var(--font-cjk);color:var(--csop-blue-700)')}>{t.label}</span>
                  ))}
                  <span style={s('font:400 12px/1.5 var(--font-cjk);color:var(--ink-400);margin-left:4px')}>标签来自合作名单 · 画像由区间内类型分布自动生成</span>
                </div>
              </div>
              <div style={s('flex:none;display:flex;align-items:center;gap:10px')}>
                <div onClick={v.prevGo} style={s(`display:flex;align-items:center;padding:7px 13px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:${v.prevFg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>← 上一位</div>
                <div onClick={v.nextGo} style={s(`display:flex;align-items:center;padding:7px 13px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:${v.nextFg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>下一位 →</div>
              </div>
            </div>
            <div style={s('display:flex;align-items:stretch;border-top:1px solid var(--border-1)')}>
              {v.stats.map((k) => (
                <div key={k.label} style={s('flex:1;padding:15px 22px;border-right:1px solid var(--border-1)')}>
                  <div style={s('font:500 13px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:9px')}>{k.label}</div>
                  <div style={s('display:flex;align-items:baseline;gap:6px')}>
                    <span style={s(`font:600 26px/1 var(--font-mono);letter-spacing:-0.01em;color:${k.fg}`)}>{k.value}</span>
                    <span style={s('font:500 14px/1 var(--font-cjk);color:var(--ink-500)')}>{k.unit}</span>
                    <span style={s(`margin-left:auto;font:600 14px/1 var(--font-mono);color:${k.pfg}`)}>{k.pct}</span>
                  </div>
                  <div style={s('margin-top:7px;font:400 12px/1.5 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>{k.sub}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={s('display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:20px;margin-bottom:20px')}>

            <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06);min-width:0;display:flex;flex-direction:column')}>
              <div style={s('padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
                <div style={s('font:600 18px/1.3 var(--font-cjk)')}>这段时间发的帖</div>
                <div style={s('margin-top:5px;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>共 {v.postCount} 篇 · 点一行，右侧时间线高亮那一天，并展开原文</div>
              </div>
              <div style={s('flex:1;min-height:0;max-height:620px;overflow-y:auto')}>
                <table>
                  <thead>
                    <tr style={s('background:var(--canvas-alt)')}>
                      <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:9px 14px;width:74px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>时间 · ETF</th>
                      <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:9px 6px;width:72px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>类型</th>
                      <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:9px 8px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>AI 摘要</th>
                      <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:left;padding:9px 6px;width:56px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>阵营</th>
                      <th style={s('position:sticky;top:0;z-index:2;background:var(--canvas-alt);text-align:right;padding:9px 14px 9px 6px;width:88px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>互动 · 原帖</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.posts.map((p) => (
                      <tr key={p.key} onClick={p.go} style={s(`background:${p.bg};cursor:pointer`)} className={hover('background:var(--csop-blue-50)')}>
                        <td style={s('padding:11px 14px;vertical-align:top;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                          <div style={s('font:500 13px/1.5 var(--font-mono);color:var(--ink-800)')}>{p.time}</div>
                          <span style={s(`display:inline-block;margin-top:5px;padding:1px 7px;border-radius:9999px;background:${p.pbg};font:600 12px/1.5 var(--font-mono);color:${p.pfg}`)}>{p.code}</span>
                        </td>
                        <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                          <div style={s('display:flex;flex-direction:column;align-items:flex-start;gap:4px')}>
                            <span style={s(`padding:2px 7px;border-radius:4px;background:${p.tbg};font:600 12px/1.6 var(--font-cjk);color:${p.tfg};white-space:nowrap`)}>{p.type}</span>
                            {p.hasDir && (
                              <span title="操作方向 · AI 判定" style={s(`padding:2px 7px;border-radius:4px;background:${p.dbg};font:600 12px/1.6 var(--font-cjk);color:${p.dfg};white-space:nowrap`)}>{p.dir}</span>
                            )}
                            {/* 徽章文案由 `review_state` 决定（ADR-0019 §2），逐字取自 PRD §3.9 */}
                            {p.review && (
                              <span style={s('padding:0 6px;border-radius:9999px;background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500);white-space:nowrap')}>{p.review}</span>
                            )}
                          </div>
                        </td>
                        <td style={s('padding:11px 8px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                          <div style={s('font:400 14px/1.6 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{p.summary}</div>
                          {p.noSummary && (
                            <div style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-400)')}>暂无摘要 · 图片帖，第一期不覆盖图片内容</div>
                          )}
                          {p.summaryNa && (
                            <div style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-400)')}>摘要暂不可用 · AI 标注尚未生成</div>
                          )}
                          {p.open && (
                            <>
                              {p.hasText ? (
                                <div style={s('margin-top:9px;padding:10px 12px;border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);font:400 13px/1.8 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>
                                  {p.sentences.map((q, i) => <span key={i} style={s(`background:${q.bg};box-shadow:${q.sh};border-radius:2px`)}>{q.text}</span>)}
                                </div>
                              ) : (
                                <div style={s('margin-top:9px;padding:10px 12px;border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);font:400 13px/1.8 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>原文暂不可用：正文分句尚未生成。</div>
                              )}
                              <div style={s('margin-top:5px;font:400 12px/1.5 var(--font-cjk);color:var(--ink-400)')}>类型置信度 {p.conf}{p.evidenceNote}</div>
                            </>
                          )}
                        </td>
                        <td style={s('padding:11px 6px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                          <div style={s('display:flex;flex-direction:column;align-items:flex-start;gap:3px')}>
                            {p.camps.map((c) => (
                              <span key={c.label} style={s(`padding:2px 8px;border-radius:9999px;background:${c.bg};font:600 12px/1.5 var(--font-cjk);color:${c.fg};white-space:nowrap`)}>{c.label}</span>
                            ))}
                          </div>
                        </td>
                        <td style={s('padding:11px 14px 11px 6px;vertical-align:top;text-align:right;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                          <div title={`赞 ${p.likes} · 评 ${p.comments} · 转 ${p.shares}`} style={s('font:600 13px/1.5 var(--font-mono);color:var(--ink-800)')}>{p.eng}</div>
                          <div style={s('font:400 11px/1.4 var(--font-mono);color:var(--ink-400)')}>{p.likes}/{p.comments}/{p.shares}</div>
                          <a href={p.url} target="_blank" rel="noopener" onClick={p.stop} style={s('display:inline-flex;align-items:center;margin-top:5px;padding:2px 9px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 12px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>原帖 ↗</a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div style={s('display:flex;flex-direction:column;gap:20px;min-width:0')}>
              <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
                <div style={s('padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
                  <div style={s('font:600 18px/1.3 var(--font-cjk)')}>自家 vs 竞品发帖时间线</div>
                  <div style={s('margin-top:5px;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>按天 · 每格一篇 · {v.chartNote}</div>
                </div>
                <div style={s('padding:16px 20px 14px')}>
                  <div style={s('display:flex;align-items:center;gap:14px;margin-bottom:12px;flex-wrap:wrap')}>
                    <div style={s('display:flex;align-items:center;gap:6px')}><span style={s('width:12px;height:12px;border-radius:2px;background:#2361AD')}></span><span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>只提自家</span></div>
                    <div style={s('display:flex;align-items:center;gap:6px')}><span style={s('width:12px;height:12px;border-radius:2px;background:#7DA5DC')}></span><span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>双方都提</span></div>
                    <div style={s('display:flex;align-items:center;gap:6px')}><span style={s('width:12px;height:12px;border-radius:2px;background:#B7BFC9')}></span><span style={s('font:400 12px/1.4 var(--font-cjk);color:var(--ink-600)')}>只提竞品</span></div>
                  </div>
                  <div style={s(`display:flex;align-items:flex-end;gap:${v.colGap}px;height:172px;border-bottom:1px solid var(--border-2)`)}>
                    {v.days.map((d) => (
                      <div key={d.key} onClick={d.go} title={d.title} style={s(`flex:1;min-width:0;height:172px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:flex-end;padding:0 ${v.colPad}px 0;background:${d.bg};border-radius:4px 4px 0 0;cursor:pointer`)}>
                        <div style={s(`height:${d.peerH}px;background:#B7BFC9;border-radius:2px 2px 0 0;margin-bottom:${d.peerGap}px`)}></div>
                        <div style={s(`height:${d.bothH}px;background:#7DA5DC;margin-bottom:${d.bothGap}px`)}></div>
                        <div style={s(`height:${d.ownH}px;background:#2361AD`)}></div>
                      </div>
                    ))}
                  </div>
                  <div style={s(`display:flex;gap:${v.colGap}px;margin-top:6px`)}>
                    {v.days.map((d) => (
                      <div key={d.key} style={s(`flex:1;min-width:0;text-align:center;font:${d.lfw} 10px/1.4 var(--font-mono);color:${d.lfg};white-space:nowrap;overflow:hidden`)}>{d.label}</div>
                    ))}
                  </div>
                  <div style={s('margin-top:12px;display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border-1);border:1px solid var(--border-1);border-radius:6px;overflow:hidden')}>
                    {v.campCells.map((c) => (
                      <div key={c.label} style={s('background:#fff;padding:10px 12px')}>
                        <div style={s('font:500 12px/1.4 var(--font-cjk);color:var(--ink-500);margin-bottom:6px')}>{c.label}</div>
                        <div style={s('display:flex;align-items:baseline;gap:6px')}><span style={s(`font:600 18px/1 var(--font-mono);color:${c.fg}`)}>{c.value}</span><span style={s('font:400 12px/1 var(--font-cjk);color:var(--ink-400)')}>篇</span></div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
                <div style={s('padding:16px 20px;border-bottom:1px solid var(--border-1)')}>
                  <div style={s('font:600 18px/1.3 var(--font-cjk)')}>帖子类型构成</div>
                  <div style={s('margin-top:5px;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>区间内 {v.postCount} 篇按 8 类归类 · {v.pendingNote}</div>
                </div>
                {v.typeNa ? (
                  <div style={s('padding:28px 20px;text-align:center;font:400 14px/1.7 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>暂不可用<div style={s('margin-top:6px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-400)')}>帖子类型标注尚未生成，构成比例无法计算</div></div>
                ) : (
                <div style={s('padding:16px 20px 12px')}>
                  <div style={s('display:flex;height:22px;border-radius:5px;overflow:hidden;background:var(--ink-100);margin-bottom:12px')}>
                    {v.typeBar.map((b) => (
                      <div key={b.key} title={b.title} style={s(`width:${b.pct}%;height:100%;background:${b.color}`)}></div>
                    ))}
                  </div>
                  <div style={s('display:grid;grid-template-columns:1fr 1fr;gap:0 20px')}>
                    {v.typeRows.map((t) => (
                      <div key={t.key} style={s('display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--ink-100)')}>
                        <span style={s(`flex:none;width:9px;height:9px;border-radius:2px;background:${t.color}`)}></span>
                        <span style={s(`flex:1;font:500 13px/1.4 var(--font-cjk);color:${t.fg}`)}>{t.label}</span>
                        <span style={s(`flex:none;font:600 13px/1.4 var(--font-mono);color:${t.fg}`)}>{t.n}</span>
                        <span style={s('flex:none;width:38px;text-align:right;font:400 12px/1.4 var(--font-mono);color:var(--ink-400)')}>{t.pct}</span>
                      </div>
                    ))}
                  </div>
                </div>
                )}
              </div>
            </div>
          </div>

          <div style={s('background:#fff;border:1px solid var(--border-1);border-radius:8px;box-shadow:0 1px 2px rgba(14,42,82,0.04),0 4px 12px rgba(14,42,82,0.06)')}>
            <div style={s('display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 22px;border-bottom:1px solid var(--border-1)')}>
              <div style={s('min-width:0')}>
                <div style={s('font:600 18px/1.3 var(--font-cjk)')}>其他产品观点及操作</div>
                <div style={s('margin-top:5px;font:400 13px/1.4 var(--font-cjk);color:var(--ink-500)')}>{v.opCount} 条 · 该 KOL 在评论与转发中提到的其他产品（竞品 + 南方东英其他产品），一条内容一行；类型与操作方向由 AI 识别，点「原文」核对</div>
              </div>
              {!v.opsNa && (
                <div onClick={v.csvGo} style={s('flex:none;display:flex;align-items:center;gap:7px;padding:8px 15px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 14px/1.4 var(--font-cjk);color:var(--ink-700);cursor:pointer')} className={hover('background:var(--csop-blue-50)')}>导出 CSV</div>
              )}
            </div>
            {v.opsNa ? (
              <div style={s('padding:34px 22px;text-align:center;font:400 14px/1.7 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>暂不可用<div style={s('margin-top:6px;font:400 13px/1.6 var(--font-cjk);color:var(--ink-400)')}>观点、操作与情绪净值均来自 AI 标注，尚未生成 —— 这不等于「他没有提到其他产品」</div></div>
            ) : (
            <table>
              <thead>
                <tr style={s('background:var(--canvas-alt)')}>
                  <th style={s('text-align:left;padding:11px 22px;width:104px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>产品</th>
                  <th style={s('text-align:left;padding:11px 10px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>观点（AI 摘要）</th>
                  <th style={s('text-align:left;padding:11px 10px;width:176px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>类型 · 操作</th>
                  <th style={s('text-align:left;padding:11px 10px;width:96px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>日期</th>
                  <th style={s('text-align:right;padding:11px 10px;width:76px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>互动量</th>
                  <th style={s('text-align:right;padding:11px 22px;width:88px;font:600 14px/1.5 var(--font-cjk);color:var(--ink-800);border-bottom:1px solid var(--border-1)')}>链接</th>
                </tr>
              </thead>
              <tbody>
                {v.ops.map((o) => (
                  <tr key={o.key} style={s(`background:${o.bg}`)}>
                    <td style={s('padding:14px 22px;vertical-align:top;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                      <div style={s('font:600 14px/1.4 var(--font-mono);color:var(--ink-900)')}>{o.code}</div>
                      <div style={s(`margin-top:4px;display:inline-block;padding:1px 7px;border-radius:9999px;background:${o.ownBg};font:600 11px/1.5 var(--font-cjk);color:${o.ownFg}`)}>{o.ownLabel}</div>
                    </td>
                    <td style={s('padding:14px 10px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                      <div style={s('font:400 14px/1.65 var(--font-cjk);color:var(--ink-800);text-wrap:pretty')}>{o.summary}</div>
                      <div style={s('margin-top:5px;font:400 12px/1.4 var(--font-cjk);color:var(--ink-400);white-space:nowrap;overflow:hidden;text-overflow:ellipsis')}>{o.name} · {o.issuer}</div>
                      {o.open && (
                        <div style={s('margin-top:10px;padding:11px 12px;border:1px solid var(--border-1);border-radius:6px;background:var(--canvas);font:400 13px/1.8 var(--font-cjk);color:var(--ink-700);text-wrap:pretty')}>{o.excerpt}</div>
                      )}
                    </td>
                    <td style={s('padding:14px 10px;vertical-align:top;border-bottom:1px solid var(--ink-100)')}>
                      <div style={s('display:flex;align-items:center;gap:6px;flex-wrap:wrap')}>
                        <span style={s(`display:inline-flex;padding:3px 9px;border-radius:4px;background:${o.tbg};font:600 12px/1.6 var(--font-cjk);color:${o.tfg};white-space:nowrap`)}>{o.type}</span>
                        {o.hasDir && (
                          <>
                            <span style={s('font:400 13px/1.6 var(--font-cjk);color:var(--ink-400)')}>·</span>
                            <span style={s(`display:inline-flex;padding:3px 9px;border-radius:4px;background:${o.actBg};font:600 12px/1.6 var(--font-cjk);color:${o.actFg};white-space:nowrap`)}>{o.direction}</span>
                          </>
                        )}
                      </div>
                      {o.pending && (
                        <div style={s('margin-top:5px;display:inline-block;padding:0 6px;border-radius:9999px;background:var(--ink-100);font:600 11px/1.6 var(--font-cjk);color:var(--ink-500)')}>待确认 {o.conf}</div>
                      )}
                    </td>
                    <td style={s('padding:14px 10px;vertical-align:top;border-bottom:1px solid var(--ink-100);white-space:nowrap')}>
                      <div style={s('font:500 13px/1.5 var(--font-mono);color:var(--ink-700)')}>{o.date}</div>
                      <div style={s('font:400 12px/1.5 var(--font-mono);color:var(--ink-400)')}>{o.time}</div>
                    </td>
                    <td style={s('padding:14px 10px;vertical-align:top;text-align:right;border-bottom:1px solid var(--ink-100);font:600 14px/1.5 var(--font-mono);color:var(--ink-800)')}>{o.engagement}</td>
                    <td style={s('padding:14px 22px;vertical-align:top;text-align:right;border-bottom:1px solid var(--ink-100)')}>
                      <div style={s('display:inline-flex;flex-direction:column;align-items:stretch;gap:6px')}>
                        <a href={o.url} target="_blank" rel="noopener" style={s('display:inline-flex;align-items:center;justify-content:center;padding:5px 12px;border:1px solid var(--border-2);border-radius:6px;background:#fff;font:500 13px/1.4 var(--font-cjk);color:var(--csop-blue-600);text-decoration:none;white-space:nowrap')} className={hover('background:var(--csop-blue-50);text-decoration:none')}>原帖 ↗</a>
                        <span onClick={o.toggle} style={s(`display:inline-flex;align-items:center;justify-content:center;padding:5px 12px;border:1px solid ${o.linkBc};border-radius:6px;background:${o.linkBg};font:500 13px/1.4 var(--font-cjk);color:${o.linkFg};cursor:pointer;white-space:nowrap`)} className={hover('background:var(--csop-blue-50)')}>{o.linkLabel}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
            <div style={s('padding:14px 22px;border-top:1px solid var(--border-1);font:400 13px/1.7 var(--font-cjk);color:var(--ink-400);text-wrap:pretty')}>「类型」与「操作」为同一套 8 类枚举下的两层标注：操作类帖子（晒单 / 操作宣告）附买卖方向；行情解读等观点类如识别出持有 / 观望意向也一并标出。置信度低于 {v.lcText} 标「待确认」。</div>
          </div>
        </div>
      </div>
    )
  }
}
