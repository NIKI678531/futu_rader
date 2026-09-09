/* @ds-bundle: {"format":4,"namespace":"CSOPDesignSystem_019e15","components":[{"name":"DisclaimerModal","sourcePath":"ui_kits/website/DisclaimerModal.jsx"},{"name":"Footer","sourcePath":"ui_kits/website/Footer.jsx"},{"name":"FundDetail","sourcePath":"ui_kits/website/FundDetail.jsx"},{"name":"FundTable","sourcePath":"ui_kits/website/FundTable.jsx"},{"name":"Header","sourcePath":"ui_kits/website/Header.jsx"},{"name":"Hero","sourcePath":"ui_kits/website/Hero.jsx"}],"sourceHashes":{"ui_kits/website/DisclaimerModal.jsx":"0da972def4a6","ui_kits/website/Footer.jsx":"540c4615a992","ui_kits/website/FundDetail.jsx":"95aac2ee4dc6","ui_kits/website/FundTable.jsx":"abde80046627","ui_kits/website/Header.jsx":"23d7a65d7ff9","ui_kits/website/Hero.jsx":"67378e2f3c30","ui_kits/website/app-shell.jsx":"e227628af121","ui_kits/website/print-app.jsx":"2bd4e6dba390"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.CSOPDesignSystem_019e15 = window.CSOPDesignSystem_019e15 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// ui_kits/website/DisclaimerModal.jsx
try { (() => {
/* DisclaimerModal — first-visit jurisdiction gate (Hong Kong style). */

