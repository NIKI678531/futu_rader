/* KOL 页面里只依赖已下发帖子事实的轻量聚合。 */

export function sumPostViews(posts) {
  var total = 0;
  for (var i = 0; i < posts.length; i++) {
    var raw = posts[i].views;
    if (raw == null || raw === '') return null;
    var value = Number(raw);
    if (!Number.isFinite(value)) return null;
    total += value;
  }
  return total;
}

export function compareKolReach(a, b) {
  if (a.views == null && b.views == null) {
    return b.n - a.n
      || compareKnownNumbers(a.comments, b.comments)
      || String(a.kol).localeCompare(String(b.kol), 'zh-Hans-CN');
  }
  if (a.views == null) return 1;
  if (b.views == null) return -1;
  return b.views - a.views
    || b.n - a.n
    || compareKnownNumbers(a.comments, b.comments)
    || String(a.kol).localeCompare(String(b.kol), 'zh-Hans-CN');
}

function compareKnownNumbers(a, b) {
  if (a == null) return b == null ? 0 : 1;
  if (b == null) return -1;
  return b - a;
}

function percent(n, total) {
  return total ? Math.round(n / total * 100) : null;
}

/*
 * 饼图按每篇帖子的正文主 ETF（code / ownership）统计，因此一篇只会进入一个扇区。
 * 正文未提及池内 ETF 是已知空态，和主数据缺失的 unknown 分开；其他公司明细也使用
 * 同一主 ETF 口径，避免把正文里的次要提及混进来后与饼图对不上。
 */
export function kolProductComposition(posts) {
  var own = 0, other = 0, unmentioned = 0, unknown = 0;
  var companies = new Map();

  posts.forEach(function (post) {
    if (!post.code && Array.isArray(post.mentioned) && post.mentioned.length === 0) {
      unmentioned++;
      return;
    }
    if (post.ownership === 'own') {
      own++;
      return;
    }
    if (post.ownership !== 'peer' && post.ownership !== 'competitor') {
      unknown++;
      return;
    }

    other++;
    var issuer = post.issuer || '发行商暂不可用';
    var company = companies.get(issuer);
    if (!company) {
      company = { issuer: issuer, n: 0, products: new Map() };
      companies.set(issuer, company);
    }
    company.n++;

    var code = post.code || '—';
    var product = company.products.get(code);
    if (!product) {
      product = { code: code, name: post.name || '产品名称暂不可用', n: 0 };
      company.products.set(code, product);
    }
    product.n++;
  });

  var total = posts.length;
  var companyRows = Array.from(companies.values()).map(function (company) {
    return {
      issuer: company.issuer,
      n: company.n,
      pct: percent(company.n, total),
      products: Array.from(company.products.values()).map(function (product) {
        return Object.assign({}, product, { pct: percent(product.n, total) });
      }).sort(function (a, b) {
        return b.n - a.n || String(a.code).localeCompare(String(b.code));
      })
    };
  }).sort(function (a, b) {
    return b.n - a.n || String(a.issuer).localeCompare(String(b.issuer), 'zh-Hans-CN');
  });

  return {
    total: total,
    own: own,
    other: other,
    unmentioned: unmentioned,
    unknown: unknown,
    ownPct: percent(own, total),
    otherPct: percent(other, total),
    unmentionedPct: percent(unmentioned, total),
    unknownPct: percent(unknown, total),
    companies: companyRows
  };
}
