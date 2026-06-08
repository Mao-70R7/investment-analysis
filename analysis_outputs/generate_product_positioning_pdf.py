import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "basic_data" / "data" / "basic_summary.js"
OUTPUT_DIR = ROOT / "output" / "pdf"
PDF_PATH = OUTPUT_DIR / "product_system_series_positioning_supplement.pdf"

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def load_assignment_json(path: Path):
    text = path.read_text(encoding="utf-8")
    rhs = text.split("=", 1)[1].strip()
    if rhs.endswith(";"):
        rhs = rhs[:-1]
    return json.loads(rhs)


def load_summary():
    return load_assignment_json(SUMMARY_PATH)


def load_detail(detail_file):
    if not detail_file:
        return {}
    path = ROOT / "basic_data" / detail_file
    if not path.exists():
        return {}
    return load_assignment_json(path)


def num(value):
    try:
        if value is None or value == "":
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except Exception:
        return None


def pct(value, digits=1):
    value = num(value)
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def fmt(value, digits=1):
    value = num(value)
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def median(values):
    arr = sorted(v for v in (num(x) for x in values) if v is not None)
    if not arr:
        return None
    mid = len(arr) // 2
    if len(arr) % 2:
        return arr[mid]
    return (arr[mid - 1] + arr[mid]) / 2


def avg(values):
    arr = [v for v in (num(x) for x in values) if v is not None]
    if not arr:
        return None
    return sum(arr) / len(arr)


def clean_text(text, limit=None):
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def field_map(detail, names):
    rows = detail.get("profileFields", []) or []
    values = {row.get("字段"): row.get("值") for row in rows}
    summary = detail.get("summary", {}) or {}
    out = {}
    for name in names:
        out[name] = values.get(name, summary.get(name))
    return out


def current_holdings(detail, limit=5):
    snapshots = detail.get("positionSnapshots", []) or []
    current = next((snap for snap in snapshots if snap.get("id") == "current"), None)
    holdings = list((current or {}).get("holdings", []) or [])
    holdings.sort(key=lambda row: num(row.get("权重")) or 0, reverse=True)
    return holdings[:limit]


def detail_intro(detail):
    values = field_map(detail, ["策略概念", "策略描述"])
    return clean_text(values.get("策略描述") or values.get("策略概念"), 160)


def top_business_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("业务分类") or "未分类"].append(row)
    out = []
    for name, items in groups.items():
        out.append(
            {
                "业务分类": name,
                "数量": len(items),
                "近1年中位": median(row.get("近1年") for row in items),
                "广发数量": sum(1 for row in items if "广发" in f"{row.get('投顾机构','')} {row.get('策略名称','')}"),
            }
        )
    out.sort(key=lambda row: row["数量"], reverse=True)
    return out[:8]


def team_positioning(name, rows):
    top_names = "、".join(row.get("策略名称", "") for row in rows[:3])
    businesses = [row.get("业务分类") or "未分类" for row in rows]
    major = Counter(businesses).most_common(2)
    major_text = "、".join(f"{k}{v}只" for k, v in major)
    if "中欧" in name:
        comment = "高收益代表产品集中在AI、硬科技、中国智造等科技赛道，系列化主题能力突出；收益弹性强，同时回撤和波动处于高位。"
    elif "南方" in name or "司南" in name:
        comment = "司南系列以行业主题基金精选为核心，科技和新能源产品表现靠前，适合作为赛道型组合货架观察对象。"
    elif "广发" in name:
        comment = "广发侧代表产品为科技主题组合，近一年表现进入全市场前列；可围绕科技/全球成长场景提炼营销样板，并复盘同系列扩展空间。"
    else:
        comment = f"代表产品包括{top_names}，主要分布在{major_text}。"
    return comment


def p(text, style):
    return Paragraph(clean_text(text), style)


def draw_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawString(18 * mm, 12 * mm, "全市场投顾分析平台｜产品体系与系列定位补充")
    canvas.drawRightString(192 * mm, 12 * mm, f"{doc.page}")
    canvas.restoreState()