function DisclaimerModal({
  open,
  onAccept
}) {
  if (!open) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "csop-modal__scrim",
    role: "dialog",
    "aria-modal": "true"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-modal"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-modal__head"
  }, /*#__PURE__*/React.createElement("img", {
    src: window.__resources && window.__resources.logo || "../../assets/logo-bilingual.png",
    alt: "CSOP",
    className: "csop-modal__logo"
  }), /*#__PURE__*/React.createElement("h2", null, "Important \u2014 please read before entering")), /*#__PURE__*/React.createElement("div", {
    className: "csop-modal__body"
  }, /*#__PURE__*/React.createElement("p", null, "By clicking the \"I accept\" button below, you confirm that you have read and understood this important notice and the terms set out below."), /*#__PURE__*/React.createElement("ul", null, /*#__PURE__*/React.createElement("li", null, "You are a ", /*#__PURE__*/React.createElement("strong", null, "Hong Kong resident"), " or a person located in Hong Kong."), /*#__PURE__*/React.createElement("li", null, "The information on this website does not constitute an offer or solicitation in any jurisdiction in which such offer is not authorised."), /*#__PURE__*/React.createElement("li", null, "Investment involves risk. Past performance is ", /*#__PURE__*/React.createElement("em", null, "not"), " indicative of future results."), /*#__PURE__*/React.createElement("li", null, "This website has not been reviewed by the Securities and Futures Commission of Hong Kong."))), /*#__PURE__*/React.createElement("div", {
    className: "csop-modal__actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "csop-btn csop-btn--ghost"
  }, "Leave site"), /*#__PURE__*/React.createElement("button", {
    className: "csop-btn csop-btn--primary",
    onClick: onAccept
  }, "I accept the terms"))));
}
window.DisclaimerModal = DisclaimerModal;
Object.assign(__ds_scope, { DisclaimerModal });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/DisclaimerModal.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/Footer.jsx
try { (() => {
/* Footer — sitemap, regulatory note, address. */

function Footer() {
  const cols = [{
    head: "Products",
    items: ["ETF series", "Leveraged & Inverse", "Active funds", "USD Money Market Fund", "CIES eligible funds"]
  }, {
    head: "Insights",
    items: ["Market commentary", "ETF education", "ESG reports", "Press releases"]
  }, {
    head: "About",
    items: ["Our story", "Leadership team", "Awards & recognition", "ESG", "Careers"]
  }, {
    head: "Investor",
    items: ["Subscription portal", "Authorised participants", "Account inquiry", "Forms & downloads"]
  }];
  return /*#__PURE__*/React.createElement("footer", {
    className: "csop-footer"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-container"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__top"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__brand"
  }, /*#__PURE__*/React.createElement("img", {
    src: window.__resources && window.__resources.logo || "../../assets/logo-bilingual.png",
    alt: "CSOP Asset Management"
  }), /*#__PURE__*/React.createElement("p", null, "CSOP Asset Management Limited is the leading asset manager in Hong Kong and the first overseas subsidiary established by a Chinese mainland public fund company."), /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__address"
  }, /*#__PURE__*/React.createElement("strong", null, "Hong Kong Office"), /*#__PURE__*/React.createElement("br", null), "2801\u20132802 Two Exchange Square", /*#__PURE__*/React.createElement("br", null), "8 Connaught Place, Central", /*#__PURE__*/React.createElement("br", null), "Hong Kong")), /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__cols"
  }, cols.map(c => /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__col",
    key: c.head
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__head"
  }, c.head), c.items.map(i => /*#__PURE__*/React.createElement("a", {
    href: "#",
    key: i
  }, i)))))), /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__legal"
  }, /*#__PURE__*/React.createElement("p", null, "CSOP Asset Management Limited is regulated in Hong Kong by the Securities and Futures Commission (SFC). This website contains information about CSOP and the services and products offered by CSOP. The information provided is not intended for distribution to, or use by, any person or entity in any jurisdiction or country that would subject CSOP or its affiliates to any registration requirement within such jurisdiction or country."), /*#__PURE__*/React.createElement("div", {
    className: "csop-footer__copy"
  }, /*#__PURE__*/React.createElement("span", null, "\xA9 2026 CSOP Asset Management Limited. All rights reserved."), /*#__PURE__*/React.createElement("span", {
    className: "csop-footer__links"
  }, /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Privacy Policy"), /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Terms of Use"), /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Cookies"), /*#__PURE__*/React.createElement("a", {
    href: "#"
  }, "Sitemap"))))));
}
window.Footer = Footer;
Object.assign(__ds_scope, { Footer });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/Footer.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/FundDetail.jsx
try { (() => {
/* FundDetail — single-fund product page. Hero strip + key facts + perf chart placeholder + risk warning. */

function Sparkline({
  data,
  color = "#2361AD",
  height = 120
}) {
  const w = 520,
    h = height,
    pad = 8;
  const min = Math.min(...data),
    max = Math.max(...data);
  const dx = (w - pad * 2) / (data.length - 1);
  const sy = v => h - pad - (v - min) / (max - min || 1) * (h - pad * 2);
  const points = data.map((v, i) => `${pad + i * dx},${sy(v)}`).join(" ");
  const area = `M${pad},${h - pad} L${points.split(" ").join(" L ")} L${w - pad},${h - pad} Z`;
  return /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${w} ${h}`,
    className: "csop-spark",
    preserveAspectRatio: "none"
  }, /*#__PURE__*/React.createElement("defs", null, /*#__PURE__*/React.createElement("linearGradient", {
    id: "sparkFill",
    x1: "0",
    x2: "0",
    y1: "0",
    y2: "1"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0%",
    stopColor: color,
    stopOpacity: "0.22"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "100%",
    stopColor: color,
    stopOpacity: "0"
  }))), /*#__PURE__*/React.createElement("path", {
    d: area,
    fill: "url(#sparkFill)"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: points,
    fill: "none",
    stroke: color,
    strokeWidth: "2",
    strokeLinejoin: "round",
    strokeLinecap: "round"
  }));
}
const FdIcon = ({
  size = 14,
  style,
  children
}) => /*#__PURE__*/React.createElement("svg", {
  width: size,
  height: size,
  style: style,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "1.75",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, children);
const FdArrow = p => /*#__PURE__*/React.createElement(FdIcon, p, /*#__PURE__*/React.createElement("path", {
  d: "M5 12h14M13 5l7 7-7 7"
}));
const FdDoc = p => /*#__PURE__*/React.createElement(FdIcon, p, /*#__PURE__*/React.createElement("path", {
  d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
}), /*#__PURE__*/React.createElement("path", {
  d: "M14 2v6h6M8 13h8M8 17h6"
}));
function FundDetail({
  fund,
  onBack
}) {
  const series = [88, 90, 92, 89, 93, 96, 99, 97, 101, 104, 103, 107, 112, 116, 119, 121, 124, 127, 132, 135];
  const positive = fund.d1 >= 0;
  return /*#__PURE__*/React.createElement("article", {
    className: "csop-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-container"
  }, /*#__PURE__*/React.createElement("button", {
    className: "csop-back",
    onClick: onBack
  }, /*#__PURE__*/React.createElement(FdArrow, {
    size: 14,
    style: {
      transform: "rotate(180deg)"
    }
  }), "Back to all funds"), /*#__PURE__*/React.createElement("header", {
    className: "csop-detail__head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "csop-eyebrow"
  }, fund.cat, " \xB7 ", fund.region), /*#__PURE__*/React.createElement("h1", {
    className: "csop-detail__title"
  }, fund.name), /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__tickerline"
  }, /*#__PURE__*/React.createElement("span", {
    className: "csop-ticker csop-ticker--lg"
  }, fund.ticker), /*#__PURE__*/React.createElement("span", null, "Listed on ", fund.region === "HK" ? "HKEX" : fund.region === "SG" ? "SGX" : fund.region === "SA" ? "Tadawul" : "HKEX"), /*#__PURE__*/React.createElement("span", null, "ISIN HK0000493743"))), /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__nav"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__nav-label"
  }, "NAV per unit \xB7 as at 11 May 2026"), /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__nav-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "csop-detail__nav-price num"
  }, "HK$ ", fund.nav.toFixed(3)), /*#__PURE__*/React.createElement("span", {
    className: "csop-detail__nav-chg num " + (positive ? "pos" : "neg")
  }, positive ? "▲" : "▼", " ", Math.abs(fund.d1).toFixed(2), "%")), /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__nav-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "csop-btn csop-btn--primary"
  }, /*#__PURE__*/React.createElement(FdDoc, {
    size: 14
  }), " Download factsheet"), /*#__PURE__*/React.createElement("button", {
    className: "csop-btn csop-btn--secondary"
  }, "View prospectus")))), /*#__PURE__*/React.createElement("section", {
    className: "csop-detail__chart-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-detail__chart-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "csop-section__title-sm"
  }, "Cumulative performance"), /*#__PURE__*/React.createElement("div", {
    className: "csop-caption"
  }, "Rebased to 100 \xB7 since launch")), /*#__PURE__*/React.createElement("div", {
    className: "csop-chip-row"
  }, ["1M", "3M", "6M", "YTD", "1Y", "3Y", "ITD"].map((p, i) => /*#__PURE__*/React.createElement("button", {
    key: p,
    className: "csop-chip" + (i === 3 ? " is-active" : "")
  }, p)))), /*#__PURE__*/React.createElement(Sparkline, {
    data: series
  })), /*#__PURE__*/React.createElement("section", {
    className: "csop-detail__grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Underlying index"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value"
  }, fund.cat === "Fixed Inc." ? "Bloomberg China Treasury + Policy Bank Bond Index" : "Hang Seng TECH Index")), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Manager"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value"
  }, "CSOP Asset Management Limited")), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Inception date"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value num"
  }, "28 August 2020")), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Ongoing charges"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value num"
  }, "0.49% p.a.")), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Distribution policy"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value"
  }, "No distribution")), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__label"
  }, "Trading currency"), /*#__PURE__*/React.createElement("div", {
    className: "csop-fact__value"
  }, "HKD \xB7 RMB \xB7 USD counters"))), /*#__PURE__*/React.createElement("section", {
    className: "csop-risk"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-risk__head"
  }, "Important risk warning"), /*#__PURE__*/React.createElement("p", null, "The Sub-Fund is not principal guaranteed and your investments may suffer losses. There is no assurance that the Sub-Fund will achieve its investment objective. The Sub-Fund is passively managed and the Manager will not have the discretion to adapt to market changes due to the inherent investment nature of the Sub-Fund."), /*#__PURE__*/React.createElement("p", {
    className: "csop-caption"
  }, "This document has not been reviewed by the Securities and Futures Commission of Hong Kong. Investors should refer to the Prospectus and Product Key Facts Statement for further details."))));
}
window.FundDetail = FundDetail;
Object.assign(__ds_scope, { FundDetail });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/FundDetail.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/FundTable.jsx
try { (() => {
/* FundTable — interactive ETF list with category filter and search box.
   Click a row to navigate to FundDetail.                                  */

const FUND_DATA = [{
  ticker: "3033.HK",
  name: "CSOP Hang Seng TECH Index ETF",
  cat: "Equity",
  region: "HK",
  nav: 4.728,
  d1: 0.42,
  ytd: 12.61,
  aum: "31.2 B"
}, {
  ticker: "3037.HK",
  name: "CSOP Hang Seng Index ETF",
  cat: "Equity",
  region: "HK",
  nav: 88.140,
  d1: 0.18,
  ytd: 4.92,
  aum: "5.6 B"
}, {
  ticker: "2822.HK",
  name: "CSOP FTSE China A50 ETF",
  cat: "Equity",
  region: "CN",
  nav: 13.420,
  d1: -0.31,
  ytd: -2.18,
  aum: "18.4 B"
}, {
  ticker: "3443.HK",
  name: "CSOP FTSE Hong Kong Equity ETF",
  cat: "Equity",
  region: "HK",
  nav: 10.082,
  d1: 0.05,
  ytd: 3.41,
  aum: "0.4 B"
}, {
  ticker: "3066.HK",
  name: "CSOP Bitcoin Futures ETF",
  cat: "Virtual",
  region: "GL",
  nav: 12.840,
  d1: -1.81,
  ytd: 58.20,
  aum: "1.1 B"
}, {
  ticker: "3068.HK",
  name: "CSOP Ether Futures ETF",
  cat: "Virtual",
  region: "GL",
  nav: 8.910,
  d1: -2.14,
  ytd: 42.11,
  aum: "0.3 B"
}, {
  ticker: "9078.HK",
  name: "CSOP Bloomberg China Treasury Bond ETF",
  cat: "Fixed Inc.",
  region: "CN",
  nav: 31.220,
  d1: 0.04,
  ytd: 1.84,
  aum: "12.7 B"
}, {
  ticker: "9410.SR",
  name: "Albilad CSOP MSCI Hong Kong China",
  cat: "Equity",
  region: "SA",
  nav: 9.640,
  d1: 0.92,
  ytd: 7.20,
  aum: "9.5 B"
}, {
  ticker: "SQU.SG",
  name: "CSOP iEdge SEA+ TECH Index ETF",
  cat: "Equity",
  region: "SG",
  nav: 1.084,
  d1: 0.18,
  ytd: 6.41,
  aum: "0.3 B"
}];
const CATEGORIES = ["All", "Equity", "Fixed Inc.", "Virtual"];
const FtIcon = ({
  size = 16,
  children
}) => /*#__PURE__*/React.createElement("svg", {
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "1.75",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, children);
const FtSearch = p => /*#__PURE__*/React.createElement(FtIcon, p, /*#__PURE__*/React.createElement("circle", {
  cx: "11",
  cy: "11",
  r: "7"
}), /*#__PURE__*/React.createElement("path", {
  d: "m20 20-3.5-3.5"
}));
const FtArrow = p => /*#__PURE__*/React.createElement(FtIcon, p, /*#__PURE__*/React.createElement("path", {
  d: "M5 12h14M13 5l7 7-7 7"
}));
function FundTable({
  onSelect
}) {
  const [cat, setCat] = React.useState("All");
  const [q, setQ] = React.useState("");
  const rows = FUND_DATA.filter(f => (cat === "All" || f.cat === cat) && (q === "" || f.name.toLowerCase().includes(q.toLowerCase()) || f.ticker.toLowerCase().includes(q.toLowerCase())));
  return /*#__PURE__*/React.createElement("section", {
    className: "csop-section"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-container"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-section__head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "csop-eyebrow"
  }, "Funds & ETFs"), /*#__PURE__*/React.createElement("h2", {
    className: "csop-section__title"
  }, "Browse the CSOP ETF series"), /*#__PURE__*/React.createElement("p", {
    className: "csop-section__sub"
  }, FUND_DATA.length, " active products covering A-shares, HK stocks, US equities, thematic, fixed income and virtual assets.")), /*#__PURE__*/React.createElement("div", {
    className: "csop-search"
  }, /*#__PURE__*/React.createElement(FtSearch, {
    size: 16
  }), /*#__PURE__*/React.createElement("input", {
    value: q,
    onChange: e => setQ(e.target.value),
    placeholder: "Search by name or ticker"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "csop-tabs"
  }, CATEGORIES.map(c => /*#__PURE__*/React.createElement("button", {
    key: c,
    className: "csop-tab" + (cat === c ? " is-active" : ""),
    onClick: () => setCat(c)
  }, c, /*#__PURE__*/React.createElement("span", {
    className: "csop-tab__count"
  }, c === "All" ? FUND_DATA.length : FUND_DATA.filter(f => f.cat === c).length)))), /*#__PURE__*/React.createElement("div", {
    className: "csop-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "csop-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Fund"), /*#__PURE__*/React.createElement("th", null, "Category"), /*#__PURE__*/React.createElement("th", null, "Region"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "NAV"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "1-Day"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "YTD"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "AUM"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, rows.map(f => /*#__PURE__*/React.createElement("tr", {
    key: f.ticker,
    onClick: () => onSelect(f)
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("div", {
    className: "csop-fundname"
  }, f.name), /*#__PURE__*/React.createElement("div", {
    className: "csop-ticker"
  }, f.ticker)), /*#__PURE__*/React.createElement("td", null, f.cat), /*#__PURE__*/React.createElement("td", null, f.region), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, f.nav.toFixed(3)), /*#__PURE__*/React.createElement("td", {
    className: "num " + (f.d1 >= 0 ? "pos" : "neg")
  }, (f.d1 >= 0 ? "+" : "−") + Math.abs(f.d1).toFixed(2), "%"), /*#__PURE__*/React.createElement("td", {
    className: "num " + (f.ytd >= 0 ? "pos" : "neg")
  }, (f.ytd >= 0 ? "+" : "−") + Math.abs(f.ytd).toFixed(2), "%"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, f.aum), /*#__PURE__*/React.createElement("td", {
    className: "csop-table__chev"
  }, /*#__PURE__*/React.createElement(FtArrow, {
    size: 16
  })))), rows.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: 8,
    className: "csop-table__empty"
  }, "No funds match your filter."))))), /*#__PURE__*/React.createElement("div", {
    className: "csop-foot-note"
  }, "Source: CSOP, Bloomberg, as at 11 May 2026. NAV and AUM figures are for reference only. Past performance is not indicative of future results.")));
}
window.FundTable = FundTable;
window.FUND_DATA = FUND_DATA;
Object.assign(__ds_scope, { FundTable });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/FundTable.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/Header.jsx
try { (() => {
/* Global header for the CSOP site. Sticky 72px → 56px on scroll.
   Bilingual lockup left, primary nav, language switcher right.            */

const HIcon = ({
  size = 20,
  children
}) => /*#__PURE__*/React.createElement("svg", {
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "1.75",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, children);
const HSearch = p => /*#__PURE__*/React.createElement(HIcon, p, /*#__PURE__*/React.createElement("circle", {
  cx: "11",
  cy: "11",
  r: "7"
}), /*#__PURE__*/React.createElement("path", {
  d: "m20 20-3.5-3.5"
}));
const HGlobe = p => /*#__PURE__*/React.createElement(HIcon, p, /*#__PURE__*/React.createElement("circle", {
  cx: "12",
  cy: "12",
  r: "9"
}), /*#__PURE__*/React.createElement("path", {
  d: "M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"
}));
const HCaret = p => /*#__PURE__*/React.createElement(HIcon, p, /*#__PURE__*/React.createElement("path", {
  d: "m6 9 6 6 6-6"
}));
function Header({
  view,
  setView
}) {
  const [scrolled, setScrolled] = React.useState(false);
  const [lang, setLang] = React.useState("EN");
  const [region, setRegion] = React.useState("Hong Kong");
  const [open, setOpen] = React.useState(null);
  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", onScroll, {
      passive: true
    });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  const navItems = [{
    id: "products",
    label: "Products",
    children: ["ETF series", "Leveraged & Inverse", "Active funds", "Money Market"]
  }, {
    id: "insights",
    label: "Insights",
    children: ["Market commentary", "ETF education", "ESG reports"]
  }, {
    id: "about",
    label: "About",
    children: ["Overview", "Leadership", "Awards", "ESG"]
  }, {
    id: "investor",
    label: "Investor relations",
    children: null
  }, {
    id: "contact",
    label: "Contact",
    children: null
  }];
  return /*#__PURE__*/React.createElement("header", {
    className: "csop-header" + (scrolled ? " is-scrolled" : "")
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-header__inner"
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => {
      e.preventDefault();
      setView("home");
    },
    className: "csop-logo"
  }, /*#__PURE__*/React.createElement("img", {
    src: window.__resources && window.__resources.logo || "../../assets/logo-bilingual.png",
    alt: "CSOP Asset Management"
  })), /*#__PURE__*/React.createElement("nav", {
    className: "csop-nav"
  }, navItems.map(item => /*#__PURE__*/React.createElement("div", {
    key: item.id,
    className: "csop-nav__item",
    onMouseEnter: () => item.children && setOpen(item.id),
    onMouseLeave: () => setOpen(null)
  }, /*#__PURE__*/React.createElement("button", {
    className: "csop-nav__link"
  }, item.label, item.children && /*#__PURE__*/React.createElement(HCaret, {
    size: 14
  })), item.children && open === item.id && /*#__PURE__*/React.createElement("div", {
    className: "csop-nav__menu"
  }, item.children.map(c => /*#__PURE__*/React.createElement("a", {
    key: c,
    href: "#",
    className: "csop-nav__menu-item"
  }, c)))))), /*#__PURE__*/React.createElement("div", {
    className: "csop-header__right"
  }, /*#__PURE__*/React.createElement("button", {
    className: "csop-iconbtn",
    "aria-label": "Search"
  }, /*#__PURE__*/React.createElement(HSearch, {
    size: 18
  })), /*#__PURE__*/React.createElement("button", {
    className: "csop-region"
  }, /*#__PURE__*/React.createElement(HGlobe, {
    size: 16
  }), /*#__PURE__*/React.createElement("span", null, region), /*#__PURE__*/React.createElement(HCaret, {
    size: 12
  })), /*#__PURE__*/React.createElement("div", {
    className: "csop-lang"
  }, ["EN", "繁中", "简中"].map(l => /*#__PURE__*/React.createElement("button", {
    key: l,
    className: "csop-lang__btn" + (lang === l ? " is-active" : ""),
    onClick: () => setLang(l)
  }, l))))));
}
window.Header = Header;
Object.assign(__ds_scope, { Header });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/Header.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/Hero.jsx
try { (() => {
/* Hero module for the CSOP homepage.
   Navy → sapphire wash. Featured ETF on the left, stat strip on the right. */

const HeroArrow = ({
  size = 16
}) => /*#__PURE__*/React.createElement("svg", {
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "1.75",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, /*#__PURE__*/React.createElement("path", {
  d: "M5 12h14M13 5l7 7-7 7"
}));
function Hero() {
  return /*#__PURE__*/React.createElement("section", {
    className: "csop-hero"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__swirl",
    "aria-hidden": "true"
  }), /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__inner"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__lead"
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-eyebrow csop-eyebrow--onDark"
  }, "Featured ETF \xB7 \u7126\u9EDE ETF"), /*#__PURE__*/React.createElement("h1", {
    className: "csop-hero__title"
  }, "The world's largest Saudi\xA0Arabia ETF"), /*#__PURE__*/React.createElement("p", {
    className: "csop-hero__sub"
  }, "Albilad CSOP MSCI Hong Kong China Equity ETF debuted on Saudi Arabia's Tadawul with an initial size exceeding ", /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, "USD 1.2 billion"), " \u2014 the largest ETF in the Middle East."), /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__actions"
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    className: "csop-btn csop-btn--onDark-primary"
  }, "View fund factsheet ", /*#__PURE__*/React.createElement(HeroArrow, {
    size: 16
  })), /*#__PURE__*/React.createElement("a", {
    href: "#",
    className: "csop-btn csop-btn--onDark-secondary"
  }, "Read prospectus")), /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__src"
  }, "Source: CSOP, as at 31 December 2024.")), /*#__PURE__*/React.createElement("div", {
    className: "csop-hero__stats"
  }, [{
    kpi: "USD 20B+",
    label: "Total AUM, group-wide"
  }, {
    kpi: "45",
    label: "Permissible CIES products"
  }, {
    kpi: "#2",
    label: "ETF issuer in Hong Kong by AUM"
  }, {
    kpi: "Since 2008",
    label: "First offshore Chinese asset manager"
  }].map((s, i) => /*#__PURE__*/React.createElement("div", {
    className: "csop-stat",
    key: i
  }, /*#__PURE__*/React.createElement("div", {
    className: "csop-stat__kpi num"
  }, s.kpi), /*#__PURE__*/React.createElement("div", {
    className: "csop-stat__label"
  }, s.label))))));
}
window.Hero = Hero;
Object.assign(__ds_scope, { Hero });
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/Hero.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/app-shell.jsx
try { (() => {
const {
  Header,
  Hero,
  FundTable,
  FundDetail,
  Footer,
  DisclaimerModal
} = window.CSOPDesignSystem_019e15;
/* App shell — orchestrates view state for the CSOP website demo. */

const {
  useState: useStateApp
} = React;
function App() {
  const [view, setView] = useStateApp("home");
  const [fund, setFund] = useStateApp(null);
  const [showDisclaimer, setShowDisclaimer] = useStateApp(true);
  const onSelectFund = f => {
    setFund(f);
    setView("fund-detail");
    window.scrollTo({
      top: 0,
      behavior: "instant"
    });
  };
  return /*#__PURE__*/React.createElement("div", {
    "data-screen-label": "CSOP Site Shell"
  }, /*#__PURE__*/React.createElement(DisclaimerModal, {
    open: showDisclaimer,
    onAccept: () => setShowDisclaimer(false)
  }), /*#__PURE__*/React.createElement(Header, {
    view: view,
    setView: setView
  }), view === "home" && /*#__PURE__*/React.createElement("main", {
    "data-screen-label": "01 Home"
  }, /*#__PURE__*/React.createElement(Hero, null), /*#__PURE__*/React.createElement(FundTable, {
    onSelect: onSelectFund
  })), view === "fund-detail" && fund && /*#__PURE__*/React.createElement("main", {
    "data-screen-label": "02 Fund Detail"
  }, /*#__PURE__*/React.createElement(FundDetail, {
    fund: fund,
    onBack: () => {
      setView("home");
      setFund(null);
    }
  })), /*#__PURE__*/React.createElement(Footer, null));
}
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(/*#__PURE__*/React.createElement(App, null));
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/app-shell.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/print-app.jsx
try { (() => {
const {
  Header,
  Hero,
  FundTable,
  FundDetail,
  Footer,
  DisclaimerModal
} = window.CSOPDesignSystem_019e15;
/* PrintApp — renders both Home and Fund Detail stacked for PDF export. */

function PrintApp() {
  const sampleFund = window.FUND_DATA[0]; // Hang Seng TECH ETF

  return /*#__PURE__*/React.createElement("div", {
    "data-screen-label": "CSOP Site \u2014 Print"
  }, /*#__PURE__*/React.createElement("div", {
    className: "print-page"
  }, /*#__PURE__*/React.createElement(Header, {
    view: "home",
    setView: () => {}
  }), /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement(Hero, null), /*#__PURE__*/React.createElement(FundTable, {
    onSelect: () => {}
  })), /*#__PURE__*/React.createElement(Footer, null)), /*#__PURE__*/React.createElement("div", {
    className: "print-page print-page--break"
  }, /*#__PURE__*/React.createElement(Header, {
    view: "fund-detail",
    setView: () => {}
  }), /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement(FundDetail, {
    fund: sampleFund,
    onBack: () => {}
  })), /*#__PURE__*/React.createElement(Footer, null)));
}
const printRoot = ReactDOM.createRoot(document.getElementById("root"));
printRoot.render(/*#__PURE__*/React.createElement(PrintApp, null));

/* Signal readiness for print after render + fonts settle. */
(async () => {
  try {
    await document.fonts.ready;
  } catch (e) {}
  setTimeout(() => {
    window.__printReady = true;
    if (window.__autoPrint) window.print();
  }, 500);
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/print-app.jsx", error: String((e && e.message) || e) }); }

__ds_ns.DisclaimerModal = __ds_scope.DisclaimerModal;

__ds_ns.Footer = __ds_scope.Footer;

__ds_ns.FundDetail = __ds_scope.FundDetail;

__ds_ns.FundTable = __ds_scope.FundTable;

__ds_ns.Header = __ds_scope.Header;

__ds_ns.Hero = __ds_scope.Hero;

})();
