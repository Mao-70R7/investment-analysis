from __future__ import annotations

from html import escape

NAV_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "产品分析",
        (
            ("institutions.html", "机构总览", "institutions"),
            ("strategies.html", "策略列表", "strategies"),
            ("compare.html", "策略对比", "compare"),
            ("ai-strategy.html", "AI选策略", "ai"),
        ),
    ),
    (
        "基金与排名",
        (
            ("mixed-performance-scatter.html", "全市场产品排名", "mixed_performance_scatter"),
        ),
    ),
)

DETAIL_ACTIVE_ALIASES = {
    "qd_detail": "qd",
    "strategy_detail": "strategies",
    "fund_detail": "mixed_performance_scatter",
}


def normalized_active(active: str = "") -> str:
    return DETAIL_ACTIVE_ALIASES.get(active, active)


def nav_links_html(active: str = "") -> str:
    active_key = normalized_active(active)
    groups: list[str] = []
    for group_label, items in NAV_GROUPS:
        item_html = "\n".join(
            (
                f'<a class="nav-link {"is-active" if key == active_key else ""}" '
                f'href="./{escape(href, quote=True)}">{escape(label)}</a>'
            )
            for href, label, key in items
        )
        groups.append(
            '<div class="nav-group">'
            f'<div class="nav-group-title">{escape(group_label)}</div>'
            f'{item_html}'
            '</div>'
        )
    return "\n".join(groups)


def render_system_topbar(
    active: str = "",
    *,
    brand_mark: str = "天",
    brand_title: str = "投顾数据天眼",
    brand_subtitle: str = "最小发布集",
) -> str:
    return f"""<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="./index.html">
      <span class="brand-mark">{escape(brand_mark)}</span>
      <span>
        <strong>{escape(brand_title)}</strong>
        <small>{escape(brand_subtitle)}</small>
      </span>
    </a>
    <nav class="nav" aria-label="主导航">{nav_links_html(active)}</nav>
  </div>
</header>"""


SIDEBAR_CSS = r"""
body { padding-left:0; background:#f4f4f4; }
.topbar {
  position:sticky;
  top:0;
  z-index: 30;
  width:auto;
  overflow:visible;
  background:rgba(255,255,255,.98);
  border:0;
  border-bottom:1px solid #e8e8e8;
  box-shadow:none;
}
.topbar-inner {
  min-height:70px;
  max-width:1440px;
  margin:0 auto;
  padding:0 28px;
  display:flex;
  flex-direction:row;
  align-items:center;
  justify-content:space-between;
  gap:28px;
}
.brand {
  flex:0 0 auto;
  min-width:215px;
  width:auto;
  padding:0;
  border:0;
}
.nav {
  display:flex;
  flex:1 1 auto;
  min-width:0;
  gap:0;
  justify-content:flex-end;
  overflow-x:auto;
  overflow-y:hidden;
}
.nav-group { display:contents; }
.nav-group-title { display:none; }
.nav-link {
  position:relative;
  display:flex;
  align-items:center;
  justify-content:center;
  min-height:70px;
  width:auto;
  padding:0 19px;
  border-radius:0;
  background:transparent;
  color:#333;
  white-space:nowrap;
  font-weight:500;
}
.nav-link:hover,.nav-link.is-active { color:#ef6815; }
.nav-link.is-active::after { position:absolute; right:16px; bottom:0; left:16px; height:3px; background:#f36b15; content:""; }
.page-shell,
.global-quality-gate { max-width:1440px; }
.internal-test-notice {
  margin: 5px 0 0;
  color: #b42318;
  font-size: 12px;
  line-height: 1.55;
  font-weight: 750;
}
.page-loading-status {
  margin: 16px 0;
  border: 1px solid #f0b8b0;
  border-radius: 8px;
  background: #fff6f3;
  color: #b42318;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.5;
  font-weight: 750;
}
.page-loading-status[hidden] { display: none !important; }
@media (max-width: 960px) {
  .topbar-inner { min-height:58px; padding:0 14px; gap:14px; }
  .brand { min-width:auto; }
  .brand small { display:none; }
  .nav-link { min-height:58px; padding:0 12px; font-size:12px; }
}
@media (max-width: 620px) {
  .topbar-inner { align-items:flex-start; flex-direction:column; gap:0; padding-top:8px; }
  .nav { width:100%; justify-content:flex-start; }
  .nav-link { min-height:46px; }
}
"""