def make_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName=FONT,
            fontSize=22,
            leading=30,
            textColor=colors.HexColor("#0f172a"),
            alignment=TA_LEFT,
            wordWrap="CJK",
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName=FONT,
            fontSize=10.5,
            leading=16,
            textColor=colors.HexColor("#475569"),
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "h1",
            fontName=FONT,
            fontSize=15,
            leading=21,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=12,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=FONT,
            fontSize=12,
            leading=17,
            textColor=colors.HexColor("#111827"),
            spaceBefore=8,
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "body",
            fontName=FONT,
            fontSize=9.3,
            leading=14.2,
            textColor=colors.HexColor("#263238"),
            wordWrap="CJK",
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small",
            fontName=FONT,
            fontSize=8.3,
            leading=12,
            textColor=colors.HexColor("#64748b"),
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "table",
            fontName=FONT,
            fontSize=7.8,
            leading=10.4,
            textColor=colors.HexColor("#111827"),
            wordWrap="CJK",
        ),
        "tableRight": ParagraphStyle(
            "tableRight",
            fontName=FONT,
            fontSize=7.8,
            leading=10.4,
            textColor=colors.HexColor("#111827"),
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "kpi": ParagraphStyle(
            "kpi",
            fontName=FONT,
            fontSize=18,
            leading=23,
            textColor=colors.HexColor("#0f766e"),
            alignment=TA_CENTER,
        ),
        "kpiLabel": ParagraphStyle(
            "kpiLabel",
            fontName=FONT,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
    }
    return styles


def table(data, widths, header=True, repeat=1):
    tbl = Table(data, colWidths=widths, repeatRows=repeat if header else 0, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ]
    for r in range(1 if header else 0, len(data)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#fafafa")))
    tbl.setStyle(TableStyle(style))
    return tbl


def build_pdf():
    summary = load_summary()
    strategies = summary.get("strategies", []) or []
    valid = [
        row
        for row in strategies
        if num(row.get("近1年")) is not None and not str(row.get("风险等级", "")).startswith("D0")
    ]
    valid.sort(key=lambda row: num(row.get("近1年")) or -999, reverse=True)

    top_products = []
    for row in valid[:3]:
        detail = load_detail(row.get("detailFile"))
        merged = dict(row)
        merged["detail"] = detail
        merged["intro"] = detail_intro(detail)
        top_products.append(merged)

    by_org = defaultdict(list)
    for row in valid:
        by_org[row.get("投顾机构") or "未披露"].append(row)
    team_rows = []
    for org, rows in by_org.items():
        rows = sorted(rows, key=lambda row: num(row.get("近1年")) or -999, reverse=True)
        team_rows.append(
            {
                "投顾机构": org,
                "样本数": len(rows),
                "最佳产品": rows[0].get("策略名称"),
                "最佳近1年": rows[0].get("近1年"),
                "中位近1年": median(row.get("近1年") for row in rows),
                "平均近1年": avg(row.get("近1年") for row in rows),
                "top": rows[:5],
            }
        )
    team_rows.sort(key=lambda row: num(row.get("最佳近1年")) or -999, reverse=True)
    top_teams = team_rows[:3]

    business_rows = top_business_summary(valid)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    styles = make_styles()
    doc = BaseDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title="产品体系与系列定位补充报告",
        author="全市场投顾分析平台",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="normal", frames=[frame], onPage=draw_page)])

    story = []
    story.append(p("产品体系与系列定位补充报告", styles["title"]))
    story.append(p("二、产品体系与系列定位 - 近一年绩优产品与投顾团队补充", styles["subtitle"]))
    story.append(Spacer(1, 5 * mm))

    kpi_data = [
        [
            p(str(len(valid)), styles["kpi"]),
            p(pct(top_products[0].get("近1年")), styles["kpi"]),
            p(pct(top_products[2].get("近1年")), styles["kpi"]),
            p(str(len(by_org)), styles["kpi"]),
        ],
        [
            p("可比策略数", styles["kpiLabel"]),
            p("近1年第一名", styles["kpiLabel"]),
            p("前三门槛", styles["kpiLabel"]),
            p("投顾机构/团队数", styles["kpiLabel"]),
        ],
    ]
    kpi_tbl = table(kpi_data, [40 * mm, 40 * mm, 40 * mm, 40 * mm], header=False, repeat=0)
    kpi_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe3ea")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ]
        )
    )
    story.append(kpi_tbl)
    story.append(Spacer(1, 6 * mm))

    story.append(p("一、核心判断", styles["h1"]))
    bullets = [
        "近一年收益前三产品全部落在R5权益/进取风险等级，收益来源集中在AI、硬科技、科技基金精选等高弹性科技赛道。",
        "前三产品近一年收益均超过126%，但最大回撤约40%-53%，不应解读为低风险产品；更适合作为进取型客户、科技主题营销和赛道型投研复盘样本。",
        "当前数据没有稳定披露个人投资经理姓名。报告中的“投资经理”按可审计的投顾机构/主理团队口径展示，并保留数据口径说明，避免编造个人信息。",
        "广发侧最佳代表为“带你投科技”，近一年收益110.0%，未进入产品前三但进入团队代表产品前三，说明科技主题具备可营销的优势样本。",
    ]
    for item in bullets:
        story.append(p("• " + item, styles["body"]))

    story.append(p("二、产品体系与系列定位", styles["h1"]))
    story.append(
        p(
            "从产品体系看，绩优样本并不是均衡配置或现金管理产品，而是集中在高权益、高主题纯度的进取型产品。"
            "因此该章节建议把“系列定位”拆成两层：基础货架用于风险等级和业务分类互斥归一；绩优观察池用于识别阶段性行情中最有营销与复盘价值的系列。",
            styles["body"],
        )
    )
    business_table = [[p("业务分类", styles["table"]), p("市场数", styles["tableRight"]), p("广发数", styles["tableRight"]), p("近1年中位", styles["tableRight"])]]
    for row in business_rows:
        business_table.append(
            [
                p(row["业务分类"], styles["table"]),
                p(str(row["数量"]), styles["tableRight"]),
                p(str(row["广发数量"]), styles["tableRight"]),
                p(pct(row["近1年中位"]), styles["tableRight"]),
            ]
        )
    story.append(table(business_table, [72 * mm, 28 * mm, 28 * mm, 35 * mm]))
    story.append(Spacer(1, 3 * mm))
    story.append(
        p(
            "定位建议：主题/行业型、海外/全球型是近一年高收益最集中的观察层；纯债/短债型和现金管理型主要承担底仓与稳健配置功能；目标盈系列应继续单独按系列而非期次观察。",
            styles["small"],
        )
    )

    story.append(PageBreak())
    story.append(p("三、近一年收益率最高的三个产品", styles["h1"]))
    rank_table = [[p("排名", styles["table"]), p("产品", styles["table"]), p("机构", styles["table"]), p("业务分类", styles["table"]), p("近1年", styles["tableRight"]), p("最大回撤", styles["tableRight"])]]
    for i, row in enumerate(top_products, 1):
        rank_table.append(
            [
                p(str(i), styles["table"]),
                p(row.get("策略名称", ""), styles["table"]),
                p(row.get("投顾机构", ""), styles["table"]),
                p(row.get("业务分类", ""), styles["table"]),
                p(pct(row.get("近1年")), styles["tableRight"]),
                p(pct(row.get("最大回撤")), styles["tableRight"]),
            ]
        )
    story.append(table(rank_table, [12 * mm, 43 * mm, 42 * mm, 32 * mm, 18 * mm, 22 * mm]))
    story.append(Spacer(1, 4 * mm))

    for i, row in enumerate(top_products, 1):
        detail = row["detail"]
        f = field_map(detail, ["成立日期", "建议持有时长", "业绩基准", "披露风险等级"])
        story.append(p(f"{i}. {row.get('策略名称')}｜{row.get('投顾机构')}", styles["h2"]))
        metrics = (
            f"近1年收益{pct(row.get('近1年'))}，最大回撤{pct(row.get('最大回撤'))}，"
            f"波动率{pct(row.get('波动率'))}，夏普比率{fmt(row.get('夏普比率'), 2)}；"
            f"测算风险等级{row.get('风险等级')}，披露风险等级{f.get('披露风险等级') or '-'}。"
        )
        story.append(p(metrics, styles["body"]))
        story.append(p("产品定位：" + (row.get("intro") or "未披露策略描述。"), styles["body"]))
        story.append(
            p(
                f"基础信息：成立日期{f.get('成立日期') or '-'}，建议持有{f.get('建议持有时长') or '-'}，"
                f"持仓基金数{row.get('持仓基金数') or '-'}只，年化投顾费率{pct(row.get('年化投顾费率'), 2)}。",
                styles["body"],
            )
        )
        asset = (
            f"权益权重{pct(row.get('权益基金权重'))}，债券权重{pct(row.get('债券基金权重'))}，"
            f"货币权重{pct(row.get('货币基金权重'))}，QDII权重{pct(row.get('QDII权重'))}，"
            f"指数权重{pct(row.get('指数基金权重'))}。"
        )
        story.append(p("仓位结构：" + asset, styles["body"]))
        holdings = current_holdings(detail, 5)
        hold_data = [[p("前五大持仓基金", styles["table"]), p("权重", styles["tableRight"])]]
        for holding in holdings:
            hold_data.append([p(holding.get("基金名称", ""), styles["table"]), p(pct(holding.get("权重")), styles["tableRight"])])
        story.append(table(hold_data, [126 * mm, 28 * mm]))
        if i < len(top_products):
            story.append(Spacer(1, 5 * mm))

    story.append(p("四、业绩最好的三个投资经理/主理团队", styles["h1"]))
    story.append(
        p(
            "口径说明：当前策略详情库未稳定披露个人投资经理姓名。本节使用“投顾机构/主理团队”作为经理口径，按该团队旗下近一年最佳代表产品收益排序；同时展示样本数、中位收益和代表产品，用于判断业绩是否只来自单一爆款。",
            styles["small"],
        )
    )
    team_table = [[p("排名", styles["table"]), p("投顾机构/主理团队", styles["table"]), p("样本数", styles["tableRight"]), p("最佳产品", styles["table"]), p("最佳近1年", styles["tableRight"]), p("中位近1年", styles["tableRight"])]]
    for i, row in enumerate(top_teams, 1):
        team_table.append(
            [
                p(str(i), styles["table"]),
                p(row["投顾机构"], styles["table"]),
                p(str(row["样本数"]), styles["tableRight"]),
                p(row["最佳产品"], styles["table"]),
                p(pct(row["最佳近1年"]), styles["tableRight"]),
                p(pct(row["中位近1年"]), styles["tableRight"]),
            ]
        )
    story.append(table(team_table, [12 * mm, 42 * mm, 18 * mm, 43 * mm, 24 * mm, 24 * mm]))
    story.append(Spacer(1, 4 * mm))

    for i, row in enumerate(top_teams, 1):
        rows = row["top"]
        story.append(p(f"{i}. {row['投顾机构']}", styles["h2"]))
        story.append(
            p(
                f"代表产品为{row['最佳产品']}，近1年收益{pct(row['最佳近1年'])}；"
                f"旗下可比策略{row['样本数']}只，近1年收益中位数{pct(row['中位近1年'])}，平均值{pct(row['平均近1年'])}。",
                styles["body"],
            )
        )
        story.append(p("定位解读：" + team_positioning(row["投顾机构"], rows), styles["body"]))
        top_data = [[p("代表产品", styles["table"]), p("业务分类", styles["table"]), p("风险等级", styles["table"]), p("近1年", styles["tableRight"]), p("最大回撤", styles["tableRight"])]]
        for prod in rows[:3]:
            top_data.append(
                [
                    p(prod.get("策略名称", ""), styles["table"]),
                    p(prod.get("业务分类", ""), styles["table"]),
                    p(prod.get("风险等级", ""), styles["table"]),
                    p(pct(prod.get("近1年")), styles["tableRight"]),
                    p(pct(prod.get("最大回撤")), styles["tableRight"]),
                ]
            )
        story.append(table(top_data, [46 * mm, 35 * mm, 34 * mm, 22 * mm, 24 * mm]))
        story.append(Spacer(1, 4 * mm))

    story.append(p("五、后续数据采集建议", styles["h1"]))
    for item in [
        "策略合同和产品介绍应新增结构化字段：投资经理姓名、主理人姓名、团队名称、任职起始日、是否多人共管。",
        "投顾团队业绩应同时展示最佳产品、平均收益和中位收益，避免只用单一爆款代表团队能力。",
        "绩优产品应与风险等级、最大回撤和波动率同步展示，营销场景中优先标注适配客户类型和风险承受能力。",
    ]:
        story.append(p("• " + item, styles["body"]))

    doc.build(story)
    return PDF_PATH


if __name__ == "__main__":
    path = build_pdf()
    print(path)
