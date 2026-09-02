from __future__ import annotations

import argparse
import calendar
import gc
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from basic_data_navigation import SIDEBAR_CSS, render_system_topbar
from business_naming import canonical_advisor_institution, canonical_business_channel


def locate_code_root() -> Path:
    configured = str(os.environ.get("ADVISOR_CODE_ROOT") or "").strip()
    if configured:
        candidate = Path(configured).resolve()
        if (candidate / "节点脚本").is_dir() and (candidate / "basic_data").is_dir():
            return candidate
    for candidate in (Path.cwd().resolve(), *Path(__file__).resolve().parents):
        if (candidate / "节点脚本").is_dir() and (candidate / "basic_data").is_dir():
            return candidate
    raise RuntimeError("unable to locate code root containing 节点脚本 and basic_data")


PROJECT_ROOT = locate_code_root()
DEFAULT_DB_PATH = Path(os.environ.get("ADVISOR_DATABASE_ROOT") or PROJECT_ROOT / "data") / "analysis_zh_current.sqlite"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "site"
DEFAULT_SITE_DIR = DEFAULT_REPORT_ROOT / "basic_data"
ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
ASSET_VERSION = "basic_data_20260811_minimal_orange_risk_weight_v3"
PERFORMANCE_RECENCY_TOLERANCE_DAYS = 5
PERFORMANCE_MIN_POINT_COUNT = 2
PERFORMANCE_MAX_GAP_DAYS = 45
REPORT_TEMPLATE_DIR = PROJECT_ROOT / "basic_data"
DISPLAY_STRATEGY_CHANNEL_IDS = {"ttfund", "gffunds", "gfsec_fima", "gfsec_robot", "qieman", "southern"}
LIST_ONLY_DISPLAY_CHANNELS: set[str] = {"gfsec_robot"}
LEGACY_ARCHIVE_CHANNEL_IDS: set[str] = {"gfsec_robot"}
FUND_RANK_PERIODS = [
    ("近一月", 31),
    ("近三月", 92),
    ("近6月", 183),
    ("近1年", 365),
]
BENCHMARK_ASSET_MAJOR_FIELDS = [
    "基准资产大类-权益",
    "基准资产大类-债券",
    "基准资产大类-现金",
    "基准资产大类-商品",
    "基准资产大类-另类",
    "基准资产大类-其他",
]
BENCHMARK_ASSET_CATEGORY_FIELDS = [
    "基准资产类别-A股",
    "基准资产类别-港股",
    "基准资产类别-海外权益",
    "基准资产类别-债券",
    "基准资产类别-商品",
    "基准资产类别-现金",
    "基准资产类别-其他",
]
BENCHMARK_ASSET_META_FIELDS = [
    "基准风险资产权重",
    "基准风险资产权重_百分比",
    "基准风险资产权重说明",
    "权益中枢",
    "固收中枢",
    "基准风险资产中枢",
    "海外配置中枢",
    "指数化程度",
    "主动管理程度",
    "风险资产偏离",
    "配置风格标签",
    "基准结构类型",
    "非权益比较轨道",
    "正式可比池",
    "可比池样本资格",
    "可比池说明",
    "基准互斥权重合计_百分比",
    "基准港股权益权重",
    "基准海外权益权重",
    "是否多元策略",
    "多元策略标签",
    "基准映射置信度",
    "基准资产已映射权重",
    "基准资产未映射权重",
]
BENCHMARK_ASSET_DISPLAY_FIELDS = [
    *BENCHMARK_ASSET_META_FIELDS,
    *BENCHMARK_ASSET_MAJOR_FIELDS,
    *BENCHMARK_ASSET_CATEGORY_FIELDS,
]


def project_arg(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def load_template_asset_text(relative_path: str, fallback: str) -> str:
    asset_path = REPORT_TEMPLATE_DIR / relative_path
    if asset_path.exists():
        try:
            return asset_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return fallback
    return fallback


FIELD_DICTIONARY: dict[str, str] = {
    "数据更新至": "本页使用当前分析库中可用业务日期的最大值，综合策略业绩日期、基金净值日期和调仓日期判断。",
    "数据刷新时间": "本次页面数据包生成或刷新完成的本地时间，用于判断页面是否已经重新导出。",
    "数据来源标记": "页面字段名称不带 * 表示采集数据或平台直接披露字段；字段名称带 * 表示基于基础数据加工、汇总、计算或规则归类得到。",
    "接入渠道数": "策略主表中已经接入并可识别的渠道数量。",
    "策略总数": "策略信息表中的统一策略数量，包含天天基金、基金公司直销和公开页来源。",
    "天天策略数": "渠道为天天基金/投顾的策略数量。",
    "有历史调仓策略数": "至少有一条历史调仓事件的策略数量。",
    "有官方业绩策略数": "至少有一条 App 或渠道披露日度业绩曲线的策略数量。",
    "纳入回放策略数": "当前统一算法版本下已纳入自建净值回放的策略数量。",
    "策略基金净值缺失数": "所有策略调仓和持仓涉及基金中，在基金日度净值表没有任何净值记录的基金数量。",
    "基金数": "基金信息表中的基金代码数量。",
    "基金净值行数": "基金日度净值表中的历史净值记录数。",
    "基金分红事件数": "基金分红送配表中的分红记录数，回放时默认按红利再投处理。",
    "渠道": "策略数据来源渠道，使用业务展示名称，不展示内部渠道代码。",
    "渠道类型": "渠道所属业务类型，例如第三方销售平台、基金公司直销、财富管理平台。",
    "策略数": "该渠道下的策略主表记录数。",
    "入选策略数": "AI核心仓位达到入选标准的策略数量。同一策略只计算一次，用于判断当前主题下可进入重点观察池的产品规模。",
    "均值达标策略数": "最近一年平均AI核心仓位达到50%的策略数量。该指标更看重持续配置，而不是短期单次持有。",
    "峰值达标策略数": "最近一年曾在任一持仓快照中AI核心仓位达到50%的策略数量。该指标用于发现阶段性重仓AI主题的策略。",
    "AI核心基金数": "入选策略中贡献AI核心仓位的底层基金去重数量。用于判断入选结果是由少数基金集中贡献，还是覆盖了更多基金工具。",
    "标准实体AI基金数": "底层基金已被标准主题实体识别为AI核心相关的基金数量。只统计有明确主题证据的基金。",
    "点阵样本数": "当前点阵图中可比较的策略数量。样本需要具备收益、回撤和AI核心暴露等必要指标。",
    "AI核心均值暴露": "策略在观察期内平均持有AI核心相关基金的仓位比例。数值越高，代表策略对AI主题的持续配置越强。",
    "AI核心峰值暴露": "策略在观察期内AI核心相关基金仓位达到过的最高比例。用于识别阶段性重仓AI主题的策略。",
    "当前AI核心暴露": "策略最新持仓中AI核心相关基金的仓位比例。用于观察策略当前是否仍在配置AI主题。",
    "峰值日期": "策略AI核心仓位达到观察期最高值的日期。用于判断重仓AI主题发生在近期还是历史阶段。",
    "主要AI核心基金": "对策略AI核心仓位贡献较高的底层基金。展示这些基金是为了说明策略为什么被纳入AI主题观察池。",
    "完整策略数": "该渠道内同时具备官方披露业绩和标准回放净值、可以做同区间业绩对比的策略数量。",
    "官方业绩覆盖": "该渠道有官方日度业绩曲线的策略数占该渠道策略数的比例。",
    "历史调仓覆盖": "该渠道有历史调仓事件的策略数占该渠道策略数的比例。",
    "当前持仓覆盖": "该渠道有当前持仓记录的策略数占该渠道策略数的比例。天天基金若 App 只披露基金列表不披露权重，会在详情页用推算持仓补齐；广发当前持仓披露日早于最新基金净值日时，按最后调仓仓位和基金复权收益滚动到最新净值日。",
    "回放覆盖": "该渠道已纳入统一净值回放的策略数占该渠道策略数的比例。",
    "最新业绩日": "该渠道官方日度业绩曲线的最大交易日期。",
    "最新调仓日": "该渠道历史调仓事件的最大调仓日期。",
    "有基准": "策略主档存在可追溯的业绩基准文本时为是。",
    "有业绩走势": "策略至少有两个可画的官方披露净值点时为是。",
    "有历史仓位": "存在权重完整闭合的官方历史仓位快照或完整调仓后仓位时为是；发车指令比例不计入。",
    "对客未终止": "当前对客展示口径为是，且未命中停止、终止、下架、清盘、历史接口留档等治理规则时为是。",
    "官方历史仓位快照数": "标准化层直接披露并已落入主库的官方历史仓位快照数量。",
    "完整历史仓位快照数": "基金权重全部精确且合计在99%至101%之间的官方历史仓位快照数量。",
    "完整调仓后仓位数": "普通调仓明细中调后权重全部披露且合计在99%至101%之间的事件数量。",
    "历史仓位口径": "说明历史仓位来自官方完整快照、完整调仓后仓位或两者。",
    "表名": "当前分析库中的中文业务表名。",
    "记录数": "对应业务表当前保留的有效记录数。",
    "策略名称": "渠道披露或内部策略清单中的策略展示名称。",
    "投顾机构": "提供投顾服务或策略算法的机构名称。",
    "策略类型": "渠道披露的策略分类或产品系列。",
    "披露策略类型": "渠道原始披露的策略分类或产品系列。该字段并非所有渠道稳定披露，只作为原始披露保留，不作为正式同类比较口径。",
    "披露风险等级": "渠道原始披露的风险等级文本。正式筛选仍使用统一展示的风险等级字段。",
    "风险等级": "渠道披露的策略风险等级；未披露时展示为未披露。",
    "研报产品类型": "面向业务研报和总览统计的互斥产品类型，基于主可比池、当前持仓资产权重和业绩基准资产结构生成。",
    "研报股票子类型": "股票型策略的进一步归类，例如主题/行业、海外/全球股票、指数/被动或主动权益；非股票型为空。",
    "业务分类": "经营分析使用的主业务分类，默认沿用互斥主可比池，用于目标盈系列压缩、货架机会和策略列表筛选。",
    "业务分类依据": "系统生成研报产品类型和业务分类时使用的资产权重、基准解析和关键词规则说明。",
    "天天当前对客展示": "策略是否按当前采集口径判断为对客展示。天天基金策略结合运作状态判断，其他展示渠道按已进入业务展示口径处理。",
    "天天展示状态": "策略在天天基金或当前展示渠道的对客状态说明，用于过滤已终止、隐藏或非对客样本。",
    "天天展示判定依据": "生成天天当前对客展示和天天展示状态时使用的渠道、运作状态和展示口径说明。",
    "主可比池": "策略评价的第一层互斥分类。优先按目标日期/养老、目标盈系列、海外/全球、主题/行业、现金管理、纯债/短债、固收增强、偏股配置、多资产配置顺序归属，确保同一策略只进入一个主排名池；海外/全球必须有 QDII/海外持仓、海外基准或明确境外市场名称证据。",
    "市场地域": "按当前持仓 QDII/海外基金权重、海外业绩基准和策略名称/标签中的明确境外市场词识别国内、海外/全球或混合地域；通用投资范围、机构品牌、黄金/商品不触发海外地域。",
    "主动被动": "按底层基金标准分类字典中的主动被动标签、指数基金/ETF/联接基金标识加权汇总后归并为主动为主、指数/被动为主或主动被动混合。",
    "特殊标签": "主题行业、目标日期/养老、海外全球、商品黄金等可能影响可比性的多标签。该字段可重复命中，仅用于解释和筛选，不用于重复排名。",
    "策略实现标签": "按持仓基金工具属性生成的多标签，例如主动基金组合、指数工具组合、主动+指数混合、QDII/海外工具、FOF/养老工具等。",
    "权益基金权重": "当前仓位中按基金标准分类字典归为权益/偏股/股票方向的基金权重合计；混合基金另列，不直接等同穿透股票仓位。未取得基金级当前持仓时显示未披露，不用 0 代替。",
    "债券基金权重": "当前仓位中按基金标准分类字典归为债券、短债、纯债、可转债或固收方向的基金权重合计。未取得基金级当前持仓时显示未披露，不用 0 代替。",
    "货币基金权重": "当前仓位中按基金标准分类字典归为货币基金、现金管理或货币市场基金方向的基金权重合计。",
    "混合基金权重": "当前仓位中按基金标准分类字典归为混合基金但无法进一步稳定拆分权益/债券的基金权重合计。",
    "QDII权重": "当前仓位中基金标准分类字典标识为 QDII 或市场地域标签为海外/全球的基金权重合计。",
    "指数基金权重": "当前仓位中指数基金、ETF、ETF 联接或指数增强基金权重合计，用于判断策略实现方式是否偏被动工具。",
    "主动基金权重": "当前仓位中标准分类字典主动被动标签为主动的基金权重合计。",
    "基准权益权重": "从业绩基准文本中解析出的权益类指数或偏股基金指数组件权重合计；未披露或无法解析时为空。",
    "基准债券权重": "从业绩基准文本中解析出的债券类指数、债券基金指数或中债指数组件权重合计；未披露或无法解析时为空。",
    "基准货币权重": "从业绩基准文本中解析出的货币基金指数或现金组件权重合计；未披露或无法解析时为空。",
    "基准风险资产权重": "按业绩基准中权益、商品和另类风险资产合计权重分档：0%为L0，0%-10%为L1，之后每10个百分点一档，90%-100%为L10。该字段是策略分类、页面筛选和同类比较的首层口径。",
    "基准风险资产权重_百分比": "业绩基准中权益、商品和另类资产权重合计百分比；港股和海外权益属于权益子项，不重复计入。",
    "基准风险资产权重说明": "说明基准风险资产权重的业务口径和可用性；若基准未知权重超过0.01%，不硬分档。",
    "权益中枢": "当前组合的权益基金权重加上50%的混合基金权重，用于近似观察组合长期权益暴露。",
    "固收中枢": "当前组合的债券、货币基金权重加上50%的混合基金权重，用于近似观察组合长期固收暴露。",
    "基准风险资产中枢": "与基准风险资产权重相同，用于同组合权益中枢比较并计算风险资产偏离。",
    "海外配置中枢": "当前QDII/海外基金权重与业绩基准海外权益权重中的较大值，用于判断策略海外配置强度。",
    "指数化程度": "当前指数基金、ETF及联接基金权重合计，用于判断策略工具实现方式。",
    "主动管理程度": "当前主动基金权重合计，用于判断策略对主动管理工具的依赖程度。",
    "风险资产偏离": "权益中枢减去基准风险资产中枢，正值表示组合当前风险资产暴露高于基准，负值表示低于基准。",
    "配置风格标签": "由基准风险资产权重、权益/固收中枢、海外配置、指数化程度和主动管理程度组合形成的业务标签。",
    "非权益比较轨道": "在同一L档内，非权益资产中单一资产占非权益合计80%以上时按债券、货币、商品或另类主导归类；否则为多资产，非权益为0时为纯权益。",
    "正式可比池": "由基准风险资产权重与非权益比较轨道组成。只有互斥资产向量合计100%±0.01%、未知权重不超过0.01%的产品可进入。",
    "可比池样本资格": "是否满足进入正式可比池的基准解析完整性要求。样本不足5只时仍保留池归属，但不计算同类分位数。",
    "基准互斥权重合计_百分比": "权益、债券、货币、商品、另类、未知六类互斥资产权重之和；港股权益和海外权益是权益子项，不重复计入。",
    "基准港股权益权重": "总权益中的港股权益子项，只用于地域解释，不重复计入互斥资产合计。",
    "基准海外权益权重": "总权益中的海外权益子项，只用于地域解释，不重复计入互斥资产合计。",
    "是否多元策略": "基准资产类别同时包含A股、海外（港股或海外权益）和另类（商品等）时标记为1，否则为0。",
    "多元策略标签": "展示基准资产类别中同时出现的多元配置组合，例如A股+海外+另类。",
    "基准映射置信度": "基于基准分类表识别业绩基准组件后的结构化置信度；高表示组件和权重均已稳定映射。",
    "基准资产已映射权重": "业绩基准文本中已被基准分类表映射到资产大类和资产类别的权重合计。",
    "基准资产未映射权重": "业绩基准文本中识别出权重但暂未在基准分类表中找到资产分类的组件权重合计。",
    "基准资产大类-权益": "按基准分类表将业绩基准组件归入资产大类后，权益大类的权重合计。",
    "基准资产大类-债券": "按基准分类表将业绩基准组件归入资产大类后，债券大类的权重合计。",
    "基准资产大类-其他": "按基准分类表暂未映射到权益或债券大类的基准组件权重合计。",
    "基准资产类别-A股": "按基准分类表将业绩基准组件归入资产类别后，A股类别的权重合计。",
    "基准资产类别-港股": "按基准分类表将业绩基准组件归入资产类别后，港股类别的权重合计。",
    "基准资产类别-海外权益": "按基准分类表将业绩基准组件归入资产类别后，海外权益类别的权重合计。",
    "基准资产类别-债券": "按基准分类表将业绩基准组件归入资产类别后，债券类别的权重合计。",
    "基准资产类别-商品": "按基准分类表将业绩基准组件归入资产类别后，商品类别的权重合计。",
    "基准资产类别-现金": "按基准分类表将业绩基准组件归入资产类别后，现金类别的权重合计。",
    "基准资产类别-其他": "按基准分类表暂未映射到已有资产类别的基准组件权重合计。",
    "基准可用状态": "策略基准费率状态表对文本基准、日度基准曲线和区间基准可用性的汇总结果。",
    "基础数据等级": "策略基准费率状态表给出的基础数据可用等级，用于提示是否适合进入正式评价。",
    "费率状态": "策略基准费率状态表对投顾服务费率是否已披露、是否可结构化为年化百分比的判断。",
    "年化投顾费率": "从投顾费率文本结构化得到的年化投顾服务费率百分比；无法结构化时为空。",
    "分类依据": "系统生成主可比池、市场地域、主动被动和策略实现标签时使用的主要权重、文本和优先级规则说明。",
    "成立日期": "策略成立或开始披露业绩的日期，优先取策略主表成立日期。",
    "运作天数": "从策略成立日期到最新收益数据日的自然日天数；若缺少任一日期则不展示。",
    "运作状态": "将渠道原始状态归并为正常运作、公开披露、已终止或未披露，避免展示过多内部状态码。",
    "业绩完整": "最新业绩日期距全库最新业绩日不超过5天、至少有2个可画净值点，且整段业绩曲线相邻点最大间隔不超过45天时标记为是。该口径不要求基准或仓位完整。",
    "业绩完整性": "按业绩时效、可画净值点数量和曲线连续性判断为完整或缺失，用于AI选策略的默认候选池。",
    "业绩完整性说明": "说明业绩是否满足AI筛选口径；不满足时明确列出过期、点位不足或曲线中断等原因。",
    "数据完整性": "只展示完整或不完整。完整表示历史调仓链、底层基金净值依赖、标准回放净值和官方披露业绩均可用于同区间对比；不完整表示至少一个环节缺失或不可比。",
    "近一周": "基于 App 或渠道披露单位净值计算，取最新披露日相对 7 个自然日前最近可用披露净值的收益率。",
    "近一月": "基于 App 或渠道披露单位净值计算，以最新披露日往前 1 个自然月为目标日，取目标日当日或之前最近可用披露净值的收益率。",
    "近三月": "基于 App 或渠道披露单位净值计算，以最新披露日往前 3 个自然月为目标日，取目标日当日或之前最近可用披露净值的收益率。",
    "近6月": "基于 App 或渠道披露单位净值计算，以最新披露日往前 6 个自然月为目标日，取目标日当日或之前最近可用披露净值的收益率。",
    "近1年": "基于 App 或渠道披露单位净值计算，以最新披露日往前 12 个自然月为目标日，取目标日当日或之前最近可用披露净值的收益率。",
    "今年以来": "基于 App 或渠道披露单位净值计算，取最新披露日相对当年首个可用披露净值的收益率；仅有一个净值点时不可计算，显示未披露。",
    "累计收益率": "优先取 App 或渠道披露的最新累计收益率；缺失时用披露单位净值从首日到最新日反推。",
    "最新业绩日期": "该策略 App 或渠道官方披露业绩曲线的最新可画交易日期，可能早于整体数据更新日期。",
    "收益数据截至": "用于列表区间收益计算的最新 App 或渠道披露净值日期。",
    "质检情况": "按策略历史调仓数据、基金净值数据、策略净值数据、官方披露业绩、模拟业绩五类展示完整或不完整及原因。",
    "稽核结论": "最新持仓推算稽核对该策略当前持仓是否可直接使用、是否需要推算补齐、是否存在结构差异给出的中文结论。",
    "官方累计收益": "渠道或 App 直接披露的最新成立以来累计收益率。",
    "自建累计收益": "基于历史调仓基金仓位、基金净值、分红和投顾费口径回放得到的最新累计收益率；默认展示贴近 App 的费前口径。",
    "与官方偏差": "自建回放在官方可比区间内相对官方披露收益率的差值，单位为百分点。",
    "最大回撤": "优先基于至少两个清洗后的 App 或渠道披露单位净值计算，计算公式为历史高点至后续低点的最大跌幅；只有一个净值点时不可计算，显示未披露；无可用披露曲线时回退自建回放或平台披露字段。",
    "最新持仓日": "当前持仓或推算持仓对应日期。广发策略若 App 当前持仓日期滞后，会优先展示按最后调仓仓位和基金复权收益滚动到全库最新基金净值日的日期。",
    "持仓基金数": "当前可展示的基金级持仓数量。",
    "最近调仓日": "该策略最近一次历史调仓日期。",
    "调仓次数": "该策略在当前分析库中保留的调仓事件数量。",
    "策略代码": "渠道侧策略代码，用于和 App 或内部清单回连。",
    "统一策略ID": "本分析系统统一生成的策略主键，由渠道和渠道策略代码组合而来。",
    "起投金额": "渠道披露的最低购买金额。",
    "投顾费率": "渠道披露的投顾服务费率文本。",
    "建议持有时长": "渠道披露的建议持有周期。",
    "业绩基准": "渠道披露的策略业绩比较基准。",
    "标签": "策略主表中的标签字段，已从 JSON 标签解析为中文标签列表。",
    "策略概念": "优先使用渠道披露标签归纳策略概念；标签缺失时使用策略描述摘要。",
    "业绩基准说明": "策略详情页展示的原始业绩比较基准文本，用于解释基准曲线的来源。",
    "业绩基准来源策略ID": "业绩基准在当前页面使用的可追溯策略ID；策略自身披露时为自身ID，已验证母子关系共享官方业绩时为母策略ID。",
    "业绩基准继承口径": "说明业绩基准来自策略自身披露，还是来自已验证母子关系下共享的母策略官方业绩域。",
    "基准公式解析": "系统从业绩基准文本中识别出的指数、现金组件和权重；无法映射的组件会在说明中列出。",
    "策略描述": "渠道披露的策略说明文本。",
    "官方单位净值": "渠道或 App 披露的最新策略单位净值。",
    "自建单位净值": "统一回放算法计算出的最新策略单位净值。",
    "费后单位净值": "扣除投顾费后的内部资产口径单位净值。",
    "费前单位净值": "不扣投顾费、更贴近 App 展示的单位净值口径。",
    "年化收益": "优先使用渠道官方披露的年化收益率；未披露时，使用自建回放净值区间折算的年化收益率。",
    "当前回撤": "优先基于清洗后的 App 或渠道披露单位净值计算，计算公式为最新单位净值相对历史高点的跌幅；无可用披露曲线时回退自建回放字段。",
    "波动率": "优先使用自建日收益率按年化交易日折算的波动率；没有可回放曲线时，回退渠道官方披露的波动率。",
    "夏普比率": "优先使用自建日收益率年化收益除以年化波动率计算的夏普比率，当前无风险收益率按 0 处理；没有可回放曲线时，回退渠道官方披露值。",
    "单次平均换手率": "按每次调仓明细的权重变化绝对值合计除以 2 估算单边换手率，再对可计算调仓事件取平均；缺少调前或权重变化时不计入。",
    "年化换手率": "将可计算调仓事件的单边换手率合计除以策略运作年数得到，运作年数按成立日至最新业绩日期折算。",
    "调仓频率": "调仓次数除以策略运作年数，单位为次/年。用于观察策略是否高频调仓。",
    "最近一年调仓次数": "最新业绩日期向前 365 天内的调仓事件数量。",
    "官方对比口径": "当前策略与官方业绩对比时采用的区间和费前/费后口径。",
    "可比记录数": "官方曲线与自建曲线可用于同区间比较的交易日记录数。",
    "持仓来源": "当前持仓来自 App 直接披露权重，或由最后一次调仓仓位结合基金净值、分红推算补齐。广发披露持仓日期滞后时，也会写入推算补齐表并作为页面当前仓位。",
    "资产类型": "基金在持仓中披露或推断的资产分类。优先使用基金标准分类字典中的投顾资产分类桶、标准资产大类和天天基金分类；缺失时再用平台资产类型、分组名称和基金名称关键词兜底。",
    "分组": "基金在页面内的展示细分组。优先取标准资产细类、天天基金细分类或二级分类；缺失时回退平台披露分组和资产大类。",
    "二级分类": "基金展示细分分类。优先取基金标准分类字典中的标准资产细类、天天基金细分类或天天基金二级分类；缺失时回退平台披露分组、基金类型或研报大类资产。该字段用于策略持仓、调仓明细、基金详情和数据洞察中的同类产品识别，不参与收益计算。",
    "调仓原因": "渠道或 App 对该次调仓披露的原因文本。缺失时页面显示未披露；AI 投研总结会基于调仓前后资产、行业和基金变化补充解释，但不会反向改写原始披露原因。",
    "历史盈利概率": "基于策略披露业绩曲线优先、模拟业绩曲线兜底，滚动计算从任意历史日期买入并持有 1 月、3 月、6 月、1 年后收益大于 0 的窗口占比。盈利概率=正收益窗口数/可计算窗口数。",
    "策略对比": "数据洞察页中面向最多 5 只策略的横向比较视图。核心指标、业绩曲线、资产配置、行业配置、权益主题、选基效果和历史盈利概率均只使用当前选中的策略集合计算。",
    "大类资产配置对比": "策略对比页按最新持仓快照中的研报大类资产分类聚合。每只基金使用基金经济暴露快照拆分进入多个大类；原始季报资产配置只在基金详情作为审计口径展示。",
    "行业配置对比": "策略对比页按最新持仓快照中的研报A股行业分类聚合。行业暴露来自基金经济暴露快照中的行业或主题穿透；黄金/商品、纯债、货币、海外债券不适用股票行业穿透。",
    "权益主题配置对比": "策略对比页按最新持仓快照中的权益行业主题分类聚合，用于观察权益方向的主题集中度；缺少主题拆分的数据不强行归类。",
    "选基效果": "策略对比页的选基效果由两部分组成：历史调仓胜率按已完成评价的调仓事件中跑赢或正超额事件占比计算；当前持仓选基质量按近1月、近3月、近6月、近1年同类收益排名前50%的基金仓位占比计算。",
    "历史调仓胜率": "按策略历史调仓事件的调仓评价或调仓超额收益判断胜负。跑赢、胜、正超额记为胜；跑输、负超额记为负；无收益窗口或评价不足的事件不纳入分母。",
    "前50%仓位占比": "对策略当前正权重持仓逐只基金计算。基金同类分组优先使用投顾资产分类桶、天天基金细分类/大类和标准资产分类；在同一分组内按近1月、近3月、近6月、近1年复权收益排名，排名位于前50%的基金权重合计除以当前持仓正权重合计。",
    "基金同类分组": "基金用于同类排名的分组。优先取基金标准分类字典中的投顾资产分类桶，其次取天天基金细分类、天天基金大类、标准资产细类或标准资产大类；仍缺失时归入未分类。",
    "基金分类来源": "基金分类和暴露字段的数据来源。主业务口径统一使用基金经济暴露快照；东财F10季报、基金标准分类、名称/指数规则和人工补充规则会合并成可审计的穿透方法与质量状态。",
    "基金穿透报告期": "当前基金经济暴露使用的报告期。历史持仓快照会优先选择报告期不晚于持仓日期的分类快照，避免使用未来报告期回填历史。",
    "基金穿透覆盖状态": "基金穿透覆盖状态。exact_quarterly_asset_and_stock 表示已有季报资产配置和股票持仓行业推导；exact_quarterly_asset_only 表示仅有季报资产配置；空值表示走规则估算兜底。",
    "近一月同类排名": "基金近31个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
    "近三月同类排名": "基金近92个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
    "近6月同类排名": "基金近183个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
    "近1年同类排名": "基金近365个自然日复权收益在同类分组内从高到低排序得到的名次，使用基金日度净值表中目标日前后可用净值点计算。",
    "基金代码": "基金产品代码。",
    "基金名称": "基金产品名称。",
    "基金权重": "该基金在当前组合中的资产占比，单位为百分比。",
    "基金净值": "持仓日期或最新可用日期对应的基金单位净值。",
    "净值日期": "基金净值对应日期。",
    "日涨幅": "基金最新日收益率或日涨跌幅，单位为百分比。",
    "调仓日期": "策略调仓事件发生或披露的日期。",
    "披露日期": "渠道披露该调仓信息的日期。",
    "调仓标题": "渠道披露的调仓标题或事件名称。",
    "调后权重和": "本次调仓后所有正基金权重的合计，用于检查是否接近 100%。",
    "调仓基金数": "本次调仓明细中涉及的基金数量。",
    "调前权重": "调仓前该基金在组合中的权重。",
    "调后权重": "调仓后该基金在组合中的目标权重。",
    "权重变化": "调后权重减调前权重，正数代表增配，负数代表减配。",
    "调仓动作": "根据调前、调后权重变化归并得到的买入、卖出、增配、减配或持有。",
    "区间": "收益区间，例如近 1 周、近 1 月、今年以来、成立以来。",
    "年度": "自然年度。年度业绩按上年末或年初前最近可用点到该年度最后可用点计算；成立当年从成立首个可用点起算。",
    "策略收益": "渠道披露的该区间策略收益率。",
    "基准收益": "渠道披露的该区间业绩基准收益率。",
    "披露业绩": "App 或渠道对客披露的策略业绩曲线；页面导出会用完整披露净值曲线，并用策略日度业绩 public quote 补齐更新日期更晚的点。",
    "模拟业绩": "本系统按统一标准算法、历史调仓基金仓位、基金净值和分红回放得到的费前策略业绩。",
    "基准业绩": "优先使用 App 或渠道披露的日度基准收益率；若缺失则解析业绩基准公式并用指数日度行情推算，可计算组件不足时再退回可明确映射的沪深300。",
    "沪深300业绩": "使用指数日度行情基础表中的沪深300收盘点位生成，并按策略曲线区间起点重新归零。",
    "曲线来源": "净值曲线和对照曲线的取数来源说明。披露业绩来自 App 或渠道披露，自建业绩来自统一算法回放，指数来自指数日度行情基础表。",
    "曲线数据提示": "导出时对净值曲线做机械质检：非正净值点直接剔除；若相邻收益 <= -60% 且净值 <= 0.30，或累计收益从正常区间跳到 -80% 以下，则判为疑似断点并隔离，直到净值恢复到上一个有效净值 0.50-1.80 倍区间才继续展示。",
    "洞察评价对象": "数据洞察页默认纳入两类策略：一是数据完整、可做正式同区间评价的完整策略；二是虽未完全达标、但已具备最新披露业绩且有最新持仓明细的扩展样本。扩展样本用于排名、仓位和调仓核验，不替代正式完整策略口径。",
    "全市场完整策略": "当前列表口径下数据完整、可做同区间评价的策略数量，作为竞争格局和机会评估分母。",
    "广发基金投顾完整策略": "广发基金内数据完整、可做同区间评价的策略数量。",
    "市场覆盖率": "广发基金完整策略数除以天天基金/投顾 + 广发基金分析样本完整策略数，用于观察当前样本中的产品覆盖份额。",
    "覆盖可比池数": "广发基金已有完整策略落入的主可比池数量。",
    "头部策略数": "广发基金策略在同一主可比池内按所选收益指标排名进入前25%的数量；收益越高越靠前。",
    "中位差": "广发基金在同一筛选口径下所选收益指标中位数减去分析样本中位数，单位为百分点。",
    "机会评分": "数据洞察页的排序指标，综合分析样本规模、广发覆盖缺口、头部能力差距、标杆差距和风险差异生成，分值越高越值得业务优先复盘。",
    "头部差距": "同一主可比池内分析样本前25%收益阈值减去广发基金最佳收益；广发无样本时展示为未披露。",
    "排名分位": "策略在同一主可比池内按所选收益指标计算的分位，100% 表示位于该池最高收益端。",
    "复盘建议": "数据洞察页按收益分位、回撤、换手、基准可用状态和产品覆盖缺口生成的业务动作建议。",
    "数据洞察来源": "数据洞察页基于当前导出的策略列表、分类字段、清洗后披露收益和基础数据状态汇总计算；未读取额外外部来源。",
    "覆盖策略类型数": "广发基金已有完整策略落入的互斥策略类型数量，分母为当前筛选下可见策略类型数量。",
    "筛选口径": "洞察页当前所选渠道、收益指标、策略类型、市场地域、主动/被动、换手率和波动率条件。",
    "分类口径": "策略类型为互斥主归属；市场地域、主动/被动、波动率分层、换手率分层为并列观察维度，不改变主归属。",
    "广发覆盖率": "广发基金样本数除以当前筛选下分析样本数；在分组表中为该分组内广发样本数除以分析样本数。",
    "中位波动": "对应分组内年化波动率字段的中位数；优先来自统一回放质量表。",
    "风险收益象限": "以当前筛选下全市场中位收益和中位波动率为阈值，将广发基金策略归为稳健领先、高收益高波动、防御低收益、待复盘或信息不足。",
    "高波动策略数": "年化波动率大于等于 15% 的广发基金策略数量。",
    "高换手策略数": "年化换手率大于等于 120% 的广发基金策略数量。",
    "广发头部产品数": "广发基金策略在同一策略类型内按所选收益指标排名进入前25%的数量；收益越高越靠前。",
    "广发基金投顾中位收益": "当前筛选下广发基金策略所选收益指标的中位数。",
    "广发基金投顾中位回撤": "当前筛选下广发基金策略最大回撤字段的中位数，数值越低表示同口径持有体验越稳。",
    "广发基金投顾中位波动率": "当前筛选下广发基金策略年化波动率字段的中位数。",
    "广发Top3平均收益": "同策略类型内，广发基金按所选收益指标排序前3只策略的平均收益；样本不足3只时按已有可计算样本平均。",
    "广发Top5平均收益": "同策略类型内，广发基金按所选收益指标排序前5只策略的平均收益；样本不足5只时按已有可计算样本平均。",
    "市场Top3平均收益": "同策略类型内，天天基金/投顾 + 广发基金分析样本按所选收益指标排序前3只策略的平均收益。",
    "市场Top5平均收益": "同一分组内，天天基金/投顾 + 广发基金分析样本按所选收益指标排序前5只策略的平均收益。多维结论优先用市场Top5而不是单一Top1做头部参照，降低个别极端产品对结论的影响。",
    "广发Top3差距": "广发Top3平均收益减去市场Top3平均收益，单位为百分点。",
    "广发Top3对Top5差距": "广发Top3平均收益减去市场Top5平均收益，单位为百分点。用于判断广发头部产品是否接近市场第一梯队。",
    "头部达标数": "广发基金在同一分组内进入分析样本前25%收益阈值的策略数量。",
    "广发Top3平均排名": "广发Top3产品在同一分组市场排序中的平均名次和平均前百分比；数值越靠前表示头部产品越接近第一梯队。",
    "广发Top3产品": "同策略类型内广发基金收益排名前3的策略，并展示其在分析样本内的收益排名和收益率。",
    "产品对比": "折叠明细行中展示广发Top3和市场Top3/标杆产品的收益、排名、回撤、波动率、换手率和机构，用于替代表格单元格中的拥挤文本。",
    "相对标杆": "广发基金最佳策略相对该策略类型市场标杆的收益、最大回撤、波动率和年化换手率差异。",
    "深层结论": "基于广发Top3、市场Top3、市场标杆、风险和换手差异生成的业务判断。",
    "业务风险": "该策略类型下广发基金可能面对的产品竞争、风险收益、长尾拖累或数据治理风险。",
    "业务动作": "业务人员可执行的动作建议，例如重点包装、渠道话术、产品补齐、策略复盘或风险沟通。",
    "标杆产品": "当前筛选或分组内按所选收益指标排序最高的分析样本策略，用于和广发最佳产品对标。",
    "标杆机构": "当前筛选或分组内调仓质量、收益表现或样本表现最靠前的投顾机构，用于观察广发对标对象。",
    "维度结论": "按策略类型、市场地域、主动/被动、波动率和换手率等维度，将全市场标杆与广发样本结合后生成的业务判断。",
    "机会风险": "该维度下广发相对市场标杆暴露出的经营机会、产品短板或风险点。",
    "建议": "面向业务人员的下一步动作，例如强化卖点、补齐产品、复盘策略或收敛推广范围。",
    "经营重点": "数据洞察页面向业务负责人的高价值入口，只保留能判断、能行动、能复盘的信息，不展示完整明细。",
    "经营判断": "按策略类型的头部能力、市场覆盖、风险边界和周度信号归并出的业务动作分类，例如重点经营、头部可包装、需要复盘、产品补齐或暂不主推。",
    "业务重点": "把收益、风险、覆盖、调仓或数据问题翻译成业务人员需要优先关注的一句话。",
    "重点经营": "广发在该策略类型已接近或超过市场头部，适合优先筛选产品、强化卖点和渠道表达。",
    "头部可包装": "广发已有可经营头部产品，但未形成明显全线优势，适合围绕少数优势产品做包装。",
    "需要复盘": "广发头部或中位表现明显落后市场，或收益表现伴随较高风险，需要先复盘再决定是否主推。",
    "产品补齐": "市场已有较多供给但广发覆盖不足或缺位，需要判断是否补齐产品或明确不参与竞争。",
    "暂不主推": "短期收益、长期相对表现、风险指标、调仓质量或数据完整性不支持作为当前主推对象。",
    "重点名单": "经营重点页按业务动作分组列出的产品或策略类型清单，包括可主推、需复盘、可补齐和暂不主推。",
    "近一周经营总览": "数据洞察页新增的周度经营视图，综合近一周收益、风险、产品类型、广发相对位置和主动调仓事件，按事实、观点、逻辑、动作输出。",
    "事实": "基于当前筛选样本直接计算得到的客观数据描述，例如近一周中位收益、上涨占比、样本数、事件数或广发相对差。",
    "观点": "在事实基础上生成的业务判断，只作为周度经营研判，不直接等同正式考核结论。",
    "逻辑": "说明观点成立所依赖的指标口径、阈值或对比方式，例如与市场中位数、市场Top5、前25%阈值、调仓胜率的比较。",
    "动作": "面向业务团队的下一步可执行动作，例如重点经营、复盘归因、补充话术、数据核验或风险沟通。",
    "近一周全市场中位收益": "当前筛选下天天基金/投顾 + 广发基金完整策略近一周收益率中位数。",
    "近一周广发中位收益": "当前筛选下广发基金完整策略近一周收益率中位数。",
    "近一周广发相对差": "近一周广发中位收益减去近一周全市场中位收益，单位为百分点。",
    "近一周上涨占比": "当前筛选下近一周收益率大于等于 0 的可计算策略数除以近一周收益可计算策略数。",
    "近一周广发Top5": "当前筛选下广发基金按近一周收益率排序前5只策略；用于周度经营观察，不作为长期产品考核依据。",
    "近一周市场Top5": "当前筛选下分析样本按近一周收益率排序前5只策略；用于观察市场热点和对标产品。",
    "广发周度位置": "按近一周广发中位收益、同类前25%达标数和广发最佳产品排名生成的短期相对位置判断。",
    "周度业务动作": "近一周经营总览根据短期收益、风险、调仓和产品覆盖生成的业务动作建议。",
    "平台对标参考": "数据洞察页用于说明借鉴天天基金/投顾、国内研究报告和海外 robo-advisor 平台的产品分析方法，不作为本地采集数据字段。",
    "公开资料": "用于说明分类或洞察框架借鉴来源的公开网页、监管提示、研究报告或平台说明。外部链接只作为方法参考，不代表本地数据已直接采集自该页面。",
    "近一周主动调仓": "调仓分析页中主动为主或主动被动混合策略在最新调仓日期向前 7 天内的调仓事件数。",
    "近一月主动调仓": "调仓分析页中主动为主或主动被动混合策略在最新调仓日期向前 30 天内的调仓事件数。",
    "近一月广发调仓": "广发基金主动为主或主动被动混合策略在最新调仓日期向前 30 天内的调仓事件数。",
    "可评价事件数": "调仓事件中胜负字段为胜、负或平的事件数量；不可评估事件不进入胜率分母。",
    "市场调仓胜率": "当前筛选下分析样本可评价调仓事件中，结果为胜的事件数除以可评价事件数。",
    "广发调仓胜率": "当前筛选下广发基金可评价调仓事件中，结果为胜的事件数除以可评价事件数。",
    "标杆机构胜率": "同一调仓口径下，样本满足最低可评价事件数要求的机构中调仓胜率最高机构的胜率。",
    "胜率差距": "广发调仓胜率减去市场、标杆机构或对应分组调仓胜率，单位为百分点。",
    "调仓质量结论": "综合广发调仓胜率、平均调仓超额、全市场水平和标杆机构表现生成的调仓质量判断。",
    "调仓质量风险": "调仓质量分析中识别出的风险点，例如胜率落后、样本不足、超额为负或交易逻辑不稳定。",
    "调仓质量建议": "调仓质量分析给出的业务动作，例如复盘低胜率逻辑、提炼高胜率逻辑、对标优秀机构或补充样本。",
    "近一月中位换手率": "近一月主动调仓事件的单次换手率中位数；单次换手率等于该次调仓权重变化绝对值合计除以 2。",
    "近一年平均调仓超额": "当前导出的最近约一年主动调仓事件在调仓质量事件分析表中的平均调仓超额。",
    "高频持仓基金门槛": "全市场高频持仓基金、广发基金高频持仓基金和广发基金机会表只把单策略期末持仓比例大于0.5%的基金计为有效持仓。低于等于0.5%的尾部仓位仍保留在策略详情和基金详情，但不进入高频统计，避免小仓位噪声放大持仓策略数。",
    "调仓逻辑": "按调仓标题、调仓原因和涉及资产关键词归因为产品替换/基金优选、固收久期/债券配置、风险控制/再平衡、海外/商品配置、权益结构/主题切换或组合再平衡/常规调整。",
    "权重": "当前选中仓位快照中的基金权重；当前仓位优先使用推算补齐权重，历史调仓使用调后权重。",
    "上次调仓后权重": "当前仓位场景下为最后一次调仓后的基金目标权重；历史调仓场景下为该次调仓前权重。",
    "调仓后收益率": "该基金从调仓后起算日至当前净值日期或下一调仓区间结束日的区间收益率。",
    "调仓后收益贡献": "调仓后权重乘以调仓后收益率得到的收益贡献，单位为百分点。",
    "调仓贡献曲线": "比较两次调仓之间调仓前仓位、调仓后仓位、基准和沪深300的区间收益表现。且慢策略仅在官方调前/调后基金权重完整、基金复权净值权重覆盖率达到98%时，按基金逐日净值回放；不满足条件时不画推测曲线。",
    "原始数据来源": "该展示字段来自策略主表、官方业绩、历史调仓、当前持仓、推算持仓或自建回放结果。",
}

FIELD_DICTIONARY.update(
    {
        "策略治理状态": "统一后处理生成的策略生命周期标签。测试组合剔除、信号类策略、已停止目标盈期次、已停止策略、当前基金权重未完整披露、正常运行会分开标注，避免混入同一个排名池。",
        "分析分组": "策略治理状态对应的分析分层。常规运行进入普通策略分析；信号服务按买入/卖出信号展示；目标盈期次按生命周期和到期收益单独复盘；测试/非正式组合不进入常规排名。",
        "是否测试组合": "策略名称命中“测试/test/内部测试/演示”等非正式组合关键词时置为1。该类组合保留原始记录追溯，但默认剔除常规排名和洞察样本。",
        "是否信号类组合": "策略被确认为按份数、100份、发车带投、智能发车/滚动带投或买卖/止盈信号管理、不是普通基金组合调仓时置为1。当前已确认砺远+、中欧/钱滚滚薪动月月投、超级定投家、指数100份及若干100份/智能发车带投策略；后续可在治理脚本配置中继续补充。",
        "是否目标盈期次": "名称、类型、标签或描述命中目标盈/小目标/小盈加/智盈等品牌，或同时具备明确目标收益/达标止盈机制与期次、到期、赎回等生命周期证据时置为1；普通止盈止损、预期兑现后止盈、目标日期到期时间不单独触发。",
        "是否已停止": "渠道状态或展示状态命中 stopped、终止、停止、下架、到期、清盘、结束等关键词时置为1。",
        "是否纳入常规排名": "常规策略榜单和数据洞察默认使用的样本开关。测试组合、信号类策略、已停止目标盈期次和已停止策略默认不纳入；当前权重缺披露但可推算的正常运行策略仍可纳入。",
        "仅列表展示": "渠道产品主档可查询但关键业绩、持仓或调仓证据未达到排名要求时置为1；该类策略保留在策略列表和详情页，不进入常规排名与数据洞察评价。",
        "是否单独分析": "需要独立口径展示的策略置为1，包括信号服务、目标盈期次、已停止策略和测试/非正式组合。",
        "业绩分析截止日期": "策略治理层为该策略确定的收益分析截止日。停止或期次型策略不继续滚动到全市场最新日，优先使用自身最后可用披露业绩日。",
        "持仓处理方式": "治理层给出的当前持仓使用规则。普通策略优先使用App披露持仓，缺基金级权重时用最后调仓后权重和基金复权收益滚动；信号类策略不把候选基金池等同真实组合权重。",
        "调仓展示方式": "治理层给出的调仓展示规则。普通策略按调仓事件和调仓后收益展示；信号类策略按买入/卖出信号时间线展示；目标盈期次按生命周期复盘。",
        "治理规则说明": "生成策略治理状态的具体命中规则，用于检查为什么某只策略被剔除常规排名或进入单独分析。",
        "信号事件数": "信号类策略从天天调仓/发车接口缓存抽取出的历史信号事件数量。每个事件代表一次局部调仓或发车指令，不等同于普通组合的完整调仓。",
        "最近信号日": "信号类策略最新一条发车/买卖信号的披露日期，来自信号策略事件表。",
        "信号指令数": "信号事件下基金级买入、卖出、加仓、减仓指令数量合计。",
        "信号胜率": "信号类基金指令按方向评价后的胜率。买入/加仓后基金上涨判为胜，卖出/减仓后基金下跌判为胜；平局和不可评价不计入胜率分母。",
        "信号加权方向收益": "按基金指令调整强度加权的方向收益。买入/加仓取基金区间收益，卖出/减仓取基金区间收益的相反数，用于评估信号方向是否有效。",
    }
)


STATUS_MAP = {
    None: "未披露",
    "": "未披露",
    "开放申购 开放赎回": "正常运作",
    "运作中": "正常运作",
    "public": "公开披露",
    "public_fee_list": "公开披露",
    "public_strategy_doc": "公开披露",
    "public_strategy_category": "公开披露",
    "public_selected_strategy": "公开披露",
    "public_disclosure_only": "公开披露",
    "on_sale_window": "开放窗口",
    "normal": "正常运作",
    "active_authenticated_ui": "正常运作",
    "delete": "已下架",
    "stopped": "已终止",
    "已止盈": "已止盈",
    "期满": "期满",
    "0": "原始状态0",
    "1": "原始状态1",
    "2": "原始状态2",
}


ACTION_MAP = {
    "buy": "买入",
    "sell": "卖出",
    "increase": "增配",
    "decrease": "减配",
    "keep": "持有",
}


INTERVAL_ORDER = {"1w": 1, "1m": 2, "3m": 3, "6m": 4, "1y": 5, "2y": 6, "3y": 7, "ytd": 8, "since_inception": 9, "std": 10}
DISPLAY_INTERVALS = [
    ("近一周", "days", 7),
    ("近一月", "months", 1),
    ("近三月", "months", 3),
    ("近6月", "months", 6),
    ("近1年", "months", 12),
    ("今年以来", "ytd", None),
    ("成立以来", "since", None),
]
OFFICIAL_INTERVAL_SUMMARY_FIELD_BY_CODE = {
    "1w": "近一周",
    "1m": "近一月",
    "3m": "近三月",
    "6m": "近6月",
    "1y": "近1年",
    "ytd": "今年以来",
    "since_inception": "累计收益率",
    "std": "累计收益率",
    "annualized": "年化收益",
}
OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE = {
    "1w": "近一周",
    "1m": "近一月",
    "3m": "近三月",
    "6m": "近6月",
    "1y": "近1年",
    "ytd": "今年以来",
    "since_inception": "成立以来",
    "std": "成立以来",
}
QIEMAN_CONTRIBUTION_EVENT_LIMIT = 12
CONTRIBUTION_MIN_NAV_WEIGHT_COVERAGE_PCT = 98.0
RECENT_POINTS_TO_KEEP = 180
# Strategy detail is the date-level audit surface.  Do not thin official or
# benchmark histories here; compact list/overview packs have their own limits.
DETAIL_CURVE_MAX_POINTS: int | None = None
CURVE_HARD_DROP_THRESHOLD = -60.0
CURVE_NEAR_ZERO_NAV_THRESHOLD = 0.30
CURVE_RESUME_MIN_RATIO = 0.50
CURVE_RESUME_MAX_RATIO = 1.80
CORE_GLOBAL_BENCHMARK_CODES = [
    "000300.SH",
    "000001.SH",
    "000015.SH",
    "000922.CSI",
    "000906.SH",
    "000905.SH",
    "000852.SH",
    "000510.SH",
    "000985.CSI",
    "930903.CSI",
    "000993.CSI",
    "399006.SZ",
    "H30318.CSI",
    "000698.SH",
    "000941.SH",
    "000827.SH",
    "000979.CSI",
    "H30009.CSI",
    "H11061.CSI",
    "HSI.HI",
    "990100.MI",
    "SPX.GI",
    "NDX.GI",
    "000012.SH",
    "H11006.CSI",
    "H11008.CSI",
    "H11001.CSI",
    "H11009.CSI",
    "H11015.CSI",
    "H11025.CSI",
    "H11023.CSI",
    "930950.CSI",
    "930609.CSI",
    "930610.CSI",
    "CBA00201.CS",
    "CBA00203.CS",
    "CBA00303.CS",
    "CBA00123.CS",
    "CBA00121.CS",
    "CBA00103.CS",
    "CBA00601.CS",
    "CBA00603.CS",
    "AU9999.SGE",
    "NHCI.NHF",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出基础数据展示页面：整体统计、策略列表、策略详情。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--algorithm-version", default=ALGORITHM_VERSION)
    parser.add_argument("--static-only", action="store_true", help="Update only HTML/CSS/JS shell files.")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def safe_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def load_strategy_relationship_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略关系"):
        return {}
    rows = fetch_all(
        conn,
        '''
        SELECT r.*, child."策略名称" AS "子策略名称", parent."策略名称" AS "母策略名称"
        FROM "策略关系" r
        JOIN "策略信息" child ON child."统一策略ID" = r."子策略ID"
        JOIN "策略信息" parent ON parent."统一策略ID" = r."母策略ID"
        WHERE r."关系状态"='active'
        ''',
    )
    return {str(row["子策略ID"]): row for row in rows}


def apply_relationship_aliases(
    values: dict[str, Any],
    relationships: dict[str, dict[str, Any]],
    source_field: str = "官方业绩策略ID",
) -> dict[str, Any]:
    """Copy proven source-domain values to children without changing source facts."""
    output = dict(values)
    for child_id, relationship in relationships.items():
        source_id = clean_text(relationship.get(source_field), "")
        if source_id and source_id in values:
            source_value = values[source_id]
            if isinstance(source_value, dict):
                output[child_id] = dict(source_value)
            elif isinstance(source_value, list):
                output[child_id] = list(source_value)
            else:
                output[child_id] = source_value
    return output


def fill_missing_relationship_aliases(
    values: dict[str, Any],
    relationships: dict[str, dict[str, Any]],
    source_field: str = "官方业绩策略ID",
) -> dict[str, Any]:
    """Fill an absent child disclosure from its proven source without replacing child facts."""
    output = dict(values)
    for child_id, relationship in relationships.items():
        if clean_text(output.get(child_id), ""):
            continue
        source_id = clean_text(relationship.get(source_field), "")
        if source_id and clean_text(values.get(source_id), ""):
            source_value = values[source_id]
            if isinstance(source_value, dict):
                output[child_id] = dict(source_value)
            elif isinstance(source_value, list):
                output[child_id] = list(source_value)
            else:
                output[child_id] = source_value
    return output


def fill_missing_relationship_records(
    values: dict[str, dict[str, Any]],
    relationships: dict[str, dict[str, Any]],
    required_field: str,
    missing_values: tuple[str, ...] = ("", "缺失", "未解析"),
    source_field: str = "官方业绩策略ID",
) -> dict[str, dict[str, Any]]:
    """Replace an empty placeholder record with the proven source-domain record."""
    output = {key: dict(value) for key, value in values.items()}
    for child_id, relationship in relationships.items():
        child_value = values.get(child_id) or {}
        child_marker = clean_text(child_value.get(required_field), "")
        if child_marker not in missing_values:
            continue
        source_id = clean_text(relationship.get(source_field), "")
        source_value = values.get(source_id) or {}
        source_marker = clean_text(source_value.get(required_field), "")
        if source_id and source_marker not in missing_values:
            output[child_id] = dict(source_value)
    return output


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def round_or_none(value: Any, digits: int = 4) -> float | None:
    number = as_float(value)
    if number is None:
        return None
    return round(number, digits)


def percent_ratio(part: int | float | None, total: int | float | None) -> float | None:
    if not total:
        return None
    return round(float(part or 0) / float(total) * 100, 2)


def clean_text(value: Any, fallback: str = "未披露") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def normalize_benchmark_text(value: Any) -> str:
    """Normalize presentation-only differences without weakening benchmark semantics."""
    text = unicodedata.normalize("NFKC", clean_text(value, "")).lower()
    text = text.replace("×", "*").replace("✕", "*").replace("·", "")
    return re.sub(r"[\s,，;；。]+", "", text)


def resolve_relationship_benchmark_domains(
    strategies: dict[str, dict[str, Any]],
    relationships: dict[str, dict[str, Any]],
    benchmark_statuses: dict[str, dict[str, Any]],
    benchmark_assets: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, str],
    dict[str, dict[str, str]],
]:
    """Resolve benchmark facts inside a proven shared official-performance domain.

    A child can inherit the source benchmark only when its own text is empty or
    semantically equivalent after conservative formatting normalization.  An
    explicit conflicting text is preserved and surfaced to the audit instead of
    being silently overwritten.
    """
    text_map = {
        strategy_id: clean_text(
            row.get("业绩基准") or (benchmark_statuses.get(strategy_id) or {}).get("业绩基准文本"),
            "",
        )
        for strategy_id, row in strategies.items()
    }
    status_map = {strategy_id: dict(value) for strategy_id, value in benchmark_statuses.items()}
    asset_map = {strategy_id: dict(value) for strategy_id, value in benchmark_assets.items()}
    inherited_sources: dict[str, str] = {}
    conflicts: dict[str, dict[str, str]] = {}

    for child_id, relationship in relationships.items():
        source_id = clean_text(relationship.get("官方业绩策略ID"), "")
        if not source_id or source_id == child_id:
            continue
        source_text = clean_text(text_map.get(source_id), "")
        child_text = clean_text(text_map.get(child_id), "")
        if not source_text:
            continue
        if child_text and normalize_benchmark_text(child_text) != normalize_benchmark_text(source_text):
            conflicts[child_id] = {
                "source_strategy_id": source_id,
                "child_text": child_text,
                "source_text": source_text,
            }
            continue

        effective_text = child_text or source_text
        text_map[child_id] = effective_text
        source_status = benchmark_statuses.get(source_id) or {}
        child_status = dict(benchmark_statuses.get(child_id) or {})
        for field in ("基准曲线状态", "基准可用状态", "最近更新时间"):
            if source_status.get(field) not in (None, ""):
                child_status[field] = source_status[field]
        child_status["业绩基准文本"] = effective_text
        child_status["基准文本状态"] = "已披露"

        has_curve = clean_text(child_status.get("基准可用状态"), "") in {"文本+曲线", "仅曲线"}
        has_fee = child_status.get("年化投顾费率_百分比") is not None
        child_status["基础数据等级"] = "A" if has_fee and has_curve else "B"
        actions: list[str] = []
        if not has_fee:
            actions.append("补投顾费率")
        if not has_curve:
            actions.append("补基准收益曲线或基准公式")
        child_status["建议补采动作"] = "无需优先补采" if not actions else "；".join(actions)
        status_map[child_id] = child_status

        if source_id in benchmark_assets:
            child_asset = dict(benchmark_assets[source_id])
            child_asset["统一策略ID"] = child_id
            child_asset["业绩基准文本"] = effective_text
            asset_map[child_id] = child_asset
        inherited_sources[child_id] = source_id

    return text_map, status_map, asset_map, inherited_sources, conflicts


def parse_tags(value: Any) -> list[str]:
    text = clean_text(value, "")
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()][:12]


def operation_status(value: Any) -> str:
    key = None if value is None else str(value).strip()
    return STATUS_MAP.get(key, clean_text(value))


def data_completeness(compare: dict[str, Any] | None, quality: dict[str, Any] | None) -> str:
    if compare and clean_text(compare.get("对比状态"), "") == "可对比":
        return "完整"
    if quality and int(quality.get("是否纳入模拟") or 0) == 1 and compare and int(compare.get("官方披露记录数") or 0) > 0:
        return "完整"
    return "不完整"


def binary_status(ok: bool) -> str:
    return "完整" if ok else "不完整"


def parse_ymd(value: Any):
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def calendar_months_ago(value: date, months: int) -> date:
    """Return the same calendar day N months earlier, clamped to month end."""
    month_index = value.year * 12 + value.month - 1 - int(months)
    target_year, target_month_index = divmod(month_index, 12)
    target_month = target_month_index + 1
    target_day = min(value.day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def rebalance_business_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        clean_text(row.get("统一策略ID"), ""),
        clean_text(row.get("调仓日期"), ""),
        clean_text(row.get("调仓标题"), ""),
        clean_text(row.get("调仓原因"), ""),
    )


def rebalance_row_score(row: dict[str, Any]) -> tuple[float, float, int, int, int, str]:
    fund_count = int(row.get("调仓基金数") or row.get("fund_count") or 0)
    after_sum = as_float(row.get("调后权重和") or row.get("after_weight_sum"))
    after_score = -abs((after_sum if after_sum is not None else 0.0) - 100.0)
    reason_len = len(clean_text(row.get("调仓原因"), ""))
    title_len = len(clean_text(row.get("调仓标题"), ""))
    sequence = int(as_float(row.get("事件序号")) or 999999)
    return (fund_count, after_score, reason_len, title_len, -sequence, clean_text(row.get("调仓事件ID") or row.get("事件ID"), ""))


def dedupe_rebalance_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_id: dict[str, dict[str, Any]] = {}
    no_id_rows: list[dict[str, Any]] = []
    for row in rows:
        event_id = clean_text(row.get("调仓事件ID") or row.get("事件ID"), "")
        if event_id:
            current = best_by_id.get(event_id)
            if current is None or rebalance_row_score(row) > rebalance_row_score(current):
                best_by_id[event_id] = row
            continue
        no_id_rows.append(row)
    best_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in no_id_rows:
        key = rebalance_business_key(row)
        if not key[0] or not key[1]:
            continue
        current = best_by_key.get(key)
        if current is None or rebalance_row_score(row) > rebalance_row_score(current):
            best_by_key[key] = row
    return list(best_by_id.values()) + list(best_by_key.values())


def rebalance_event_sort_key(row: dict[str, Any]) -> tuple[str, int, int, str]:
    date_value = parse_ymd(row.get("调仓日期"))
    sequence = int(as_float(row.get("事件序号")) or 0)
    return (
        clean_text(row.get("统一策略ID"), ""),
        -(date_value.toordinal() if date_value else 0),
        -sequence,
        clean_text(row.get("调仓事件ID") or row.get("事件ID"), ""),
    )


def fetch_rebalance_metric_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        WITH detail AS (
            SELECT "调仓事件ID",
                   COUNT(*) AS fund_count,
                   SUM(CASE WHEN COALESCE("调后权重_百分比", 0) > 0 THEN 1 ELSE 0 END) AS positive_after_count,
                   SUM(CASE WHEN COALESCE("调后权重_百分比", 0) > 0 THEN "调后权重_百分比" ELSE 0 END) AS after_weight_sum,
                   SUM(
                       ABS(
                           COALESCE(
                               "权重变化_百分比",
                               COALESCE("调后权重_百分比", 0) - COALESCE("调前权重_百分比", 0)
                           )
                       )
                   ) / 2.0 AS event_turnover
            FROM "策略调仓明细"
            GROUP BY "调仓事件ID"
        )
        SELECT e."统一策略ID", e."调仓事件ID", e."事件序号", e."调仓日期", e."调仓标题", e."调仓原因",
               COALESCE(d.fund_count, 0) AS fund_count,
               COALESCE(d.positive_after_count, 0) AS positive_after_count,
               d.after_weight_sum,
               d.event_turnover
        FROM "策略调仓事件" e
        LEFT JOIN detail d ON d."调仓事件ID" = e."调仓事件ID"
        WHERE e."调仓日期" IS NOT NULL
        ORDER BY e."统一策略ID", e."调仓日期", COALESCE(e."事件序号", 0), e."调仓事件ID"
        """,
    )
    return dedupe_rebalance_event_rows(rows)


def build_rebalance_stats_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, rows in grouped_rows(fetch_rebalance_metric_events(conn), "统一策略ID").items():
        dates = [clean_text(row.get("调仓日期"), "") for row in rows if clean_text(row.get("调仓日期"), "")]
        if not dates:
            continue
        result[strategy_id] = {
            "统一策略ID": strategy_id,
            "rebalance_count": len(rows),
            "latest_rebalance_date": max(dates),
            "first_rebalance_date": min(dates),
        }
    return result


def build_latest_rebalance_holding_stats_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, rows in grouped_rows(sorted(fetch_rebalance_metric_events(conn), key=rebalance_event_sort_key), "统一策略ID").items():
        for row in rows:
            after_sum = as_float(row.get("after_weight_sum"))
            fund_count = int(row.get("fund_count") or 0)
            positive_count = int(row.get("positive_after_count") or 0)
            if after_sum is None or fund_count <= 0:
                continue
            result[strategy_id] = {
                "latest_rebalance_holding_date": row.get("调仓日期"),
                "latest_rebalance_holding_fund_count": positive_count,
                "latest_rebalance_after_weight_sum": round_or_none(after_sum),
            }
            break
    return result


def calc_return(latest_nav: float | None, base_nav: float | None) -> float | None:
    if latest_nav is None or base_nav in (None, 0):
        return None
    return round((latest_nav / base_nav - 1.0) * 100.0, 4)


def build_official_interval_return_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "统计日期", "区间代码", "策略收益率_百分比", "基准收益率_百分比"
        FROM "策略区间业绩"
        WHERE "策略收益率_百分比" IS NOT NULL
          AND NOT (
              "渠道ID" = 'ttfund'
              AND COALESCE("原始快照ID", '') LIKE 'ttfund-quote_batch-%'
          )
        """,
    )
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped_rows(rows, "统一策略ID").items():
        by_code: dict[str, dict[str, Any]] = {}
        for row in strategy_rows:
            code = clean_text(row.get("区间代码"), "")
            stat_date = parse_ymd(row.get("统计日期"))
            if not code or stat_date is None:
                continue
            current = by_code.get(code)
            current_date = parse_ymd(current.get("统计日期")) if current else None
            if current is None or current_date is None or stat_date > current_date:
                by_code[code] = row
        if not by_code:
            continue
        latest_date = max(parse_ymd(row.get("统计日期")) for row in by_code.values() if parse_ymd(row.get("统计日期")))
        result[strategy_id] = {
            "latest_date": latest_date.isoformat(),
            "by_code": by_code,
        }
    return result


def official_interval_fields(
    official: dict[str, Any] | None,
    field_by_code: dict[str, str],
    curve_latest_date: date | None = None,
    max_stale_days: int = 7,
    value_field: str = "策略收益率_百分比",
) -> dict[str, float]:
    if not official:
        return {}
    official_latest = parse_ymd(official.get("latest_date"))
    if curve_latest_date and official_latest and (curve_latest_date - official_latest).days > max_stale_days:
        return {}
    values: dict[str, float] = {}
    for code, field in field_by_code.items():
        row = (official.get("by_code") or {}).get(code)
        if not row:
            continue
        value = round_or_none(row.get(value_field))
        if value is not None:
            values[field] = value
    return values


def sanitize_nav_rows(
    rows: list[dict[str, Any]],
    date_key: str,
    nav_key: str,
    cumulative_key: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    ordered: list[dict[str, Any]] = []
    invalid_dates: list[str] = []
    for row in sorted(rows, key=lambda item: str(item.get(date_key) or "")):
        date_text = clean_text(row.get(date_key), "")
        date_value = parse_ymd(date_text)
        nav = as_float(row.get(nav_key))
        if not date_text or date_value is None or nav is None:
            continue
        if nav <= 0:
            invalid_dates.append(date_text)
            continue
        ordered.append(row)
    warnings: list[str] = []
    if invalid_dates:
        warnings.append(
            f"检测到 {len(invalid_dates)} 个非正净值点，已从展示和指标计算中剔除；首个日期 {invalid_dates[0]}。"
        )
    if not ordered:
        return [], warnings

    sanitized: list[dict[str, Any]] = []
    last_good: dict[str, Any] | None = None
    quarantine: dict[str, Any] | None = None

    def nav_of(item: dict[str, Any] | None) -> float | None:
        return as_float(item.get(nav_key)) if item else None

    def cum_of(item: dict[str, Any] | None) -> float | None:
        return as_float(item.get(cumulative_key)) if item and cumulative_key else None

    def finish_quarantine(resume_date: str | None = None, resume_ratio: float | None = None) -> None:
        nonlocal quarantine
        if not quarantine:
            return
        resume_text = (
            f"；{resume_date} 恢复到上一个有效净值的 {resume_ratio:.2f} 倍，恢复展示。"
            if resume_date and resume_ratio is not None
            else f"；此后未恢复到上一个有效净值的 {CURVE_RESUME_MIN_RATIO:.2f}-{CURVE_RESUME_MAX_RATIO:.2f} 倍区间，后续异常段暂不展示。"
        )
        warnings.append(
            "检测到疑似曲线断点："
            f"{quarantine['prev_date']}->{quarantine['first_bad_date']}，"
            f"净值由 {quarantine['prev_nav']:.6g} 降至 {quarantine['first_bad_nav']:.6g}，"
            f"相邻收益 {quarantine['drop_pct']:.2f}%"
            f"{quarantine.get('cum_text', '')}；"
            f"当前阈值为相邻收益 <= {CURVE_HARD_DROP_THRESHOLD:.0f}% 且净值 <= {CURVE_NEAR_ZERO_NAV_THRESHOLD:.2f}。"
            f"已剔除 {quarantine['removed_count']} 个可疑点"
            f"（{quarantine['first_bad_date']} 至 {quarantine['last_bad_date']}）"
            f"{resume_text}"
        )
        quarantine = None

    for row in ordered:
        nav = nav_of(row)
        date_text = clean_text(row.get(date_key), "")
        if nav is None:
            continue
        if last_good is None:
            sanitized.append(row)
            last_good = row
            continue
        last_nav = nav_of(last_good)
        if last_nav is None or last_nav <= 0:
            sanitized.append(row)
            last_good = row
            continue
        if quarantine:
            ratio = nav / last_nav if last_nav else None
            if ratio is not None and CURVE_RESUME_MIN_RATIO <= ratio <= CURVE_RESUME_MAX_RATIO:
                finish_quarantine(date_text, ratio)
                sanitized.append(row)
                last_good = row
            else:
                quarantine["removed_count"] += 1
                quarantine["last_bad_date"] = date_text
            continue
        drop_pct = (nav / last_nav - 1.0) * 100.0
        cum = cum_of(row)
        prev_cum = cum_of(last_good)
        hard_drop = last_nav > 0.5 and drop_pct <= CURVE_HARD_DROP_THRESHOLD and nav <= CURVE_NEAR_ZERO_NAV_THRESHOLD
        cumulative_break = cum is not None and prev_cum is not None and cum <= -80 and prev_cum > -50
        if hard_drop or cumulative_break:
            cum_text = ""
            if cum is not None and prev_cum is not None:
                cum_text = f"，累计收益由 {prev_cum:.2f}% 变为 {cum:.2f}%"
            quarantine = {
                "prev_date": clean_text(last_good.get(date_key), ""),
                "first_bad_date": date_text,
                "last_bad_date": date_text,
                "prev_nav": last_nav,
                "first_bad_nav": nav,
                "drop_pct": drop_pct,
                "cum_text": cum_text,
                "removed_count": 1,
            }
            continue
        sanitized.append(row)
        last_good = row
    finish_quarantine()
    return sanitized, warnings


def sanitize_series_map(
    series_map: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    sanitized: dict[str, list[dict[str, Any]]] = {}
    warnings: dict[str, list[str]] = {}
    for strategy_id, rows in series_map.items():
        clean_rows, row_warnings = sanitize_nav_rows(rows, "日期", "数值")
        sanitized[strategy_id] = clean_rows
        if row_warnings:
            warnings[strategy_id] = row_warnings
    return sanitized, warnings


def status_detail_item(name: str, ok: bool, desc: str) -> dict[str, Any]:
    return {"项目": name, "结论": binary_status(ok), "说明": desc}


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", value)
    return safe[:160] or "strategy"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")


def template_text(relative_path: str, fallback: str | None = None) -> str:
    path = REPORT_TEMPLATE_DIR / relative_path
    if fallback is not None and relative_path.endswith(".html"):
        return fallback
    if path.exists() and path.is_file():
        text = path.read_text(encoding="utf-8-sig")
        return re.sub(r"\?v=[^\"'\s<>]+", f"?v={ASSET_VERSION}", text)
    if fallback is not None:
        return fallback
    raise FileNotFoundError(f"Missing report template file: {path}")


def write_js_assignment(path: Path, lhs: str, payload: Any) -> None:
    write_text(path, f"{lhs} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n")


INSIGHT_LAZY_FIELDS = {
    "策略资产变化明细",
    "调仓基金月度汇总",
    "当前持仓策略基金明细",
    "当前持仓基金风险明细",
    "调仓基金明细",
    "调仓方向汇总",
    "当前持仓基金公司风险明细",
    "持仓行业时间序列",
    "持仓时间序列",
    "当前持仓基金类型",
    "当前持仓基金",
    "当前持仓基金公司",
    "广发基金调仓机会",
}


def split_basic_summary_payload(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    insight = summary.get("insightData") or {}
    if not isinstance(insight, dict):
        insight = {}
    lazy_insight = {key: insight[key] for key in INSIGHT_LAZY_FIELDS if key in insight}
    core = {key: value for key, value in summary.items() if key != "insightData"}
    core_insight = {key: value for key, value in insight.items() if key not in INSIGHT_LAZY_FIELDS}
    core_insight["__lazyPack"] = {
        "externalScript": f"./data/insight_data_pack.js?v={ASSET_VERSION}",
        "fields": sorted(lazy_insight.keys()),
    }
    core["insightData"] = core_insight
    core["summaryPackMode"] = "core_lazy_v1"
    return core, {
        "version": 1,
        "generatedAt": format_beijing_minute(),
        "fields": sorted(lazy_insight.keys()),
        "insightData": lazy_insight,
    }


def load_js_assignment_payload(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
        _, sep, payload = text.partition("=")
        if not sep:
            return None
        payload = payload.strip()
        if payload.endswith(";"):
            payload = payload[:-1]
        return json.loads(payload)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def write_basic_summary_packs(site: Path, summary: dict[str, Any], write_full: bool = False) -> None:
    if isinstance(summary.get("overview"), dict):
        summary["overview"]["数据刷新时间"] = format_beijing_minute()
    if write_full:
        write_js_assignment(site / "data" / "basic_summary.js", "window.__BASIC_DATA__.summary", summary)
    core, lazy = split_basic_summary_payload(summary)
    write_js_assignment(site / "data" / "basic_summary_core.js", "window.__BASIC_DATA__.summary", core)
    write_js_assignment(site / "data" / "insight_data_pack.js", "window.__BASIC_INSIGHT_DATA_PACK__", lazy)


def write_basic_summary_packs_from_existing(site: Path) -> None:
    summary = load_js_assignment_payload(site / "data" / "basic_summary.js")
    if isinstance(summary, dict):
        write_basic_summary_packs(site, summary, write_full=False)


AI_SEMANTIC_INDEX_FIELDS = ["统一策略ID", "策略名称", "持仓日期", "基金代码", "基金名称", "基金公司", "资产类型", "二级分类", "分组", "基金同类分组", "权重"]


def latest_position_snapshot(detail: dict[str, Any]) -> dict[str, Any]:
    snapshots = detail.get("positionSnapshots") or []
    if not isinstance(snapshots, list):
        return {}
    for snapshot in snapshots:
        if snapshot.get("id") == "current" or snapshot.get("类型") == "当前仓位":
            return snapshot
    return snapshots[0] if snapshots else {}


def ai_semantic_rows_from_detail(detail: dict[str, Any]) -> list[list[Any]]:
    summary = detail.get("summary") or {}
    strategy_id = clean_text(summary.get("统一策略ID"), "")
    strategy_name = clean_text(summary.get("策略名称"), "")
    if not strategy_id:
        return []
    snapshot = latest_position_snapshot(detail)
    holdings = snapshot.get("holdings") or []
    if not isinstance(holdings, list):
        return []
    snapshot_date = clean_text(snapshot.get("日期") or (detail.get("holdingMeta") or {}).get("最新持仓日") or summary.get("最新持仓日"), "")
    rows: list[list[Any]] = []
    for holding in holdings:
        if not isinstance(holding, dict):
            continue
        fund_code = clean_text(holding.get("基金代码"), "")
        fund_name = clean_text(holding.get("基金名称"), "")
        weight = round_or_none(holding.get("权重"))
        if not fund_code and not fund_name:
            continue
        if weight is not None and weight <= 0:
            continue
        rows.append([
            strategy_id,
            strategy_name,
            clean_text(holding.get("持仓日期") or snapshot_date, ""),
            fund_code,
            fund_name,
            clean_text(holding.get("基金公司"), ""),
            clean_text(holding.get("资产类型"), ""),
            clean_text(holding.get("二级分类") or holding.get("分组"), ""),
            clean_text(holding.get("分组"), ""),
            clean_text(holding.get("基金同类分组"), ""),
            weight,
        ])
    return rows


def ai_semantic_index_payload(details: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    strategy_ids: set[str] = set()
    for detail in details:
        detail_rows = ai_semantic_rows_from_detail(detail)
        rows.extend(detail_rows)
        if detail_rows:
            strategy_ids.add(str(detail_rows[0][0]))
    return {
        "version": 2,
        "source": "strategy_detail_current_holdings",
        "fields": AI_SEMANTIC_INDEX_FIELDS,
        "strategyCount": len(strategy_ids),
        "holdingCount": len(rows),
        "rows": rows,
    }


def load_detail_js_payload(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
        _, sep, payload = text.partition("=")
        if not sep:
            return None
        payload = payload.strip()
        if payload.endswith(";"):
            payload = payload[:-1]
        return json.loads(payload)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


SEMANTIC_ENTITY_SECTION_KEYS = [
    "generatedAt",
    "entityCount",
    "fundEntityCount",
    "strategyEntityCount",
    "dataQuality",
    "entityGraph",
    "entityCatalog",
    "queryAliasIndex",
    "fundEntities",
    "strategyEntities",
]


def semantic_index_has_fund_entities(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    fund_entities = payload.get("fundEntities")
    return isinstance(fund_entities, dict) and bool(fund_entities.get("rows"))


def write_ai_semantic_index(site: Path, details: list[dict[str, Any]]) -> int:
    payload = ai_semantic_index_payload(details)
    target = site / "data" / "ai_semantic_index.js"
    existing = load_detail_js_payload(target)
    if semantic_index_has_fund_entities(existing):
        for key in SEMANTIC_ENTITY_SECTION_KEYS:
            if key in existing:
                payload[key] = existing[key]
        payload["version"] = max(int(payload.get("version") or 0), int(existing.get("version") or 0))
        payload["source"] = existing.get("source") or payload.get("source")
    write_js_assignment(target, "window.__AI_STRATEGY_SEMANTIC_INDEX__", payload)
    return int(payload["holdingCount"])


def write_ai_semantic_index_from_detail_files(site: Path) -> int:
    target = site / "data" / "ai_semantic_index.js"
    existing = load_detail_js_payload(target)
    if semantic_index_has_fund_entities(existing):
        return int(existing.get("holdingCount") or 0)
    details_dir = site / "data" / "details"
    if not details_dir.exists():
        return 0
    details = [payload for payload in (load_detail_js_payload(path) for path in sorted(details_dir.glob("*.js"))) if payload]
    return write_ai_semantic_index(site, details) if details else 0


def log_progress(message: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}", flush=True)


def format_beijing_minute(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日%H:%M")


def table_counts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = [
        "策略信息",
        "策略关系",
        "策略日度业绩",
        "策略区间业绩",
        "策略当前持仓",
        "策略当前持仓推算补齐",
        "策略调仓事件",
        "策略调仓明细",
        "策略模拟净值",
        "策略模拟净值质量",
        "策略官方偏差分析",
        "策略产品披露净值",
        "策略标准业绩净值",
        "策略业绩口径对比",
        "最新持仓推算稽核策略汇总",
        "策略基准费率状态",
        "基金信息",
        "基金标准分类字典",
        "基金日度净值",
        "基金分红送配",
        "指数日度行情",
        "数据来源清单",
    ]
    return [{"表名": table, "记录数": safe_count(conn, table)} for table in tables if table_exists(conn, table)]


def missing_nav_fund_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        WITH used AS (
            SELECT DISTINCT "基金代码" AS code FROM "策略调仓明细"
            WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
            UNION
            SELECT DISTINCT "基金代码" AS code FROM "策略当前持仓"
            WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        ),
        missing AS (
            SELECT u.code
            FROM used u
            LEFT JOIN "基金日度净值" n ON n."基金代码" = u.code
            GROUP BY u.code
            HAVING COUNT(n."基金代码") = 0
        )
        SELECT COUNT(*) FROM missing
        """
    ).fetchone()
    return int(row[0] or 0)


def latest_business_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(value) FROM (
            SELECT MAX("交易日期") AS value FROM "策略日度业绩"
            WHERE NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
            UNION ALL SELECT MAX("交易日期") FROM "基金日度净值"
            UNION ALL SELECT MAX("调仓日期") FROM "策略调仓事件"
        )
        """
    ).fetchone()
    return row[0]


def build_channel_stats(conn: sqlite3.Connection, algorithm_version: str) -> list[dict[str, Any]]:
    display_channels = tuple(sorted(DISPLAY_STRATEGY_CHANNEL_IDS))
    placeholders = ",".join("?" for _ in display_channels)
    channels = fetch_all(
        conn,
        f"""
        SELECT c."渠道ID", c."渠道名称", c."渠道类型", c."登录要求",
               COUNT(s."统一策略ID") AS "策略数"
        FROM "渠道信息" c
        LEFT JOIN "策略信息" s ON s."渠道ID" = c."渠道ID"
        WHERE c."渠道ID" IN ({placeholders})
        GROUP BY c."渠道ID", c."渠道名称", c."渠道类型", c."登录要求"
        ORDER BY "策略数" DESC, c."渠道名称"
        """,
        display_channels,
    )
    metrics = {
        row["渠道ID"]: row
        for row in fetch_all(
            conn,
            """
            WITH perf AS (
                SELECT "统一策略ID", MAX("交易日期") AS latest_perf_date
                FROM "策略日度业绩"
                WHERE NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
                GROUP BY "统一策略ID"
            ),
            rebalance AS (
                SELECT "统一策略ID", MAX("调仓日期") AS latest_rebalance_date
                FROM "策略调仓事件"
                GROUP BY "统一策略ID"
            ),
            holding AS (
                SELECT DISTINCT "统一策略ID"
                FROM "策略当前持仓"
            ),
            simulated AS (
                SELECT DISTINCT "统一策略ID"
                FROM "策略模拟净值质量"
                WHERE "算法版本" = ? AND "是否纳入模拟" = 1
            ),
            comparable AS (
                SELECT DISTINCT "统一策略ID"
                FROM "策略业绩口径对比"
                WHERE "算法版本" = ? AND "对比状态" = '可对比'
            )
            SELECT s."渠道ID",
                   SUM(CASE WHEN perf."统一策略ID" IS NOT NULL THEN 1 ELSE 0 END) AS official_count,
                   SUM(CASE WHEN rebalance."统一策略ID" IS NOT NULL THEN 1 ELSE 0 END) AS rebalance_count,
                   SUM(CASE WHEN holding."统一策略ID" IS NOT NULL THEN 1 ELSE 0 END) AS holding_count,
                   SUM(CASE WHEN simulated."统一策略ID" IS NOT NULL THEN 1 ELSE 0 END) AS simulated_count,
                   SUM(CASE WHEN comparable."统一策略ID" IS NOT NULL THEN 1 ELSE 0 END) AS complete_count,
                   MAX(perf.latest_perf_date) AS latest_perf_date,
                   MAX(rebalance.latest_rebalance_date) AS latest_rebalance_date
            FROM "策略信息" s
            LEFT JOIN perf ON perf."统一策略ID" = s."统一策略ID"
            LEFT JOIN rebalance ON rebalance."统一策略ID" = s."统一策略ID"
            LEFT JOIN holding ON holding."统一策略ID" = s."统一策略ID"
            LEFT JOIN simulated ON simulated."统一策略ID" = s."统一策略ID"
            LEFT JOIN comparable ON comparable."统一策略ID" = s."统一策略ID"
            GROUP BY s."渠道ID"
            """,
            (algorithm_version, algorithm_version),
        )
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in channels:
        channel_id = row["渠道ID"]
        stat = metrics.get(channel_id, {})
        total = int(row["策略数"] or 0)
        business_channel = canonical_business_channel(channel_id, row["渠道名称"])
        current = grouped.setdefault(
            business_channel,
            {
                "渠道": business_channel,
                "渠道类型": clean_text(row["渠道类型"]),
                "策略数": 0,
                "official_count": 0,
                "rebalance_count": 0,
                "holding_count": 0,
                "simulated_count": 0,
                "完整策略数": 0,
                "最新业绩日": None,
                "最新调仓日": None,
            },
        )
        current["策略数"] += total
        for field in ("official_count", "rebalance_count", "holding_count", "simulated_count"):
            current[field] += int(stat.get(field) or 0)
        current["完整策略数"] += int(stat.get("complete_count") or 0)
        for source_field, target_field in (
            ("latest_perf_date", "最新业绩日"),
            ("latest_rebalance_date", "最新调仓日"),
        ):
            value = clean_text(stat.get(source_field), "")
            if value and (not current[target_field] or value > current[target_field]):
                current[target_field] = value

    result: list[dict[str, Any]] = []
    for current in grouped.values():
        total = int(current["策略数"] or 0)
        result.append(
            {
                "渠道": current["渠道"],
                "渠道类型": current["渠道类型"],
                "策略数": total,
                "官方业绩覆盖": percent_ratio(current["official_count"], total),
                "历史调仓覆盖": percent_ratio(current["rebalance_count"], total),
                "当前持仓覆盖": percent_ratio(current["holding_count"], total),
                "回放覆盖": percent_ratio(current["simulated_count"], total),
                "完整策略数": current["完整策略数"],
                "最新业绩日": current["最新业绩日"],
                "最新调仓日": current["最新调仓日"],
            }
        )
    return sorted(result, key=lambda item: (-int(item["策略数"] or 0), str(item["渠道"])))


def latest_map(conn: sqlite3.Connection, table: str, date_col: str, value_cols: list[str], where: str = "", params: tuple[Any, ...] = ()) -> dict[str, dict[str, Any]]:
    sql = f"""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY "统一策略ID" ORDER BY "{date_col}" DESC) AS rn
            FROM "{table}"
            {where}
        )
        SELECT {", ".join(f'"{col}"' for col in ["统一策略ID", *value_cols])}
        FROM ranked
        WHERE rn = 1
    """
    return {row["统一策略ID"]: row for row in fetch_all(conn, sql, params)}


def build_disclosed_return_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    official_interval_returns = build_official_interval_return_map(conn)
    rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "披露单位净值" AS nav,
               "披露累计收益率_百分比" AS cumulative_return,
               1 AS source_priority
        FROM "策略产品披露净值"
        WHERE "是否可画曲线" = 1 AND "披露单位净值" IS NOT NULL AND "披露单位净值" > 0
        UNION ALL
        SELECT "统一策略ID", "交易日期", "单位净值" AS nav,
               "累计收益率_百分比" AS cumulative_return,
               2 AS source_priority
        FROM "策略日度业绩"
        WHERE "单位净值" IS NOT NULL AND "单位净值" > 0
          AND NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
        ORDER BY "统一策略ID", "交易日期", source_priority
        """,
    )
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped_rows(rows, "统一策略ID").items():
        rows_by_date: dict[str, dict[str, Any]] = {}
        for row in strategy_rows:
            trade_date = parse_ymd(row.get("交易日期"))
            nav = as_float(row.get("nav"))
            if trade_date and nav and nav > 0:
                date_text = row["交易日期"]
                current = rows_by_date.get(date_text)
                if current is None or int(row.get("source_priority") or 99) < int(current.get("source_priority") or 99):
                    rows_by_date[date_text] = {
                        "date": trade_date,
                        "date_text": date_text,
                        "nav": nav,
                        "cum": as_float(row.get("cumulative_return")),
                        "source_priority": int(row.get("source_priority") or 99),
                    }
        clean_rows = sorted(rows_by_date.values(), key=lambda item: item["date"])
        clean_rows, _warnings = sanitize_nav_rows(clean_rows, "date_text", "nav", "cum")
        if not clean_rows:
            continue
        latest = clean_rows[-1]

        def baseline_by_days(days: int) -> float | None:
            target = latest["date"] - timedelta(days=days)
            base = None
            for item in clean_rows:
                if item["date"] <= target:
                    base = item
                else:
                    break
            return base["nav"] if base else None

        def baseline_by_months(months: int) -> float | None:
            target = calendar_months_ago(latest["date"], months)
            base = None
            for item in clean_rows:
                if item["date"] <= target:
                    base = item
                else:
                    break
            return base["nav"] if base else None

        ytd_start = latest["date"].replace(month=1, day=1)
        ytd_base = None
        for item in clean_rows:
            if item["date"] <= ytd_start:
                ytd_base = item
            else:
                break
        if ytd_base is None:
            ytd_base = next((item for item in clean_rows if item["date"] >= ytd_start), None)
        first = clean_rows[0]
        max_gap_days = max(
            (
                (current["date"] - previous["date"]).days
                for previous, current in zip(clean_rows, clean_rows[1:])
            ),
            default=None,
        )
        cumulative = latest["cum"]
        if cumulative is None:
            cumulative = calc_return(latest["nav"], first["nav"])
        computed = {
            "近一周": calc_return(latest["nav"], baseline_by_days(7)),
            "近一月": calc_return(latest["nav"], baseline_by_months(1)),
            "近三月": calc_return(latest["nav"], baseline_by_months(3)),
            "近6月": calc_return(latest["nav"], baseline_by_months(6)),
            "近1年": calc_return(latest["nav"], baseline_by_months(12)),
            "今年以来": (
                calc_return(latest["nav"], ytd_base["nav"])
                if ytd_base and ytd_base["date"] < latest["date"]
                else None
            ),
            "累计收益率": round_or_none(cumulative),
            "收益数据截至": latest["date_text"],
            "业绩点数": len(clean_rows),
            "业绩最大间隔天数": max_gap_days,
        }
        official = official_interval_returns.get(strategy_id)
        official_fields = official_interval_fields(
            official,
            OFFICIAL_INTERVAL_SUMMARY_FIELD_BY_CODE,
            latest["date"],
        )
        computed.update(official_fields)
        official_latest = parse_ymd((official or {}).get("latest_date"))
        if official_fields and official_latest and official_latest >= latest["date"]:
            computed["收益数据截至"] = official_latest.isoformat()
        result[strategy_id] = computed
    # Some channels disclose interval returns but no daily NAV curve. Keep the
    # official observations visible instead of dropping the whole strategy from
    # the page. A zero point count explicitly prevents these observations from
    # being mistaken for a drawable or internally reconstructed NAV series.
    for strategy_id, official in official_interval_returns.items():
        if strategy_id in result:
            continue
        official_fields = official_interval_fields(
            official,
            OFFICIAL_INTERVAL_SUMMARY_FIELD_BY_CODE,
        )
        if not official_fields:
            continue
        interval_only = {
            "近一周": None,
            "近一月": None,
            "近三月": None,
            "近6月": None,
            "近1年": None,
            "今年以来": None,
            "累计收益率": None,
            "收益数据截至": official.get("latest_date"),
            "业绩点数": 0,
            "业绩最大间隔天数": None,
        }
        interval_only.update(official_fields)
        result[strategy_id] = interval_only
    return result


def build_disclosed_latest_value_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    official_interval_returns = build_official_interval_return_map(conn)
    rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "披露单位净值" AS nav,
               "披露累计收益率_百分比" AS cumulative_return,
               1 AS source_priority
        FROM "策略产品披露净值"
        WHERE "是否可画曲线" = 1 AND "披露单位净值" IS NOT NULL AND "披露单位净值" > 0
        UNION ALL
        SELECT "统一策略ID", "交易日期", "单位净值" AS nav,
               "累计收益率_百分比" AS cumulative_return,
               2 AS source_priority
        FROM "策略日度业绩"
        WHERE "单位净值" IS NOT NULL AND "单位净值" > 0
          AND NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
        ORDER BY "统一策略ID", "交易日期", source_priority
        """,
    )
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped_rows(rows, "统一策略ID").items():
        rows_by_date: dict[str, dict[str, Any]] = {}
        for row in strategy_rows:
            date_text = clean_text(row.get("交易日期"), "")
            nav = as_float(row.get("nav"))
            if not date_text or nav is None or nav <= 0:
                continue
            current = rows_by_date.get(date_text)
            if current is None or int(row.get("source_priority") or 99) < int(current.get("source_priority") or 99):
                rows_by_date[date_text] = row
        if not rows_by_date:
            continue
        clean_rows, _warnings = sanitize_nav_rows(list(rows_by_date.values()), "交易日期", "nav", "cumulative_return")
        if not clean_rows:
            continue
        latest = clean_rows[-1]
        latest_date = latest["交易日期"]
        latest_value = {
            "最新业绩日期": latest_date,
            "官方单位净值": round_or_none(latest.get("nav"), 6),
            "官方累计收益": round_or_none(latest.get("cumulative_return")),
        }
        official = official_interval_returns.get(strategy_id)
        official_latest = parse_ymd((official or {}).get("latest_date"))
        curve_latest = parse_ymd(latest_date)
        official_since = official_interval_fields(
            official,
            {
                "since_inception": "官方累计收益",
                "std": "官方累计收益",
            },
            curve_latest,
        ).get("官方累计收益")
        if official_latest and curve_latest and official_latest > curve_latest and official_since is not None:
            latest_value["最新业绩日期"] = official_latest.isoformat()
            latest_value["官方累计收益"] = official_since
            latest_value["官方单位净值"] = round_or_none(1.0 + official_since / 100.0, 6)
        result[strategy_id] = latest_value
    for strategy_id, official in official_interval_returns.items():
        if strategy_id in result:
            continue
        official_since = official_interval_fields(
            official,
            {
                "since_inception": "官方累计收益",
                "std": "官方累计收益",
            },
        ).get("官方累计收益")
        if official_since is None:
            continue
        result[strategy_id] = {
            "最新业绩日期": official.get("latest_date"),
            "官方单位净值": None,
            "官方累计收益": official_since,
        }
    return result


def build_disclosed_risk_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "披露单位净值" AS nav,
               "披露累计收益率_百分比" AS cumulative_return,
               1 AS source_priority
        FROM "策略产品披露净值"
        WHERE "是否可画曲线" = 1 AND "披露单位净值" IS NOT NULL AND "披露单位净值" > 0
        UNION ALL
        SELECT "统一策略ID", "交易日期", "单位净值" AS nav,
               "累计收益率_百分比" AS cumulative_return,
               2 AS source_priority
        FROM "策略日度业绩"
        WHERE "单位净值" IS NOT NULL AND "单位净值" > 0
          AND NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
        ORDER BY "统一策略ID", "交易日期", source_priority
        """,
    )
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped_rows(rows, "统一策略ID").items():
        rows_by_date: dict[str, dict[str, Any]] = {}
        for row in strategy_rows:
            date_text = clean_text(row.get("交易日期"), "")
            nav = as_float(row.get("nav"))
            if not date_text or nav is None or nav <= 0:
                continue
            current = rows_by_date.get(date_text)
            if current is None or int(row.get("source_priority") or 99) < int(current.get("source_priority") or 99):
                rows_by_date[date_text] = row
        clean_rows, _warnings = sanitize_nav_rows(list(rows_by_date.values()), "交易日期", "nav", "cumulative_return")
        if len(clean_rows) < 2:
            continue
        peak = as_float(clean_rows[0].get("nav"))
        max_drawdown = 0.0
        current_drawdown = 0.0
        for row in clean_rows:
            nav = as_float(row.get("nav"))
            if nav is None or nav <= 0:
                continue
            if peak is None or nav > peak:
                peak = nav
            if peak:
                current_drawdown = max(0.0, (peak - nav) / peak * 100.0)
                max_drawdown = max(max_drawdown, current_drawdown)
        result[strategy_id] = {
            "最大回撤": round_or_none(max_drawdown),
            "当前回撤": round_or_none(current_drawdown),
            "风险来源": "官方披露净值曲线计算",
        }
    official_rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "统计日期", "区间代码",
               "官方最大回撤_百分比", "官方波动率_百分比", "官方夏普",
               "数据来源字段"
        FROM "策略披露风险指标"
        WHERE "官方最大回撤_百分比" IS NOT NULL
           OR "官方波动率_百分比" IS NOT NULL
           OR "官方夏普" IS NOT NULL
        """,
    )
    for strategy_id, strategy_rows in grouped_rows(official_rows, "统一策略ID").items():
        ordered = sorted(
            strategy_rows,
            key=lambda row: (
                2 if clean_text(row.get("区间代码"), "") == "std" else
                1 if clean_text(row.get("区间代码"), "") == "official_card" else 0,
                parse_ymd(row.get("统计日期")) or date.min,
            ),
            reverse=True,
        )
        disclosed = result.setdefault(strategy_id, {})
        field_map = {
            "最大回撤": "官方最大回撤_百分比",
            "波动率": "官方波动率_百分比",
            "夏普比率": "官方夏普",
        }
        used_rows: list[dict[str, Any]] = []
        for target_field, source_field in field_map.items():
            if disclosed.get(target_field) is not None:
                continue
            source_row = next((row for row in ordered if as_float(row.get(source_field)) is not None), None)
            if source_row is None:
                continue
            disclosed[target_field] = round_or_none(source_row.get(source_field))
            used_rows.append(source_row)
        if used_rows:
            source_dates = [parse_ymd(row.get("统计日期")) for row in used_rows]
            disclosed["风险数据截至"] = max(value for value in source_dates if value).isoformat() if any(source_dates) else None
            disclosed.setdefault("风险来源", "渠道官方披露风险指标")
    return result


def build_strategy_count_map(conn: sqlite3.Connection, table: str, date_col: str, where: str = "", params: tuple[Any, ...] = ()) -> dict[str, dict[str, Any]]:
    sql = f"""
        SELECT "统一策略ID", COUNT(*) AS record_count, MIN("{date_col}") AS min_date, MAX("{date_col}") AS max_date
        FROM "{table}"
        {where}
        GROUP BY "统一策略ID"
    """
    return {row["统一策略ID"]: row for row in fetch_all(conn, sql, params)}


def build_quality_checks(
    quality: dict[str, Any] | None,
    compare: dict[str, Any] | None,
    rebalance: dict[str, Any],
    disclosure_stats: dict[str, Any],
    standard_stats: dict[str, Any],
) -> list[dict[str, Any]]:
    included = bool(quality and int(quality.get("是否纳入模拟") or 0) == 1)
    rebalance_count = int(rebalance.get("rebalance_count") or 0)
    valid_intervals = int((quality or {}).get("有效区间数") or 0)
    disclosure_count = int(disclosure_stats.get("record_count") or compare.get("官方披露记录数") or 0) if compare else int(disclosure_stats.get("record_count") or 0)
    standard_count = int(standard_stats.get("record_count") or compare.get("标准回放记录数") or 0) if compare else int(standard_stats.get("record_count") or 0)
    compare_status = clean_text((compare or {}).get("对比状态"), "不完整")
    issue = clean_text((quality or {}).get("问题说明") or (compare or {}).get("问题说明"), "")
    fix = clean_text((quality or {}).get("修复说明"), "")
    nav_desc_extra = f"修复说明：{fix}" if fix else (f"问题说明：{issue}" if issue else "基金净值依赖满足当前回放口径。")
    return [
        status_detail_item(
            "策略历史调仓数据",
            rebalance_count > 0 and valid_intervals > 0 and included,
            f"调仓事件 {rebalance_count} 次，有效回放区间 {valid_intervals} 个，最近调仓日 {rebalance.get('latest_rebalance_date') or '未披露'}。",
        ),
        status_detail_item(
            "基金净值数据",
            included,
            nav_desc_extra,
        ),
        status_detail_item(
            "策略净值数据",
            standard_count > 0,
            f"标准回放净值 {standard_count} 个交易日，区间 {standard_stats.get('min_date') or '未生成'} 至 {standard_stats.get('max_date') or '未生成'}。",
        ),
        status_detail_item(
            "官方披露业绩",
            disclosure_count > 0,
            f"官方披露净值 {disclosure_count} 个交易日，区间 {disclosure_stats.get('min_date') or '未披露'} 至 {disclosure_stats.get('max_date') or '未披露'}。",
        ),
        status_detail_item(
            "模拟业绩",
            compare_status == "可对比" and standard_count > 0,
            f"对比状态：{compare_status}；共同交易日 {int((compare or {}).get('共同交易日数') or 0)}。",
        ),
    ]


def truthy_flag(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "是", "是的"}


def has_any_keyword(text: str, keywords: list[str]) -> bool:
    return any(word and word in text for word in keywords)


OVERSEAS_BENCHMARK_CODES = {"HSI.HI", "990100.MI", "SPX.GI", "NDX.GI"}
DOMESTIC_MSCI_RE = re.compile(r"MSCI\s*(沪深\s*300|中国A股|中国)", re.I)
STRONG_OVERSEAS_TEXT_RE = re.compile(
    r"QDII|海外|港股|美股|恒生|纳斯达克|纳指|标普|S&P|美国|印度|越南|日经|日本|德国|DAX|"
    r"全球(?!版)(?:资产|配置|精选|优选|权益|股票|债券|多元|组合|市场)?",
    re.I,
)


def overseas_text_scope(*values: Any) -> str:
    text = " ".join(clean_text(value, "") for value in values if clean_text(value, ""))
    # “兴证全球”是机构品牌，不代表组合配置海外资产。
    text = text.replace("兴证全球", "兴证")
    text = DOMESTIC_MSCI_RE.sub("", text)
    return text


def has_strong_overseas_text(*values: Any) -> bool:
    return bool(STRONG_OVERSEAS_TEXT_RE.search(overseas_text_scope(*values)))


def normalize_holding_weight_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weighted = []
    total = 0.0
    for row in rows:
        weight = as_float(row.get("weight"))
        if weight is None or weight <= 0:
            continue
        total += weight
        current = dict(row)
        current["raw_weight"] = weight
        weighted.append(current)
    if not weighted or total <= 0:
        return []
    if total <= 1.5:
        scale = 100.0
    elif total < 80.0 or total > 105.0:
        scale = 100.0 / total
    else:
        scale = 1.0
    for row in weighted:
        row["weight"] = row["raw_weight"] * scale
    return weighted


def fund_asset_bucket(row: dict[str, Any]) -> str:
    text = "".join(
        clean_text(row.get(key), "")
        for key in ["基金名称", "基金简称", "天天基金细分类", "天天基金大类", "天天基金二级分类", "标准资产大类", "标准资产细类", "投顾资产分类桶", "资产类型", "分组名称"]
    )
    if truthy_flag(row.get("是否货币基金")) or "货币" in text or "现金" in text or "保证金" in text:
        return "cash"
    if truthy_flag(row.get("是否商品黄金")) or "黄金" in text or "商品" in text:
        return "commodity"
    if truthy_flag(row.get("是否债券基金")) or has_any_keyword(text, ["债券", "短债", "纯债", "可转债", "固收"]):
        return "bond"
    if truthy_flag(row.get("是否权益基金")) or has_any_keyword(text, ["权益", "股票", "偏股"]):
        return "equity"
    if truthy_flag(row.get("是否混合基金")) or "混合" in text:
        return "mixed"
    return "unknown"


def holding_display_asset_type(row: dict[str, Any]) -> str:
    bucket_label = {
        "equity": "权益类",
        "bond": "债券类",
        "cash": "货币现金",
        "mixed": "混合类",
        "commodity": "商品/黄金",
    }.get(fund_asset_bucket(row))
    if bucket_label:
        return bucket_label
    for key in ["标准资产大类", "天天基金大类", "资产类型", "分组名称", "投顾资产分类桶"]:
        value = clean_text(row.get(key), "")
        if value and value not in ("unknown", "other", "a", "1", "2", "3", "4", "5", "6", "7", "8"):
            return value
    return "未披露"


def holding_display_group(row: dict[str, Any]) -> str:
    for key in ["标准资产细类", "天天基金细分类", "天天基金二级分类", "分组名称", "标准资产大类", "天天基金大类", "资产类型"]:
        value = clean_text(row.get(key), "")
        if value and value not in ("unknown", "other", "a", "1", "2", "3", "4", "5", "6", "7", "8"):
            return value
    return holding_display_asset_type(row)


def load_latest_holding_classification_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金标准分类字典"):
        return {}
    direct_rows = fetch_all(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", MAX("持仓日期") AS latest_date
            FROM "策略当前持仓"
            WHERE "基金权重_百分比" IS NOT NULL
            GROUP BY "统一策略ID"
        )
        SELECT h."统一策略ID", h."基金代码", h."基金名称", h."基金权重_百分比" AS weight,
               h."资产类型", h."分组名称", h."持仓日期" AS snapshot_date,
               f."天天基金细分类", f."天天基金大类", f."天天基金二级分类",
               f."是否货币基金", f."是否债券基金", f."是否权益基金", f."是否混合基金",
               f."是否指数基金", f."是否ETF", f."是否ETF联接", f."是否指数增强",
               f."是否QDII", f."是否FOF", f."是否商品黄金", f."是否短债", f."是否纯债", f."是否可转债",
               f."标准资产大类", f."标准资产细类", f."市场地域标签", f."主动被动标签", f."投顾资产分类桶"
        FROM "策略当前持仓" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."持仓日期"
        LEFT JOIN "基金标准分类字典" f ON f."基金代码" = h."基金代码"
        WHERE h."基金权重_百分比" IS NOT NULL
        ORDER BY h."统一策略ID", h."基金权重_百分比" DESC
        """,
    )
    projected_rows = fetch_all(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", MAX("推算持仓日期") AS latest_date
            FROM "策略当前持仓推算补齐"
            GROUP BY "统一策略ID"
        )
        SELECT h."统一策略ID", h."基金代码", h."基金名称", h."推算基金权重_百分比" AS weight,
               NULL AS "资产类型", NULL AS "分组名称", h."推算持仓日期" AS snapshot_date,
               f."天天基金细分类", f."天天基金大类", f."天天基金二级分类",
               f."是否货币基金", f."是否债券基金", f."是否权益基金", f."是否混合基金",
               f."是否指数基金", f."是否ETF", f."是否ETF联接", f."是否指数增强",
               f."是否QDII", f."是否FOF", f."是否商品黄金", f."是否短债", f."是否纯债", f."是否可转债",
               f."标准资产大类", f."标准资产细类", f."市场地域标签", f."主动被动标签", f."投顾资产分类桶"
        FROM "策略当前持仓推算补齐" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."推算持仓日期"
        LEFT JOIN "基金标准分类字典" f ON f."基金代码" = h."基金代码"
        ORDER BY h."统一策略ID", h."推算基金权重_百分比" DESC
        """,
    )
    direct_by_strategy = grouped_rows(direct_rows, "统一策略ID")
    projected_by_strategy = grouped_rows(projected_rows, "统一策略ID")
    result: dict[str, dict[str, Any]] = {}
    for strategy_id in sorted(set(direct_by_strategy) | set(projected_by_strategy)):
        source = "推算持仓" if projected_by_strategy.get(strategy_id) else "直接披露持仓"
        raw_rows = projected_by_strategy.get(strategy_id) or direct_by_strategy.get(strategy_id) or []
        rows = normalize_holding_weight_rows(raw_rows)
        profile = {
            "持仓来源": source,
            "持仓基金数": len(rows),
            "权益基金权重": 0.0,
            "债券基金权重": 0.0,
            "货币基金权重": 0.0,
            "混合基金权重": 0.0,
            "商品黄金权重": 0.0,
            "未知基金权重": 0.0,
            "QDII权重": 0.0,
            "指数基金权重": 0.0,
            "主动基金权重": 0.0,
            "FOF权重": 0.0,
            "持仓分类覆盖权重": 0.0,
        }
        for row in rows:
            weight = as_float(row.get("weight")) or 0.0
            bucket = fund_asset_bucket(row)
            if bucket == "equity":
                profile["权益基金权重"] += weight
                profile["持仓分类覆盖权重"] += weight
            elif bucket == "bond":
                profile["债券基金权重"] += weight
                profile["持仓分类覆盖权重"] += weight
            elif bucket == "cash":
                profile["货币基金权重"] += weight
                profile["持仓分类覆盖权重"] += weight
            elif bucket == "mixed":
                profile["混合基金权重"] += weight
                profile["持仓分类覆盖权重"] += weight
            elif bucket == "commodity":
                profile["商品黄金权重"] += weight
                profile["持仓分类覆盖权重"] += weight
            else:
                profile["未知基金权重"] += weight
            region = clean_text(row.get("市场地域标签"), "")
            if truthy_flag(row.get("是否QDII")) or has_any_keyword(region, ["海外", "全球", "港股", "美国"]):
                profile["QDII权重"] += weight
            if truthy_flag(row.get("是否指数基金")) or truthy_flag(row.get("是否ETF")) or truthy_flag(row.get("是否ETF联接")) or truthy_flag(row.get("是否指数增强")):
                profile["指数基金权重"] += weight
            active_passive = clean_text(row.get("主动被动标签"), "")
            if "主动" in active_passive:
                profile["主动基金权重"] += weight
            if truthy_flag(row.get("是否FOF")):
                profile["FOF权重"] += weight
        for key, value in list(profile.items()):
            if key.endswith("权重"):
                profile[key] = round_or_none(value)
        result[strategy_id] = profile
    return result


def load_benchmark_status_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略基准费率状态"):
        return {}
    return {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "统一策略ID", "投顾费率文本", "年化投顾费率_百分比", "费率状态",
                   "业绩基准文本", "基准文本状态", "基准曲线状态", "基准可用状态", "基础数据等级",
                   "建议补采动作", "最近更新时间"
            FROM "策略基准费率状态"
            """,
        )
    }


def load_benchmark_asset_classification_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略基准资产配置"):
        return {}
    return {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT *
            FROM "策略基准资产配置"
            """,
        )
    }


def benchmark_asset_mix(benchmark_text: Any, benchmark_asset_row: dict[str, Any] | None = None) -> dict[str, Any]:
    parsed = parse_benchmark_formula(benchmark_text)
    equity_codes = {"000918.SH", "000300.SH", "000001.SH", "000015.SH", "000922.CSI", "000906.SH", "000905.SH", "000852.SH", "000510.SH", "000985.CSI", "930903.CSI", "000993.CSI", "399006.SZ", "H30318.CSI", "000698.SH", "000171.SH", "000941.SH", "399967.SZ", "000998.SH", "000942.SH", "000933.SH", "000827.SH", "000979.CSI", "HSI.HI", "990100.MI", "SPX.GI", "NDX.GI", "930950.CSI"}
    bond_codes = {"000012.SH", "H11006.CSI", "H11008.CSI", "H11001.CSI", "H11009.CSI", "H11015.CSI", "H11023.CSI", "930609.CSI", "930610.CSI", "CBA00603.CS", "CBA00601.CS", "CBA00203.CS", "CBA00201.CS", "CBA00303.CS", "CBA00123.CS", "CBA00121.CS", "CBA00103.CS", "CBA00101.CS"}
    cash_codes = {"H11025.CSI", "CASH"}
    mix = {"基准权益权重": None, "基准债券权重": None, "基准货币权重": None, "基准公式解析": parsed.get("说明"), "基准缺失组件": parsed.get("missing", [])}
    if benchmark_asset_row:
        for field in BENCHMARK_ASSET_DISPLAY_FIELDS:
            mix[field] = benchmark_asset_row.get(field)
        if benchmark_asset_row.get("基准公式解析"):
            mix["基准公式解析"] = benchmark_asset_row.get("基准公式解析")
        if benchmark_asset_row.get("基准缺失组件"):
            try:
                mix["基准缺失组件"] = json.loads(str(benchmark_asset_row.get("基准缺失组件")))
            except json.JSONDecodeError:
                mix["基准缺失组件"] = [clean_text(benchmark_asset_row.get("基准缺失组件"), "")]
    if not parsed.get("components"):
        return mix
    equity = bond = cash = 0.0
    for item in parsed.get("components", []):
        code = item.get("code")
        weight = float(item.get("weight") or 0.0) * 100.0
        if code in equity_codes:
            equity += weight
        elif code in bond_codes:
            bond += weight
        elif code in cash_codes:
            cash += weight
    mix["基准权益权重"] = round_or_none(equity)
    mix["基准债券权重"] = round_or_none(bond)
    mix["基准货币权重"] = round_or_none(cash)
    return mix


def benchmark_overseas_weight(benchmark_text: Any) -> float:
    parsed = parse_benchmark_formula(benchmark_text)
    total = 0.0
    for item in parsed.get("components") or []:
        if item.get("code") in OVERSEAS_BENCHMARK_CODES:
            total += float(item.get("weight") or 0.0) * 100.0
    return round(total, 4)


def broad_equity_bucket_from_percent(value: float | None) -> str:
    if value is None:
        return ""
    if value <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(value / 10.0)))}"


def benchmark_risk_asset_fields(benchmark_mix: dict[str, Any]) -> dict[str, Any]:
    equity = as_float(benchmark_mix.get("基准资产大类-权益"))
    if equity is None:
        equity = as_float(benchmark_mix.get("基准权益权重"))
    commodity = as_float(benchmark_mix.get("基准资产大类-商品")) or 0.0
    alternative = as_float(benchmark_mix.get("基准资产大类-另类")) or 0.0
    unknown = as_float(benchmark_mix.get("基准资产未映射权重"))
    if unknown is None:
        unknown = as_float(benchmark_mix.get("基准资产大类-其他")) or 0.0
    if unknown > 0.01:
        return {
            "基准风险资产权重": None,
            "基准风险资产权重_百分比": None,
            "基准风险资产权重说明": "基准未知权重超过0.01%，基准风险资产权重不硬分档。",
        }
    if equity is None and commodity == 0 and alternative == 0:
        return {
            "基准风险资产权重": None,
            "基准风险资产权重_百分比": None,
            "基准风险资产权重说明": "缺少权益、商品、另类权重，无法计算基准风险资产权重。",
        }
    broad = max(0.0, min(100.0, (equity or 0.0) + commodity + alternative))
    return {
        "基准风险资产权重": broad_equity_bucket_from_percent(broad),
        "基准风险资产权重_百分比": round_or_none(broad),
        "基准风险资产权重说明": "基准风险资产权重按基准权益+基准商品+基准另类合计并划分 L0—L10；港股/海外权益是权益子项，不重复计入。",
    }


def is_target_profit_product_text(text: str) -> bool:
    normalized = clean_text(text, "")
    if not normalized:
        return False
    strong_brand = re.search(r"目标盈|小目标|小赢家|步步高|小星愿|小盈加|智盈|智慧目标投|小常乐|常乐", normalized)
    explicit_goal = re.search(
        r"目标收益|收益目标|绝对收益目标|目标止盈|止盈目标|达标即止盈|达标止盈|止盈达标|止盈提醒|达到目标|目标达成|达标退出|达标赎回",
        normalized,
    )
    lifecycle = re.search(
        r"期次|第[零一二三四五六七八九十百千万\d]+期|\d{1,2}期|到期|期满|运作期|封闭期|续作|赎回|退出|发售|发行|自动终止|stopped|两年期|一年期|年中版|新年特供",
        normalized,
        re.I,
    )
    return bool(strong_brand or (explicit_goal and lifecycle))


def classify_strategy(
    row: dict[str, Any],
    holding_profile: dict[str, Any],
    benchmark_status: dict[str, Any],
    benchmark_asset_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    benchmark_text = row.get("业绩基准") or benchmark_status.get("业绩基准文本")
    benchmark_mix = benchmark_asset_mix(benchmark_text, benchmark_asset_row)
    benchmark_overseas = max(
        benchmark_overseas_weight(benchmark_text),
        (as_float(benchmark_mix.get("基准资产类别-港股")) or 0.0) + (as_float(benchmark_mix.get("基准资产类别-海外权益")) or 0.0),
    )
    tags = "、".join(parse_tags(row.get("标签JSON")))
    text = " ".join(
        clean_text(value, "")
        for value in [
            row.get("策略名称"),
            row.get("策略类型"),
            row.get("风险等级"),
            tags,
            row.get("策略描述"),
            benchmark_text,
        ]
    )
    strong_overseas_text = has_strong_overseas_text(
        row.get("策略名称"),
        row.get("策略类型"),
        benchmark_text if benchmark_overseas <= 0 else "",
    )
    equity_weight = as_float(holding_profile.get("权益基金权重")) or 0.0
    bond_weight = as_float(holding_profile.get("债券基金权重")) or 0.0
    cash_weight = as_float(holding_profile.get("货币基金权重")) or 0.0
    mixed_weight = as_float(holding_profile.get("混合基金权重")) or 0.0
    qdii_weight = as_float(holding_profile.get("QDII权重")) or 0.0
    commodity_weight = as_float(holding_profile.get("商品黄金权重")) or 0.0
    index_weight = as_float(holding_profile.get("指数基金权重")) or 0.0
    active_weight = as_float(holding_profile.get("主动基金权重")) or 0.0
    holding_profile_available = any(
        as_float(holding_profile.get(field)) is not None
        for field in ("权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重")
    )
    benchmark_equity = as_float(benchmark_mix.get("基准权益权重")) or 0.0
    benchmark_bond = as_float(benchmark_mix.get("基准债券权重")) or 0.0
    benchmark_cash = as_float(benchmark_mix.get("基准货币权重")) or 0.0
    effective_equity = max(equity_weight + mixed_weight * 0.5, benchmark_equity)
    effective_bond_cash = max(bond_weight + cash_weight + mixed_weight * 0.5, benchmark_bond + benchmark_cash)
    is_target_date = bool(re.search(r"目标日期|养老|退休|20[3-6][0-9]", text))
    is_target_profit = is_target_profit_product_text(text)
    is_overseas = qdii_weight >= 30.0 or benchmark_overseas >= 30.0 or strong_overseas_text
    is_theme = commodity_weight >= 20.0 or has_any_keyword(text, ["主题", "行业", "科技", "硬科技", "AI", "人工智能", "医药", "消费", "军工", "新能源", "碳中和", "环保", "黄金", "战略新兴", "制造", "半导体", "港股互联网"])
    overseas_basis = (
        f"QDII/海外持仓{round_or_none(qdii_weight)}%，海外基准{round_or_none(benchmark_overseas)}%"
        + ("，策略名称/披露类型/基准含明确海外配置证据" if strong_overseas_text else "")
    )
    if is_target_date:
        primary_pool = "目标日期/养老型"
        rule = "目标日期/养老关键词优先归属"
    elif is_target_profit:
        primary_pool = "目标盈系列产品"
        rule = "目标盈/小目标/期次/明确目标收益或达标止盈机制优先归属"
    elif is_overseas:
        primary_pool = "海外/全球型"
        rule = f"强海外证据归属：{overseas_basis}；通用投资范围、机构品牌和黄金/商品不触发海外分类"
    elif is_theme:
        primary_pool = "主题/行业型"
        rule = "主题行业关键词或商品黄金权重优先归属"
    elif max(cash_weight, benchmark_cash) >= 80.0:
        primary_pool = "现金管理型"
        rule = "货币基金或货币基金基准权重不低于80%"
    elif effective_bond_cash >= 90.0 and effective_equity < 10.0:
        primary_pool = "纯债/短债型"
        rule = "债券+货币权重不低于90%且权益中枢低于10%"
    elif effective_bond_cash >= 70.0 and effective_equity < 40.0:
        primary_pool = "固收增强型"
        rule = "债券+货币权重不低于70%且权益中枢低于40%"
    elif effective_equity >= 60.0:
        primary_pool = "偏股配置型"
        rule = "权益基金或权益基准权重不低于60%"
    else:
        primary_pool = "多资产配置型"
        rule = "不满足前置专属池，归入跨资产配置"
    if is_overseas:
        region = "海外/全球"
    elif qdii_weight >= 10.0 or benchmark_overseas >= 10.0:
        region = "国内+海外"
    else:
        region = "国内"
    if index_weight >= 70.0:
        active_passive = "指数/被动为主"
    elif active_weight >= 60.0 and index_weight < 40.0:
        active_passive = "主动为主"
    elif index_weight >= 30.0 and active_weight >= 30.0:
        active_passive = "主动被动混合"
    else:
        active_passive = "未稳定识别"
    special_labels = []
    if is_target_date:
        special_labels.append("目标日期/养老")
    if is_target_profit:
        special_labels.append("目标盈/止盈")
    if is_overseas:
        special_labels.append("海外全球")
    if is_theme:
        special_labels.append("主题行业")
    if commodity_weight >= 10.0 or "黄金" in text:
        special_labels.append("商品黄金")
    implementation_labels = []
    if index_weight >= 70.0:
        implementation_labels.append("指数/被动工具组合")
    elif active_weight >= 60.0:
        implementation_labels.append("主动基金组合")
    elif index_weight > 0 or active_weight > 0:
        implementation_labels.append("主动+指数混合")
    if qdii_weight >= 10.0:
        implementation_labels.append("QDII/海外工具")
    if as_float(holding_profile.get("FOF权重")):
        implementation_labels.append("FOF工具")
    if not implementation_labels:
        implementation_labels.append("未稳定识别")
    classification_basis = (
        f"{rule}；持仓来源={holding_profile.get('持仓来源') or '未取得权重'}；"
        f"权益{round_or_none(equity_weight)}%、债券{round_or_none(bond_weight)}%、货币{round_or_none(cash_weight)}%、"
        f"混合{round_or_none(mixed_weight)}%、QDII{round_or_none(qdii_weight)}%、指数{round_or_none(index_weight)}%；"
        f"基准权益{benchmark_mix.get('基准权益权重') if benchmark_mix.get('基准权益权重') is not None else '未解析'}%、"
        f"基准债券{benchmark_mix.get('基准债券权重') if benchmark_mix.get('基准债券权重') is not None else '未解析'}%、"
        f"基准货币{benchmark_mix.get('基准货币权重') if benchmark_mix.get('基准货币权重') is not None else '未解析'}%"
    )
    risk_asset_fields = benchmark_risk_asset_fields(benchmark_mix)
    benchmark_risk_weight = as_float(risk_asset_fields.get("基准风险资产权重_百分比"))
    benchmark_bucket = clean_text(risk_asset_fields.get("基准风险资产权重"), "")
    non_equity_track = clean_text(benchmark_mix.get("非权益比较轨道"), "")
    if primary_pool not in {"目标日期/养老型", "目标盈系列产品", "海外/全球型", "主题/行业型"} and benchmark_bucket:
        bucket_level = int(benchmark_bucket[1:]) if re.fullmatch(r"L(?:10|[0-9])", benchmark_bucket) else -1
        if non_equity_track in {"商品轨道", "另类轨道"}:
            primary_pool, rule = "商品/另类型", f"基准风险资产权重={benchmark_bucket}且非权益比较轨道={non_equity_track}"
        elif non_equity_track == "货币轨道":
            primary_pool, rule = "现金管理型", f"基准风险资产权重={benchmark_bucket}且非权益比较轨道=货币轨道"
        elif non_equity_track == "债券轨道" and bucket_level <= 1:
            primary_pool, rule = "纯债/短债型", f"基准风险资产权重={benchmark_bucket}且非权益比较轨道=债券轨道"
        elif non_equity_track == "债券轨道" and bucket_level <= 3:
            primary_pool, rule = "固收增强型", f"基准风险资产权重={benchmark_bucket}且非权益比较轨道=债券轨道"
        elif 1 <= bucket_level <= 3:
            primary_pool, rule = "固收增强型", f"基准风险资产权重={benchmark_bucket}"
        elif 4 <= bucket_level <= 6:
            primary_pool, rule = "多资产配置型", f"基准风险资产权重={benchmark_bucket}"
        elif 7 <= bucket_level <= 10:
            primary_pool, rule = "偏股配置型", f"基准风险资产权重={benchmark_bucket}"
    equity_center = max(0.0, min(100.0, equity_weight + mixed_weight * 0.5)) if holding_profile_available else None
    fixed_income_center = max(0.0, min(100.0, bond_weight + cash_weight + mixed_weight * 0.5)) if holding_profile_available else None
    overseas_center = max(0.0, min(100.0, max(qdii_weight, benchmark_overseas)))
    risk_deviation = None if benchmark_risk_weight is None or equity_center is None else equity_center - benchmark_risk_weight
    style_labels = [benchmark_bucket] if benchmark_bucket else []
    if equity_center is not None:
        style_labels.append("低权益中枢" if equity_center < 20 else "中权益中枢" if equity_center < 60 else "高权益中枢")
    if fixed_income_center is not None and fixed_income_center >= 70:
        style_labels.append("固收为主")
    if overseas_center >= 20:
        style_labels.append("海外配置")
    if index_weight >= 60:
        style_labels.append("指数化为主")
    elif active_weight >= 60:
        style_labels.append("主动管理为主")
    if risk_deviation is not None:
        style_labels.append("高于基准风险" if risk_deviation > 10 else "低于基准风险" if risk_deviation < -10 else "贴近基准风险")
    classification_basis = f"{rule}；基准风险资产权重={benchmark_bucket or '未分档'}（{round_or_none(benchmark_risk_weight) if benchmark_risk_weight is not None else '未解析'}%）；" + classification_basis
    result = {
        "主可比池": primary_pool,
        "市场地域": region,
        "主动被动": active_passive,
        "特殊标签": "、".join(special_labels) if special_labels else "无",
        "策略实现标签": "、".join(implementation_labels),
        "权益基金权重": round_or_none(equity_weight),
        "债券基金权重": round_or_none(bond_weight),
        "货币基金权重": round_or_none(cash_weight),
        "混合基金权重": round_or_none(mixed_weight),
        "QDII权重": round_or_none(qdii_weight),
        "指数基金权重": round_or_none(index_weight),
        "主动基金权重": round_or_none(active_weight),
        "基准权益权重": benchmark_mix.get("基准权益权重"),
        "基准债券权重": benchmark_mix.get("基准债券权重"),
        "基准货币权重": benchmark_mix.get("基准货币权重"),
        **risk_asset_fields,
        "权益中枢": round_or_none(equity_center),
        "固收中枢": round_or_none(fixed_income_center),
        "基准风险资产中枢": round_or_none(benchmark_risk_weight),
        "海外配置中枢": round_or_none(overseas_center),
        "指数化程度": round_or_none(index_weight) if holding_profile_available else None,
        "主动管理程度": round_or_none(active_weight) if holding_profile_available else None,
        "风险资产偏离": round_or_none(risk_deviation),
        "配置风格标签": "、".join(style_labels),
        "基准可用状态": clean_text(benchmark_status.get("基准可用状态"), "未评估"),
        "基础数据等级": clean_text(benchmark_status.get("基础数据等级"), "未评估"),
        "费率状态": clean_text(benchmark_status.get("费率状态"), "未评估"),
        "年化投顾费率": round_or_none(benchmark_status.get("年化投顾费率_百分比")),
        "基准公式解析": benchmark_mix.get("基准公式解析"),
        "分类依据": classification_basis,
    }
    for field in BENCHMARK_ASSET_DISPLAY_FIELDS:
        if field in result:
            continue
        result[field] = benchmark_mix.get(field)
    return result


def report_product_classification(
    classification: dict[str, Any],
    holding_fund_count: int,
) -> tuple[str, str, str]:
    pool = clean_text(classification.get("主可比池"), "")
    equity = as_float(classification.get("权益基金权重")) or 0.0
    mixed = as_float(classification.get("混合基金权重")) or 0.0
    bond = as_float(classification.get("债券基金权重")) or 0.0
    cash = as_float(classification.get("货币基金权重")) or 0.0
    qdii = as_float(classification.get("QDII权重")) or 0.0
    index_weight = as_float(classification.get("指数基金权重")) or 0.0
    active_weight = as_float(classification.get("主动基金权重")) or 0.0
    benchmark_equity = as_float(classification.get("基准权益权重")) or 0.0
    benchmark_bond_cash = (as_float(classification.get("基准债券权重")) or 0.0) + (as_float(classification.get("基准货币权重")) or 0.0)
    effective_equity = max(equity + mixed * 0.5, benchmark_equity)
    effective_bond_cash = max(bond + cash + mixed * 0.5, benchmark_bond_cash)
    basis = (
        f"主可比池={pool or '未分类'}；持仓基金数={holding_fund_count}；"
        f"有效权益{round_or_none(effective_equity)}%，有效债券货币{round_or_none(effective_bond_cash)}%，"
        f"QDII{round_or_none(qdii)}%，指数{round_or_none(index_weight)}%，主动{round_or_none(active_weight)}%"
    )
    if holding_fund_count <= 0 and pool in {"多资产配置型", ""} and effective_equity == 0 and effective_bond_cash == 0:
        return "持仓缺失/不入池", "", basis + "；缺少可验证持仓和基准资产结构"
    if pool in {"现金管理型", "纯债/短债型"}:
        return "纯债型", "", basis
    if pool == "固收增强型":
        return "固收+型", "", basis
    if pool == "偏股配置型" or pool == "主题/行业型":
        subtype = "主题/行业" if pool == "主题/行业型" else ("指数/被动" if index_weight >= 70 else "主动权益" if active_weight >= 60 else "权益配置")
        return "股票型", subtype, basis
    if pool == "海外/全球型":
        subtype = "海外/全球股票" if effective_equity >= 40 or qdii >= 30 else ""
        return "股票型" if subtype else "多元配置型", subtype, basis
    if pool == "目标盈系列产品":
        if effective_equity >= 60:
            return "股票型", "目标盈权益", basis
        if effective_equity >= 20:
            return "股债混合型", "", basis
        if effective_bond_cash >= 70:
            return "固收+型", "", basis
        return "多元配置型", "", basis
    if effective_equity >= 70:
        return "股票型", "权益配置", basis
    if effective_equity >= 20:
        return "股债混合型", "", basis
    if effective_bond_cash >= 70:
        return "固收+型", "", basis
    return "多元配置型", "", basis


def display_status_fields(channel_id: str, channel_name: str, status: str) -> dict[str, str]:
    normalized = clean_text(status, "未披露")
    if channel_id in LEGACY_ARCHIVE_CHANNEL_IDS:
        return {
            "天天当前对客展示": "否",
            "天天展示状态": "历史接口留档/非当前财富管家货架",
            "天天展示判定依据": (
                f"渠道={channel_name or channel_id}；该渠道为贝塔牛历史接口留档，"
                "当前广发证券 App 的财富管家使用 gfsec_fima 独立产品目录；历史产品仅供查询，不进入当前货架和排名"
            ),
        }
    stopped = bool(re.search(r"终止|停止|下架|到期|清盘|结束|非对客|未展示|隐藏|暂停", normalized))
    if stopped:
        current = "否"
        display = f"{normalized}/非对客或已结束"
    else:
        current = "是"
        display = normalized if normalized != "未披露" else "已进入业务展示口径"
    if channel_id != "ttfund" and not stopped:
        display = f"{channel_name or channel_id}展示口径"
    return {
        "天天当前对客展示": current,
        "天天展示状态": display,
        "天天展示判定依据": f"渠道={channel_name or channel_id}；归一运作状态={normalized}；终止/隐藏关键词={ '命中' if stopped else '未命中' }",
    }


MISSING_TEXT_VALUES = {"", "未披露", "未分类", "未评估", "未知", "--", "-", "null", "None", "undefined", "NaN"}


def is_present_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return str(value).strip() not in MISSING_TEXT_VALUES


def group_coverage(rows: list[dict[str, Any]], group_key: str, value_key: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        group = clean_text(row.get(group_key), "未披露")
        slot = groups.setdefault(group, {"策略数": 0, "有效": 0})
        slot["策略数"] += 1
        if is_present_value(row.get(value_key)):
            slot["有效"] += 1
    return [
        {
            group_key: group,
            "策略数": data["策略数"],
            f"有{value_key}": data["有效"],
            f"无{value_key}": data["策略数"] - data["有效"],
            "披露覆盖率": percent_ratio(data["有效"], data["策略数"]),
        }
        for group, data in sorted(groups.items(), key=lambda item: (-item[1]["策略数"], item[0]))
    ]


def build_benchmark_disclosure(strategies: list[dict[str, Any]]) -> dict[str, Any]:
    ttfund_rows = [row for row in strategies if row.get("渠道") == "天天基金/投顾"]
    total = len(ttfund_rows)
    disclosed = sum(1 for row in ttfund_rows if is_present_value(row.get("业绩基准说明")))
    missing_rows = [row for row in ttfund_rows if not is_present_value(row.get("业绩基准说明"))]
    by_type = []
    for row in group_coverage(ttfund_rows, "研报产品类型", "业绩基准说明"):
        by_type.append(
            {
                "研报产品类型": row["研报产品类型"],
                "策略数": row["策略数"],
                "有业绩基准说明": row["有业绩基准说明"],
                "无业绩基准说明": row["无业绩基准说明"],
                "披露覆盖率": row["披露覆盖率"],
            }
        )
    by_institution = []
    for row in group_coverage(ttfund_rows, "投顾机构", "业绩基准说明"):
        by_institution.append(
            {
                "投顾机构": row["投顾机构"],
                "策略数": row["策略数"],
                "有业绩基准说明": row["有业绩基准说明"],
                "无业绩基准说明": row["无业绩基准说明"],
                "披露覆盖率": row["披露覆盖率"],
            }
        )
    return {
        "统计口径": "天天基金/投顾渠道全部展示策略；按详情页业绩基准说明文本统计。ADB深扫后仍为空的策略保留为未披露，不用基准曲线状态替代文本。",
        "总览": {
            "策略数": total,
            "有业绩基准说明": disclosed,
            "无业绩基准说明": total - disclosed,
            "披露覆盖率": percent_ratio(disclosed, total),
            "详情缺失策略数": 0,
        },
        "按研报产品类型": by_type,
        "按机构": by_institution,
        "缺失样本": [
            {
                "策略代码": row.get("策略代码"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "研报产品类型": row.get("研报产品类型"),
                "业务分类": row.get("业务分类"),
            }
            for row in missing_rows[:120]
        ],
    }


def build_field_missingness_audit(strategies: list[dict[str, Any]]) -> dict[str, Any]:
    benchmark_missing_by_channel: dict[str, int] = {}
    for row in strategies:
        if not is_present_value(row.get("业绩基准说明")):
            channel = clean_text(row.get("渠道"), "未披露")
            benchmark_missing_by_channel[channel] = benchmark_missing_by_channel.get(channel, 0) + 1
    benchmark_reason = "；".join(f"{channel}{count}条" for channel, count in sorted(benchmark_missing_by_channel.items())) or "无缺失"
    specs = [
        ("策略名称", "主键展示字段，来自策略主表；缺失通常为严重采集异常。", "保持强校验。"),
        ("投顾机构", "明确采集字段；天天基金已补 cfHInfo.fortuneName fallback。", "继续在增量脚本中监控缺失。"),
        ("研报产品类型", "导出加工字段，基于主可比池、持仓资产权重和基准资产结构生成。", "已补齐，用于正式研报统计。"),
        ("业务分类", "导出加工字段，默认沿用互斥主可比池。", "已补齐，用于目标盈归并和业务筛选。"),
        ("披露策略类型", "渠道原始披露字段，天天基金等渠道不稳定披露。", "业务价值有限，待后续从总览指标中删减或仅详情保留。"),
        ("风险等级", "渠道直接披露字段，少量缺失影响风险筛选。", "缺失样本需后续按渠道详情继续补。"),
        ("运作状态", "渠道状态字段，不同渠道披露口径差异大。", "用于识别终止/非对客；缺失时保留展示口径判断。"),
        ("业绩基准说明", f"天天基金剩余缺失已做接口/ADB深扫；当前缺失分布：{benchmark_reason}。", "天天基金剩余按平台未披露处理；其他渠道如需同口径需新增对应采集器字段。"),
        ("年化投顾费率", "结构化自投顾费率文本；低缺失字段。", "继续作为关键质量字段监控。"),
        ("最新业绩日期", "官方披露业绩曲线最新可画日期。", "缺失会影响收益和图表展示，需保持增量更新。"),
        ("最新持仓日", "当前持仓或推算持仓日期。", "缺失会影响分类、回放和持仓分析。"),
        ("最近调仓日", "历史调仓事件最新日期。", "渠道未披露历史调仓时允许为空，但需标注覆盖率。"),
        ("近一周", "官方披露净值计算收益。", "缺失一般来自业绩曲线缺失或最新净值不足。"),
        ("近1年", "官方披露净值计算收益。", "新成立不足一年或曲线不足时允许为空。"),
        ("最大回撤", "优先官方披露曲线计算，回退标准回放。", "低缺失字段，继续监控。"),
        ("波动率", "基于标准回放或披露曲线计算。", "缺失通常意味着不可回放或曲线不足。"),
        ("夏普比率", "基于年化收益和波动率计算。", "缺失与波动率/收益曲线完整性相关。"),
    ]
    rows = []
    total = len(strategies)
    for field, reason, action in specs:
        valid = sum(1 for row in strategies if is_present_value(row.get(field)))
        rows.append(
            {
                "指标": field,
                "样本数": total,
                "有效样本": valid,
                "缺失样本": total - valid,
                "覆盖率": percent_ratio(valid, total),
                "缺失原因判断": reason,
                "处理建议": action,
            }
        )
    severe = [row for row in rows if row["覆盖率"] is not None and row["覆盖率"] < 80]
    return {
        "统计口径": "业务数据总览当前展示策略样本；空值、未披露、未分类、未评估等均按缺失处理。披露策略类型为渠道原始字段，不作为正式研报分类口径。",
        "字段缺失审计": rows,
        "严重缺失字段": severe,
        "待后续删减字段": [row for row in rows if row["指标"] == "披露策略类型"],
    }


def insight_scope_label(
    data_completeness: str,
    latest_performance_date: str,
    global_latest_performance_dt: Any,
    holding_fund_count: int,
) -> str:
    if holding_fund_count <= 0:
        return "仅列表保留"
    if clean_text(data_completeness, "") == "完整":
        return "完整策略"
    latest_dt = parse_ymd(latest_performance_date)
    if latest_dt and global_latest_performance_dt and holding_fund_count > 0 and latest_dt >= global_latest_performance_dt - timedelta(days=5):
        return "扩展样本"
    return "仅列表保留"


def performance_completeness_fields(
    latest_performance_date: str,
    global_latest_performance_dt: Any,
    performance_point_count: int,
    max_gap_days: Any,
) -> dict[str, str]:
    """Return the business performance-completeness fields used by AI filtering.

    This deliberately does not depend on benchmark, holding, rebalance or
    reconstructed-NAV completeness.  Those are separate business dimensions
    and must not silently shrink a pure strategy-performance query.
    """
    latest_dt = parse_ymd(latest_performance_date)
    reasons: list[str] = []
    if latest_dt is None:
        reasons.append("缺少有效最新业绩日期")
    if global_latest_performance_dt is None:
        reasons.append("全库最新业绩日不可用")
    elif latest_dt is not None:
        stale_days = (global_latest_performance_dt - latest_dt).days
        if stale_days > PERFORMANCE_RECENCY_TOLERANCE_DAYS:
            reasons.append(
                f"最新业绩落后全库最新业绩日{stale_days}天，超过{PERFORMANCE_RECENCY_TOLERANCE_DAYS}天"
            )
    if int(performance_point_count or 0) < PERFORMANCE_MIN_POINT_COUNT:
        reasons.append(f"可画净值点少于{PERFORMANCE_MIN_POINT_COUNT}个")
    gap = as_float(max_gap_days)
    if gap is not None and gap > PERFORMANCE_MAX_GAP_DAYS:
        reasons.append(f"业绩曲线相邻点最大间隔{int(gap)}天，超过{PERFORMANCE_MAX_GAP_DAYS}天")
    if reasons:
        return {
            "业绩完整": "否",
            "业绩完整性": "缺失",
            "业绩完整性说明": "；".join(reasons),
        }
    return {
        "业绩完整": "是",
        "业绩完整性": "完整",
        "业绩完整性说明": (
            f"最新业绩距全库最新业绩日不超过{PERFORMANCE_RECENCY_TOLERANCE_DAYS}天；"
            f"可画净值点{int(performance_point_count)}个；曲线最大间隔"
            f"{int(gap or 0)}天"
        ),
    }


def build_strategy_rows(conn: sqlite3.Connection, algorithm_version: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    log_progress("build strategy rows: load strategy base tables")
    display_channels = tuple(sorted(DISPLAY_STRATEGY_CHANNEL_IDS))
    placeholders = ",".join("?" for _ in display_channels)
    strategies = fetch_all(
        conn,
        f'SELECT * FROM "策略信息" WHERE "渠道ID" IN ({placeholders}) ORDER BY "渠道ID", "投顾机构", "策略名称"',
        display_channels,
    )
    strategy_map = {str(row["统一策略ID"]): row for row in strategies}
    channel_map = {row["渠道ID"]: row for row in fetch_all(conn, 'SELECT * FROM "渠道信息"')}
    relationship_map = load_strategy_relationship_map(conn)
    log_progress(f"build strategy rows: strategies={len(strategies)}")
    official_latest = latest_map(
        conn,
        "策略产品披露净值",
        "交易日期",
        ["交易日期", "披露单位净值", "披露累计收益率_百分比", "最大回撤_百分比"],
        'WHERE "是否可画曲线" = 1',
    )
    log_progress("build strategy rows: official latest loaded")
    quality_map = {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            'SELECT * FROM "策略模拟净值质量" WHERE "算法版本" = ?',
            (algorithm_version,),
        )
    }
    deviation_map = {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            'SELECT * FROM "策略官方偏差分析" WHERE "算法版本" = ?',
            (algorithm_version,),
        )
    }
    compare_map = {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            'SELECT * FROM "策略业绩口径对比" WHERE "算法版本" = ?',
            (algorithm_version,),
        )
    }
    log_progress("build strategy rows: quality maps loaded")
    disclosure_return_map = build_disclosed_return_map(conn)
    disclosure_latest_value_map = build_disclosed_latest_value_map(conn)
    disclosure_risk_map = build_disclosed_risk_map(conn)
    official_latest = apply_relationship_aliases(official_latest, relationship_map)
    disclosure_return_map = apply_relationship_aliases(disclosure_return_map, relationship_map)
    disclosure_latest_value_map = apply_relationship_aliases(disclosure_latest_value_map, relationship_map)
    disclosure_risk_map = apply_relationship_aliases(disclosure_risk_map, relationship_map)
    log_progress("build strategy rows: disclosure maps loaded")
    holding_classification_map = load_latest_holding_classification_map(conn)
    benchmark_status_map = load_benchmark_status_map(conn)
    benchmark_asset_map = load_benchmark_asset_classification_map(conn)
    (
        benchmark_text_map,
        benchmark_status_map,
        benchmark_asset_map,
        inherited_benchmark_source_map,
        benchmark_relationship_conflicts,
    ) = resolve_relationship_benchmark_domains(
        strategy_map,
        relationship_map,
        benchmark_status_map,
        benchmark_asset_map,
    )
    if benchmark_relationship_conflicts:
        log_progress(
            "build strategy rows: preserved "
            f"{len(benchmark_relationship_conflicts)} explicit child/source benchmark conflicts for audit"
        )
    log_progress("build strategy rows: holding classification and benchmark maps loaded")
    disclosure_count_map = build_strategy_count_map(conn, "策略产品披露净值", "交易日期", 'WHERE "是否可画曲线" = 1')
    disclosure_count_map = apply_relationship_aliases(disclosure_count_map, relationship_map)
    standard_count_map = build_strategy_count_map(conn, "策略标准业绩净值", "交易日期", 'WHERE "算法版本" = ?', (algorithm_version,))
    log_progress("build strategy rows: performance count maps loaded")
    global_latest_performance_dt = max(
        (
            parse_ymd(item.get("收益数据截至") or item.get("max_date"))
            for item in [*disclosure_return_map.values(), *disclosure_count_map.values()]
        ),
        default=None,
    )
    complete_channel_counts = {
        row["渠道ID"]: int(row["完整策略数"] or 0)
        for row in fetch_all(
            conn,
            """
            SELECT "渠道ID", COUNT(DISTINCT "统一策略ID") AS "完整策略数"
            FROM "策略业绩口径对比"
            WHERE "算法版本" = ? AND "对比状态" = '可对比'
            GROUP BY "渠道ID"
            """,
            (algorithm_version,),
        )
    }
    holding_audit_map = {row["统一策略ID"]: row for row in fetch_all(conn, 'SELECT * FROM "最新持仓推算稽核策略汇总"')}
    log_progress("build strategy rows: holding audit map loaded")
    governance_map = (
        {row["统一策略ID"]: row for row in fetch_all(conn, 'SELECT * FROM "策略治理标签"')}
        if table_exists(conn, "策略治理标签")
        else {}
    )
    log_progress("build strategy rows: strategy governance map loaded")
    signal_summary_map = load_signal_summary_map(conn)
    log_progress("build strategy rows: signal strategy summary map loaded")
    holding_stats = {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "统一策略ID", MAX("持仓日期") AS latest_holding_date,
                   COUNT(*) AS holding_rows,
                   COUNT(DISTINCT "基金代码") AS holding_fund_count,
                   SUM(CASE WHEN "基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS weighted_rows
            FROM "策略当前持仓"
            WHERE "基金权重_百分比" IS NOT NULL AND "基金权重_百分比" > 0
            GROUP BY "统一策略ID"
            """,
        )
    }
    log_progress("build strategy rows: direct holding stats loaded")
    historical_position_stats = build_historical_position_stats_map(conn)
    log_progress("build strategy rows: historical position stats loaded")
    projected_holding_stats = {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "统一策略ID", MAX("推算持仓日期") AS projected_holding_date,
                   COUNT(DISTINCT "基金代码") AS projected_fund_count
            FROM "策略当前持仓推算补齐"
            WHERE "推算基金权重_百分比" IS NOT NULL AND "推算基金权重_百分比" > 0
            GROUP BY "统一策略ID"
            """,
        )
    }
    log_progress("build strategy rows: projected holding stats loaded")
    rebalance_stats = build_rebalance_stats_map(conn)
    rebalance_metric_map = build_rebalance_metric_map(conn)
    latest_rebalance_holding_stats = build_latest_rebalance_holding_stats_map(conn)
    log_progress("build strategy rows: rebalance stats loaded")
    rebalance_date_map: dict[str, list[str]] = {}
    for event in fetch_rebalance_metric_events(conn):
        rebalance_date_map.setdefault(str(event["统一策略ID"]), []).append(str(event["调仓日期"]))
    log_progress("build strategy rows: rebalance dates loaded")
    list_rows: list[dict[str, Any]] = []
    context: dict[str, dict[str, Any]] = {}
    for row in strategies:
        strategy_id = row["统一策略ID"]
        channel_id = row["渠道ID"]
        if channel_id not in DISPLAY_STRATEGY_CHANNEL_IDS:
            continue
        channel = channel_map.get(row["渠道ID"], {})
        official = official_latest.get(strategy_id, {})
        quality = quality_map.get(strategy_id)
        deviation = deviation_map.get(strategy_id, {})
        compare = compare_map.get(strategy_id, {})
        audit = holding_audit_map.get(strategy_id)
        governance = governance_map.get(strategy_id, {})
        relationship = relationship_map.get(strategy_id, {})
        signal_summary = signal_summary_map.get(strategy_id, {})
        holding = holding_stats.get(strategy_id, {})
        historical_position = historical_position_stats.get(strategy_id, {})
        projected = projected_holding_stats.get(strategy_id, {})
        rebalance = rebalance_stats.get(strategy_id, {})
        latest_rebalance_holding = latest_rebalance_holding_stats.get(strategy_id, {})
        rebalance_metric = rebalance_metric_map.get(strategy_id, {})
        disclosed_returns = disclosure_return_map.get(strategy_id, {})
        disclosed_latest = disclosure_latest_value_map.get(strategy_id, {})
        disclosed_risk = disclosure_risk_map.get(strategy_id, {})
        disclosure_stats = disclosure_count_map.get(strategy_id, {})
        standard_stats = standard_count_map.get(strategy_id, {})
        benchmark_status = dict(benchmark_status_map.get(strategy_id, {}))
        benchmark_asset_row = benchmark_asset_map.get(strategy_id, {})
        official_performance_source_id = clean_text(relationship.get("官方业绩策略ID"), "")
        benchmark_is_inherited = (
            inherited_benchmark_source_map.get(strategy_id) == official_performance_source_id
        )
        benchmark_source_id = official_performance_source_id if benchmark_is_inherited else strategy_id
        benchmark_text = clean_text(
            benchmark_text_map.get(strategy_id)
            or benchmark_status.get("业绩基准文本"),
            "",
        )
        # The strategy master is refreshed by the channel collector before the
        # derived benchmark-status table.  Prefer its more specific official
        # text so a stale generic label such as "业绩基准" cannot overwrite a
        # newly collected formula in the page detail pack.
        if benchmark_text:
            benchmark_status["业绩基准文本"] = benchmark_text
            benchmark_status["基准文本状态"] = "已披露"
        signal_event_count = int(signal_summary.get("信号事件数") or 0)
        is_signal_strategy = int(governance.get("是否信号类组合") or 0) == 1 or signal_event_count > 0
        classification = classify_strategy(row, holding_classification_map.get(strategy_id, {}), benchmark_status, benchmark_asset_row)
        if is_signal_strategy:
            classification["主可比池"] = "信号类策略"
            labels = [item for item in clean_text(classification.get("特殊标签"), "").split("、") if item and item != "无"]
            if "信号服务" not in labels:
                labels.append("信号服务")
            classification["特殊标签"] = "、".join(labels) if labels else "信号服务"
            classification["分类依据"] = f"治理层确认信号类策略优先归属；{classification.get('分类依据') or ''}"
        quality_checks = build_quality_checks(quality, compare, rebalance, disclosure_stats, standard_stats)
        holding_date = projected.get("projected_holding_date") or holding.get("latest_holding_date")
        holding_fund_count = int((projected.get("projected_fund_count") or 0) or (holding.get("holding_fund_count") or 0))
        latest_rebalance_holding_date = latest_rebalance_holding.get("latest_rebalance_holding_date")
        if latest_rebalance_holding_date and (not holding_date or latest_rebalance_holding_date > holding_date):
            holding_date = latest_rebalance_holding_date
            holding_fund_count = int(latest_rebalance_holding.get("latest_rebalance_holding_fund_count") or holding_fund_count)
        latest_performance_date = disclosed_returns.get("收益数据截至") or disclosure_stats.get("max_date")
        latest_perf_dt = parse_ymd(latest_performance_date)
        inception_dt = parse_ymd(row["成立日期"])
        operation_years = ((latest_perf_dt - inception_dt).days + 1) / 365.25 if latest_perf_dt and inception_dt and latest_perf_dt >= inception_dt else None
        total_turnover = as_float(rebalance_metric.get("total_turnover"))
        rebalance_count = int(rebalance.get("rebalance_count") or 0)
        annual_turnover = round_or_none(total_turnover / operation_years) if total_turnover is not None and operation_years and operation_years > 0 else None
        rebalance_frequency = round_or_none(rebalance_count / operation_years) if operation_years and operation_years > 0 else None
        recent_cutoff = latest_perf_dt - timedelta(days=365) if latest_perf_dt else None
        recent_rebalance_count = sum(1 for value in rebalance_date_map.get(strategy_id, []) if recent_cutoff and parse_ymd(value) and parse_ymd(value) >= recent_cutoff)
        channel_name = canonical_business_channel(channel_id, channel.get("渠道名称"))
        operation_status_text = operation_status(row.get("策略状态"))
        report_product_type, report_stock_subtype, report_basis = report_product_classification(classification, holding_fund_count)
        performance_point_count = int(disclosed_returns.get("业绩点数") or 0)
        performance_fields = performance_completeness_fields(
            latest_performance_date,
            global_latest_performance_dt,
            performance_point_count,
            disclosed_returns.get("业绩最大间隔天数"),
        )
        performance_is_complete = performance_fields.get("业绩完整") == "是"
        sparse_list_only_performance = channel_id in LIST_ONLY_DISPLAY_CHANNELS and performance_point_count < 2
        if holding_fund_count <= 0:
            for field in ("权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重"):
                classification[field] = None
            classification["分类依据"] = f"{classification.get('分类依据') or ''}；未取得基金级当前持仓，资产权重均按未披露展示，不以0代替。"
        display_fields = display_status_fields(str(channel_id), str(channel_name), operation_status_text)
        benchmark_bucket = classification.get("基准风险资产权重")
        is_legacy_archive = channel_id in LEGACY_ARCHIVE_CHANNEL_IDS
        fallback_governance_status = "信号类组合" if is_signal_strategy else ("缺官方业绩" if not performance_is_complete else "未生成")
        fallback_analysis_group = "信号服务" if is_signal_strategy else ("业绩缺失" if not performance_is_complete else "常规运行")
        current = {
            "策略治理状态": "历史接口留档" if is_legacy_archive else clean_text(governance.get("治理状态"), fallback_governance_status),
            "分析分组": "历史贝塔牛接口-仅查询" if is_legacy_archive else clean_text(governance.get("分析分组"), fallback_analysis_group),
            "是否测试组合": int(governance.get("是否测试组合") or 0),
            "是否信号类组合": int(is_signal_strategy),
            "是否目标盈期次": int(governance.get("是否目标盈期次") or 0),
            "是否已停止": int(governance.get("是否已停止") or 0),
            "是否历史接口留档": int(is_legacy_archive),
            "是否纳入常规排名": (
                0
                if channel_id in LIST_ONLY_DISPLAY_CHANNELS or is_signal_strategy or not performance_is_complete
                else int(governance.get("是否纳入常规排名") if governance.get("是否纳入常规排名") is not None else 1)
            ),
            "仅列表展示": int(channel_id in LIST_ONLY_DISPLAY_CHANNELS),
            "是否单独分析": 1 if is_legacy_archive else int(governance.get("是否单独分析") or 0),
            "母策略ID": clean_text(relationship.get("母策略ID"), ""),
            "母策略名称": clean_text(relationship.get("母策略名称"), ""),
            "策略关系类型": clean_text(relationship.get("关系类型"), ""),
            "官方业绩来源策略ID": clean_text(relationship.get("官方业绩策略ID"), ""),
            "官方业绩口径": (
                f'共享母策略“{clean_text(relationship.get("母策略名称"), relationship.get("母策略ID"))}”披露业绩，非本期独立净值'
                if relationship.get("官方业绩策略ID")
                else "本策略独立披露业绩"
            ),
            "业绩分析截止日期": clean_text(governance.get("业绩分析截止日期"), latest_performance_date or ""),
            "持仓处理方式": clean_text(governance.get("持仓处理方式"), ""),
            "调仓展示方式": clean_text(governance.get("调仓展示方式"), ""),
            "治理规则说明": (
                "贝塔牛为广发证券历史接口产品，官方业绩大多停留在2023年且旧日度曲线路由已不可用；"
                "已有官方区间收益保留查询，不视为当前财富管家在架产品，不进入当前排名。"
                if is_legacy_archive
                else clean_text(governance.get("规则说明"), "")
            ),
            "信号事件数": signal_event_count,
            "最近信号日": clean_text(signal_summary.get("最近信号日"), ""),
            "信号指令数": int(signal_summary.get("信号指令数") or 0),
            "买入指令数": int(signal_summary.get("买入指令数") or 0),
            "卖出指令数": int(signal_summary.get("卖出指令数") or 0),
            "加仓指令数": int(signal_summary.get("加仓指令数") or 0),
            "减仓指令数": int(signal_summary.get("减仓指令数") or 0),
            "信号胜率_1月": round_or_none(signal_summary.get("信号胜率_1月")),
            "信号胜率_3月": round_or_none(signal_summary.get("信号胜率_3月")),
            "信号胜率_6月": round_or_none(signal_summary.get("信号胜率_6月")),
            "信号胜率_1年": round_or_none(signal_summary.get("信号胜率_1年")),
            "信号加权方向收益_1月": round_or_none(signal_summary.get("信号加权方向收益_1月")),
            "信号加权方向收益_3月": round_or_none(signal_summary.get("信号加权方向收益_3月")),
            "信号加权方向收益_6月": round_or_none(signal_summary.get("信号加权方向收益_6月")),
            "信号加权方向收益_1年": round_or_none(signal_summary.get("信号加权方向收益_1年")),
            "统一策略ID": strategy_id,
            "策略代码": row["渠道策略ID"],
            "策略名称": row["策略名称"],
            "渠道": channel_name,
            "投顾机构": canonical_advisor_institution(
                row["投顾机构"],
                channel_id,
                channel_name,
            ),
            "策略类型": clean_text(row["策略类型"]),
            "披露策略类型": clean_text(row["策略类型"]),
            "披露风险等级": clean_text(row["风险等级"]),
            "研报产品类型": report_product_type,
            "研报股票子类型": report_stock_subtype,
            "业务分类": classification["主可比池"],
            "业务分类依据": report_basis,
            "天天当前对客展示": display_fields["天天当前对客展示"],
            "天天展示状态": display_fields["天天展示状态"],
            "天天展示判定依据": display_fields["天天展示判定依据"],
            "有基准": "是" if benchmark_text else "否",
            "有业绩走势": "是" if performance_point_count >= PERFORMANCE_MIN_POINT_COUNT else "否",
            "有历史仓位": "是" if historical_position.get("has_complete_history") else "否",
            "对客未终止": (
                "是"
                if display_fields["天天当前对客展示"] == "是"
                and int(governance.get("是否已停止") or 0) == 0
                and not is_legacy_archive
                else "否"
            ),
            "官方历史仓位快照数": int(historical_position.get("explicit_snapshot_count") or 0),
            "完整历史仓位快照数": int(historical_position.get("complete_explicit_snapshot_count") or 0),
            "完整调仓后仓位数": int(historical_position.get("complete_rebalance_position_count") or 0),
            "历史仓位起始日": historical_position.get("history_first_date"),
            "历史仓位最新日": historical_position.get("history_latest_date"),
            "历史仓位口径": clean_text(historical_position.get("history_source"), "未取得完整历史仓位"),
            "风险等级": clean_text(row["风险等级"]),
            "成立日期": row["成立日期"],
            "运作状态": operation_status_text,
            **performance_fields,
            "数据完整性": data_completeness(compare, quality),
            "近一周": disclosed_returns.get("近一周"),
            "近一月": disclosed_returns.get("近一月"),
            "近三月": disclosed_returns.get("近三月"),
            "近6月": disclosed_returns.get("近6月"),
            "近1年": disclosed_returns.get("近1年"),
            "今年以来": disclosed_returns.get("今年以来"),
            "累计收益率": disclosed_returns.get("累计收益率"),
            "最新业绩日期": latest_performance_date,
            "收益数据截至": disclosed_returns.get("收益数据截至"),
            "官方单位净值": disclosed_latest.get("官方单位净值") or round_or_none(official.get("披露单位净值"), 6),
            "官方累计收益": disclosed_returns.get("累计收益率") if disclosed_returns else round_or_none(official.get("披露累计收益率_百分比")),
            "自建累计收益": round_or_none((compare or {}).get("标准费前区间收益率_百分比") or (quality or {}).get("App展示同区间收益率_百分比") or (quality or {}).get("模拟费前累计收益率_百分比")),
            "与官方偏差": round_or_none((compare or {}).get("标准费前相对披露偏差_百分点") or deviation.get("App展示默认官方偏差_百分点") or (quality or {}).get("App展示官方收益差_百分点")),
            "最大回撤": (
                disclosed_risk.get("最大回撤")
                if disclosed_risk.get("最大回撤") is not None
                else (
                    None
                    if sparse_list_only_performance
                    else round_or_none(
                        (quality or {}).get("模拟最大回撤_百分比")
                        or official.get("最大回撤_百分比")
                    )
                )
            ),
            "当前回撤": None if sparse_list_only_performance else disclosed_risk.get("当前回撤"),
            "年化收益": (
                disclosed_returns.get("年化收益")
                if disclosed_returns.get("年化收益") is not None
                else round_or_none((quality or {}).get("模拟年化收益率_百分比"))
            ),
            "波动率": round_or_none(
                (quality or {}).get("模拟波动率_年化_百分比")
                if (quality or {}).get("模拟波动率_年化_百分比") is not None
                else disclosed_risk.get("波动率")
            ),
            "夏普比率": round_or_none(
                (quality or {}).get("模拟夏普_年化无风险0")
                if (quality or {}).get("模拟夏普_年化无风险0") is not None
                else disclosed_risk.get("夏普比率")
            ),
            "风险数据截至": disclosed_risk.get("风险数据截至"),
            "风险来源": disclosed_risk.get("风险来源"),
            "主可比池": classification["主可比池"],
            "市场地域": classification["市场地域"],
            "主动被动": classification["主动被动"],
            "特殊标签": classification["特殊标签"],
            "策略实现标签": classification["策略实现标签"],
            "权益基金权重": classification["权益基金权重"],
            "债券基金权重": classification["债券基金权重"],
            "货币基金权重": classification["货币基金权重"],
            "混合基金权重": classification["混合基金权重"],
            "QDII权重": classification["QDII权重"],
            "指数基金权重": classification["指数基金权重"],
            "主动基金权重": classification["主动基金权重"],
            "基准权益权重": classification["基准权益权重"],
            "基准债券权重": classification["基准债券权重"],
            "基准货币权重": classification["基准货币权重"],
            "基准风险资产权重": benchmark_bucket,
            **{field: classification.get(field) for field in BENCHMARK_ASSET_DISPLAY_FIELDS},
            "基准可用状态": classification["基准可用状态"],
            "业绩基准说明": benchmark_text,
            "业绩基准": benchmark_text,
            "业绩基准来源策略ID": benchmark_source_id or strategy_id,
            "业绩基准继承口径": "已验证母子关系共享官方业绩" if benchmark_is_inherited else "策略自身披露",
            "基础数据等级": classification["基础数据等级"],
            "费率状态": classification["费率状态"],
            "年化投顾费率": classification["年化投顾费率"],
            "分类依据": classification["分类依据"],
            "最新持仓日": holding_date,
            "持仓基金数": holding_fund_count,
            "洞察评价对象": insight_scope_label(
                data_completeness(compare, quality),
                latest_performance_date,
                global_latest_performance_dt,
                holding_fund_count,
            ),
            "最近调仓日": rebalance.get("latest_rebalance_date"),
            "调仓次数": rebalance_count,
            "单次平均换手率": round_or_none(rebalance_metric.get("avg_turnover")),
            "年化换手率": annual_turnover,
            "调仓频率": rebalance_frequency,
            "最近一年调仓次数": recent_rebalance_count,
            "质检情况": "；".join(f'{item["项目"]}:{item["结论"]}' for item in quality_checks),
            "detailFile": f"data/details/{safe_filename(strategy_id)}.js",
            "searchText": " ".join(
                clean_text(value, "")
                for value in [
                    strategy_id,
                    row["渠道策略ID"],
                    row["策略名称"],
                    row["投顾机构"],
                    channel.get("渠道名称"),
                    row["策略类型"],
                    row["风险等级"],
                    report_product_type,
                    report_stock_subtype,
                    classification["主可比池"],
                    display_fields["天天展示状态"],
                    classification["主可比池"],
                    classification["市场地域"],
                    classification["主动被动"],
                    classification["特殊标签"],
                    classification["策略实现标签"],
                    benchmark_bucket,
                    classification.get("多元策略标签"),
                    benchmark_text,
                ]
            ),
        }
        list_rows.append(current)
        context[strategy_id] = {
            "strategy": row,
            "channel": channel,
            "official": official,
            "quality": quality or {},
            "deviation": deviation,
            "compare": compare,
            "holdingAudit": audit or {},
            "holdingStats": holding,
            "historicalPositionStats": historical_position,
            "projectedHoldingStats": projected,
            "rebalanceStats": rebalance,
            "rebalanceMetrics": rebalance_metric,
            "disclosureStats": disclosure_stats,
            "standardStats": standard_stats,
            "qualityChecks": quality_checks,
            "classification": classification,
            "benchmarkStatus": benchmark_status,
            "benchmarkAsset": benchmark_asset_row,
            "governance": governance,
            "signalSummary": signal_summary,
            "strategyRelationship": relationship,
            "listRow": current,
        }
    log_progress(f"build strategy rows: rows ready={len(list_rows)}")
    return list_rows, context


def sample_series(rows: list[dict[str, Any]], date_key: str, value_key: str, max_points: int | None = 240, mode: str = "nav") -> list[dict[str, Any]]:
    clean_rows = [
        {"日期": row[date_key], "数值": round_or_none(row[value_key], 6), "模式": mode}
        for row in rows
        if row.get(date_key) and row.get(value_key) is not None
    ]
    if max_points is None or len(clean_rows) <= max_points:
        return clean_rows
    recent_count = min(RECENT_POINTS_TO_KEEP, max_points // 2, len(clean_rows))
    recent_rows = clean_rows[-recent_count:]
    older_rows = clean_rows[:-recent_count]
    older_budget = max(1, max_points - recent_count)
    step = math.ceil(len(older_rows) / older_budget) if older_rows else 1
    sampled = older_rows[::step] if older_rows else []
    if sampled and recent_rows and sampled[-1]["日期"] >= recent_rows[0]["日期"]:
        sampled = sampled[:-1]
    return sampled + recent_rows


def grouped_rows(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row[key]), []).append(row)
    return result


def load_signal_summary_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "信号策略事件"):
        return {}
    return {
        row["统一策略ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "统一策略ID",
                   COUNT(*) AS "信号事件数",
                   MAX("信号日期") AS "最近信号日",
                   SUM(COALESCE("指令数", 0)) AS "信号指令数",
                   SUM(COALESCE("买入指令数", 0)) AS "买入指令数",
                   SUM(COALESCE("卖出指令数", 0)) AS "卖出指令数",
                   SUM(COALESCE("加仓指令数", 0)) AS "加仓指令数",
                   SUM(COALESCE("减仓指令数", 0)) AS "减仓指令数",
                   AVG("胜率_1月") AS "信号胜率_1月",
                   AVG("胜率_3月") AS "信号胜率_3月",
                   AVG("胜率_6月") AS "信号胜率_6月",
                   AVG("胜率_1年") AS "信号胜率_1年",
                   AVG("加权方向收益_1月") AS "信号加权方向收益_1月",
                   AVG("加权方向收益_3月") AS "信号加权方向收益_3月",
                   AVG("加权方向收益_6月") AS "信号加权方向收益_6月",
                   AVG("加权方向收益_1年") AS "信号加权方向收益_1年"
            FROM "信号策略事件"
            GROUP BY "统一策略ID"
            """,
        )
    }


def load_signal_detail_maps(conn: sqlite3.Connection) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, list[dict[str, Any]]]]]:
    if not table_exists(conn, "信号策略事件") or not table_exists(conn, "信号策略基金指令"):
        return {}, {}
    event_rows = fetch_all(
        conn,
        """
        SELECT *
        FROM "信号策略事件"
        ORDER BY "统一策略ID", "信号日期" DESC, "信号时间" DESC, "原始事件序号" DESC
        """,
    )
    events_by_strategy: dict[str, list[dict[str, Any]]] = {
        sid: rows[:80]
        for sid, rows in grouped_rows(event_rows, "统一策略ID").items()
    }
    selected_event_ids = {
        clean_text(row.get("信号事件ID"), "")
        for rows in events_by_strategy.values()
        for row in rows
        if clean_text(row.get("信号事件ID"), "")
    }
    if not selected_event_ids:
        return events_by_strategy, {}
    placeholders = ",".join("?" for _ in selected_event_ids)
    instruction_rows = fetch_all(
        conn,
        f"""
        SELECT *
        FROM "信号策略基金指令"
        WHERE "信号事件ID" IN ({placeholders})
        ORDER BY "统一策略ID", "信号日期" DESC, "信号时间" DESC, ABS(COALESCE("权重变化_百分点", 0)) DESC, "基金代码"
        """,
        tuple(sorted(selected_event_ids)),
    )
    instructions_by_strategy_event: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in instruction_rows:
        sid = clean_text(row.get("统一策略ID"), "")
        event_id = clean_text(row.get("信号事件ID"), "")
        if not sid or not event_id:
            continue
        instructions_by_strategy_event.setdefault(sid, {}).setdefault(event_id, []).append(row)
    return events_by_strategy, instructions_by_strategy_event


def load_curve_map(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
    date_key: str,
    value_key: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    current_id: str | None = None
    buffer: list[dict[str, Any]] = []
    for sqlite_row in conn.execute(sql, params):
        row = dict(sqlite_row)
        strategy_id = str(row["统一策略ID"])
        if current_id is not None and strategy_id != current_id:
            result[current_id] = sample_series(buffer, date_key, value_key)
            buffer = []
        current_id = strategy_id
        buffer.append(row)
    if current_id is not None:
        result[current_id] = sample_series(buffer, date_key, value_key)
    return result


def load_series_map(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
    date_key: str,
    value_key: str,
    mode: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for sqlite_row in conn.execute(sql, params):
        row = dict(sqlite_row)
        value = round_or_none(row.get(value_key), 8)
        if row.get(date_key) and value is not None:
            result.setdefault(str(row["统一策略ID"]), []).append({"日期": row[date_key], "数值": value, "模式": mode})
    return result


def merge_series_by_date(primary: dict[str, list[dict[str, Any]]], secondary: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for strategy_id in set(primary) | set(secondary):
        by_date: dict[str, dict[str, Any]] = {}
        for row in secondary.get(strategy_id, []):
            date_text = str(row.get("日期") or "")
            if date_text:
                by_date[date_text] = row
        for row in primary.get(strategy_id, []):
            date_text = str(row.get("日期") or "")
            if date_text:
                by_date[date_text] = row
        result[strategy_id] = sorted(by_date.values(), key=lambda row: str(row.get("日期") or ""))
    return result


def normalize_between(value: float | None, base: float | None, mode: str) -> float | None:
    if value is None or base is None:
        return None
    if mode == "return":
        return round(value, 4)
    if mode == "return_pct":
        denominator = 1.0 + base / 100.0
        if denominator == 0:
            return None
        return round(((1.0 + value / 100.0) / denominator - 1.0) * 100.0, 4)
    if base == 0:
        return None
    return round((value / base - 1.0) * 100.0, 4)


def series_interval_return(series: list[dict[str, Any]], interval_type: str, amount: int | None = None) -> float | None:
    clean = [row for row in series if row.get("日期") and row.get("数值") is not None]
    if len(clean) < 2:
        return None
    latest = clean[-1]
    latest_date = parse_ymd(latest["日期"])
    if latest_date is None:
        return None
    if interval_type in {"days", "months"}:
        target = (
            calendar_months_ago(latest_date, int(amount or 0))
            if interval_type == "months"
            else latest_date - timedelta(days=int(amount or 0))
        )
        base = None
        for row in clean:
            date_value = parse_ymd(row["日期"])
            if date_value and date_value <= target:
                base = row
            elif date_value and date_value > target:
                break
        if base is None:
            return None
    elif interval_type == "ytd":
        year_start = latest_date.replace(month=1, day=1)
        base = None
        for row in clean:
            date_value = parse_ymd(row["日期"])
            if date_value and date_value <= year_start:
                base = row
            elif date_value and date_value > year_start:
                break
        if base is None:
            base = next((row for row in clean if (parse_ymd(row["日期"]) or latest_date) >= year_start), None)
        if base is None:
            return None
    else:
        base = clean[0]
    return normalize_between(as_float(latest["数值"]), as_float(base["数值"]), str(latest.get("模式") or "nav"))


def build_interval_matrix(
    series_by_name: dict[str, list[dict[str, Any]]],
    official_disclosure_fields: dict[str, float] | None = None,
    official_benchmark_fields: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"]:
        series = series_by_name.get(name) or []
        row: dict[str, Any] = {"口径": name}
        for label, interval_type, amount in DISPLAY_INTERVALS:
            row[label] = series_interval_return(series, interval_type, amount)
        if name == "披露业绩" and official_disclosure_fields:
            for field, value in official_disclosure_fields.items():
                if field in row:
                    row[field] = value
        if name == "基准业绩" and official_benchmark_fields:
            for field, value in official_benchmark_fields.items():
                if field in row:
                    row[field] = value
        rows.append(row)
    return rows


def build_annual_return_matrix(
    series_by_name: dict[str, list[dict[str, Any]]],
    official_disclosure_fields: dict[str, float] | None = None,
    official_latest_date: date | None = None,
) -> list[dict[str, Any]]:
    years: set[str] = set()
    by_series_year: dict[str, dict[str, float | None]] = {}
    for name in ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"]:
        series = sorted(
            [row for row in (series_by_name.get(name) or []) if row.get("日期") and row.get("数值") is not None],
            key=lambda item: str(item["日期"]),
        )
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in series:
            date_value = parse_ymd(row["日期"])
            if date_value:
                year = str(date_value.year)
                buckets.setdefault(year, []).append(row)
                years.add(year)
        by_series_year[name] = {}
        for year, rows in buckets.items():
            ordered = sorted(rows, key=lambda item: str(item["日期"]))
            end = ordered[-1]
            year_start = date(int(year), 1, 1)
            base = None
            for candidate in series:
                date_value = parse_ymd(candidate["日期"])
                if date_value and date_value <= year_start:
                    base = candidate
                elif date_value and date_value > year_start:
                    break
            if base is None:
                base = ordered[0]
            if base is end:
                by_series_year[name][year] = None
                continue
            mode = str(end.get("模式") or base.get("模式") or ordered[0].get("模式") or "nav")
            by_series_year[name][year] = normalize_between(as_float(end["数值"]), as_float(base["数值"]), mode)
        if name == "披露业绩" and official_disclosure_fields and official_latest_date:
            latest_year = str(official_latest_date.year)
            ytd = official_disclosure_fields.get("今年以来")
            if ytd is not None:
                years.add(latest_year)
                by_series_year[name][latest_year] = ytd
    output: list[dict[str, Any]] = []
    for year in sorted(years, reverse=True):
        row: dict[str, Any] = {"年度": year}
        for name in ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"]:
            row[name] = by_series_year.get(name, {}).get(year)
        output.append(row)
    return output


def load_latest_fund_nav_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金日度净值"):
        return {}
    return {
        row["基金代码"]: row
        for row in fetch_all(
            conn,
            """
            WITH ranked AS (
                SELECT "基金代码", "交易日期", "单位净值", "日收益率_百分比",
                       ROW_NUMBER() OVER (PARTITION BY "基金代码" ORDER BY "交易日期" DESC) AS rn
                FROM "基金日度净值"
                WHERE "单位净值" IS NOT NULL
            )
            SELECT "基金代码", "交易日期", "单位净值", "日收益率_百分比"
            FROM ranked
            WHERE rn = 1
            """,
        )
    }


def load_fund_rank_bucket_map(conn: sqlite3.Connection) -> dict[str, str]:
    buckets: dict[str, str] = {}
    if table_exists(conn, "基金标准分类字典"):
        for row in fetch_all(
            conn,
            """
            SELECT "基金代码", "投顾资产分类桶", "天天基金细分类", "天天基金大类",
                   "标准资产细类", "标准资产大类", "天天基金二级分类"
            FROM "基金标准分类字典"
            WHERE "基金代码" IS NOT NULL
            """,
        ):
            code = clean_text(row.get("基金代码"), "")
            if not code:
                continue
            for key in ["投顾资产分类桶", "天天基金细分类", "天天基金大类", "标准资产细类", "标准资产大类", "天天基金二级分类"]:
                value = clean_text(row.get(key), "")
                if value and value not in ("unknown", "other", "未披露"):
                    buckets[code] = value
                    break
    if table_exists(conn, "基金信息"):
        for row in fetch_all(
            conn,
            """
            SELECT "基金代码", "基金类型"
            FROM "基金信息"
            WHERE "基金代码" IS NOT NULL
            """,
        ):
            code = clean_text(row.get("基金代码"), "")
            value = clean_text(row.get("基金类型"), "")
            if code and value and code not in buckets:
                buckets[code] = value
    return buckets


def load_fund_return_rank_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金日度净值"):
        return {}
    latest = fetch_one(
        conn,
        """
        SELECT MAX("交易日期") AS latest_date
        FROM "基金日度净值"
        WHERE "单位净值" IS NOT NULL OR "累计净值" IS NOT NULL OR "日收益率_百分比" IS NOT NULL
        """,
    )
    latest_date = clean_text((latest or {}).get("latest_date"), "")
    latest_dt = parse_ymd(latest_date)
    if latest_dt is None:
        return {}
    fund_rows = fetch_all(
        conn,
        """
        SELECT DISTINCT "基金代码"
        FROM "基金日度净值"
        WHERE "基金代码" IS NOT NULL
        """,
    )
    fund_codes = {clean_text(row.get("基金代码"), "") for row in fund_rows if clean_text(row.get("基金代码"), "")}
    if not fund_codes:
        return {}
    start_date = (latest_dt - timedelta(days=max(days for _, days in FUND_RANK_PERIODS) + 45)).strftime("%Y-%m-%d")
    nav_cache = load_fund_nav_cache(conn, fund_codes, start_date, latest_date)
    bucket_map = load_fund_rank_bucket_map(conn)
    rank_map: dict[str, dict[str, Any]] = {
        code: {"基金同类分组": bucket_map.get(code, "未分类")}
        for code in fund_codes
    }
    grouped_returns: dict[tuple[str, str], list[tuple[str, float]]] = {}

    for code, series in nav_cache.items():
        if len(series) < 2:
            continue
        end_date, end_value = series[-1]
        if end_value <= 0:
            continue
        bucket = bucket_map.get(code, "未分类")
        for label, days in FUND_RANK_PERIODS:
            target_date = date_offset_text(end_date, -days)
            if not target_date:
                continue
            base_point = point_on_or_before(series, target_date) or point_on_or_after(series, target_date)
            if not base_point or base_point[1] <= 0 or base_point[0] == end_date:
                continue
            fund_return = (end_value / base_point[1] - 1.0) * 100.0
            rank_map.setdefault(code, {"基金同类分组": bucket})[f"{label}收益"] = round_or_none(fund_return)
            grouped_returns.setdefault((label, bucket), []).append((code, fund_return))

    for (label, bucket), values in grouped_returns.items():
        ordered = sorted(values, key=lambda item: item[1], reverse=True)
        sample_count = len(ordered)
        last_return: float | None = None
        last_rank = 0
        for index, (code, fund_return) in enumerate(ordered, start=1):
            if last_return is None or not math.isclose(fund_return, last_return, rel_tol=0, abs_tol=1e-10):
                last_rank = index
                last_return = fund_return
            item = rank_map.setdefault(code, {"基金同类分组": bucket})
            item[f"{label}同类排名"] = last_rank
            item[f"{label}同类样本数"] = sample_count
            item[f"{label}同类前50%"] = last_rank <= math.ceil(sample_count * 0.5)
    return rank_map


def add_fund_rank_fields(row: dict[str, Any], fund_code: Any, fund_rank_map: dict[str, dict[str, Any]]) -> None:
    rank = fund_rank_map.get(clean_text(fund_code, ""))
    if not rank:
        return
    row["基金同类分组"] = rank.get("基金同类分组")
    for label, _days in FUND_RANK_PERIODS:
        for suffix in ["收益", "同类排名", "同类样本数", "同类前50%"]:
            key = f"{label}{suffix}"
            if key in rank:
                row[key] = rank[key]


def fund_family_name(value: Any) -> str:
    text = clean_text(value, "")
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    return re.sub(r"(?:A/E|A类|B类|C类|D类|E类|I类|Y类|A|B|C|D|E|I|Y)$", "", text)


def load_fund_nav_profiles(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    if not table_exists(conn, "基金日度净值"):
        return {}
    rows = fetch_all(
        conn,
        """
        SELECT "基金代码", "基金名称", MIN("交易日期") AS "最早净值日", MAX("交易日期") AS "最新净值日", COUNT(*) AS "净值行数"
        FROM "基金日度净值"
        WHERE "基金代码" IS NOT NULL AND "基金名称" IS NOT NULL
        GROUP BY "基金代码", "基金名称"
        """,
    )
    profiles: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = fund_family_name(row.get("基金名称"))
        if not family:
            continue
        profiles.setdefault(family, []).append(row)
    for values in profiles.values():
        values.sort(key=lambda item: (clean_text(item.get("最早净值日"), "9999-99-99"), -int(item.get("净值行数") or 0), clean_text(item.get("基金代码"), "")))
    return profiles


def build_rebalance_metric_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, rows in grouped_rows(fetch_rebalance_metric_events(conn), "统一策略ID").items():
        dates = [clean_text(row.get("调仓日期"), "") for row in rows if clean_text(row.get("调仓日期"), "")]
        turnovers = [as_float(row.get("event_turnover")) for row in rows]
        positive_turnovers = [value for value in turnovers if value is not None and value > 0]
        result[strategy_id] = {
            "统一策略ID": strategy_id,
            "event_count": len(rows),
            "first_rebalance_date": min(dates) if dates else None,
            "latest_rebalance_date": max(dates) if dates else None,
            "total_turnover": sum(positive_turnovers) if positive_turnovers else None,
            "avg_turnover": (sum(positive_turnovers) / len(positive_turnovers)) if positive_turnovers else None,
        }
    return result


def build_historical_position_stats_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Describe complete historical positions without equating signal ratios with portfolio weights."""

    result: dict[str, dict[str, Any]] = {}
    if table_exists(conn, "策略历史持仓"):
        rows = fetch_all(
            conn,
            '''
            WITH snapshots AS (
                SELECT "统一策略ID", "历史快照ID", MIN("持仓日期") AS position_date,
                       COUNT(*) AS row_count,
                       SUM(CASE WHEN "是否精确权重"=1 AND "基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS exact_count,
                       SUM(COALESCE("基金权重_百分比", 0)) AS weight_sum
                FROM "策略历史持仓"
                GROUP BY "统一策略ID", "历史快照ID"
            )
            SELECT "统一策略ID",
                   COUNT(*) AS explicit_snapshot_count,
                   SUM(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN 1 ELSE 0 END) AS complete_explicit_snapshot_count,
                   MIN(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN position_date END) AS explicit_first_date,
                   MAX(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN position_date END) AS explicit_latest_date
            FROM snapshots
            GROUP BY "统一策略ID"
            ''',
        )
        for row in rows:
            result[str(row["统一策略ID"])] = dict(row)
    if table_exists(conn, "策略调仓事件") and table_exists(conn, "策略调仓明细"):
        rows = fetch_all(
            conn,
            '''
            WITH snapshots AS (
                SELECT e."统一策略ID", e."调仓事件ID", e."调仓日期",
                       COUNT(*) AS row_count,
                       SUM(CASE WHEN d."调后权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS exact_count,
                       SUM(COALESCE(d."调后权重_百分比", 0)) AS weight_sum
                FROM "策略调仓事件" e
                JOIN "策略调仓明细" d ON d."调仓事件ID"=e."调仓事件ID"
                GROUP BY e."统一策略ID", e."调仓事件ID", e."调仓日期"
            )
            SELECT "统一策略ID",
                   SUM(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN 1 ELSE 0 END) AS complete_rebalance_position_count,
                   MIN(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN "调仓日期" END) AS rebalance_first_date,
                   MAX(CASE WHEN exact_count=row_count AND weight_sum BETWEEN 99 AND 101 THEN "调仓日期" END) AS rebalance_latest_date
            FROM snapshots
            GROUP BY "统一策略ID"
            ''',
        )
        for row in rows:
            target = result.setdefault(str(row["统一策略ID"]), {})
            target.update(dict(row))
    for item in result.values():
        explicit = int(item.get("complete_explicit_snapshot_count") or 0)
        rebalance = int(item.get("complete_rebalance_position_count") or 0)
        sources = []
        if explicit:
            sources.append("官方历史仓位快照")
        if rebalance:
            sources.append("完整调仓后仓位")
        item["has_complete_history"] = bool(sources)
        item["history_source"] = "＋".join(sources)
        dates = [
            clean_text(item.get("explicit_first_date"), ""),
            clean_text(item.get("rebalance_first_date"), ""),
        ]
        dates = [value for value in dates if value]
        item["history_first_date"] = min(dates) if dates else None
        dates = [
            clean_text(item.get("explicit_latest_date"), ""),
            clean_text(item.get("rebalance_latest_date"), ""),
        ]
        dates = [value for value in dates if value]
        item["history_latest_date"] = max(dates) if dates else None
    return result


def build_institution_adjustment_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    regular = fetch_all(
        conn,
        '''SELECT "调仓事件ID" AS event_id, "统一策略ID", "调仓日期" AS event_date,
                  '普通调仓' AS event_type, "调仓标题" AS event_title, "调仓原因" AS event_reason,
                  '' AS event_summary
           FROM "策略调仓事件" WHERE "调仓日期" IS NOT NULL''',
    ) if table_exists(conn, "策略调仓事件") else []
    signal = fetch_all(
        conn,
        '''SELECT "信号事件ID" AS event_id, "统一策略ID", "信号日期" AS event_date,
                  '发车信号' AS event_type, "信号标题" AS event_title, "信号原因" AS event_reason,
                  "信号摘要" AS event_summary
           FROM "信号策略事件" WHERE "信号日期" IS NOT NULL''',
    ) if table_exists(conn, "信号策略事件") else []
    rows = [*regular, *signal]
    latest = max((clean_text(row.get("event_date"), "") for row in rows), default="")
    latest_dt = parse_ymd(latest)
    cutoff = latest_dt - timedelta(days=30) if latest_dt else None
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        event_date = parse_ymd(row.get("event_date"))
        if not event_date or (cutoff and event_date < cutoff):
            continue
        key = (clean_text(row.get("event_type"), ""), clean_text(row.get("event_id"), ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "事件ID": key[1],
                "统一策略ID": clean_text(row.get("统一策略ID"), ""),
                "调整日期": event_date.isoformat(),
                "事件类型": key[0],
                "调整说明": clean_text(
                    row.get("event_reason") or row.get("event_title") or row.get("event_summary"),
                    key[0],
                ),
            }
        )
    return sorted(output, key=lambda row: (row["调整日期"], row["统一策略ID"], row["事件ID"]))


def build_rebalance_insight_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "策略调仓事件") or not table_exists(conn, "策略调仓明细"):
        return []
    latest = fetch_one(
        conn,
        'SELECT MAX("调仓日期") AS "latest_date" FROM "策略调仓事件" WHERE "调仓日期" IS NOT NULL',
    )
    latest_dt = parse_ymd((latest or {}).get("latest_date"))
    cutoff = (latest_dt - timedelta(days=370)).isoformat() if latest_dt else "1900-01-01"
    rows = fetch_all(
        conn,
        """
        WITH detail AS (
            SELECT d."调仓事件ID",
                   COUNT(*) AS "调仓基金数",
                   SUM(CASE WHEN COALESCE(d."权重变化_百分比", 0) > 0 THEN COALESCE(d."权重变化_百分比", 0) ELSE 0 END) AS "加仓权重合计",
                   SUM(CASE WHEN COALESCE(d."权重变化_百分比", 0) < 0 THEN -COALESCE(d."权重变化_百分比", 0) ELSE 0 END) AS "减仓权重合计",
                   SUM(ABS(COALESCE(d."权重变化_百分比", COALESCE(d."调后权重_百分比", 0) - COALESCE(d."调前权重_百分比", 0)))) / 2.0 AS "单次换手率",
                   SUM(CASE WHEN COALESCE(d."调后权重_百分比", 0) > 0 THEN COALESCE(d."调后权重_百分比", 0) ELSE 0 END) AS "调后权重和",
                   GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(d."分组名称", '')), '')) AS "涉及资产"
            FROM "策略调仓明细" d
            GROUP BY d."调仓事件ID"
        ),
        quality AS (
            SELECT q."调仓事件ID",
                   MAX(q."调仓超额_百分比") AS "调仓超额",
                   MAX(q."胜负") AS "胜负",
                   MAX(q."结果评价") AS "结果评价",
                   MAX(q."方向性超额_百分比") AS "方向性超额",
                   MAX(q."最优贡献基金") AS "最优贡献基金",
                   MAX(q."最差贡献基金") AS "最差贡献基金"
            FROM "调仓质量事件分析" q
            GROUP BY q."调仓事件ID"
        )
        SELECT e."调仓事件ID",
               e."统一策略ID",
               e."渠道ID",
               COALESCE(c."渠道名称", e."渠道ID") AS "渠道",
               e."渠道策略ID",
               s."策略名称",
               s."投顾机构",
               e."调仓日期",
               e."披露日期",
               e."调仓标题",
               e."调仓原因",
               COALESCE(d."调仓基金数", 0) AS "调仓基金数",
               d."加仓权重合计",
               d."减仓权重合计",
               d."单次换手率",
               d."调后权重和",
               d."涉及资产",
               q."调仓超额",
               q."胜负",
               q."结果评价",
               q."方向性超额",
               q."最优贡献基金",
               q."最差贡献基金"
        FROM "策略调仓事件" e
        LEFT JOIN "策略信息" s ON s."统一策略ID" = e."统一策略ID"
        LEFT JOIN "渠道信息" c ON c."渠道ID" = e."渠道ID"
        LEFT JOIN detail d ON d."调仓事件ID" = e."调仓事件ID"
        LEFT JOIN quality q ON q."调仓事件ID" = e."调仓事件ID"
        WHERE e."调仓日期" IS NOT NULL AND e."调仓日期" >= ?
        ORDER BY e."调仓日期" DESC, COALESCE(e."事件序号", 0) DESC, e."调仓事件ID" DESC
        LIMIT 2500
        """,
        (cutoff,),
    )
    rows = sorted(
        dedupe_rebalance_event_rows(rows),
        key=lambda row: (
            -(parse_ymd(row.get("调仓日期")).toordinal() if parse_ymd(row.get("调仓日期")) else 0),
            clean_text(row.get("统一策略ID"), ""),
            clean_text(row.get("调仓事件ID"), ""),
        ),
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "调仓事件ID": row.get("调仓事件ID"),
                "统一策略ID": row.get("统一策略ID"),
                "渠道ID": row.get("渠道ID"),
                "渠道": clean_text(row.get("渠道"), ""),
                "渠道策略ID": row.get("渠道策略ID"),
                "策略名称": clean_text(row.get("策略名称"), ""),
                "投顾机构": clean_text(row.get("投顾机构"), ""),
                "调仓日期": row.get("调仓日期"),
                "披露日期": row.get("披露日期"),
                "调仓标题": clean_text(row.get("调仓标题"), ""),
                "调仓原因": clean_text(row.get("调仓原因"), ""),
                "调仓基金数": int(row.get("调仓基金数") or 0),
                "加仓权重合计": round_or_none(row.get("加仓权重合计")),
                "减仓权重合计": round_or_none(row.get("减仓权重合计")),
                "单次换手率": round_or_none(row.get("单次换手率")),
                "调后权重和": round_or_none(row.get("调后权重和")),
                "涉及资产": clean_text(row.get("涉及资产"), ""),
                "调仓超额": round_or_none(row.get("调仓超额")),
                "胜负": clean_text(row.get("胜负"), ""),
                "结果评价": clean_text(row.get("结果评价"), ""),
                "方向性超额": round_or_none(row.get("方向性超额")),
                "最优贡献基金": clean_text(row.get("最优贡献基金"), ""),
                "最差贡献基金": clean_text(row.get("最差贡献基金"), ""),
            }
        )
    return output


REBALANCE_EVENT_STRATEGY_FIELDS = [
    "研报产品类型",
    "研报股票子类型",
    "业务分类",
    "业务分类依据",
    "风险等级",
    "披露风险等级",
    "策略类型",
    "披露策略类型",
    "市场地域",
    "主动被动",
    "主可比池",
    "特殊标签",
    "策略实现标签",
    "天天当前对客展示",
    "天天展示状态",
    "天天展示判定依据",
    "运作状态",
    "数据完整性",
    "洞察评价对象",
    "最新业绩日期",
    "收益数据截至",
    "最新持仓日",
    "最近调仓日",
    "调仓次数",
    "近一周",
    "近一月",
    "近三月",
    "近6月",
    "近1年",
    "今年以来",
    "累计收益率",
    "最大回撤",
    "当前回撤",
    "年化收益",
    "波动率",
    "夏普比率",
    "权益基金权重",
    "债券基金权重",
    "货币基金权重",
    "混合基金权重",
    "QDII权重",
    "指数基金权重",
    "主动基金权重",
]


def enrich_rebalance_events_with_strategy_fields(
    events: list[dict[str, Any]],
    context: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    list_rows = {
        strategy_id: ctx.get("listRow") or {}
        for strategy_id, ctx in context.items()
    }
    enriched_events: list[dict[str, Any]] = []
    for event in events:
        strategy_id = clean_text(event.get("统一策略ID"), "")
        list_row = list_rows.get(strategy_id) or {}
        enriched = dict(event)
        if list_row:
            for field in REBALANCE_EVENT_STRATEGY_FIELDS:
                value = list_row.get(field)
                if value is not None and value != "":
                    enriched[field] = value
            for field in ["策略名称", "渠道", "渠道策略ID"]:
                if not clean_text(enriched.get(field), "") and list_row.get(field):
                    enriched[field] = list_row.get(field)
            # Institution-based rebalance rankings must use the same canonical
            # name as the strategy list.  Keeping a non-empty raw alias here
            # would split one manager into multiple rows downstream.
            source_channel_id = strategy_id.split("__", 1)[0]
            enriched["投顾机构"] = canonical_advisor_institution(
                list_row.get("投顾机构") or enriched.get("投顾机构"),
                source_channel_id,
                list_row.get("渠道") or enriched.get("渠道"),
            )
            gf_text = f'{clean_text(list_row.get("投顾机构"), "")} {clean_text(list_row.get("渠道"), "")}'
            enriched["是否广发"] = "是" if re.search(r"广发基金|广发投顾", gf_text) else "否"
        else:
            source_channel_id = strategy_id.split("__", 1)[0]
            enriched["投顾机构"] = canonical_advisor_institution(
                enriched.get("投顾机构"),
                source_channel_id,
                enriched.get("渠道"),
            )
        enriched_events.append(enriched)
    return enriched_events


def sample_pre_rebalance_line(start_date: str | None, end_date: str | None, end_return: Any, reference_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    end_value = round_or_none(end_return)
    if not start_date or not end_date or end_value is None:
        return []
    reference_points = [
        {"日期": str(row["日期"]), "数值": as_float(row.get("数值"))}
        for row in reference_series
        if start_date <= str(row.get("日期")) <= end_date and row.get("数值") is not None
    ]
    reference_points = [row for row in reference_points if row["数值"] is not None]
    if len(reference_points) >= 2:
        if reference_points[0]["日期"] != start_date:
            reference_points.insert(0, {"日期": start_date, "数值": reference_points[0]["数值"]})
        if reference_points[-1]["日期"] != end_date:
            reference_points.append({"日期": end_date, "数值": reference_points[-1]["数值"]})
        start_value = as_float(reference_points[0]["数值"]) or 0.0
        final_reference = as_float(reference_points[-1]["数值"]) or 0.0
        denominator = final_reference - start_value
        if abs(denominator) > 1e-8:
            return [
                {"日期": row["日期"], "数值": round(end_value * ((as_float(row["数值"]) or 0.0) - start_value) / denominator, 4), "模式": "return"}
                for row in reference_points
            ]
    dates = [row["日期"] for row in reference_series if start_date <= str(row.get("日期")) <= end_date]
    if not dates:
        dates = [start_date, end_date]
    if dates[0] != start_date:
        dates.insert(0, start_date)
    if dates[-1] != end_date:
        dates.append(end_date)
    if len(dates) == 1:
        dates.append(end_date)
    result: list[dict[str, Any]] = []
    denominator = max(1, len(dates) - 1)
    for index, date_value in enumerate(dates):
        result.append({"日期": date_value, "数值": round(end_value * index / denominator, 4), "模式": "return"})
    return result


def normalize_segment(series: list[dict[str, Any]], start_date: str | None, end_date: str | None, max_points: int = 160) -> list[dict[str, Any]]:
    if not start_date or not end_date:
        return []
    segment = [row for row in series if start_date <= str(row.get("日期")) <= end_date and row.get("数值") is not None]
    if not segment:
        return []
    base = as_float(segment[0]["数值"])
    mode = str(segment[0].get("模式") or "nav")
    normalized = [
        {"日期": row["日期"], "数值": normalize_between(as_float(row["数值"]), base, mode), "模式": "return"}
        for row in segment
    ]
    normalized = [row for row in normalized if row["数值"] is not None]
    return sample_series(normalized, "日期", "数值", max_points=max_points, mode="return")


def date_offset_text(value: str | None, days: int) -> str | None:
    parsed = parse_ymd(value)
    if parsed is None:
        return None
    return (parsed + timedelta(days=days)).strftime("%Y-%m-%d")


def load_fund_nav_cache(conn: sqlite3.Connection, fund_codes: set[str], start_date: str | None, end_date: str | None) -> dict[str, list[tuple[str, float]]]:
    if not fund_codes:
        return {}
    lower = date_offset_text(start_date, -45) if start_date else None
    upper = date_offset_text(end_date, 7) if end_date else None
    ordered_codes = sorted(code for code in fund_codes if code)
    cache: dict[str, list[tuple[str, float]]] = {code: [] for code in ordered_codes}
    for index in range(0, len(ordered_codes), 400):
        chunk = ordered_codes[index : index + 400]
        placeholders = ",".join("?" for _ in chunk)
        params: list[Any] = list(chunk)
        date_filter = ""
        if lower:
            date_filter += ' AND "交易日期" >= ?'
            params.append(lower)
        if upper:
            date_filter += ' AND "交易日期" <= ?'
            params.append(upper)
        rows = fetch_all(
            conn,
            f"""
            SELECT "基金代码", "交易日期", "净值口径", "单位净值", "累计净值", "复权净值", "日收益率_百分比", "是否货币基金"
            FROM "基金日度净值"
            WHERE "基金代码" IN ({placeholders}) {date_filter}
            ORDER BY "基金代码", "交易日期"
            """,
            tuple(params),
        )
        last_code: str | None = None
        money_factor = 1.0
        previous_value: float | None = None
        previous_raw_value: float | None = None
        for row in rows:
            code = str(row["基金代码"])
            if code != last_code:
                money_factor = 1.0
                previous_value = None
                previous_raw_value = None
                last_code = code
            is_money = int(row.get("是否货币基金") or 0) == 1 or clean_text(row.get("净值口径"), "") == "货币基金收益"
            adjusted_value = as_float(row.get("复权净值"))
            raw_value = as_float(row.get("累计净值")) or as_float(row.get("单位净值"))
            daily_return = as_float(row.get("日收益率_百分比"))
            if adjusted_value is not None:
                value = adjusted_value
            elif previous_value is not None:
                # Incremental source rows can arrive before their adjusted NAV is
                # rebuilt. Continue the existing adjusted series with the daily
                # return (or raw NAV ratio) instead of switching from an adjusted
                # index around 100 to a unit NAV around 1.
                if daily_return is not None and daily_return > -100:
                    value = previous_value * (1.0 + daily_return / 100.0)
                elif raw_value is not None and previous_raw_value is not None and previous_raw_value > 0:
                    value = previous_value * raw_value / previous_raw_value
                else:
                    value = None
            elif is_money:
                if daily_return is not None and daily_return > -100:
                    money_factor *= 1.0 + daily_return / 100.0
                value = money_factor
            else:
                value = raw_value
            if row.get("交易日期") and value is not None and value > 0:
                cache.setdefault(code, []).append((row["交易日期"], value))
                previous_value = value
            if raw_value is not None and raw_value > 0:
                previous_raw_value = raw_value
    return cache


def value_on_or_before(series: list[tuple[str, float]], date_value: str) -> float | None:
    left = 0
    right = len(series)
    while left < right:
        middle = (left + right) // 2
        if series[middle][0] <= date_value:
            left = middle + 1
        else:
            right = middle
    index = left - 1
    return series[index][1] if index >= 0 else None


def point_on_or_before(series: list[tuple[str, float]], date_value: str) -> tuple[str, float] | None:
    left = 0
    right = len(series)
    while left < right:
        middle = (left + right) // 2
        if series[middle][0] <= date_value:
            left = middle + 1
        else:
            right = middle
    index = left - 1
    return series[index] if index >= 0 else None


def point_on_or_after(series: list[tuple[str, float]], date_value: str) -> tuple[str, float] | None:
    left = 0
    right = len(series)
    while left < right:
        middle = (left + right) // 2
        if series[middle][0] < date_value:
            left = middle + 1
        else:
            right = middle
    return series[left] if left < len(series) else None


def fund_interval_return(
    series: list[tuple[str, float]],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    if not series or not start_date or not end_date:
        return {}
    start_point = point_on_or_before(series, start_date) or point_on_or_after(series, start_date)
    end_point = point_on_or_before(series, end_date)
    if start_point is None or end_point is None:
        return {}
    start_date_used, start_value = start_point
    end_date_used, end_value = end_point
    if start_value <= 0 or end_value <= 0 or end_date_used < start_date_used:
        return {}
    return {
        "收益率": round_or_none((end_value / start_value - 1.0) * 100.0),
        "起始日期": start_date_used,
        "结束日期": end_date_used,
        "期末净值": round_or_none(end_value, 6),
    }


def weighted_fund_return_series(
    rows: list[dict[str, Any]],
    weight_key: str,
    start_date: str | None,
    end_date: str | None,
    fund_nav_cache: dict[str, list[tuple[str, float]]],
    reference_series: list[dict[str, Any]] | None = None,
    max_points: int = 160,
) -> list[dict[str, Any]]:
    if not start_date or not end_date:
        return []
    weights: dict[str, float] = {}
    for row in rows:
        code = clean_text(row.get("基金代码_分析") or row.get("基金代码"), "")
        weight = as_float(row.get(weight_key))
        if not code or weight is None or weight <= 0:
            continue
        weights[code] = weights.get(code, 0.0) + weight
    if not weights:
        return []
    total_weight = sum(weights.values())
    scale = 100.0 / total_weight if total_weight > 105.0 else 1.0
    base_values = {code: value_on_or_before(fund_nav_cache.get(code, []), start_date) for code in weights}
    if not any(value is not None for value in base_values.values()):
        return []
    date_set = {start_date, end_date}
    if reference_series:
        date_set.update(str(row["日期"]) for row in reference_series if start_date <= str(row.get("日期")) <= end_date)
    else:
        longest_series = max((fund_nav_cache.get(code, []) for code in weights), key=len, default=[])
        date_set.update(date for date, _ in longest_series if start_date <= date <= end_date)
    points: list[dict[str, Any]] = []
    for date_value in sorted(date_set):
        portfolio_return = 0.0
        used_any = False
        for code, weight in weights.items():
            base = base_values.get(code)
            series = fund_nav_cache.get(code, [])
            current = value_on_or_before(series, date_value) if series else None
            if base is None or current is None or base <= 0:
                continue
            portfolio_return += (weight * scale / 100.0) * (current / base - 1.0)
            used_any = True
        if used_any:
            points.append({"日期": date_value, "数值": round(portfolio_return * 100.0, 4), "模式": "return"})
    if not points or points[0]["日期"] != start_date:
        points.insert(0, {"日期": start_date, "数值": 0.0, "模式": "return"})
    return sample_series(points, "日期", "数值", max_points=max_points, mode="return")


def latest_evaluable_event_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Select the newest evaluable quality event for every strategy."""

    selected: set[str] = set()
    seen_strategies: set[str] = set()
    ordered = sorted(
        rows,
        key=lambda row: (
            clean_text(row.get("统一策略ID"), ""),
            clean_text(row.get("调仓日期"), ""),
            clean_text(row.get("调仓事件ID"), ""),
        ),
        reverse=True,
    )
    for row in ordered:
        strategy_id = clean_text(row.get("统一策略ID"), "")
        event_id = clean_text(row.get("调仓事件ID"), "")
        if (
            not strategy_id
            or strategy_id in seen_strategies
            or not event_id
            or clean_text(row.get("评估状态"), "") != "可评估"
        ):
            continue
        selected.add(event_id)
        seen_strategies.add(strategy_id)
    return selected


def fund_nav_weight_coverage(
    rows: list[dict[str, Any]],
    weight_key: str,
    start_date: str | None,
    end_date: str | None,
    fund_nav_cache: dict[str, list[tuple[str, float]]],
) -> float:
    if not start_date or not end_date:
        return 0.0
    total_weight = 0.0
    covered_weight = 0.0
    for row in rows:
        weight = as_float(row.get(weight_key))
        code = clean_text(row.get("基金代码_分析") or row.get("基金代码"), "")
        if not code or weight is None or weight <= 0:
            continue
        total_weight += weight
        series = fund_nav_cache.get(code, [])
        start_value = value_on_or_before(series, start_date)
        end_value = value_on_or_before(series, end_date)
        if start_value is not None and end_value is not None and start_value > 0 and end_value > 0:
            covered_weight += weight
    return round(covered_weight / total_weight * 100.0, 4) if total_weight > 0 else 0.0


def load_index_levels(conn: sqlite3.Connection, index_code: str = "000300.SH") -> list[dict[str, Any]]:
    if not table_exists(conn, "指数日度行情"):
        return []
    rows = fetch_all(
        conn,
        """
        SELECT "交易日期", "收盘点位" AS value, "数据来源"
        FROM "指数日度行情"
        WHERE "指数代码" = ? AND "收盘点位" IS NOT NULL
        ORDER BY "交易日期"
        """,
        (index_code,),
    )
    return [
        {"日期": row["交易日期"], "数值": round_or_none(row["value"], 6), "模式": "nav", "数据来源": row["数据来源"]}
        for row in rows
        if row.get("交易日期") and row.get("value") is not None
    ]


def collect_strategy_dates(*series_maps: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    dates: dict[str, set[str]] = {}
    for series_map in series_maps:
        for strategy_id, rows in series_map.items():
            bucket = dates.setdefault(strategy_id, set())
            for row in rows:
                if row.get("日期"):
                    bucket.add(str(row["日期"]))
    return {strategy_id: sorted(values) for strategy_id, values in dates.items()}


def align_index_series(dates_by_strategy: dict[str, list[str]], index_levels: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not index_levels:
        return {}
    index_rows = [(row["日期"], row["数值"]) for row in index_levels if row.get("日期") and row.get("数值") is not None]
    result: dict[str, list[dict[str, Any]]] = {}
    for strategy_id, dates in dates_by_strategy.items():
        pointer = 0
        last_value: float | None = None
        rows: list[dict[str, Any]] = []
        for date_value in dates:
            while pointer < len(index_rows) and index_rows[pointer][0] <= date_value:
                last_value = as_float(index_rows[pointer][1])
                pointer += 1
            if last_value is not None:
                rows.append({"日期": date_value, "数值": round(last_value, 6), "模式": "nav"})
        if rows:
            result[strategy_id] = rows
    return result


BENCHMARK_INDEX_COMPONENTS: list[dict[str, Any]] = [
    {"code": "000918.SH", "name": "沪深300成长", "aliases": ["沪深300成长", "000918"]},
    {"code": "000300.SH", "name": "沪深300", "aliases": ["沪深300", "CSI300", "000300"]},
    {"code": "000001.SH", "name": "上证综合", "aliases": ["上证综合指数", "上证综指", "上证指数", "000001"]},
    {"code": "000015.SH", "name": "上证红利", "aliases": ["上证红利", "000015"]},
    {"code": "000922.CSI", "name": "中证红利", "aliases": ["中证红利", "000922"]},
    {"code": "000906.SH", "name": "中证800", "aliases": ["中证800", "000906"]},
    {"code": "000905.SH", "name": "中证500", "aliases": ["中证500", "000905"]},
    {"code": "000852.SH", "name": "中证1000", "aliases": ["中证1000", "000852"]},
    {"code": "000510.SH", "name": "中证A500", "aliases": ["中证A500指数", "中证A500", "000510"]},
    {"code": "000985.CSI", "name": "中证全指", "aliases": ["中证全指指数", "中证全指", "000985"]},
    {"code": "930903.CSI", "name": "中证A股", "aliases": ["中证A股指数", "中证 A 股指数", "中证A股", "中证 A 股", "930903"]},
    {"code": "000993.CSI", "name": "中证全指信息", "aliases": ["中证全指信息指数", "中证全指信息", "000993"]},
    {"code": "399006.SZ", "name": "创业板指", "aliases": ["创业板指", "399006"]},
    {"code": "H30318.CSI", "name": "TMT150", "aliases": ["TMT150指数", "TMT150", "H30318"]},
    {"code": "000698.SH", "name": "科创100", "aliases": ["科创100", "000698"]},
    {"code": "000171.SH", "name": "中国战略新兴产业", "aliases": ["中国战略新兴", "000171"]},
    {"code": "000941.SH", "name": "中证新能源", "aliases": ["中证新能源指数", "中证新能源", "新能源指数", "000941"]},
    {"code": "399967.SZ", "name": "中证军工", "aliases": ["中证军工", "399967"]},
    {"code": "000998.SH", "name": "中证TMT", "aliases": ["中证TMT", "000998"]},
    {"code": "000942.SH", "name": "中证内地消费主题", "aliases": ["中证内地消费", "000942"]},
    {"code": "000933.SH", "name": "中证医药卫生", "aliases": ["中证医药卫生", "中证医药指数", "中证医药", "000933"]},
    {"code": "000827.SH", "name": "中证环保", "aliases": ["中证环保", "000827"]},
    {"code": "000979.CSI", "name": "中证大宗商品股票", "aliases": ["中证大宗商品股票", "中证大宗商品", "000979"]},
    {"code": "H30009.CSI", "name": "中证商品CFI", "aliases": ["中证商品 CFI", "中证商品CFI指数", "中证商品CFI", "商品CFI", "大宗商品指数", "大宗商品", "H30009"]},
    {"code": "H11061.CSI", "name": "中证商品期货综合(CFCI)", "aliases": ["中证商品期货综合指数", "中证商品期货综合", "中证商品CFCI综合指数", "中证商品CFCI", "商品CFCI", "中证商品CIFI指数", "中证商品CIFI", "商品CIFI", "H11061"]},
    {"code": "HSI.HI", "name": "恒生指数", "aliases": ["香港恒生指数", "恒生指数", "恒指", "HSI.HI", "HSI"]},
    {"code": "990100.MI", "name": "MSCI全球/发达市场", "aliases": ["MSCI全球指数", "MSCI全球", "人民币计价的 MSCI 全球指数", "人民币计价的MSCI全球指数", "MSCI发达市场指数", "MSCI发达市场", "990100"]},
    {"code": "SPX.GI", "name": "标普500", "aliases": ["标准普尔500", "标普500", "标普 500", "S&P500", "S&P 500", "SP500", "SPX"]},
    {"code": "NDX.GI", "name": "纳斯达克100", "aliases": ["纳斯达克100", "纳斯达克 100", "纳指100", "纳指 100", "NASDAQ100", "NDX"]},
    {"code": "000012.SH", "name": "上证国债", "aliases": ["上证国债", "国债指数", "000012"]},
    {"code": "H11006.CSI", "name": "中证国债", "aliases": ["中证国债", "H11006"]},
    {"code": "H11008.CSI", "name": "中证企业债", "aliases": ["中证企业债", "H11008"]},
    {"code": "H11001.CSI", "name": "中证全债", "aliases": ["中证全债", "H11001"]},
    {"code": "H11009.CSI", "name": "中证综合债", "aliases": ["中证综合债", "中证综合债券", "H11009"]},
    {"code": "H11015.CSI", "name": "中证短债", "aliases": ["中证短债", "H11015"]},
    {"code": "H11025.CSI", "name": "中证货币基金", "aliases": ["中证货币基金", "中证货币型基金", "中证货币市场基金", "中证货币指数", "货币基金指数", "货币市场基金指数", "货币基金", "货币市场基金", "H11025"]},
    {"code": "H11023.CSI", "name": "中证债券型基金", "aliases": ["中证债券型基金", "债券型基金指数", "H11023"]},
    {"code": "930950.CSI", "name": "中证偏股型基金", "aliases": ["中证偏股型基金", "中证偏股基金", "中证偏股混合基金", "偏股基金", "930950"]},
    {"code": "930609.CSI", "name": "中证纯债债券型基金", "aliases": ["中证纯债债券型基金", "中证纯债基金", "中证纯债债基", "纯债债基", "纯债债券型基金", "930609"]},
    {"code": "930610.CSI", "name": "中证普通债券型基金", "aliases": ["中证普通债券型基金", "中证普通债券基金", "普通债券型基金", "普通债券基金", "930610"]},
    {"code": "CBA00603.CS", "name": "中债-新综合全价(总值)", "aliases": ["中债新综合全价", "中债-新综合全价", "CBA00603"]},
    {"code": "CBA00601.CS", "name": "中债-新综合财富(总值)", "aliases": ["中债新综合财富", "中债-新综合财富", "中债-新综合指数", "中债新综合指数", "CBA00601"]},
    {"code": "CBA00203.CS", "name": "中债-综合全价(总值)", "aliases": ["中债综合全价", "中债-综合全价", "中债综合总全价", "中债-综合总全价", "中债-综合指数(全价)", "中债综合指数全价", "中证综合全价", "CBA00203"]},
    {"code": "CBA00201.CS", "name": "中债-综合财富(总值)", "aliases": ["中债综合财富", "中债-综合财富", "中债综合指数", "中债综合", "CBA00201"]},
    {"code": "CBA00303.CS", "name": "中债-总指数全价", "aliases": ["中债-总指数(全价)", "中债总指数全价", "CBA00303"]},
    {"code": "CBA00123.CS", "name": "中债-新综合全价(1-3年)", "aliases": ["中债-新综合全价1-3年", "中债新综合全价1-3年", "中债-新综合全价（1-3年）", "中债新综合全价（1-3年）", "CBA00123"]},
    {"code": "CBA00121.CS", "name": "中债-新综合财富(1-3年)", "aliases": ["中债-新综合财富1-3年", "中债新综合财富1-3年", "中债-新综合财富（1-3年）", "中债新综合财富（1-3年）", "CBA00121"]},
    {"code": "CBA00103.CS", "name": "中债-总全价(总值)", "aliases": ["中债总全价", "中债-总全价", "CBA00103"]},
    {"code": "CBA00101.CS", "name": "中债-总财富(总值)", "aliases": ["中债总财富", "中债财富总指数", "中债-总财富", "CBA00101"]},
    {"code": "AU9999.SGE", "name": "上海黄金Au99.99", "aliases": ["上海黄金9999", "黄金9999", "SGE黄金9999", "AU99.99", "Au99.99", "AU9999"]},
    {"code": "NHCI.NHF", "name": "南华商品指数", "aliases": ["南华商品指数", "南华商品", "NHCI"]},
    {"code": "CASH", "name": "现金/存款", "aliases": ["现金", "活期存款", "定期存款"]},
]

UNMAPPED_BENCHMARK_WORDS = [
    "南华商品",
    "MSCI",
    "恒生",
    "纳斯达克",
    "黄金",
]


def compact_benchmark_text(text: str) -> str:
    return re.sub(r"[\s（）()\[\]【】\-—_]+", "", text).upper()


def benchmark_component_for_text(text: str) -> dict[str, Any] | None:
    compact = compact_benchmark_text(text)
    best_item: dict[str, Any] | None = None
    best_alias_len = -1
    for item in BENCHMARK_INDEX_COMPONENTS:
        for alias in item["aliases"]:
            alias_compact = compact_benchmark_text(alias)
            if alias_compact in compact and len(alias_compact) > best_alias_len:
                best_item = item
                best_alias_len = len(alias_compact)
    return best_item


def parse_weight_from_part(part: str) -> float | None:
    weight_area = re.split(r"其中|其\s*中|下表|年份|注[:：]|说明[:：]|[,，;；。]", part, maxsplit=1)[0]
    compact = (
        re.sub(r"\s+", "", weight_area)
        .replace("×", "*")
        .replace("＊", "*")
        .replace("％", "%")
        .replace("#", "%")
        .replace("【", "")
        .replace("】", "")
    )
    percent_match = re.search(r"(\d+(?:\.\d+)?)%", compact)
    if percent_match:
        return float(percent_match.group(1)) / 100.0
    match = re.search(r"\*(\d+(?:\.\d+)?)(?:$|[^\d.])", compact)
    if not match:
        match = re.match(r"^(\d+(?:\.\d+)?)(?:\*|[^\d.])", compact)
    if match:
        value = float(match.group(1))
        return value / 100.0 if value > 1.0 else value
    return None


def load_structured_benchmark_components(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略业绩基准成分"):
        return {}
    rows = fetch_all(
        conn,
        '''
        SELECT "统一策略ID", "指数代码", "指数名称", "指数类型", "权重_百分比",
               "是否精确拆分", "置信度", "原始快照ID"
        FROM "策略业绩基准成分"
        WHERE "权重_百分比" IS NOT NULL AND "权重_百分比" >= 0
        ORDER BY "统一策略ID", "指数代码"
        ''',
    )
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, strategy_rows in grouped_rows(rows, "统一策略ID").items():
        total_weight = sum(as_float(row.get("权重_百分比")) or 0.0 for row in strategy_rows)
        exact = bool(strategy_rows) and all(int(row.get("是否精确拆分") or 0) == 1 for row in strategy_rows)
        if not exact or not 99.0 <= total_weight <= 101.0:
            continue
        components = [
            {
                "code": clean_text(row.get("指数代码"), ""),
                "name": clean_text(row.get("指数名称"), row.get("指数代码")),
                "index_type": clean_text(row.get("指数类型"), ""),
                "weight": (as_float(row.get("权重_百分比")) or 0.0) / total_weight,
            }
            for row in strategy_rows
            if clean_text(row.get("指数代码"), "")
        ]
        if not components:
            continue
        result[strategy_id] = {
            "components": components,
            "说明": " + ".join(f'{item["name"]} * {item["weight"] * 100:.2f}%' for item in components),
            "source": "渠道结构化业绩基准成分",
            "confidence": clean_text(strategy_rows[0].get("置信度"), ""),
            "source_snapshot_id": clean_text(strategy_rows[0].get("原始快照ID"), ""),
        }
    return result


def year_weight_for_date(weight_by_year: dict[int, float] | dict[str, float], date_value: str) -> float:
    years = sorted(int(year) for year in weight_by_year)
    if not years:
        return 0.0
    try:
        current_year = int(str(date_value)[:4])
    except (TypeError, ValueError):
        current_year = datetime.now().year
    selected_year = years[0]
    for year in years:
        if year <= current_year:
            selected_year = year
        else:
            break
    return float(weight_by_year.get(selected_year, weight_by_year.get(str(selected_year), 0.0)))


def parse_year_weight_table(text: str) -> dict[int, float]:
    weights: dict[int, float] = {}
    for year_text, pct_text in re.findall(r"(20\d{2})\s*(?:及以后)?\s+(\d+(?:\.\d+)?)\s*%", text):
        year = int(year_text)
        pct = float(pct_text)
        if 0.0 <= pct <= 100.0:
            weights[year] = pct / 100.0
    return dict(sorted(weights.items()))


def parse_dynamic_x_benchmark_formula(text: str) -> dict[str, Any] | None:
    if not re.search(r"(?<![A-Za-z])X(?![A-Za-z])", text) or "1-X" not in text.replace(" ", ""):
        return None
    year_weights = parse_year_weight_table(text)
    if not year_weights:
        return None
    equity_component = benchmark_component_for_text("沪深300")
    bond_component = benchmark_component_for_text("中债综合全价")
    if not equity_component or not bond_component:
        return None
    current_weight = year_weight_for_date(year_weights, datetime.now().strftime("%Y-%m-%d"))
    bond_weights = {year: round(1.0 - weight, 8) for year, weight in year_weights.items()}
    components = [
        {
            "code": equity_component["code"],
            "name": equity_component["name"],
            "weight": round(current_weight, 8),
            "weight_by_year": {str(year): round(weight, 8) for year, weight in year_weights.items()},
        },
        {
            "code": bond_component["code"],
            "name": bond_component["name"],
            "weight": round(1.0 - current_weight, 8),
            "weight_by_year": {str(year): round(weight, 8) for year, weight in bond_weights.items()},
        },
    ]
    first_year = min(year_weights)
    last_year = max(year_weights)
    description = (
        f'{equity_component["name"]} X动态权重({first_year}={year_weights[first_year] * 100:.2f}%, '
        f'{last_year}及以后={year_weights[last_year] * 100:.2f}%) + {bond_component["name"]} 1-X'
    )
    return {"components": components, "missing": [], "说明": description}


def parse_equity_center_benchmark_formula(text: str) -> dict[str, Any] | None:
    if "权益配置中枢比例" not in text:
        return None
    match = re.search(r"初始基准\s*(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    equity_weight = float(match.group(1)) / 100.0
    cash_weight = 0.05 if "货币" in text else 0.0
    bond_weight = max(0.0, 1.0 - equity_weight - cash_weight)
    equity_component = benchmark_component_for_text("沪深300")
    bond_component = benchmark_component_for_text("上证国债")
    cash_component = benchmark_component_for_text("货币基金指数")
    if not equity_component or not bond_component:
        return None
    components = [
        {"code": equity_component["code"], "name": equity_component["name"], "weight": round(equity_weight, 8)},
        {"code": bond_component["code"], "name": bond_component["name"], "weight": round(bond_weight, 8)},
    ]
    if cash_weight and cash_component:
        components.append({"code": cash_component["code"], "name": cash_component["name"], "weight": round(cash_weight, 8)})
    description = " + ".join(f'{item["name"]}{item["weight"] * 100:.2f}%' for item in components)
    return {"components": components, "missing": [], "说明": f"{description}（按披露初始基准还原）"}


def should_flag_unmapped_benchmark_word(word: str, raw: str, parsed_items: list[dict[str, Any]], missing: list[str]) -> bool:
    if word not in raw or any(word in str(item.get("name", "")) for item in parsed_items):
        return False
    if any(word in item for item in missing):
        return False
    if word == "MSCI" and re.search(r"MSCI\s*(沪深\s*300|中国A股|中国)", raw, flags=re.IGNORECASE):
        return False
    return True


def parse_benchmark_formula(benchmark_text: Any) -> dict[str, Any]:
    raw = clean_text(benchmark_text, "")
    if not raw:
        return {"components": [], "missing": ["未披露"], "说明": "未披露业绩基准"}
    normalized = (
        raw.replace("＝", "=")
        .replace("×", "*")
        .replace("＊", "*")
        .replace("＋", "+")
        .replace("（", "(")
        .replace("）", ")")
    )
    normalized = re.sub(r"(?<=[0-9%）\)])\s*[xX]\s*(?=[A-Za-z0-9\u4e00-\u9fff（(])", "*", normalized)
    normalized = re.sub(r"^业绩比较基准\s*[=:：]", "", normalized)
    dynamic = parse_dynamic_x_benchmark_formula(normalized)
    if dynamic:
        return dynamic
    equity_center = parse_equity_center_benchmark_formula(normalized)
    if equity_center:
        return equity_center
    if "下滑曲线" in normalized:
        return {"components": [], "missing": ["动态下滑曲线权重"], "说明": f"暂未解析动态权重基准：{raw}"}
    parts = [part for part in re.split(r"[+＋]", normalized) if part.strip()]
    parsed: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for part in parts or [normalized]:
        component = benchmark_component_for_text(part)
        if not component:
            if "%" in part or "指数" in part:
                missing.append(f"未映射组件：{part.strip()}")
            continue
        weight = parse_weight_from_part(part)
        if weight is None and len(parts) <= 1:
            weight = 1.0
        if weight is None:
            missing.append(f"未解析权重：{part.strip()}")
            continue
        bucket = parsed.setdefault(component["code"], {"code": component["code"], "name": component["name"], "weight": 0.0})
        bucket["weight"] += weight
    for word in UNMAPPED_BENCHMARK_WORDS:
        if should_flag_unmapped_benchmark_word(word, raw, list(parsed.values()), missing):
            missing.append(f"暂未映射行情：{word}")
    components = list(parsed.values())
    total_weight = sum(float(item["weight"]) for item in components)
    if components and not missing and 0.0 < total_weight <= 1.5:
        for item in components:
            item["weight"] = round(float(item["weight"]) / total_weight, 8)
    elif components:
        missing.append(f"权重合计异常：{round(total_weight * 100, 4)}%")
    description = " + ".join(f'{item["name"]}{item["weight"] * 100:.2f}%' for item in components) if components else "未解析出可计算组件"
    return {"components": components, "missing": missing, "说明": description}


def load_all_index_levels(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    if not table_exists(conn, "指数日度行情"):
        return {}
    rows = fetch_all(
        conn,
        """
        SELECT "指数代码", "指数名称", "交易日期", "收盘点位" AS value, "数据来源"
        FROM "指数日度行情"
        WHERE "收盘点位" IS NOT NULL
        ORDER BY "指数代码", "交易日期"
        """,
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row["指数代码"]), []).append(
            {
                "日期": row["交易日期"],
                "数值": round_or_none(row["value"], 6),
                "模式": "nav",
                "数据来源": row["数据来源"],
                "指数名称": row["指数名称"],
            }
        )
    return result


def load_global_benchmark_catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    all_levels = load_all_index_levels(conn)
    catalog: list[dict[str, Any]] = []
    for code in CORE_GLOBAL_BENCHMARK_CODES:
        rows = all_levels.get(code, [])
        if not rows:
            continue
        name = clean_text(rows[-1].get("指数名称"), code)
        source = latest_data_source(rows, "指数日度行情")
        catalog.append(
            {
                "code": code,
                "name": name,
                "start": rows[0]["日期"],
                "end": rows[-1]["日期"],
                "rows": len(rows),
                "source": source,
                "points": sample_series(rows, "日期", "数值", max_points=1400, mode="nav"),
            }
        )
    return catalog


def build_formula_benchmark_series(
    dates: list[str],
    components: list[dict[str, Any]],
    index_levels: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not dates or not components:
        return []
    required = [item for item in components if item["code"] != "CASH"]
    if any(not index_levels.get(item["code"]) for item in required):
        return []
    pointers = {item["code"]: 0 for item in required}
    last_values: dict[str, float] = {}
    previous_values: dict[str, float] | None = None
    dynamic_nav = 1.0
    output: list[dict[str, Any]] = []
    target_dates = sorted(set(dates))
    target_date_set = set(target_dates)
    first_date, last_date = target_dates[0], target_dates[-1]
    calculation_dates = set(target_dates)
    for item in required:
        calculation_dates.update(
            str(row["日期"])
            for row in index_levels.get(item["code"], [])
            if first_date <= str(row.get("日期") or "") <= last_date
        )
    for date_value in sorted(calculation_dates):
        for item in required:
            rows = index_levels.get(item["code"], [])
            pointer = pointers[item["code"]]
            while pointer < len(rows) and str(rows[pointer]["日期"]) <= date_value:
                value = as_float(rows[pointer]["数值"])
                if value is not None:
                    last_values[item["code"]] = value
                pointer += 1
            pointers[item["code"]] = pointer
        if any(item["code"] not in last_values for item in required):
            continue
        if previous_values is None:
            previous_values = dict(last_values)
            if date_value in target_date_set:
                output.append({"日期": date_value, "数值": round(dynamic_nav, 8), "模式": "nav"})
            continue
        daily_return = 0.0
        for item in components:
            code = item["code"]
            if code == "CASH":
                continue
            previous = previous_values.get(code)
            current = last_values.get(code)
            if not previous or current is None:
                continue
            weight = year_weight_for_date(item.get("weight_by_year", {}), date_value) if item.get("weight_by_year") else float(item["weight"])
            daily_return += weight * (current / previous - 1.0)
        dynamic_nav *= 1.0 + daily_return
        if date_value in target_date_set:
            output.append({"日期": date_value, "数值": round(dynamic_nav, 8), "模式": "nav"})
        previous_values = dict(last_values)
    return output


def benchmark_uses_hs300(benchmark_text: Any) -> bool:
    text = clean_text(benchmark_text, "")
    if not re.search(r"沪深\s*300|CSI\s*300|000300", text, flags=re.IGNORECASE):
        return False
    other_index_words = ["中证800", "中证500", "中证全债", "中证综合债", "中债", "货币", "恒生", "纳斯达克", "黄金", "MSCI", "南华", "短债"]
    return not any(word in text for word in other_index_words)


def latest_data_source(index_levels: list[dict[str, Any]], fallback: str) -> str:
    for row in reversed(index_levels):
        if clean_text(row.get("数据来源"), ""):
            return clean_text(row.get("数据来源"), fallback)
    return fallback


def load_detail_maps(conn: sqlite3.Connection, algorithm_version: str) -> dict[str, Any]:
    relationship_map = load_strategy_relationship_map(conn)
    latest_fund_nav = load_latest_fund_nav_map(conn)
    fund_return_rank = load_fund_return_rank_map(conn)
    direct_holding_rows = fetch_all(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", MAX("持仓日期") AS latest_date
            FROM "策略当前持仓"
            WHERE "基金权重_百分比" IS NOT NULL
            GROUP BY "统一策略ID"
        )
        SELECT h."统一策略ID", h."基金代码", h."基金名称", h."资产类型", h."分组名称", h."持仓日期",
               h."基金权重_百分比", h."基金净值", h."基金净值日期", h."最新日涨幅_百分比",
               f."天天基金细分类", f."天天基金大类", f."天天基金二级分类",
               f."是否货币基金", f."是否债券基金", f."是否权益基金", f."是否混合基金",
               f."是否指数基金", f."是否ETF", f."是否ETF联接", f."是否指数增强",
               f."是否QDII", f."是否FOF", f."是否商品黄金", f."是否短债", f."是否纯债", f."是否可转债",
               f."标准资产大类", f."标准资产细类", f."市场地域标签", f."主动被动标签", f."投顾资产分类桶"
        FROM "策略当前持仓" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."持仓日期"
        LEFT JOIN "基金标准分类字典" f ON f."基金代码" = h."基金代码"
        WHERE h."基金权重_百分比" IS NOT NULL
        ORDER BY h."统一策略ID", h."基金权重_百分比" DESC
        """,
    )
    holding_audit_detail_rows = fetch_all(
        conn,
        """
        SELECT "统一策略ID", "基金代码", "调后权重_百分比", "收益因子_复权",
               "净值结束日", "推算日期"
        FROM "最新持仓推算稽核基金明细"
        """,
    )
    holding_audit_detail = {
        (str(row["统一策略ID"]), str(row["基金代码"])): row
        for row in holding_audit_detail_rows
    }
    latest_rebalance_detail_rows = fetch_all(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", "调仓事件ID",
                   ROW_NUMBER() OVER (PARTITION BY "统一策略ID" ORDER BY "调仓日期" DESC, "事件序号" DESC) AS rn
            FROM "策略调仓事件"
        )
        SELECT l."统一策略ID", d."基金代码", d."调后权重_百分比",
               q."基金区间收益率_百分比", q."调后收益贡献_百分比", q."基金收益结束日期"
        FROM latest l
        JOIN "策略调仓明细" d ON d."调仓事件ID" = l."调仓事件ID"
        LEFT JOIN "调仓质量基金明细" q
          ON q."调仓事件ID" = d."调仓事件ID"
         AND q."基金代码_分析" = d."基金代码"
        WHERE l.rn = 1
        """,
    )
    latest_rebalance_detail = {
        (str(row["统一策略ID"]), str(row["基金代码"])): row
        for row in latest_rebalance_detail_rows
    }
    direct_holdings: dict[str, list[dict[str, Any]]] = {}
    for sid, rows in grouped_rows(direct_holding_rows, "统一策略ID").items():
        output_rows: list[dict[str, Any]] = []
        for row in rows:
            audit_row = holding_audit_detail.get((str(sid), str(row["基金代码"])), {})
            latest_row = latest_rebalance_detail.get((str(sid), str(row["基金代码"])), {})
            fund_nav_row = latest_fund_nav.get(str(row["基金代码"]), {})
            current_weight = round_or_none(row["基金权重_百分比"])
            last_after_weight = round_or_none(audit_row.get("调后权重_百分比") if audit_row else latest_row.get("调后权重_百分比"))
            return_since_rebalance = round_or_none(((as_float(audit_row.get("收益因子_复权")) or 1.0) - 1.0) * 100.0) if audit_row else round_or_none(latest_row.get("基金区间收益率_百分比"))
            contribution_since_rebalance = round_or_none(latest_row.get("调后收益贡献_百分比"))
            if contribution_since_rebalance is None and last_after_weight is not None and return_since_rebalance is not None:
                contribution_since_rebalance = round_or_none(((as_float(last_after_weight) or 0.0) * (as_float(return_since_rebalance) or 0.0)) / 100.0)
            item = {
                "基金代码": row["基金代码"],
                "基金名称": row["基金名称"],
                "持仓日期": row["持仓日期"],
                "资产类型": holding_display_asset_type(row),
                "分组": holding_display_group(row),
                "二级分类": holding_display_group(row),
                "权重": current_weight,
                "上次调仓后权重": last_after_weight,
                "权重变化": round_or_none((as_float(current_weight) or 0.0) - (as_float(last_after_weight) or 0.0)) if last_after_weight is not None else None,
                "基金净值": round_or_none(fund_nav_row.get("单位净值") or row["基金净值"], 6),
                "净值日期": fund_nav_row.get("交易日期") or audit_row.get("净值结束日") or latest_row.get("基金收益结束日期") or row["基金净值日期"],
                "日涨幅": round_or_none(fund_nav_row.get("日收益率_百分比") or row["最新日涨幅_百分比"]),
                "调仓后收益率": return_since_rebalance,
                "调仓后收益贡献": contribution_since_rebalance,
            }
            add_fund_rank_fields(item, row["基金代码"], fund_return_rank)
            output_rows.append(item)
        direct_holdings[sid] = output_rows
    projected_rows = fetch_all(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", MAX("推算持仓日期") AS latest_date
            FROM "策略当前持仓推算补齐"
            GROUP BY "统一策略ID"
        )
        SELECT h."统一策略ID", h."基金代码", h."基金名称", h."推算持仓日期",
               h."推算基金权重_百分比", h."最后调仓后权重_百分比", h."收益因子_复权",
               f."天天基金细分类", f."天天基金大类", f."天天基金二级分类",
               f."是否货币基金", f."是否债券基金", f."是否权益基金", f."是否混合基金",
               f."是否指数基金", f."是否ETF", f."是否ETF联接", f."是否指数增强",
               f."是否QDII", f."是否FOF", f."是否商品黄金", f."是否短债", f."是否纯债", f."是否可转债",
               f."标准资产大类", f."标准资产细类", f."市场地域标签", f."主动被动标签", f."投顾资产分类桶"
        FROM "策略当前持仓推算补齐" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."推算持仓日期"
        LEFT JOIN "基金标准分类字典" f ON f."基金代码" = h."基金代码"
        ORDER BY h."统一策略ID", h."推算基金权重_百分比" DESC
        """,
    )
    projected_holdings: dict[str, list[dict[str, Any]]] = {}
    for sid, rows in grouped_rows(projected_rows, "统一策略ID").items():
        output_rows = []
        for row in rows:
            item = {
                "基金代码": row["基金代码"],
                "基金名称": row["基金名称"],
                "持仓日期": row["推算持仓日期"],
                "资产类型": holding_display_asset_type(row),
                "分组": holding_display_group(row),
                "二级分类": holding_display_group(row),
                "权重": round_or_none(row["推算基金权重_百分比"]),
                "上次调仓后权重": round_or_none(row["最后调仓后权重_百分比"]),
                "权重变化": round_or_none((as_float(row["推算基金权重_百分比"]) or 0) - (as_float(row["最后调仓后权重_百分比"]) or 0)),
                "基金净值": round_or_none((latest_fund_nav.get(str(row["基金代码"]), {}) or {}).get("单位净值"), 6),
                "净值日期": (latest_fund_nav.get(str(row["基金代码"]), {}) or {}).get("交易日期") or row["推算持仓日期"],
                "日涨幅": round_or_none((latest_fund_nav.get(str(row["基金代码"]), {}) or {}).get("日收益率_百分比")),
                "调仓后收益率": round_or_none(((as_float(row["收益因子_复权"]) or 1.0) - 1.0) * 100.0),
                "调仓后收益贡献": round_or_none(((as_float(row["最后调仓后权重_百分比"]) or 0.0) * (((as_float(row["收益因子_复权"]) or 1.0) - 1.0) * 100.0)) / 100.0),
            }
            add_fund_rank_fields(item, row["基金代码"], fund_return_rank)
            output_rows.append(item)
        projected_holdings[sid] = output_rows
    rebalance_event_rows = fetch_all(
        conn,
        """
        WITH sums AS (
            SELECT "调仓事件ID", COUNT(*) AS fund_count,
                   SUM(CASE WHEN COALESCE("调后权重_百分比", 0) > 0 THEN "调后权重_百分比" ELSE 0 END) AS after_weight_sum
            FROM "策略调仓明细"
            GROUP BY "调仓事件ID"
        )
        SELECT e."统一策略ID", e."调仓事件ID", e."事件序号", e."调仓日期", e."披露日期", e."调仓标题", e."调仓原因",
               COALESCE(s.fund_count, 0) AS fund_count, s.after_weight_sum
        FROM "策略调仓事件" e
        LEFT JOIN sums s ON s."调仓事件ID" = e."调仓事件ID"
        WHERE e."调仓日期" IS NOT NULL
        ORDER BY e."统一策略ID", e."调仓日期" DESC, COALESCE(e."事件序号", 0) DESC, e."调仓事件ID"
        """,
    )
    rebalance_event_rows = sorted(dedupe_rebalance_event_rows(rebalance_event_rows), key=rebalance_event_sort_key)
    rebalance_events = {
        sid: [
            {
                "事件ID": row["调仓事件ID"],
                "调仓日期": row["调仓日期"],
                "披露日期": row["披露日期"],
                "调仓标题": clean_text(row["调仓标题"]),
                "调仓原因": clean_text(row["调仓原因"]),
                "调后权重和": round_or_none(row["after_weight_sum"]),
                "调仓基金数": int(row["fund_count"] or 0),
            }
            for row in rows[:30]
        ]
        for sid, rows in grouped_rows(rebalance_event_rows, "统一策略ID").items()
    }
    selected_rebalance_event_ids = {
        str(event["事件ID"])
        for events in rebalance_events.values()
        for event in events
        if event.get("事件ID")
    }
    latest_fund_nav_date = max(
        (clean_text(row.get("交易日期"), "") for row in latest_fund_nav.values() if clean_text(row.get("交易日期"), "")),
        default=None,
    )
    rebalance_event_end_dates: dict[str, str | None] = {}
    for events in rebalance_events.values():
        ordered = sorted(
            (event for event in events if clean_text(event.get("调仓日期"), "")),
            key=lambda item: clean_text(item.get("调仓日期"), ""),
        )
        for index, event in enumerate(ordered):
            event_id = clean_text(event.get("事件ID"), "")
            if not event_id:
                continue
            current_date = clean_text(event.get("调仓日期"), "")
            next_date = ""
            for candidate in ordered[index + 1:]:
                candidate_date = clean_text(candidate.get("调仓日期"), "")
                if candidate_date and candidate_date > current_date:
                    next_date = candidate_date
                    break
            rebalance_event_end_dates[event_id] = date_offset_text(next_date, -1) if next_date else latest_fund_nav_date
    rebalance_detail_rows = fetch_all(
        conn,
        """
        SELECT e."统一策略ID", e."调仓事件ID", e."调仓日期",
               d."基金代码", d."基金名称", d."分组名称", d."调前权重_百分比",
               d."调后权重_百分比", d."权重变化_百分比", d."调仓动作",
               q."基金区间收益率_百分比", q."调后收益贡献_百分比",
               q."基金收益起始日期", q."基金收益结束日期"
        FROM "策略调仓事件" e
        JOIN "策略调仓明细" d ON d."调仓事件ID" = e."调仓事件ID"
        LEFT JOIN "调仓质量基金明细" q
          ON q."调仓事件ID" = d."调仓事件ID"
         AND q."基金代码_分析" = d."基金代码"
        ORDER BY e."统一策略ID", e."调仓日期" DESC, COALESCE(e."事件序号", 0) DESC, COALESCE(d."调后权重_百分比", 0) DESC, d."基金代码"
        """,
    )
    rebalance_detail_rows = [
        row for row in rebalance_detail_rows
        if str(row.get("调仓事件ID")) in selected_rebalance_event_ids
    ]
    rebalance_fund_codes = {
        clean_text(row.get("基金代码"), "")
        for row in rebalance_detail_rows
        if clean_text(row.get("基金代码"), "")
    }
    fund_nav_profiles = load_fund_nav_profiles(conn)
    alternate_fund_codes: dict[tuple[str, str], str] = {}
    for row in rebalance_detail_rows:
        fund_code = clean_text(row.get("基金代码"), "")
        family = fund_family_name(row.get("基金名称"))
        event_date = clean_text(row.get("调仓日期"), "")
        if not fund_code or not family or not event_date:
            continue
        for candidate in fund_nav_profiles.get(family, []):
            candidate_code = clean_text(candidate.get("基金代码"), "")
            if not candidate_code or candidate_code == fund_code:
                continue
            if clean_text(candidate.get("最早净值日"), "9999-99-99") <= event_date <= clean_text(candidate.get("最新净值日"), ""):
                alternate_fund_codes[(fund_code, event_date)] = candidate_code
                rebalance_fund_codes.add(candidate_code)
                break
    rebalance_start_date = min(
        (clean_text(row.get("调仓日期"), "") for row in rebalance_detail_rows if clean_text(row.get("调仓日期"), "")),
        default=None,
    )
    rebalance_end_date = max(
        (
            clean_text(row.get("基金收益结束日期"), "")
            or clean_text(rebalance_event_end_dates.get(str(row.get("调仓事件ID"))), "")
            for row in rebalance_detail_rows
            if clean_text(row.get("基金收益结束日期"), "") or clean_text(rebalance_event_end_dates.get(str(row.get("调仓事件ID"))), "")
        ),
        default=latest_fund_nav_date,
    )
    rebalance_fund_nav_cache = load_fund_nav_cache(conn, rebalance_fund_codes, rebalance_start_date, rebalance_end_date)
    rebalance_holdings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rebalance_detail_rows:
        strategy_id = str(row["统一策略ID"])
        event_id = str(row["调仓事件ID"])
        fund_code = clean_text(row.get("基金代码"), "")
        fund_nav_row = latest_fund_nav.get(str(row["基金代码"]), {})
        stored_return_since_rebalance = round_or_none(row["基金区间收益率_百分比"])
        interval_end_date = clean_text(row.get("基金收益结束日期"), "") or rebalance_event_end_dates.get(event_id)
        interval = fund_interval_return(
            rebalance_fund_nav_cache.get(fund_code, []),
            clean_text(row.get("基金收益起始日期"), "") or clean_text(row.get("调仓日期"), ""),
            interval_end_date,
        )
        interval_basis = "基金日度净值"
        if not interval:
            alternate_code = alternate_fund_codes.get((fund_code, clean_text(row.get("调仓日期"), "")))
            if alternate_code:
                interval = fund_interval_return(
                    rebalance_fund_nav_cache.get(alternate_code, []),
                    clean_text(row.get("基金收益起始日期"), "") or clean_text(row.get("调仓日期"), ""),
                    interval_end_date,
                )
                if interval:
                    interval_basis = f"同基金早期份额 {alternate_code} 估算"
        return_since_rebalance = interval.get("收益率") if interval else stored_return_since_rebalance
        contribution_since_rebalance = round_or_none(row["调后收益贡献_百分比"])
        if contribution_since_rebalance is None and return_since_rebalance is not None:
            contribution_since_rebalance = round_or_none(((as_float(row["调后权重_百分比"]) or 0.0) * return_since_rebalance) / 100.0)
        nav_value = interval.get("期末净值")
        nav_date = interval.get("结束日期") or clean_text(row.get("基金收益结束日期"), "") or fund_nav_row.get("交易日期")
        item = {
            "基金代码": row["基金代码"],
            "基金名称": row["基金名称"],
            "资产类型": clean_text(row["分组名称"]),
            "分组": clean_text(row["分组名称"]),
            "二级分类": clean_text(row["分组名称"]),
            "权重": round_or_none(row["调后权重_百分比"]),
            "上次调仓后权重": round_or_none(row["调前权重_百分比"]),
            "权重变化": round_or_none(row["权重变化_百分比"]),
            "调仓动作": ACTION_MAP.get(clean_text(row["调仓动作"], ""), clean_text(row["调仓动作"])),
            "基金净值": nav_value if nav_value is not None else round_or_none(fund_nav_row.get("单位净值"), 6),
            "净值日期": nav_date,
            "日涨幅": round_or_none(fund_nav_row.get("日收益率_百分比")),
            "调仓后收益率": return_since_rebalance,
            "调仓后收益贡献": contribution_since_rebalance,
            "调仓后收益口径": interval_basis if interval else clean_text(row.get("基金收益结束日期"), ""),
        }
        add_fund_rank_fields(item, row["基金代码"], fund_return_rank)
        rebalance_holdings.setdefault(strategy_id, {}).setdefault(event_id, []).append(item)
    contribution_weight_rows: dict[str, list[dict[str, Any]]] = {}
    contribution_fund_codes: set[str] = set()
    for row in rebalance_detail_rows:
        event_id = str(row["调仓事件ID"])
        contribution_weight_rows.setdefault(event_id, []).append(row)
        fund_code = clean_text(row.get("基金代码"), "")
        if fund_code:
            contribution_fund_codes.add(fund_code)

    official_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "披露单位净值" AS value
        FROM "策略产品披露净值"
        WHERE "是否可画曲线" = 1 AND "披露单位净值" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        (),
        "交易日期",
        "value",
        "nav",
    )
    quote_nav_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "单位净值" AS value
        FROM "策略日度业绩"
        WHERE "单位净值" IS NOT NULL
          AND NOT ("渠道ID" = 'ttfund' AND COALESCE("业绩区段类型", '') = 'public_quote')
        ORDER BY "统一策略ID", "交易日期"
        """,
        (),
        "交易日期",
        "value",
        "nav",
    )
    official_series = merge_series_by_date(official_series, quote_nav_series)
    official_series, curve_warnings = sanitize_series_map(official_series)
    official_interval_returns = build_official_interval_return_map(conn)
    official_series = apply_relationship_aliases(official_series, relationship_map)
    official_interval_returns = apply_relationship_aliases(official_interval_returns, relationship_map)
    for child_id, relationship in relationship_map.items():
        if relationship.get("官方业绩策略ID") and official_series.get(child_id):
            parent_name = clean_text(relationship.get("母策略名称"), relationship.get("母策略ID"))
            curve_warnings.setdefault(child_id, []).append(
                f"本期暂无独立披露净值，披露业绩共享母策略“{parent_name}”，不代表本期独立成立以来收益。"
            )
    for strategy_id, interval_payload in official_interval_returns.items():
        if not strategy_id.startswith("gfsec_robot__") or len(official_series.get(strategy_id, [])) >= 2:
            continue
        latest_date = clean_text((interval_payload or {}).get("latest_date"), "未披露")
        curve_warnings.setdefault(strategy_id, []).append(
            "该产品属于广发证券贝塔牛历史接口留档；"
            f"当前仅保留官方区间收益（数据截至{latest_date}），旧日度策略及基准曲线路由已不可用。"
            "页面不使用截图、插值或其他策略曲线替代。"
        )
    simulated_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "标准费前单位净值" AS value
        FROM "策略标准业绩净值"
        WHERE "算法版本" = ? AND "标准费前单位净值" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        (algorithm_version,),
        "交易日期",
        "value",
        "nav",
    )
    benchmark_disclosed_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "基准收益率_百分比" AS value
        FROM "策略产品披露净值"
        WHERE "是否可画曲线" = 1 AND "基准收益率_百分比" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        (),
        "交易日期",
        "value",
        "return_pct",
    )
    benchmark_daily_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "基准收益率_百分比" AS value
        FROM "策略日度业绩"
        WHERE "基准收益率_百分比" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        (),
        "交易日期",
        "value",
        "return_pct",
    )
    benchmark_disclosed_series = apply_relationship_aliases(benchmark_disclosed_series, relationship_map)
    benchmark_daily_series = apply_relationship_aliases(benchmark_daily_series, relationship_map)
    hs300_field_series = load_series_map(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "指数收益率_百分比" AS value
        FROM "策略日度业绩"
        WHERE "指数收益率_百分比" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        (),
        "交易日期",
        "value",
        "return_pct",
    )
    all_index_levels = load_all_index_levels(conn)
    index_levels = all_index_levels.get("000300.SH") or load_index_levels(conn)
    index_source = latest_data_source(index_levels, "指数日度行情")
    dates_by_strategy = collect_strategy_dates(official_series, simulated_series, benchmark_disclosed_series, benchmark_daily_series, hs300_field_series)
    hs300_index_series = align_index_series(dates_by_strategy, index_levels)
    hs300_series = hs300_index_series or hs300_field_series
    benchmark_text_map = {
        str(row["统一策略ID"]): row.get("业绩基准")
        for row in fetch_all(conn, 'SELECT "统一策略ID", "业绩基准" FROM "策略信息"')
    }
    benchmark_text_map = fill_missing_relationship_aliases(benchmark_text_map, relationship_map)
    structured_benchmark_map = fill_missing_relationship_aliases(
        load_structured_benchmark_components(conn),
        relationship_map,
    )
    benchmark_meta: dict[str, dict[str, Any]] = {}
    formula_benchmark_series: dict[str, list[dict[str, Any]]] = {}
    for strategy_id in sorted(set(dates_by_strategy) | set(benchmark_text_map) | set(structured_benchmark_map)):
        dates = dates_by_strategy.get(strategy_id, [])
        structured = structured_benchmark_map.get(strategy_id) or {}
        parsed = (
            {
                "components": structured.get("components", []),
                "missing": [],
                "说明": structured.get("说明"),
                "source": structured.get("source"),
            }
            if structured.get("components")
            else parse_benchmark_formula(benchmark_text_map.get(strategy_id))
        )
        factor_missing = [
            f'缺少指数行情：{item["code"]} {item["name"]}'
            for item in parsed.get("components", [])
            if item.get("code") != "CASH" and not all_index_levels.get(item.get("code"))
        ]
        if factor_missing:
            parsed = {**parsed, "missing": [*parsed.get("missing", []), *factor_missing]}
        computed = [] if parsed.get("missing") else build_formula_benchmark_series(dates, parsed.get("components", []), all_index_levels)
        formula_benchmark_series[strategy_id] = computed
        benchmark_meta[strategy_id] = {
            "业绩基准说明": clean_text(benchmark_text_map.get(strategy_id), "未披露"),
            "基准公式解析": parsed.get("说明"),
            "基准成分来源": parsed.get("source") or "业绩基准文本解析",
            "组合计算方法": "指数日收益按披露权重逐日再平衡复合",
            "可计算组件": [
                {
                    "指数代码": item["code"],
                    "指数名称": item["name"],
                    "权重": round_or_none(float(item["weight"]) * 100.0),
                }
                for item in parsed.get("components", [])
            ],
            "缺失组件": parsed.get("missing", []),
            "公式基准可画点数": len(computed),
        }
    benchmark_series: dict[str, list[dict[str, Any]]] = {}
    curve_sources: dict[str, dict[str, str]] = {}
    strategy_ids = set(official_series) | set(simulated_series) | set(benchmark_disclosed_series) | set(benchmark_daily_series) | set(hs300_series) | set(formula_benchmark_series)
    for strategy_id in strategy_ids:
        source_row = {
            "披露业绩": "策略产品披露净值.披露单位净值 + 策略日度业绩.单位净值（同日优先完整披露曲线；天天 public quote 仅保留行情血缘，不推进 App 官方业绩日）",
            "模拟业绩": "策略标准业绩净值.标准费前单位净值（统一回放算法）",
            "沪深300业绩": f"指数日度行情.沪深300(000300.SH)收盘点位；数据来源：{index_source}" if hs300_index_series.get(strategy_id) else "策略日度业绩.指数收益率_百分比（App披露指数字段）",
        }
        if strategy_id.startswith("gfbank_cgb__") and len(official_series.get(strategy_id, [])) < 2:
            source_row["披露业绩"] = (
                "广发银行 App 仅取得官方最新净值快照和区间收益，未取得可验证的结构化逐日序列；"
                "不能绘制真实走势图，页面不使用截图或图像反推点替代"
            )
        relationship = relationship_map.get(strategy_id, {})
        if relationship.get("官方业绩策略ID"):
            parent_name = clean_text(relationship.get("母策略名称"), relationship.get("母策略ID"))
            source_row["披露业绩"] = (
                f"策略关系.官方业绩策略ID → 母策略“{parent_name}”的渠道披露曲线；"
                "该曲线为产品系列共享披露业绩，非本期独立净值"
            )
        if benchmark_disclosed_series.get(strategy_id):
            benchmark_series[strategy_id] = benchmark_disclosed_series[strategy_id]
            source_row["基准业绩"] = "策略产品披露净值.基准收益率_百分比（App/渠道披露基准）"
        elif benchmark_daily_series.get(strategy_id):
            benchmark_series[strategy_id] = benchmark_daily_series[strategy_id]
            source_row["基准业绩"] = "策略日度业绩.基准收益率_百分比（App日度业绩基准）"
        elif formula_benchmark_series.get(strategy_id):
            benchmark_series[strategy_id] = formula_benchmark_series[strategy_id]
            source_row["基准业绩"] = (
                f'{benchmark_meta.get(strategy_id, {}).get("基准成分来源")}；'
                f'指数日收益按披露权重逐日再平衡复合：{benchmark_meta.get(strategy_id, {}).get("基准公式解析")}'
            )
        elif benchmark_uses_hs300(benchmark_text_map.get(strategy_id)) and hs300_series.get(strategy_id):
            benchmark_series[strategy_id] = hs300_series[strategy_id]
            source_row["基准业绩"] = "业绩基准文本可映射为沪深300，使用指数日度行情推算"
        else:
            benchmark_series[strategy_id] = []
            official_benchmark_fields = official_interval_fields(
                official_interval_returns.get(strategy_id),
                OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE,
                value_field="基准收益率_百分比",
            )
            if official_benchmark_fields:
                source_row["基准业绩"] = (
                    "策略区间业绩.基准收益率_百分比（App/渠道仅披露区间基准收益，"
                    "无结构化日度点，不能绘制基准曲线）"
                )
            else:
                source_row["基准业绩"] = f"未取得可画日度基准；业绩基准：{clean_text(benchmark_text_map.get(strategy_id), '未披露')}"
        curve_sources[strategy_id] = source_row
    curves: dict[str, dict[str, Any]] = {}
    interval_matrix: dict[str, list[dict[str, Any]]] = {}
    annual_matrix: dict[str, list[dict[str, Any]]] = {}
    for strategy_id in strategy_ids:
        series_by_name = {
            "披露业绩": official_series.get(strategy_id, []),
            "模拟业绩": simulated_series.get(strategy_id, []),
            "基准业绩": benchmark_series.get(strategy_id, []),
            "沪深300业绩": hs300_series.get(strategy_id, []),
        }
        curves[strategy_id] = {
            name: {
                "模式": (rows[0]["模式"] if rows else "nav"),
                "points": sample_series(rows, "日期", "数值", max_points=DETAIL_CURVE_MAX_POINTS, mode=(rows[0]["模式"] if rows else "nav")),
            }
            for name, rows in series_by_name.items()
        }
        if DETAIL_CURVE_MAX_POINTS is None:
            for name, rows in series_by_name.items():
                expected = sum(1 for row in rows if row.get("日期") and row.get("数值") is not None)
                actual = len(curves[strategy_id][name]["points"])
                if actual != expected:
                    raise AssertionError(
                        f"strategy detail curve lost date-level points: {strategy_id} {name} expected={expected} actual={actual}"
                    )
        official_curve_latest = parse_ymd(official_series.get(strategy_id, [])[-1]["日期"]) if official_series.get(strategy_id) else None
        official_detail_fields = official_interval_fields(
            official_interval_returns.get(strategy_id),
            OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE,
            official_curve_latest,
        )
        official_benchmark_fields = official_interval_fields(
            official_interval_returns.get(strategy_id),
            OFFICIAL_INTERVAL_MATRIX_FIELD_BY_CODE,
            official_curve_latest,
            value_field="基准收益率_百分比",
        )
        official_detail_latest = parse_ymd((official_interval_returns.get(strategy_id) or {}).get("latest_date"))
        interval_matrix[strategy_id] = build_interval_matrix(
            series_by_name,
            official_detail_fields,
            official_benchmark_fields,
        )
        annual_matrix[strategy_id] = build_annual_return_matrix(
            series_by_name,
            official_detail_fields,
            official_detail_latest,
        )

    event_quality_rows = fetch_all(
        conn,
        """
        WITH recent AS (
            SELECT e."统一策略ID", e."调仓事件ID", e."调仓日期",
                   ROW_NUMBER() OVER (PARTITION BY e."统一策略ID" ORDER BY e."调仓日期" DESC) AS rn
            FROM "调仓质量事件分析" e
        )
        SELECT q.*, r.rn AS "最近调仓序号"
        FROM "调仓质量事件分析" q
        JOIN recent r ON r."调仓事件ID" = q."调仓事件ID"
        WHERE r.rn <= 30
        ORDER BY q."统一策略ID", q."调仓日期" DESC
        """,
    )
    # A newly observed rebalance has no post-period yet. Select the latest
    # evaluable event instead of the absolute latest event so the default page
    # curve remains evidence-backed while the new event waits for NAV data.
    exact_event_ids = latest_evaluable_event_ids(event_quality_rows)
    contribution_curves: dict[str, dict[str, Any]] = {}
    for row in event_quality_rows:
        strategy_id = str(row["统一策略ID"])
        event_id = str(row["调仓事件ID"])
        start_date = row["调仓日期"]
        end_date = row["区间结束锚点日期"] or row["下次调仓日期"]
        after_series = normalize_segment(simulated_series.get(strategy_id, []), start_date, end_date)
        reference = after_series or normalize_segment(benchmark_series.get(strategy_id, []), start_date, end_date)
        raw_weight_rows = contribution_weight_rows.get(event_id, [])
        event_fund_cache: dict[str, list[tuple[str, float]]] = {}
        if event_id in exact_event_ids:
            event_fund_codes = {clean_text(item.get("基金代码"), "") for item in raw_weight_rows}
            event_fund_codes.discard("")
            event_fund_cache = load_fund_nav_cache(conn, event_fund_codes, start_date, end_date)
        pre_series = weighted_fund_return_series(raw_weight_rows, "调前权重_百分比", start_date, end_date, event_fund_cache, reference) if event_id in exact_event_ids else []
        if not pre_series and event_id not in exact_event_ids:
            pre_series = sample_pre_rebalance_line(start_date, end_date, row["调前仓位收益率_百分比"], reference)
        fund_after_series = weighted_fund_return_series(raw_weight_rows, "调后权重_百分比", start_date, end_date, event_fund_cache, reference) if event_id in exact_event_ids else []
        if event_id in exact_event_ids and fund_after_series:
            after_series = fund_after_series
        contribution_curves.setdefault(strategy_id, {})[event_id] = {
            "标题": clean_text(row["调仓标题"], "调仓贡献"),
            "起始日期": start_date,
            "结束日期": end_date,
            "评估状态": clean_text(row.get("评估状态"), "未评价"),
            "评估说明": clean_text(row.get("评估说明"), ""),
            "调仓评价": clean_text(row["结果评价"], "未评价"),
            "调仓超额": round_or_none(row["调仓超额_百分比"]),
            "series": {
                "调仓前仓位模拟": {"模式": "return", "points": pre_series},
                "调仓后仓位实际": {"模式": "return", "points": after_series},
                "基准业绩": {"模式": "return", "points": normalize_segment(benchmark_series.get(strategy_id, []), start_date, end_date)},
                "沪深300业绩": {"模式": "return", "points": normalize_segment(hs300_series.get(strategy_id, []), start_date, end_date)},
            },
        }

    # Qieman discloses complete before/after fund weights but is intentionally
    # absent from the generic rebalance-quality scoring table. Build only
    # evidence-backed contribution curves directly from those official weights.
    # If at least 98% of either side lacks adjusted fund NAV at both endpoints,
    # leave the curve absent instead of shaping/interpolating a substitute.
    for strategy_id, events in rebalance_events.items():
        if not strategy_id.startswith("qieman__"):
            continue
        for event in events[:QIEMAN_CONTRIBUTION_EVENT_LIMIT]:
            event_id = clean_text(event.get("事件ID"), "")
            if not event_id or event_id in contribution_curves.get(strategy_id, {}):
                continue
            start_date = clean_text(event.get("调仓日期"), "")
            end_date = clean_text(rebalance_event_end_dates.get(event_id), "")
            raw_weight_rows = contribution_weight_rows.get(event_id, [])
            if not start_date or not end_date or not raw_weight_rows:
                continue
            pre_sum = sum(as_float(item.get("调前权重_百分比")) or 0.0 for item in raw_weight_rows)
            post_sum = sum(as_float(item.get("调后权重_百分比")) or 0.0 for item in raw_weight_rows)
            if not (99.0 <= pre_sum <= 101.0 and 99.0 <= post_sum <= 101.0):
                continue
            analysis_rows: list[dict[str, Any]] = []
            for item in raw_weight_rows:
                analysis_item = dict(item)
                fund_code = clean_text(item.get("基金代码"), "")
                series = rebalance_fund_nav_cache.get(fund_code, [])
                if value_on_or_before(series, start_date) is None or value_on_or_before(series, end_date) is None:
                    alternate = alternate_fund_codes.get((fund_code, start_date))
                    if alternate:
                        analysis_item["基金代码_分析"] = alternate
                analysis_rows.append(analysis_item)
            pre_coverage = fund_nav_weight_coverage(
                analysis_rows, "调前权重_百分比", start_date, end_date, rebalance_fund_nav_cache
            )
            post_coverage = fund_nav_weight_coverage(
                analysis_rows, "调后权重_百分比", start_date, end_date, rebalance_fund_nav_cache
            )
            if min(pre_coverage, post_coverage) < CONTRIBUTION_MIN_NAV_WEIGHT_COVERAGE_PCT:
                continue
            reference = normalize_segment(benchmark_series.get(strategy_id, []), start_date, end_date)
            pre_series = weighted_fund_return_series(
                analysis_rows,
                "调前权重_百分比",
                start_date,
                end_date,
                rebalance_fund_nav_cache,
                reference,
                max_points=400,
            )
            post_series = weighted_fund_return_series(
                analysis_rows,
                "调后权重_百分比",
                start_date,
                end_date,
                rebalance_fund_nav_cache,
                reference,
                max_points=400,
            )
            if len(pre_series) < 2 or len(post_series) < 2:
                continue
            contribution_curves.setdefault(strategy_id, {})[event_id] = {
                "标题": clean_text(event.get("调仓标题"), "官方调仓贡献"),
                "起始日期": start_date,
                "结束日期": end_date,
                "评估状态": "官方权重净值回放",
                "评估说明": (
                    "且慢官方调前/调后基金权重均完整；按基金复权净值逐日回放，"
                    f"调前净值权重覆盖{pre_coverage:.2f}%，调后覆盖{post_coverage:.2f}%。"
                    "未使用端点插值或参考曲线形态缩放。"
                ),
                "调仓评价": "仅展示可核验贡献曲线，未混入通用调仓胜负评分",
                "调仓超额": None,
                "series": {
                    "调仓前仓位模拟": {"模式": "return", "points": pre_series},
                    "调仓后仓位实际": {"模式": "return", "points": post_series},
                    "基准业绩": {"模式": "return", "points": reference},
                    "沪深300业绩": {"模式": "return", "points": normalize_segment(hs300_series.get(strategy_id, []), start_date, end_date)},
                },
            }
    signal_events, signal_instructions = load_signal_detail_maps(conn)
    return {
        "direct_holdings": direct_holdings,
        "projected_holdings": projected_holdings,
        "rebalance_events": rebalance_events,
        "rebalance_holdings": rebalance_holdings,
        "signal_events": signal_events,
        "signal_instructions": signal_instructions,
        "curves": curves,
        "curve_sources": curve_sources,
        "curve_warnings": curve_warnings,
        "benchmark_meta": benchmark_meta,
        "interval_matrix": interval_matrix,
        "annual_matrix": annual_matrix,
        "contribution_curves": contribution_curves,
        "strategy_relationships": relationship_map,
    }


def strategy_detail(strategy_id: str, context: dict[str, Any], detail_maps: dict[str, Any]) -> dict[str, Any]:
    uid = "\u7edf\u4e00\u7b56\u7565ID"
    channel_strategy_id = "\u6e20\u9053\u7b56\u7565ID"
    strategy_name = "\u7b56\u7565\u540d\u79f0"
    channel = "\u6e20\u9053"
    advisor = "\u6295\u987e\u673a\u6784"
    strategy_type = "\u7b56\u7565\u7c7b\u578b"
    risk_level = "\u98ce\u9669\u7b49\u7ea7"
    inception_date = "\u6210\u7acb\u65e5\u671f"
    operation_status_key = "\u8fd0\u4f5c\u72b6\u6001"
    min_invest = "\u8d77\u6295\u91d1\u989d"
    service_fee = "\u6295\u987e\u8d39\u7387"
    holding_suggestion = "\u5efa\u8bae\u6301\u6709\u65f6\u957f"
    benchmark = "\u4e1a\u7ee9\u57fa\u51c6"
    benchmark_desc = "\u4e1a\u7ee9\u57fa\u51c6\u8bf4\u660e"
    tags = "\u6807\u7b7e"
    tags_json = "\u6807\u7b7eJSON"
    strategy_concept = "\u7b56\u7565\u6982\u5ff5"
    strategy_desc = "\u7b56\u7565\u63cf\u8ff0"
    raw_source = "\u539f\u59cb\u6570\u636e\u6765\u6e90"
    raw_url = "\u539f\u59cb\u6765\u6e90URL"
    official_nav = "\u5b98\u65b9\u5355\u4f4d\u51c0\u503c"
    unit_nav = "\u5355\u4f4d\u51c0\u503c"
    official_return = "\u5b98\u65b9\u7d2f\u8ba1\u6536\u76ca"
    cumulative_return = "\u7d2f\u8ba1\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
    own_nav = "\u81ea\u5efa\u5355\u4f4d\u51c0\u503c"
    gross_end_nav = "\u6a21\u62df\u8d39\u524d\u5355\u4f4d\u51c0\u503c_\u671f\u672b"
    net_end_nav = "\u6a21\u62df\u5355\u4f4d\u51c0\u503c_\u671f\u672b"
    gross_nav = "\u8d39\u524d\u5355\u4f4d\u51c0\u503c"
    net_nav = "\u8d39\u540e\u5355\u4f4d\u51c0\u503c"
    own_return = "\u81ea\u5efa\u7d2f\u8ba1\u6536\u76ca"
    official_diff = "\u4e0e\u5b98\u65b9\u504f\u5dee"
    annual_return = "\u5e74\u5316\u6536\u76ca"
    simulated_annual = "\u6a21\u62df\u5e74\u5316\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
    max_drawdown = "\u6700\u5927\u56de\u64a4"
    current_drawdown = "\u5f53\u524d\u56de\u64a4"
    volatility = "\u6ce2\u52a8\u7387"
    simulated_volatility = "\u6a21\u62df\u6ce2\u52a8\u7387_\u5e74\u5316_\u767e\u5206\u6bd4"
    sharpe = "\u590f\u666e\u6bd4\u7387"
    simulated_sharpe = "\u6a21\u62df\u590f\u666e_\u5e74\u5316\u65e0\u98ce\u96690"
    compare_basis = "\u5b98\u65b9\u5bf9\u6bd4\u53e3\u5f84"
    app_basis = "App\u5c55\u793a\u5bf9\u6bd4\u53e3\u5f84"
    default_basis = "App\u5c55\u793a\u9ed8\u8ba4\u53e3\u5f84"
    comparable_records = "\u53ef\u6bd4\u8bb0\u5f55\u6570"
    official_comparable_records = "\u5b98\u65b9\u53ef\u6bd4\u8bb0\u5f55\u6570"
    holding_source_key = "\u6301\u4ed3\u6765\u6e90"
    latest_holding_date = "\u6700\u65b0\u6301\u4ed3\u65e5"
    holding_fund_count = "\u6301\u4ed3\u57fa\u91d1\u6570"
    completeness = "\u6570\u636e\u5b8c\u6574\u6027"
    audit_conclusion = "\u7a3d\u6838\u7ed3\u8bba"
    field_key = "\u5b57\u6bb5"
    value_key = "\u503c"

    row = context["strategy"]
    list_row = context["listRow"]
    summary_row = dict(list_row)
    quality = context["quality"]
    deviation = context["deviation"]
    compare = context["compare"]
    official = context["official"]
    holding_audit = context["holdingAudit"]
    classification = context.get("classification", {})
    benchmark_status = context.get("benchmarkStatus", {})
    operation_start = parse_ymd(row[inception_date])
    operation_end = parse_ymd(list_row.get("最新业绩日期") or list_row.get("收益数据截至") or list_row.get(latest_holding_date) or list_row.get("最近调仓日"))
    operation_days = (operation_end - operation_start).days + 1 if operation_start and operation_end and operation_end >= operation_start else None
    summary_row["运作天数"] = operation_days
    official_unit_nav_value = list_row.get("官方单位净值") or (official.get("披露单位净值") if "披露单位净值" in official else official.get(unit_nav))
    official_return_value = list_row.get("官方累计收益") or (official.get("披露累计收益率_百分比") if "披露累计收益率_百分比" in official else official.get(cumulative_return))
    annual_return_value = list_row.get(annual_return)
    if annual_return_value is None:
        annual_return_value = quality.get(simulated_annual)
    volatility_value = list_row.get(volatility)
    if volatility_value is None:
        volatility_value = quality.get(simulated_volatility)
    sharpe_value = list_row.get(sharpe)
    if sharpe_value is None:
        sharpe_value = quality.get(simulated_sharpe)
    tag_text = "\u3001".join(parse_tags(row.get(tags_json))) or "\u672a\u62ab\u9732"
    concept_text = tag_text if tag_text != "\u672a\u62ab\u9732" else clean_text(row.get(strategy_desc), "\u672a\u62ab\u9732")[:120]
    profile_fields = [
        (uid, row[uid]),
        ("\u7b56\u7565\u4ee3\u7801", row[channel_strategy_id]),
        (strategy_name, row[strategy_name]),
        (channel, list_row[channel]),
        (advisor, clean_text(row[advisor])),
        ("披露策略类型", list_row.get("披露策略类型")),
        ("披露风险等级", list_row.get("披露风险等级")),
        ("研报产品类型", list_row.get("研报产品类型")),
        ("研报股票子类型", list_row.get("研报股票子类型")),
        ("业务分类", list_row.get("业务分类")),
        ("天天当前对客展示", list_row.get("天天当前对客展示")),
        ("天天展示状态", list_row.get("天天展示状态")),
        (strategy_type, clean_text(row[strategy_type])),
        (risk_level, clean_text(row[risk_level])),
        (inception_date, row[inception_date]),
        ("\u8fd0\u4f5c\u5929\u6570", operation_days),
        (operation_status_key, list_row[operation_status_key]),
        (min_invest, row[min_invest]),
        (service_fee, row[service_fee]),
        (holding_suggestion, row[holding_suggestion]),
        (benchmark, row[benchmark]),
        (benchmark_desc, row[benchmark]),
        (tags, tag_text),
        (strategy_concept, concept_text),
        (strategy_desc, row[strategy_desc]),
        (raw_source, row[raw_url]),
    ]
    performance_fields = [
        (official_nav, round_or_none(official_unit_nav_value, 6)),
        (official_return, round_or_none(official_return_value)),
        ("最新业绩日期", list_row.get("最新业绩日期") or list_row.get("收益数据截至")),
        (annual_return, round_or_none(annual_return_value)),
        (max_drawdown, list_row[max_drawdown]),
        (current_drawdown, list_row.get(current_drawdown)),
        (volatility, round_or_none(volatility_value)),
        (sharpe, round_or_none(sharpe_value, 4)),
        ("单次平均换手率", list_row.get("单次平均换手率")),
        ("年化换手率", list_row.get("年化换手率")),
        ("调仓频率", list_row.get("调仓频率")),
        ("最近一年调仓次数", list_row.get("最近一年调仓次数")),
        (compare_basis, clean_text(quality.get(app_basis) or compare.get("更接近披露口径") or deviation.get(default_basis))),
        (comparable_records, compare.get("共同交易日数") or quality.get(official_comparable_records) or deviation.get(official_comparable_records)),
    ]
    classification_fields = [
        ("研报产品类型", list_row.get("研报产品类型")),
        ("研报股票子类型", list_row.get("研报股票子类型")),
        ("业务分类", list_row.get("业务分类")),
        ("业务分类依据", list_row.get("业务分类依据")),
        ("天天当前对客展示", list_row.get("天天当前对客展示")),
        ("天天展示状态", list_row.get("天天展示状态")),
        ("天天展示判定依据", list_row.get("天天展示判定依据")),
        ("主可比池", classification.get("主可比池")),
        ("市场地域", classification.get("市场地域")),
        ("主动被动", classification.get("主动被动")),
        ("特殊标签", classification.get("特殊标签")),
        ("策略实现标签", classification.get("策略实现标签")),
        ("权益基金权重", classification.get("权益基金权重")),
        ("债券基金权重", classification.get("债券基金权重")),
        ("货币基金权重", classification.get("货币基金权重")),
        ("混合基金权重", classification.get("混合基金权重")),
        ("QDII权重", classification.get("QDII权重")),
        ("指数基金权重", classification.get("指数基金权重")),
        ("主动基金权重", classification.get("主动基金权重")),
        ("基准权益权重", classification.get("基准权益权重")),
        ("基准债券权重", classification.get("基准债券权重")),
        ("基准货币权重", classification.get("基准货币权重")),
        ("基准风险资产权重", classification.get("基准风险资产权重")),
        ("基准风险资产权重说明", classification.get("基准风险资产权重说明")),
        ("基准风险资产权重_百分比", classification.get("基准风险资产权重_百分比")),
        ("权益中枢", classification.get("权益中枢")),
        ("固收中枢", classification.get("固收中枢")),
        ("基准风险资产中枢", classification.get("基准风险资产中枢")),
        ("海外配置中枢", classification.get("海外配置中枢")),
        ("指数化程度", classification.get("指数化程度")),
        ("主动管理程度", classification.get("主动管理程度")),
        ("风险资产偏离", classification.get("风险资产偏离")),
        ("配置风格标签", classification.get("配置风格标签")),
        ("基准结构类型", classification.get("基准结构类型")),
        ("非权益比较轨道", classification.get("非权益比较轨道")),
        ("正式可比池", classification.get("正式可比池")),
        ("可比池样本资格", classification.get("可比池样本资格")),
        ("可比池说明", classification.get("可比池说明")),
        ("基准互斥权重合计_百分比", classification.get("基准互斥权重合计_百分比")),
        ("基准港股权益权重", classification.get("基准港股权益权重")),
        ("基准海外权益权重", classification.get("基准海外权益权重")),
        ("是否多元策略", classification.get("是否多元策略")),
        ("多元策略标签", classification.get("多元策略标签")),
        ("基准映射置信度", classification.get("基准映射置信度")),
        ("基准资产已映射权重", classification.get("基准资产已映射权重")),
        ("基准资产未映射权重", classification.get("基准资产未映射权重")),
        *[(field, classification.get(field)) for field in BENCHMARK_ASSET_MAJOR_FIELDS],
        *[(field, classification.get(field)) for field in BENCHMARK_ASSET_CATEGORY_FIELDS],
        ("基准可用状态", classification.get("基准可用状态")),
        ("基础数据等级", classification.get("基础数据等级")),
        ("费率状态", classification.get("费率状态")),
        ("年化投顾费率", classification.get("年化投顾费率")),
        ("分类依据", classification.get("分类依据")),
    ]
    direct_holdings = detail_maps["direct_holdings"].get(strategy_id, [])
    projected_holdings = detail_maps["projected_holdings"].get(strategy_id, [])
    if projected_holdings:
        direct_holding_date = max((clean_text(row.get("持仓日期"), "") for row in direct_holdings if clean_text(row.get("持仓日期"), "")), default=None)
        projected_holding_date = max((clean_text(row.get("持仓日期"), "") for row in projected_holdings if clean_text(row.get("持仓日期"), "")), default=None)
        if direct_holding_date and projected_holding_date and projected_holding_date > direct_holding_date:
            holding_source = "最后调仓仓位 + 基金复权收益滚动到最新净值日"
        else:
            holding_source = "\u6700\u540e\u8c03\u4ed3\u63a8\u7b97\u8865\u9f50"
        holdings = projected_holdings
    else:
        holding_source = "App\u76f4\u63a5\u62ab\u9732"
        holdings = direct_holdings
    base_holding_date = max((clean_text(row.get("持仓日期"), "") for row in holdings if clean_text(row.get("持仓日期"), "")), default=None)
    rebalance_events = detail_maps["rebalance_events"].get(strategy_id, [])
    latest_rebalance_event = rebalance_events[0] if rebalance_events else {}
    latest_rebalance_date = clean_text(latest_rebalance_event.get("调仓日期"), "")
    latest_rebalance_holdings = detail_maps["rebalance_holdings"].get(strategy_id, {}).get(str(latest_rebalance_event.get("事件ID")), []) if latest_rebalance_event else []
    latest_rebalance_positive_holdings = [row for row in latest_rebalance_holdings if (as_float(row.get("权重")) or 0.0) > 0]
    current_holding_date = list_row[latest_holding_date]
    if latest_rebalance_date and latest_rebalance_positive_holdings and (not base_holding_date or latest_rebalance_date > base_holding_date):
        holding_source = "最新调仓后仓位"
        current_holding_date = latest_rebalance_date
        holdings = []
        for row in latest_rebalance_positive_holdings:
            current = dict(row)
            current["持仓日期"] = latest_rebalance_date
            current["上次调仓后权重"] = current.get("权重")
            current["权重变化"] = 0
            holdings.append(current)
    elif latest_rebalance_date and latest_rebalance_holdings and (not base_holding_date or latest_rebalance_date > base_holding_date):
        holding_source = "最新调仓后清仓/止盈"
        current_holding_date = latest_rebalance_date
        holdings = []
    elif base_holding_date:
        current_holding_date = base_holding_date
    position_snapshots = [
        {
            "id": "current",
            "类型": "当前仓位",
            "日期": current_holding_date,
            "标题": "当前仓位",
            "说明": holding_source,
            "holdings": holdings,
        }
    ]
    for event in rebalance_events:
        event_id = event.get("事件ID")
        position_snapshots.append(
            {
                "id": event_id,
                "类型": "历史调仓",
                "日期": event.get("调仓日期"),
                "标题": event.get("调仓标题"),
                "披露日期": event.get("披露日期"),
                "调仓原因": event.get("调仓原因"),
                "说明": f'调后权重和 {round_or_none(event.get("调后权重和")) or "-"}%，基金数 {event.get("调仓基金数") or 0}',
                "holdings": detail_maps["rebalance_holdings"].get(strategy_id, {}).get(str(event_id), []),
            }
        )
    signal_events: list[dict[str, Any]] = []
    for event in detail_maps["signal_events"].get(strategy_id, []):
        event_id = clean_text(event.get("信号事件ID"), "")
        signal_events.append(
            {
                **event,
                "instructions": detail_maps["signal_instructions"].get(strategy_id, {}).get(event_id, []),
            }
        )
    signal_summary = {
        "信号事件数": list_row.get("信号事件数"),
        "最近信号日": list_row.get("最近信号日"),
        "信号指令数": list_row.get("信号指令数"),
        "买入指令数": list_row.get("买入指令数"),
        "卖出指令数": list_row.get("卖出指令数"),
        "加仓指令数": list_row.get("加仓指令数"),
        "减仓指令数": list_row.get("减仓指令数"),
        "信号胜率_1月": list_row.get("信号胜率_1月"),
        "信号胜率_3月": list_row.get("信号胜率_3月"),
        "信号胜率_6月": list_row.get("信号胜率_6月"),
        "信号胜率_1年": list_row.get("信号胜率_1年"),
        "信号加权方向收益_1月": list_row.get("信号加权方向收益_1月"),
        "信号加权方向收益_3月": list_row.get("信号加权方向收益_3月"),
        "信号加权方向收益_6月": list_row.get("信号加权方向收益_6月"),
        "信号加权方向收益_1年": list_row.get("信号加权方向收益_1年"),
    }
    curve_warnings = list(detail_maps["curve_warnings"].get(strategy_id, []))
    strategy_curve = (detail_maps["curves"].get(strategy_id, {}) or {}).get("披露业绩", {})
    strategy_curve_points = strategy_curve.get("points", []) if isinstance(strategy_curve, dict) else []
    if strategy_id.startswith("gfsec_fima__") and len(strategy_curve_points) < 2:
        curve_warnings.append(
            f"该财富管家产品成立于{clean_text(row.get(inception_date), '未披露')}，"
            "官方尚未产生至少两个可验证业绩点；新发行或仍在参与期的产品需等待官方开始披露，页面不构造收益或走势图。"
        )
    curve_warnings = list(dict.fromkeys(curve_warnings))
    return {
        "id": strategy_id,
        "summary": summary_row,
        "profileFields": [{field_key: label, value_key: value} for label, value in profile_fields],
        "performanceFields": [{field_key: label, value_key: value} for label, value in performance_fields],
        "classification": classification,
        "classificationFields": [{field_key: label, value_key: value} for label, value in classification_fields],
        "benchmarkStatus": benchmark_status,
        "qualityChecks": context["qualityChecks"],
        "intervalMatrix": detail_maps["interval_matrix"].get(strategy_id, []),
        "annualMatrix": detail_maps["annual_matrix"].get(strategy_id, []),
        "benchmarkMeta": detail_maps["benchmark_meta"].get(strategy_id, {}),
        "holdingMeta": {
            holding_source_key: holding_source,
            latest_holding_date: current_holding_date,
            holding_fund_count: len(holdings),
            completeness: list_row[completeness],
            audit_conclusion: clean_text(holding_audit.get(audit_conclusion), "\u672a\u751f\u6210\u7a3d\u6838"),
        },
        "positionSnapshots": position_snapshots,
        "signalSummary": signal_summary,
        "signalEvents": signal_events,
        "strategyRelation": detail_maps["strategy_relationships"].get(strategy_id, {}),
        "curves": detail_maps["curves"].get(strategy_id, {}),
        "curveSources": detail_maps["curve_sources"].get(strategy_id, {}),
        "curveWarnings": curve_warnings,
        "contributionCurves": detail_maps["contribution_curves"].get(strategy_id, {}),
    }

def build_payload(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    log_progress("build payload: strategy rows")
    strategies, context = build_strategy_rows(conn, args.algorithm_version)
    log_progress("build payload: overview")
    display_channels = tuple(sorted(DISPLAY_STRATEGY_CHANNEL_IDS))
    display_placeholders = ",".join("?" for _ in display_channels)
    total_strategy_count = int(
        conn.execute(
            f'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID" IN ({display_placeholders})',
            display_channels,
        ).fetchone()[0]
        or 0
    )
    ttfund_count = int(conn.execute('SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID" = ?', ("ttfund",)).fetchone()[0] or 0)
    rebalance_count = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT r."统一策略ID")
            FROM "策略调仓事件" r
            JOIN "策略信息" s ON s."统一策略ID" = r."统一策略ID"
            WHERE s."渠道ID" IN ({display_placeholders})
            """,
            display_channels,
        ).fetchone()[0]
        or 0
    )
    official_count = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT p."统一策略ID")
            FROM "策略产品披露净值" p
            JOIN "策略信息" s ON s."统一策略ID" = p."统一策略ID"
            WHERE p."是否可画曲线" = 1
              AND s."渠道ID" IN ({display_placeholders})
            """,
            display_channels,
        ).fetchone()[0]
        or 0
    )
    simulated_count = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT q."统一策略ID")
            FROM "策略模拟净值质量" q
            JOIN "策略信息" s ON s."统一策略ID" = q."统一策略ID"
            WHERE q."算法版本" = ?
              AND q."是否纳入模拟" = 1
              AND s."渠道ID" IN ({display_placeholders})
            """,
            (args.algorithm_version, *display_channels),
        ).fetchone()[0]
        or 0
    )
    quality_map = {sid: ctx["quality"] for sid, ctx in context.items()}
    display_channel_ids = {
        canonical_business_channel(ctx["strategy"]["渠道ID"], ctx["channel"].get("渠道名称"))
        for ctx in context.values()
    }
    all_channel_ids = {row["渠道ID"] for row in fetch_all(conn, 'SELECT DISTINCT "渠道ID" FROM "策略信息"')}
    hidden_channels = sorted(all_channel_ids - DISPLAY_STRATEGY_CHANNEL_IDS)
    generated_at = format_beijing_minute()
    overview = {
        "生成时间": generated_at,
        "数据刷新时间": generated_at,
        "算法版本": args.algorithm_version,
        "数据更新至": latest_business_date(conn),
        "接入渠道数": len(display_channel_ids or DISPLAY_STRATEGY_CHANNEL_IDS),
        "策略总数": total_strategy_count,
        "天天策略数": ttfund_count,
        "有历史调仓策略数": rebalance_count,
        "有官方业绩策略数": official_count,
        "纳入回放策略数": simulated_count,
        "策略基金净值缺失数": missing_nav_fund_count(conn),
        "基金数": safe_count(conn, "基金信息"),
        "基金净值行数": safe_count(conn, "基金日度净值"),
        "基金分红事件数": safe_count(conn, "基金分红送配"),
    }
    summary = {
        "overview": overview,
        "fieldDictionary": FIELD_DICTIONARY,
        "strategies": strategies,
    }
    log_progress("build payload: channel stats")
    summary["channelStats"] = build_channel_stats(conn, args.algorithm_version)
    log_progress("build payload: table counts")
    summary["tableCounts"] = table_counts(conn)
    log_progress("build payload: global benchmarks")
    summary["globalBenchmarks"] = load_global_benchmark_catalog(conn)
    log_progress("build payload: rebalance events")
    summary["rebalanceEvents"] = enrich_rebalance_events_with_strategy_fields(
        build_rebalance_insight_events(conn),
        context,
    )
    summary["institutionAdjustmentEvents"] = build_institution_adjustment_events(conn)
    log_progress("build payload: benchmark disclosure and field audit")
    summary["benchmarkDisclosure"] = build_benchmark_disclosure(strategies)
    summary["fieldMissingnessAudit"] = build_field_missingness_audit(strategies)
    summary["strategyListStats"] = {
        "展示策略数": len(strategies),
        "展示渠道数": len(display_channel_ids),
        "隐藏渠道数": len(hidden_channels),
        "隐藏渠道ID": hidden_channels,
        "完整策略数": len([row for row in strategies if row["数据完整性"] == "完整"]),
        "不完整策略数": len([row for row in strategies if row["数据完整性"] != "完整"]),
    }
    return summary, context


def page_html(title: str, active: str, main_id: str, script_name: str) -> str:
    topbar = render_system_topbar(active)
    body_attr = ""
    if active == "compare":
        body_attr = ' data-page="compare"'
    elif active == "mixed_performance_scatter":
        body_attr = ' data-page="mixed-performance-scatter"'
    pack_scripts = f"""  <script src="./data/data_quality_pack.js?v={ASSET_VERSION}"></script>
"""
    if active in {"compare", "insights"} or script_name == "ai-strategy.js":
        pack_scripts += f"""
  <script src="./data/holding_snapshot_pack.js?v={ASSET_VERSION}"></script>
"""
    if script_name == "ai-strategy.js":
        pack_scripts += f"""  <script src="./data/fund_detail_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/ai_semantic_index.js?v={ASSET_VERSION}"></script>
  <script src="./data/ai_topic_evidence_pack.js?v={ASSET_VERSION}"></script>
  <script src="./config/模型服务配置.js?v={ASSET_VERSION}"></script>
  <script src="./config/ai-strategy-local-config.js?v={ASSET_VERSION}"></script>
  <script src="./assets/ai-strategy-config.js?v={ASSET_VERSION}"></script>
"""
    if script_name == "data-quality.js":
        pack_scripts += f"""  <script src="./data/data_pack_manifest.js?v={ASSET_VERSION}"></script>
  <script src="./data/standard_entity_dictionary.js?v={ASSET_VERSION}"></script>
"""
    topic_page_config = ""
    if script_name == "topic-analysis.js":
        pack_scripts += f"""  <script src="./data/topic_analysis_manifest.js?v={ASSET_VERSION}"></script>"""
        if active == "ai_topic":
            topic_page_config = '  <script>window.__BASIC_TOPIC_ANALYSIS_PAGE__ = {"themeId":"ai_core","lockTheme":true,"title":"AI主题分析"};</script>\n'
    if script_name == "target-profit-analysis.js":
        pack_scripts += f"""  <script src="./data/target_profit_analysis_pack.js?v={ASSET_VERSION}"></script>
"""
    if script_name == "advisor-fof-ranking.js":
        pack_scripts += f"""  <script src="./data/advisor_fof_ranking_pack.js?v={ASSET_VERSION}"></script>
"""
    if script_name == "mixed-performance-scatter.js":
        pack_scripts += f"""  <script src="./data/mixed_performance_scatter_pack.js?v={ASSET_VERSION}"></script>
"""
    summary_script = "" if script_name in {"topic-analysis.js", "target-profit-analysis.js"} else f'  <script src="./data/basic_summary_core.js?v={ASSET_VERSION}"></script>\n'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/basic.css?v={ASSET_VERSION}">
</head>
<body{body_attr}>
  {topbar}
  <main id="{main_id}" class="page-shell"><section id="pageLoadingStatus" class="page-loading-status" role="status" aria-live="polite">数据正在加载，请稍等。</section></main>
  <section id="globalQualityGate" class="global-quality-gate" hidden></section>
  <div id="fieldModal" class="modal" hidden>
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="fieldModalTitle">
      <button id="fieldModalClose" class="modal-close" type="button" aria-label="关闭">×</button>
      <h2 id="fieldModalTitle"></h2>
      <p id="fieldModalBody"></p>
    </div>
  </div>
  <script src="./assets/basic-common.js?v={ASSET_VERSION}"></script>
{summary_script}  {pack_scripts}
{topic_page_config}
  <script>window.BasicData && window.BasicData.renderGlobalQualityGate && window.BasicData.renderGlobalQualityGate("{active}");</script>
  <script src="./assets/{script_name}?v={ASSET_VERSION}"></script>
</body>
</html>
"""


def strategy_page_html() -> str:
    topbar = render_system_topbar("strategy_detail", brand_subtitle="策略详情")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>策略详情</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/basic.css?v={ASSET_VERSION}">
</head>
<body>
  {topbar}
  <main id="strategyDetailPage" class="page-shell"><section id="pageLoadingStatus" class="page-loading-status" role="status" aria-live="polite">数据正在加载，请稍等。</section></main>
  <section id="globalQualityGate" class="global-quality-gate" hidden></section>
  <div id="fieldModal" class="modal" hidden>
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="fieldModalTitle">
      <button id="fieldModalClose" class="modal-close" type="button" aria-label="关闭">×</button>
      <h2 id="fieldModalTitle"></h2>
      <p id="fieldModalBody"></p>
    </div>
  </div>
  <script src="./assets/basic-common.js?v={ASSET_VERSION}"></script>
  <script src="./data/data_quality_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/basic_summary_core.js?v={ASSET_VERSION}"></script>
  <script src="./data/fund_detail_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/ai_semantic_index.js?v={ASSET_VERSION}"></script>
  <script>window.BasicData && window.BasicData.renderGlobalQualityGate && window.BasicData.renderGlobalQualityGate("strategy_detail");</script>
  <script src="./assets/strategy-detail.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""


def backup_style_strategies_html() -> str:
    topbar = render_system_topbar("strategies", brand_title="投顾业务分析", brand_subtitle="市场、产品与经营洞察")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>策略列表</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/basic.css?v={ASSET_VERSION}">
</head>
<body>
  {topbar}
  <main id="strategyListPage" class="page-shell"><section id="pageLoadingStatus" class="page-loading-status" role="status" aria-live="polite">数据正在加载，请稍等。</section></main>
  <section id="globalQualityGate" class="global-quality-gate" hidden></section>
  <div id="fieldModal" class="modal" hidden>
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="fieldModalTitle">
      <button id="fieldModalClose" class="modal-close" type="button" aria-label="关闭">×</button>
      <h2 id="fieldModalTitle"></h2>
      <div id="fieldModalBody"></div>
    </div>
  </div>
  <script src="./assets/basic-common.js?v={ASSET_VERSION}"></script>
  <script src="./data/data_quality_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/basic_summary_core.js?v={ASSET_VERSION}"></script>
  <script>window.BasicData && window.BasicData.renderGlobalQualityGate && window.BasicData.renderGlobalQualityGate("strategies");</script>
  <script src="./assets/strategies.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""


def fund_page_html() -> str:
    topbar = render_system_topbar("fund_detail", brand_title="投顾业务分析", brand_subtitle="底层基金详情")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>基金详情</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="./assets/basic.css?v={ASSET_VERSION}">
</head>
<body>
  {topbar}
  <main id="fundDetailPage" class="page-shell"><section id="pageLoadingStatus" class="page-loading-status" role="status" aria-live="polite">数据正在加载，请稍等。</section></main>
  <section id="globalQualityGate" class="global-quality-gate" hidden></section>
  <div id="fieldModal" class="modal" hidden>
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="fieldModalTitle">
      <button id="fieldModalClose" class="modal-close" type="button" aria-label="关闭">×</button>
      <h2 id="fieldModalTitle"></h2>
      <div id="fieldModalBody"></div>
    </div>
  </div>
  <script src="./assets/basic-common.js?v={ASSET_VERSION}"></script>
  <script src="./data/data_quality_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/fund_detail_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/fund_economic_exposure_pack.js?v={ASSET_VERSION}"></script>
  <script src="./data/ai_semantic_index.js?v={ASSET_VERSION}"></script>
  <script src="./data/fund_details/_manifest.js?v={ASSET_VERSION}"></script>
  <script>window.BasicData && window.BasicData.renderGlobalQualityGate && window.BasicData.renderGlobalQualityGate("fund_detail");</script>
  <script src="./assets/fund-detail.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""


CSS = r"""
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #182230;
  --muted: #657080;
  --line: #dde3ea;
  --soft: #eef3f8;
  --brand: #166c77;
  --brand-dark: #0f4f58;
  --accent: #a15c2f;
  --blue: #2563eb;
  --purple: #7c3aed;
  --gold: #b7791f;
  --bad: #b42318;
  --good: #0f7b4f;
  --pos: #c02f2f;
  --neg: #12805c;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--bg);
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
  line-height: 1.5;
}
a { color: inherit; text-decoration: none; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.96);
  border-bottom: 1px solid var(--line);
}
.topbar-inner {
  max-width: 1440px;
  margin: 0 auto;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 220px;
}
.brand-mark {
  width: 34px;
  height: 34px;
  display: inline-grid;
  place-items: center;
  background: var(--brand);
  color: #fff;
  border-radius: 6px;
  font-weight: 700;
}
.brand small { display: block; color: var(--muted); font-size: 12px; margin-top: 1px; }
.nav { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.nav-link {
  padding: 7px 10px;
  border-radius: 6px;
  color: #405063;
  font-size: 14px;
}
.nav-link:hover, .nav-link.is-active { background: var(--soft); color: var(--brand-dark); }
.page-shell { max-width: 1440px; margin: 0 auto; padding: 22px 24px 44px; }
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
.ai-candidate-note {
  font-size: 12px;
  line-height: 1.6;
}
.page-title {
  margin: 0 0 16px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-end;
}
.title-pills { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; max-width: 760px; }
h1 { margin: 0; font-size: 24px; line-height: 1.25; }
h2 { margin: 0; font-size: 18px; }
h3 { margin: 0 0 10px; font-size: 15px; }
.desc { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  min-width: 0;
}
.hero-panel { padding: 0; overflow: hidden; }
.strategy-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(340px, 0.9fr);
  gap: 16px;
  padding: 18px;
  background: linear-gradient(135deg, #f7fbfc 0%, #ffffff 52%, #fff8f2 100%);
}
.hero-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hero-title h1 { font-size: 26px; }
.hero-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.hero-dates { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-top:14px; }
.date-card { min-width:0; border:1px solid #cfe2e6; border-radius:8px; background:#f4fbfc; padding:9px 10px; }
.date-card span { display:block; color:#486172; font-size:12px; }
.date-card strong { display:block; margin-top:3px; font-size:16px; line-height:1.25; color:#0f4f58; overflow-wrap:anywhere; }
.date-card.is-date strong { font-size:20px; }
.hero-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.hero-kpi { border: 1px solid #d7e0ea; border-radius: 8px; background: rgba(255,255,255,.82); padding: 10px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
.hero-kpi span { display:block; color: #56657a; font-size:12px; }
.hero-kpi strong { display:block; margin-top:4px; font-size:20px; letter-spacing:0; color:#172033; }
.hero-kpi strong .ret-pos, .hero-kpi strong .ret-neg, .hero-kpi strong .ret-zero { font-size:22px; font-weight:750; }
.hero-kpi.is-pos { border-color:#efc2bc; background:#fff5f2; }
.hero-kpi.is-neg { border-color:#b7dfcf; background:#f2fbf7; }
.hero-kpi.is-zero { border-color:#cbd5e1; background:#f8fafc; }
.hero-kpi.is-pos span { color:#8f2e25; }
.hero-kpi.is-neg span { color:#1f6f4a; }
.hero-kpi.is-pos strong, .hero-kpi.is-pos strong .ret-pos { color:var(--pos); }
.hero-kpi.is-neg strong, .hero-kpi.is-neg strong .ret-neg { color:var(--neg); }
.hero-kpi.is-zero strong, .hero-kpi.is-zero strong .ret-zero { color:#42526a; }
.hero-support { padding: 0 18px 18px; }
.hero-support + .hero-support { border-top: 1px solid var(--line); padding-top: 14px; }
.profile-block h3 { color:#24364a; }
.profile-block { min-width:0; }
.profile-compact { display: grid; grid-template-columns: minmax(280px, .82fr) minmax(0, 1.18fr); gap: 12px 16px; align-items: start; }
.strategy-info-block { grid-column: 1; }
.evaluation-block { grid-column: 1; }
.classification-block { grid-column: 2; grid-row: span 2; }
.profile-compact .value-row { border-bottom-color: rgba(221,227,234,.72); }
.benchmark-strip { grid-column: 1 / -1; border:1px solid #f0b8b0; border-left:4px solid var(--pos); border-radius:8px; background:#fff6f3; padding:10px 12px; }
.benchmark-strip strong { display:block; color:#b42318; font-size:12px; margin-bottom:4px; }
.benchmark-strip span { display:block; color:#9f241c; font-size:13px; line-height:1.65; font-weight:650; overflow-wrap:anywhere; }
.core-metric-board { display:grid; gap:8px; }
.core-line { display:grid; grid-template-columns: 76px minmax(0, 1fr); gap:10px; align-items:center; border:1px solid #dbe4ee; border-radius:8px; background:#fbfdff; padding:9px 10px; }
.core-line h4 { margin:0; color:#234255; font-size:13px; white-space:nowrap; }
.core-line-values { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px; }
.core-cell { min-width:0; border-left:2px solid #d5e2ea; border-radius:6px; background:#fff; padding:6px 8px; }
.core-cell span { display:block; color:#647287; font-size:11px; line-height:1.2; }
.core-cell strong { display:block; color:#162336; font-size:15px; line-height:1.25; margin-top:2px; overflow-wrap:anywhere; }
.core-cell strong .ret-pos, .core-cell strong .ret-neg, .core-cell strong .ret-zero { font-size:16px; font-weight:750; }
.core-cell.is-pos { border-left-color:var(--pos); background:#fff7f4; }
.core-cell.is-neg { border-left-color:var(--neg); background:#f3fbf7; }
.core-cell.is-zero { border-left-color:#8aa0b6; background:#f8fafc; }
.core-cell.is-pos span { color:#8f2e25; }
.core-cell.is-neg span { color:#1f6f4a; }
.core-cell.is-pos strong, .core-cell.is-pos strong .ret-pos { color:var(--pos); }
.core-cell.is-neg strong, .core-cell.is-neg strong .ret-neg { color:var(--neg); }
.classification-summary { display:grid; gap:10px; }
.class-chip-grid { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:8px; }
.class-chip, .class-metric { min-width:0; border:1px solid #dbe4ee; border-radius:7px; background:#fbfdff; padding:8px 9px; }
.class-chip > span, .class-metric > span { display:block; color:#627289; font-size:11px; line-height:1.25; }
.class-chip > strong, .class-metric > strong { display:block; margin-top:3px; color:#172033; font-size:14px; line-height:1.25; overflow-wrap:anywhere; }
.class-chip.is-main { border-color:#efc2bc; background:#fff6f3; }
.class-chip.is-main strong { color:var(--pos); }
.class-section-title { color:#344054; font-weight:700; font-size:12px; margin-top:2px; }
.class-metric-grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px; }
.class-basis { border:1px solid #dbe4ee; border-left:4px solid #0f766e; border-radius:8px; background:#f7fbfb; padding:9px 10px; }
.class-basis strong { display:block; color:#21525b; font-size:12px; margin-bottom:4px; }
.class-basis span { display:block; color:#526174; font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}
.grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  min-height: 92px;
}
.metric-value { font-size: 24px; font-weight: 700; margin-top: 8px; }
.metric-sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.two-col { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; white-space: nowrap; }
th { background: var(--soft); color: #2c3a4a; font-weight: 650; }
tr:last-child td { border-bottom: 0; }
.strategy-table-shell { border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: hidden; }
.strategy-scrollbar { overflow-x: auto; overflow-y: hidden; height: 16px; border-bottom: 1px solid var(--line); background: #f2f5f8; }
.strategy-scrollbar-inner { height: 1px; }
.strategy-table-wrap { max-height: 650px; overflow: auto; scrollbar-gutter: stable; }
.strategy-table { min-width: 2260px; width: max-content; table-layout: fixed; }
.strategy-table thead th { position: sticky; top: 0; z-index: 4; }
.strategy-table tbody tr { --row-bg: #fff; background: var(--row-bg); }
.strategy-table tbody tr:nth-child(even) { --row-bg: #f7fbff; }
.strategy-table tbody tr:hover { --row-bg: #eef7f8; }
.strategy-table th, .strategy-table td { width: 118px; }
.strategy-table .sticky-name { position: sticky; left: 0; z-index: 3; width: 260px; min-width: 260px; max-width: 260px; background: var(--row-bg, var(--soft)); box-shadow: 1px 0 0 var(--line); }
.strategy-table .sticky-channel { position: sticky; left: 260px; z-index: 3; width: 170px; min-width: 170px; max-width: 170px; background: var(--row-bg, var(--soft)); box-shadow: 1px 0 0 var(--line); }
.strategy-table thead .sticky-name, .strategy-table thead .sticky-channel { z-index: 6; background: var(--soft); }
.strategy-table .wide { width: 180px; min-width: 180px; }
.strategy-table .narrow { width: 96px; min-width: 96px; }
.strategy-table .status-col { width: 100px; min-width: 100px; }
.strategy-name-cell { white-space: normal; line-height: 1.35; }
.sort-head { display:inline-flex; align-items:center; gap:5px; border:0; background:transparent; color:inherit; font:inherit; font-weight:650; cursor:pointer; padding:0; text-align:left; }
.sort-head:hover { color:var(--brand-dark); }
.sort-arrow { color:#94a3b8; font-size:10px; min-width:10px; }
.sort-head.is-active .sort-arrow { color:var(--brand-dark); }
.ret-pos { color: var(--pos); font-weight: 700; }
.ret-neg { color: var(--neg); font-weight: 700; }
.ret-zero { color: #4b5968; font-weight: 650; }
.status-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 52px; padding: 2px 8px; border-radius: 999px; font-weight: 650; border: 1px solid transparent; }
.status-badge.ok { color: var(--good); background: #e6f4ee; border-color: #b9e2d0; }
.status-badge.bad { color: var(--bad); background: #fde8e6; border-color: #f6c6c1; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.pager-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pager button { height: 32px; min-width: 34px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); cursor: pointer; }
.pager button:disabled { cursor: not-allowed; color: #a0aaba; background: #f4f6f8; }
.quality-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.quality-card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfd; min-width: 0; }
.quality-card h3 { margin: 0 0 8px; font-size: 14px; }
.quality-card p { margin: 8px 0 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.field-label { display: inline-flex; align-items: center; gap: 5px; }
.derived-star { color: var(--bad); font-weight: 800; font-size: 12px; line-height: 1; margin-left: -2px; }
.info-button {
  width: 17px;
  height: 17px;
  border: 1px solid #b8c2ce;
  border-radius: 50%;
  background: #fff;
  color: #405063;
  font-size: 11px;
  line-height: 15px;
  cursor: pointer;
  padding: 0;
}
.info-button:hover { border-color: var(--brand); color: var(--brand); }
.filter-field { display:grid; grid-template-columns:minmax(0, 1fr) 34px; gap:6px; min-width:0; }
.classification-info-button {
  width:34px;
  height:36px;
  border:1px solid #efd29d;
  border-radius:7px;
  background:#fff8e8;
  color:#9a5a16;
  font-weight:850;
  font-size:15px;
  cursor:pointer;
  padding:0;
}
.classification-info-button:hover { background:#fff2d4; border-color:#c87918; color:#7c3f08; }
.filters {
  display: grid;
  grid-template-columns: minmax(220px, 1.5fr) repeat(7, minmax(132px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.classification-explain { border:1px solid #dbe4ee; border-radius:8px; background:#fbfdff; padding:12px; margin: 0 0 12px; }
.classification-empty { color:var(--muted); font-size:12px; }
.classification-note-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px; }
.classification-note-head strong { color:#24364a; font-size:13px; }
.classification-note-head button { border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--brand-dark); height:28px; padding:0 9px; cursor:pointer; }
.classification-summary-card { border:1px solid #cfe2e6; border-left:4px solid var(--brand); border-radius:8px; background:#f6fbfc; padding:10px 12px; margin-bottom:10px; color:#42566d; font-size:12px; line-height:1.65; }
.classification-rule-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px; }
.classification-rule-card { min-width:0; border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:10px 11px; }
.classification-rule-card.is-selected { border-color:#efc2bc; background:#fff7f4; }
.classification-rule-card b { display:block; color:#0f4f58; font-size:13px; margin-bottom:5px; }
.classification-rule-card.is-selected b { color:#b42318; }
.classification-rule-card span { display:block; color:#53657a; font-size:12px; line-height:1.62; overflow-wrap:anywhere; }
.classification-note-lines { display:grid; gap:8px; }
.control { width: 100%; height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 9px; background: #fff; color: var(--ink); }
.small { color: var(--muted); font-size: 12px; }
.link { color: var(--brand-dark); font-weight: 650; }
.pill {
  display: inline-block;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #fff;
  font-size: 12px;
  color: #405063;
}
.value-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; }
.value-row { border: 1px solid #e2e8f0; border-left: 3px solid #9ab7c2; border-radius: 7px; background: linear-gradient(180deg, #ffffff 0%, #f8fbfd 100%); padding: 8px 10px; min-width: 0; }
.value-row strong { display: block; color: #52627a; font-size: 12px; margin-bottom: 3px; font-weight:650; }
.value-row span { display: block; overflow-wrap: anywhere; color:#172033; font-weight:600; }
.value-row span .ret-pos, .value-row span .ret-neg, .value-row span .ret-zero { font-size:15px; font-weight:750; }
.value-row.is-pos { border-left-color:var(--pos); background:#fff7f4; }
.value-row.is-neg { border-left-color:var(--neg); background:#f3fbf7; }
.value-row.is-zero { border-left-color:#8aa0b6; background:#f8fafc; }
.value-row.benchmark-row { grid-column:1 / -1; border-left-color:var(--pos); background:#fff6f3; }
.value-row.benchmark-row strong, .value-row.benchmark-row span { color:#b42318; }
.value-em { color:#0f4f58; font-weight:700; }
.value-code { font-family: Consolas, "SFMono-Regular", monospace; color:#344054; background:#f3f6f9; border-radius:4px; padding:1px 4px; }
.value-date { color:#0f4f58; font-weight:650; }
.value-muted { color:var(--muted); }
.fold-block { border:1px solid var(--line); border-radius:8px; background:#fbfcfd; margin-top:12px; overflow:hidden; }
.fold-block summary { cursor:pointer; padding:10px 12px; color:#344054; font-weight:650; }
.fold-block[open] summary { border-bottom:1px solid var(--line); }
.fold-block .value-list { padding:10px 12px 12px; }
.source-note-list { border:1px solid #dbe4ee; border-left:4px solid var(--brand); border-radius:8px; background:#fbfdff; padding:10px 12px; margin-top:10px; }
.source-note-list p { margin:4px 0; color:#526174; font-size:12px; line-height:1.65; overflow-wrap:anywhere; }
.source-note-list p.warn { color:var(--bad); background:#fff6f3; border:1px solid #f4c7c1; border-radius:6px; padding:6px 8px; }
.source-note-list b { color:#24364a; font-weight:700; margin-right:4px; }
.chart-panel { position: relative; }
.chart-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:10px; }
.chart-actions { display:flex; align-items:center; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
.benchmark-select { width: min(260px, 100%); }
.range-tabs { display:inline-flex; gap:4px; padding:3px; background:#f1f4f7; border:1px solid var(--line); border-radius:8px; }
.range-tabs button { border:0; background:transparent; padding:6px 10px; border-radius:6px; color:#405063; cursor:pointer; }
.range-tabs button.is-active { background:#fff; color:var(--brand-dark); box-shadow:0 1px 3px rgba(16,24,40,.10); }
.data-tabs { display:inline-flex; gap:4px; padding:3px; background:#f1f4f7; border:1px solid var(--line); border-radius:8px; }
.data-tabs button { border:0; background:transparent; padding:6px 12px; border-radius:6px; color:#405063; cursor:pointer; }
.data-tabs button.is-active { background:#fff; color:var(--brand-dark); box-shadow:0 1px 3px rgba(16,24,40,.10); }
.chart { width: 100%; height: 330px; border: 1px solid var(--line); border-radius: 8px; background: #fbfcfd; position: relative; overflow:hidden; display:flex; flex-direction:column; }
.chart svg { display:block; width:100%; flex:1; min-height:0; }
.chart-tooltip { position:absolute; pointer-events:none; min-width:210px; max-width:360px; background:rgba(255,255,255,.96); border:1px solid #cfd8e3; border-radius:8px; padding:10px 12px; box-shadow:0 12px 30px rgba(15,23,42,.16); font-size:12px; color:#263342; z-index:5; }
.chart-tooltip strong { display:block; margin-bottom:6px; font-size:13px; }
.chart-tip-row { display:flex; justify-content:space-between; gap:14px; line-height:1.8; }
.chart-tip-row span:first-child { display:inline-flex; align-items:center; gap:6px; }
.chart-dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
.legend { display:flex; flex-wrap:wrap; gap:10px 14px; color:#405063; font-size:12px; padding:10px 12px 0; }
.legend-item { display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none; }
.legend-item input { width:13px; height:13px; margin:0; accent-color:var(--brand); }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.legend i { width:18px; height:3px; border-radius:999px; display:inline-block; }
.hover-line { stroke:#64748b; stroke-width:1.2; stroke-dasharray:4 4; }
.axis-text { fill:#657080; font-size:11px; }
.tick-line { stroke:#e5ebf2; }
.interval-matrix th:first-child, .interval-matrix td:first-child { position: sticky; left: 0; background: #fff; z-index: 2; font-weight:650; }
.position-layout { display:grid; grid-template-columns: 280px minmax(0,1fr); gap:14px; align-items:start; }
.rebalance-list { border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#fff; max-height:520px; overflow-y:auto; }
.rebalance-item { display:block; width:100%; text-align:left; border:0; border-bottom:1px solid var(--line); background:#fff; padding:10px 12px; cursor:pointer; color:var(--ink); }
.rebalance-item:hover { background:#f6fafb; }
.rebalance-item.is-active { background:#eaf6f7; box-shadow: inset 3px 0 0 var(--brand); }
.rebalance-item strong { display:block; font-size:13px; margin-bottom:3px; }
.rebalance-item span { display:block; color:var(--muted); font-size:12px; }
.holding-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.holding-head h3 { margin:0; }
.holding-head p { margin:4px 0 0; color:var(--muted); font-size:12px; }
.compact-table th, .compact-table td { padding:8px 9px; }
.modal {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.35);
  display: grid;
  place-items: center;
  padding: 20px;
  z-index: 50;
}
.modal[hidden] { display: none; }
.modal-panel {
  width: min(560px, 100%);
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
  position: relative;
}
.modal-close {
  position: absolute;
  right: 12px;
  top: 10px;
  width: 30px;
  height: 30px;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: #fff;
  cursor: pointer;
  font-size: 18px;
}
.modal-panel p { margin-bottom: 0; color: #344054; white-space: pre-line; line-height:1.7; max-height:min(68vh, 560px); overflow:auto; }
.empty { color: var(--muted); padding: 18px; }
.warn { color: var(--bad); }
.insight-filters { display:grid; grid-template-columns: repeat(8, minmax(120px, 1fr)); gap:10px; margin-bottom:14px; }
.insight-tabs { display:flex; flex-wrap:wrap; gap:8px; margin:2px 0 14px; }
.insight-tab-button { border:1px solid var(--line); background:#fff; color:#344054; border-radius:8px; padding:8px 12px; cursor:pointer; font-weight:650; }
.insight-tab-button:hover { background:#f6fafb; }
.insight-tab-button.is-active { color:#fff; background:var(--brand); border-color:var(--brand); }
.insight-layout { display:grid; grid-template-columns: 1fr; gap:16px; align-items:start; }
.insight-panel-stack { display:grid; gap:16px; }
.insight-mini-grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:10px; }
.insight-dimension-grid { display:grid; grid-template-columns: 1fr; gap:16px; }
.insight-conclusion-grid { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:10px; }
.insight-conclusion-card { min-width:0; border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:11px 12px; }
.insight-conclusion-card.is-good { border-left:4px solid var(--good); background:#f5fbf8; }
.insight-conclusion-card.is-warn { border-left:4px solid var(--gold); background:#fffaf2; }
.insight-conclusion-card.is-bad { border-left:4px solid var(--bad); background:#fff6f3; }
.insight-conclusion-card strong { display:block; color:#172033; font-size:14px; margin-bottom:6px; }
.insight-conclusion-card p { margin:0; color:#526174; font-size:12px; line-height:1.6; }
.fvla-grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px; margin-top:8px; }
.fvla-item { min-width:0; border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:8px 9px; font-size:12px; line-height:1.55; color:#526174; }
.fvla-item b { display:block; margin-bottom:3px; color:#172033; font-size:12px; }
.weekly-rank-list { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; }
.weekly-rank-card { min-width:0; border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:10px 12px; }
.weekly-rank-card h3 { margin:0 0 8px; font-size:14px; color:#172033; }
.weekly-rank-card ol { margin:0; padding-left:20px; display:grid; gap:6px; color:#526174; font-size:12px; line-height:1.55; }
.focus-decision-list { display:grid; gap:10px; }
.focus-decision-card { border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:12px; display:grid; gap:8px; }
.focus-decision-card.is-good { border-left:4px solid var(--good); background:#f5fbf8; }
.focus-decision-card.is-warn { border-left:4px solid var(--gold); background:#fffaf2; }
.focus-decision-card.is-bad { border-left:4px solid var(--bad); background:#fff6f3; }
.focus-decision-card strong { color:#172033; font-size:15px; }
.focus-decision-card p { margin:0; color:#526174; font-size:12px; line-height:1.65; }
.focus-section-grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; align-items:start; }
.focus-list { display:grid; gap:8px; }
.focus-list-item { border:1px solid #dbe4ee; border-radius:8px; background:#fff; padding:10px; font-size:12px; line-height:1.6; color:#526174; }
.focus-list-item strong { display:block; color:#172033; font-size:13px; margin-bottom:3px; }
.focus-list-item .small { display:block; margin-top:3px; }
.insight-callout { border:1px solid #dbe4ee; border-left:4px solid var(--brand); border-radius:8px; background:#fbfdff; padding:10px 12px; color:#526174; font-size:12px; line-height:1.7; }
.insight-callout strong { color:#172033; }
.insight-hero { display:grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap:10px; }
.insight-kpi { min-width:0; border:1px solid #d7e0ea; border-radius:8px; background:#fff; padding:11px 12px; }
.insight-kpi span { display:block; color:#647287; font-size:12px; line-height:1.25; }
.insight-kpi strong { display:block; margin-top:5px; color:#172033; font-size:22px; line-height:1.15; overflow-wrap:anywhere; }
.insight-kpi small { display:block; margin-top:4px; color:#7a8797; font-size:11px; line-height:1.35; }
.insight-kpi.is-good { border-left:4px solid var(--good); background:#f5fbf8; }
.insight-kpi.is-warn { border-left:4px solid var(--gold); background:#fffaf2; }
.insight-kpi.is-bad { border-left:4px solid var(--bad); background:#fff6f3; }
.insight-grid { display:grid; grid-template-columns: minmax(0, 1.1fr) minmax(360px, .9fr); gap:16px; align-items:start; }
.insight-stack { display:grid; gap:16px; }
.insight-section-note { color:#5d6b7c; font-size:12px; line-height:1.7; margin:0 0 10px; }
.insight-bar-list { display:grid; gap:9px; }
.insight-bar-row { display:grid; grid-template-columns: minmax(112px, 160px) minmax(0, 1fr) 82px; align-items:center; gap:10px; font-size:12px; color:#526174; }
.insight-bar-track { position:relative; height:18px; border-radius:999px; background:#eef3f8; overflow:hidden; }
.insight-bar-fill { position:absolute; inset:0 auto 0 0; border-radius:999px; background:#166c77; min-width:2px; }
.insight-bar-fill.is-target { background:#b42318; height:8px; top:5px; }
.insight-bar-label { color:#25374b; font-weight:650; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.insight-chip { display:inline-flex; align-items:center; justify-content:center; min-width:56px; border-radius:999px; padding:2px 8px; font-size:12px; border:1px solid #dbe4ee; background:#f8fbfd; color:#405063; }
.insight-chip.good { color:var(--good); background:#edf8f2; border-color:#bde5d2; }
.insight-chip.warn { color:#9a5a16; background:#fff8e8; border-color:#efd29d; }
.insight-chip.bad { color:var(--bad); background:#fff1ef; border-color:#f4c7c1; }
.insight-secondary-row td { background:#fbfdff; border-top:0; padding-top:0; }
.row-detail { border:1px solid #dbe4ee; border-radius:8px; background:#fff; overflow:hidden; }
.row-detail > summary { cursor:pointer; list-style:none; padding:9px 11px; color:#344054; font-size:12px; font-weight:700; display:flex; gap:8px; align-items:center; justify-content:space-between; }
.row-detail > summary::-webkit-details-marker { display:none; }
.row-detail > summary::after { content:"展开"; color:#0f4f58; font-weight:750; }
.row-detail[open] > summary { border-bottom:1px solid #dbe4ee; background:#f7fbfc; }
.row-detail[open] > summary::after { content:"收起"; }
.row-detail-body { padding:10px 11px 12px; display:grid; gap:10px; }
.product-compare-note { color:#526174; font-size:12px; line-height:1.6; display:grid; gap:4px; }
.product-compare-table { overflow:auto; border:1px solid #e2e8f0; border-radius:8px; }
.product-compare-table table { min-width:860px; font-size:12px; }
.product-compare-table th { background:#f7fafc; color:#526174; }
.product-compare-table td, .product-compare-table th { padding:7px 8px; border-bottom:1px solid #edf2f7; text-align:left; vertical-align:top; }
.compare-scope { display:inline-flex; align-items:center; border-radius:999px; padding:2px 7px; border:1px solid #dbe4ee; background:#f8fbfd; color:#405063; font-size:11px; font-weight:700; }
.compare-scope.gf { border-color:#f4c7c1; background:#fff6f3; color:#b42318; }
.compare-scope.market { border-color:#bde5d2; background:#edf8f2; color:#0f7a4f; }
.opportunity-list { display:grid; gap:10px; }
.opportunity-row { display:grid; grid-template-columns: 86px minmax(0,1fr) 86px; gap:10px; align-items:center; border:1px solid #dbe4ee; border-radius:8px; background:#fbfdff; padding:10px; }
.opportunity-row strong { color:#172033; }
.opportunity-row p { margin:4px 0 0; color:#647287; font-size:12px; line-height:1.55; }
.score-badge { display:inline-flex; align-items:center; justify-content:center; width:58px; height:58px; border-radius:50%; background:#fff3f1; color:var(--bad); border:1px solid #f2b8b1; font-weight:800; }
.score-badge.mid { background:#fff8e8; color:#9a5a16; border-color:#efd29d; }
.score-badge.low { background:#edf8f2; color:var(--good); border-color:#bde5d2; }
.rank-list { display:grid; gap:8px; }
.rank-row { display:grid; grid-template-columns: minmax(0,1fr) 72px 70px; gap:10px; align-items:center; border:1px solid #dbe4ee; border-radius:8px; background:#fbfdff; padding:9px 10px; }
.rank-row strong { display:block; color:#172033; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rank-row span { display:block; margin-top:3px; color:#667085; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rank-value { text-align:right; font-weight:800; color:#172033; }
.logic-chip { display:inline-flex; align-items:center; border-radius:999px; border:1px solid #dbe4ee; background:#f8fbfd; color:#344054; padding:2px 8px; font-size:12px; white-space:nowrap; }
.insight-table table { font-size:12px; }
.insight-table td, .insight-table th { padding:8px 9px; }
.insight-table .small { display:block; margin-top:3px; line-height:1.45; white-space:normal; }
.source-method { border:1px solid #dbe4ee; border-left:4px solid var(--brand); border-radius:8px; background:#fbfdff; padding:10px 12px; color:#526174; font-size:12px; line-height:1.7; }
@media (max-width: 1100px) {
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .two-col { grid-template-columns: 1fr; }
  .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .strategy-hero, .position-layout { grid-template-columns: 1fr; }
  .profile-compact { grid-template-columns: 1fr; }
  .strategy-info-block, .evaluation-block, .classification-block { grid-column:auto; grid-row:auto; }
  .hero-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .core-line-values { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .class-metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .insight-hero { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .insight-grid, .insight-layout, .insight-dimension-grid { grid-template-columns: 1fr; }
  .insight-conclusion-grid, .classification-rule-grid, .fvla-grid, .weekly-rank-list, .focus-section-grid { grid-template-columns: 1fr; }
  .insight-filters { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .topbar-inner { align-items: flex-start; flex-direction: column; }
  .page-shell { padding: 16px; }
  .page-title { align-items: flex-start; flex-direction: column; }
  .title-pills { justify-content: flex-start; max-width: none; }
  .grid, .filters, .value-list { grid-template-columns: 1fr; }
  .quality-grid { grid-template-columns: 1fr; }
  .hero-kpis, .hero-dates, .core-line-values, .class-chip-grid, .class-metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .profile-compact, .core-line { grid-template-columns: 1fr; }
  .insight-filters, .insight-hero, .insight-bar-row, .opportunity-row, .insight-mini-grid, .rank-row { grid-template-columns: 1fr; }
}
@media (max-width: 420px) {
  .hero-kpis, .hero-dates, .core-line-values, .class-chip-grid, .class-metric-grid { grid-template-columns: 1fr; }
}
""" + SIDEBAR_CSS


COMMON_JS = r"""
window.__BASIC_DATA__ = window.__BASIC_DATA__ || { details: {} };
window.BasicData = (() => {
  const state = window.__BASIC_DATA__;
  const byId = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const INTERNAL_TEST_NOTICE = "所有数据为测试模拟数据，不构成任何投资意见，仅内部测试使用。";
  const PAGE_LOADING_TEXT = "数据正在加载，请稍等。";
  let noticeScheduled = false;
  const dict = () => state.summary?.fieldDictionary || {};
  const businessFieldDescriptions = {
    "入选策略数": "业务口径：AI核心仓位达到入选标准的策略数量。同一策略只计算一次，用于判断当前主题下可进入重点观察池的产品规模。",
    "均值达标策略数": "业务口径：最近一年平均AI核心仓位达到50%的策略数量。该指标更看重持续配置，而不是短期单次持有。",
    "峰值达标策略数": "业务口径：最近一年曾在任一持仓快照中AI核心仓位达到50%的策略数量。该指标用于发现阶段性重仓AI主题的策略。",
    "AI核心基金数": "业务口径：入选策略中贡献AI核心仓位的底层基金去重数量。用于判断入选结果是由少数基金集中贡献，还是覆盖了更多基金工具。",
    "标准实体AI基金数": "业务口径：底层基金已被标准主题实体识别为AI核心相关的基金数量。只统计有明确主题证据的基金。",
    "点阵样本数": "业务口径：当前点阵图中可比较的策略数量。样本需要具备收益、回撤和AI核心暴露等必要指标。",
    "AI核心均值暴露": "业务口径：策略在观察期内平均持有AI核心相关基金的仓位比例。数值越高，代表策略对AI主题的持续配置越强。",
    "AI核心峰值暴露": "业务口径：策略在观察期内AI核心相关基金仓位达到过的最高比例。用于识别阶段性重仓AI主题的策略。",
    "当前AI核心暴露": "业务口径：策略最新持仓中AI核心相关基金的仓位比例。用于观察策略当前是否仍在配置AI主题。",
    "峰值日期": "业务口径：策略AI核心仓位达到观察期最高值的日期。用于判断重仓AI主题发生在近期还是历史阶段。",
    "主要AI核心基金": "业务口径：对策略AI核心仓位贡献较高的底层基金。展示这些基金是为了说明策略为什么被纳入AI主题观察池。",
  };
  const collectedFieldNames = new Set([
    "数据更新至", "数据刷新时间", "渠道", "渠道类型", "策略数", "策略名称", "投顾机构", "策略类型", "风险等级",
    "成立日期", "运作状态", "策略代码", "统一策略ID", "起投金额", "投顾费率", "建议持有时长", "业绩基准",
    "标签", "策略概念", "策略描述", "业绩基准说明", "官方单位净值", "官方累计收益", "最新业绩日期", "收益数据截至",
    "最新业绩日", "最新调仓日", "最新持仓日", "最近调仓日", "基金代码", "基金名称", "资产类型", "分组",
    "基金净值", "净值日期", "日涨幅", "调仓日期", "披露日期", "调仓标题", "调前权重", "调后权重",
    "调仓基金数", "调后权重和", "投顾费率", "业绩基准", "原始数据来源", "曲线来源", "披露业绩"
  ]);
  const derivedFieldNames = new Set([
    "数据来源标记", "接入渠道数", "策略总数", "天天策略数", "有历史调仓策略数", "有官方业绩策略数", "纳入回放策略数",
    "策略基金净值缺失数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖",
    "主可比池", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重",
    "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重",
    "基准债券权重", "基准货币权重", "基准可用状态", "基础数据等级", "费率状态", "年化投顾费率", "分类依据",
    "运作天数", "数据完整性", "质检情况", "稽核结论", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来",
    "累计收益率", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "夏普比率",
    "单次平均换手率", "年化换手率", "调仓频率", "最近一年调仓次数", "官方对比口径", "可比记录数",
    "持仓来源", "持仓基金数", "权重", "上次调仓后权重", "权重变化", "调仓动作", "调仓基金数", "调后权重和", "区间", "年度",
    "策略收益", "基准收益", "模拟业绩", "基准业绩", "沪深300业绩", "基准公式解析", "调仓后收益率",
    "调仓后收益贡献", "调仓贡献曲线", "曲线数据提示"
    , "洞察评价对象", "全市场完整策略", "广发基金投顾完整策略", "市场覆盖率", "覆盖可比池数", "头部策略数", "中位差",
    "机会评分", "头部差距", "排名分位", "复盘建议", "数据洞察来源", "市场样本数", "广发样本数", "广发中位数",
    "市场中位数", "广发最佳", "广发中位回撤", "市场中位回撤", "所选收益", "池中位收益", "诊断分数",
    "基础数据A级占比", "仅曲线基准占比", "费率缺失占比", "风险未披露占比", "费率状态", "年化投顾费率",
    "覆盖策略类型数", "筛选口径", "分类口径", "广发覆盖率", "广发中位收益", "市场中位收益", "中位回撤",
    "中位波动", "高换手策略数", "高波动策略数", "风险收益象限", "广发基金投顾中位收益", "全市场中位收益",
    "广发基金投顾中位回撤", "广发基金投顾中位波动率", "最新调仓日期", "近一周主动调仓", "近一月主动调仓",
    "近一月广发调仓", "近一月中位换手率", "近一年平均调仓超额", "调仓逻辑", "事件数", "机构数",
    "可评价事件数", "市场调仓胜率", "广发调仓胜率", "标杆机构胜率", "胜率差距", "调仓质量结论", "调仓质量风险", "调仓质量建议",
    "中位单次换手率", "平均调仓超额", "示例原因", "平均单次换手率", "调仓胜率", "主要逻辑",
    "广发基金投顾完整策略", "广发覆盖率", "广发头部产品数", "广发Top3平均收益", "广发Top5平均收益",
    "市场Top3平均收益", "市场Top5平均收益", "广发Top3差距", "广发Top3对Top5差距", "头部达标数",
    "广发Top3平均排名", "广发Top3产品", "产品对比", "相对标杆", "深层结论", "业务风险", "业务动作",
    "广发样本数", "广发覆盖率", "广发中位收益", "广发Top3均值", "市场Top3均值", "标杆差距",
    "标杆产品", "标杆机构", "维度结论", "机会风险", "建议"
    , "经营重点", "经营判断", "业务重点", "重点经营", "头部可包装", "需要复盘", "产品补齐", "暂不主推", "重点名单"
    , "近一周经营总览", "事实", "观点", "逻辑", "动作", "近一周全市场中位收益", "近一周广发中位收益",
    "近一周广发相对差", "近一周上涨占比", "近一周广发Top5", "近一周市场Top5", "广发周度位置",
    "周度业务动作", "平台对标参考", "公开资料", "全市场事件数", "广发事件数", "近一周市场Top5均值"
  ]);
  const derivedFieldHints = ["覆盖", "完整", "归属", "分类", "权重", "收益", "回撤", "波动", "夏普", "换手", "频率", "次数", "偏差", "贡献", "解析", "可比", "中枢", "中位", "占比", "评分", "分位", "样本", "差距", "逻辑", "胜率", "Top", "标杆", "结论", "风险", "动作"];
  function isDerivedField(field) {
    const text = String(field || "");
    if (derivedFieldNames.has(text)) return true;
    if (collectedFieldNames.has(text)) return false;
    return derivedFieldHints.some((word) => text.includes(word));
  }
  function fieldSourceText(field) {
    const text = String(field || "");
    if (text.includes("基金") || text.includes("资产") || text.includes("行业")) {
      return "业务说明：用于观察策略底层配置到哪些基金、资产类别或行业主题，以及这些配置在当前筛选范围内的占比和集中度。";
    }
    if (text.includes("调仓") || text.includes("调前") || text.includes("调后") || text.includes("净增配")) {
      return "业务说明：用于观察策略在指定时间窗口内买入、卖出或调整仓位的方向和力度，帮助判断产品经理或投顾组合的配置变化。";
    }
    if (text.includes("收益") || text.includes("净值") || text.includes("回撤") || text.includes("波动") || text.includes("夏普")) {
      return "业务说明：用于评价策略或基金的收益风险表现。收益反映观察期涨跌幅，回撤反映阶段内最大承受损失，波动和夏普用于辅助判断收益稳定性。";
    }
    if (text.includes("持仓") || text.includes("仓位") || text.includes("权重")) {
      return "业务说明：用于观察策略当前或历史配置强度。权重越高，代表该策略对相应基金、资产或主题的配置越集中。";
    }
    return "业务说明：用于当前页面的筛选、排序、对比或概览展示。具体含义以字段名称、所在页面和当前筛选条件为准。";
  }
  function fallbackFieldDescription(field) {
    const text = String(field || "");
    if (text.includes("基金分类依据")) {
      return "计算口径：展示单只基金归类时命中的证据链。优先取基金代码/名称标准字典，其次取平台持仓披露的资产类型或分组，再用基金名称、跟踪指数、QDII/ETF/FOF/REIT/黄金/商品/短债/纯债/可转债等关键词兜底。该字段用于解释为什么基金被归入当前基金类型。";
    }
    if (text.includes("基金分类来源")) {
      return "计算口径：说明该基金分类和暴露字段的来源。主业务口径统一使用基金经济暴露快照；东财F10季报、基金标准分类、名称/指数规则和人工补充规则会合并成可审计的穿透方法与质量状态。";
    }
    if (text.includes("基金穿透报告期")) {
      return "计算口径：当前基金经济暴露使用的报告期。历史快照会优先选择报告期不晚于持仓日期的数据，避免用未来报告期回填历史。";
    }
    if (text.includes("基金穿透覆盖状态")) {
      return "计算口径：exact_quarterly_asset_and_stock 表示已有季报资产配置和股票持仓行业推导；exact_quarterly_asset_only 表示仅有季报资产配置；空值表示走规则估算兜底。";
    }
    if (text.includes("资产暴露") || text.includes("研报大类资产")) {
      return "计算口径：页面资产配置统一使用基金经济暴露快照。先保留季报原始资产配置，再按基金标准分类、名称、跟踪指数、ETF联接、FOF、QDII、黄金、固收指数等规则，把基金/其他高占比重映射为可解释资产。图表权重=sum(策略基金权重*基金经济资产暴露比例)。";
    }
    if (text.includes("行业暴露") || text.includes("研报A股行业")) {
      return "计算口径：页面行业/主题配置统一使用基金经济暴露快照。权益、海外权益、行业主题和指数权益优先使用季报股票持仓及东财股票行业映射；缺少完整股票行业时用主题、指数或名称规则标注质量状态。黄金/商品、纯债、货币、海外债券不适用股票行业穿透。";
    }
    if (text.includes("行业主题") || text.includes("行业大类") || text.includes("权益行业")) {
      return "计算口径：按基金经济资产暴露和经济行业暴露继续归并。非权益资产归入现金管理、纯债/固收、海外债券、贵金属、能源商品等；A股行业优先由季报股票持仓行业推导，再映射到科技制造、消费医药、金融周期等上层主题。";
    }
    if (text.includes("权重占比") || text.includes("占比")) {
      return "计算口径：当前筛选范围内，本行对象的权重合计除以同口径全部对象权重合计。用于基金、基金公司、资产大类、行业主题等聚合项时，先在单只策略内按基金权重或拆分暴露求和，再跨策略汇总。";
    }
    if (text.includes("总权重") || text.includes("持仓权重合计")) {
      return "计算口径：当前筛选范围内持有该基金、公司、资产或行业的策略期末持仓比例求和。该值是跨策略合计点位，不代表任何单一组合的真实仓位。";
    }
    if (text.includes("广发策略权重")) {
      return "计算口径：只统计投顾机构归属为广发的策略，在当前筛选范围内对该基金、基金公司、资产或行业的期末持仓权重合计。";
    }
    if (text.includes("非广发策略权重") || text.includes("外部策略权重")) {
      return "计算口径：剔除广发投顾策略后，其余策略在当前筛选范围内对该基金、基金公司、资产或行业的期末持仓权重合计，用于观察外部策略是否认可该底层资产。";
    }
    if (text.includes("持仓策略数")) {
      return "计算口径：当前筛选范围内期末仍持有该基金、基金公司、资产或行业的去重策略数。同一策略在同一统计项下只计一次。";
    }
    if (text.includes("调仓策略数")) {
      return "计算口径：当前调仓窗口内，对该基金、基金公司、资产或行业发生有效权重变化的去重策略数。有效变化阈值为绝对净变化大于0.0001个百分点。";
    }
    if (text.includes("中位权重")) {
      return "计算口径：当前筛选范围内持有该对象的单个策略持仓比例中位数，表示典型策略的配置强度，不受极端大仓位策略过度影响。";
    }
    if (text.includes("区间收益率") || text.includes("近一周") || text.includes("近一月") || text.includes("近三月") || text.includes("今年以来")) {
      return "计算口径：取观察窗口起止日期附近最近可用净值，收益率=(期末净值/期初净值-1)*100%。策略优先使用官方披露净值，基金使用基金日度净值；窗口内缺少可比净值时显示未披露。";
    }
    if (text.includes("回撤")) {
      return "计算口径：基于清洗后的日度净值序列，逐日计算相对历史高点的跌幅；最大回撤取区间内最深跌幅，当前回撤取最新净值相对历史高点的跌幅。";
    }
    if (text.includes("波动")) {
      return "计算口径：用日收益率标准差按252个交易日年化，公式=std(日收益率)*sqrt(252)。样本不足或净值不连续时不参与正式比较。";
    }
    if (text.includes("夏普")) {
      return "计算口径：年化收益率/年化波动率，当前无风险收益率按0处理；波动率为0或样本不足时为空。";
    }
    if (text.includes("净增配") || text.includes("权重变化")) {
      return "计算口径：调后权重-调前权重。按基金、资产、行业聚合时，先在单只策略内汇总同类基金变化，再跨策略计算合计、中位数、增配策略数和减配策略数。";
    }
    if (text.includes("加仓权重")) {
      return "计算口径：当前窗口内所有正向权重变化的合计，只统计买入或增配部分，不与减仓抵消。";
    }
    if (text.includes("减仓权重")) {
      return "计算口径：当前窗口内所有负向权重变化绝对值的合计，只统计卖出或减配部分，不与加仓抵消。";
    }
    if (text.includes("调仓强度") || text.includes("换手")) {
      return "计算口径：一次调仓中买入与卖出权重变化绝对值的综合强度。单次换手率通常按sum(abs(权重变化))/2估算，年度指标再按策略运作时间折算。";
    }
    if (text.includes("胜率")) {
      return "计算口径：只统计调仓后观察窗口已经结束且可评价的事件，胜率=正向事件数/可评价事件数*100%。未到观察窗口或缺少可比收益的事件不进入分母。";
    }
    if (text.includes("贡献")) {
      return "计算口径：把调后持仓权重与后续区间收益结合估算，近似为调后权重*调仓后收益率/100，用于比较调仓后单只基金或资产对组合收益的影响。";
    }
    if (text.includes("排名") || text.includes("分位") || text.includes("Top")) {
      return "计算口径：在当前筛选后的同一可比池内重排。排序字段随页面选择变化；分位数按同池策略或同类产品的相对位置计算，不跨产品类型混排。";
    }
    if (text.includes("数量") || text.endsWith("数") || text.includes("事件数") || text.includes("样本数")) {
      return "计算口径：按当前筛选范围去重计数。策略按统一策略ID去重，基金按基金代码去重，调仓事件按调仓事件ID去重；明细行数只在明确写作记录数时使用。";
    }
    if (text.includes("日期") || text.endsWith("日")) {
      return "计算口径：取该对象在对应业务表中的最大可用日期。策略业绩看最新业绩日，持仓看最新持仓日或推算持仓日，调仓看最新调仓日，基金净值看最新交易日期。";
    }
    return "业务口径：该指标用于当前页面的筛选、排序或展示。具体含义以字段名称、所在页面标题和当前筛选条件为准；涉及收益时通常表示观察期内涨跌幅，涉及回撤时表示相对阶段高点的下跌幅度，涉及数量时通常按策略、基金或事件去重统计。";
  }
  function showInfoModal(title, body) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").textContent = body;
    byId("fieldModalBody").scrollTop = 0;
    byId("fieldModalBody").scrollLeft = 0;
    byId("fieldModal").hidden = false;
  }
  function showHtmlModal(title, html) {
    byId("fieldModalTitle").textContent = title;
    byId("fieldModalBody").innerHTML = html;
    byId("fieldModalBody").scrollTop = 0;
    byId("fieldModalBody").scrollLeft = 0;
    byId("fieldModal").hidden = false;
  }
  function fmt(value, suffix = "") {
    if (value === null || value === undefined || value === "") return "未披露";
    if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("zh-CN") + suffix : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) + suffix;
    return esc(value);
  }
  function pct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    return `${Number(value).toFixed(2)}%`;
  }
  function pctSigned(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '<span class="small">未披露</span>';
    const number = Number(value);
    const cls = number > 0 ? "ret-pos" : number < 0 ? "ret-neg" : "ret-zero";
    return `<span class="${cls}">${number.toFixed(2)}%</span>`;
  }
  const returnFieldNames = new Set(["官方累计收益", "自建累计收益", "与官方偏差", "最大回撤", "当前回撤", "年化收益", "波动率", "权重变化", "调仓后收益率", "调仓后收益贡献", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率", "策略收益", "基准收益", "基准业绩", "调仓超额"]);
  const returnFieldHints = ["收益", "回撤", "波动", "偏差", "贡献", "涨幅", "超额"];
  function isReturnField(field) {
    return returnFieldNames.has(field) || returnFieldHints.some((word) => String(field).includes(word));
  }
  function toneClass(field, value) {
    if (!isReturnField(field) || Number.isNaN(Number(value))) return "";
    if (String(field).includes("回撤")) return Number(value) === 0 ? "is-zero" : "is-neg";
    const number = Number(value);
    if (number > 0) return "is-pos";
    if (number < 0) return "is-neg";
    return "is-zero";
  }
  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (!Number.isNaN(Number(value)) && String(field).includes("回撤")) {
      const number = Number(value);
      const cls = number === 0 ? "ret-zero" : "ret-neg";
      return `<span class="${cls}">${number.toFixed(2)}%</span>`;
    }
    if (!Number.isNaN(Number(value)) && (String(field).includes("权重") || String(field).includes("费率"))) return `<span class="value-em">${Number(value).toFixed(2)}%</span>`;
    if (!Number.isNaN(Number(value)) && isReturnField(field)) return pctSigned(value);
    if (field.includes("日期") || field.endsWith("日")) return `<span class="value-date">${fmt(value)}</span>`;
    if (field.includes("ID") || field.includes("代码")) return `<span class="value-code">${fmt(value)}</span>`;
    if (typeof value === "number") return `<span class="value-em">${fmt(value)}</span>`;
    return fmt(value);
  }
  function statusBadge(value) {
    const text = String(value || "不完整");
    const cls = text === "完整" ? "ok" : "bad";
    return `<span class="status-badge ${cls}">${esc(text)}</span>`;
  }
  function label(name) {
    const fieldName = String(name ?? "");
    const displayName = fieldName
      .replaceAll("广发基金投顾", "广发基金")
      .replaceAll("广发证券易淘金/财富管家", "广发证券")
      .replaceAll("广发证券易淘金/贝塔牛理财", "广发证券");
    const safe = esc(displayName);
    const safeField = esc(fieldName);
    const mark = isDerivedField(name) ? '<sup class="derived-star" title="基于基础数据加工">*</sup>' : "";
    return `<span class="field-label">${safe}${mark}<button class="info-button" type="button" data-field="${safeField}" title="查看字段口径">?</button></span>`;
  }
  function table(headers, rows, formatter) {
    const head = headers.map((item) => `<th>${label(item)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => `<td>${formatter ? formatter(row, h) : fmt(row[h])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function valueList(rows) {
    return `<div class="value-list">${rows.map((row) => {
      const cls = ["value-row", toneClass(row.字段, row.值), row.字段 === "业绩基准说明" ? "benchmark-row" : ""].filter(Boolean).join(" ");
      return `<div class="${cls}"><strong>${label(row.字段)}</strong><span>${valueHtml(row.字段, row.值)}</span></div>`;
    }).join("")}</div>`;
  }
  function metricValue(labelName, value, formatter) {
    const html = formatter && !String(labelName).includes("回撤") ? formatter(value) : valueHtml(labelName, value);
    return `<div class="core-cell ${toneClass(labelName, value)}"><span>${label(labelName)}</span><strong>${html}</strong></div>`;
  }
  function metric(labelName, value, sub = "") {
    return `<section class="metric"><div>${label(labelName)}</div><div class="metric-value">${fmt(value)}</div>${sub ? `<div class="metric-sub">${esc(sub)}</div>` : ""}</section>`;
  }
  function params() {
    return new URLSearchParams(window.location.search);
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`加载失败：${src}`));
      document.head.appendChild(script);
    });
  }
  function showPageLoading(message = PAGE_LOADING_TEXT) {
    const root = document.querySelector("main") || document.body || document.documentElement;
    if (!root) return null;
    let el = byId("pageLoadingStatus");
    if (!el) {
      el = document.createElement("section");
      el.id = "pageLoadingStatus";
      el.className = "page-loading-status";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      root.prepend(el);
    }
    el.textContent = message || PAGE_LOADING_TEXT;
    el.hidden = false;
    return el;
  }
  function updatePageLoading(done = 0, total = 0, detail = "") {
    const progress = total ? `（${Math.min(done, total)}/${total}）` : "";
    const suffix = detail ? ` ${detail}` : "";
    return showPageLoading(`${PAGE_LOADING_TEXT}${progress}${suffix}`);
  }
  function hidePageLoading() {
    const el = byId("pageLoadingStatus");
    if (el) el.hidden = true;
  }
  function ensureInternalTestNotice() {
    const standardTitles = Array.from(document.querySelectorAll(".page-title h1, .title-block h1, .system-page-title h1"));
    const main = document.querySelector("main");
    const firstContent = main ? Array.from(main.children).find((child) => child.id !== "pageLoadingStatus" && !child.hidden) : null;
    const fallbackTitle = firstContent?.querySelector("h1, h2");
    const titles = standardTitles.length ? standardTitles : (fallbackTitle ? [fallbackTitle] : []);
    titles.forEach((title) => {
      const parent = title.parentElement;
      if (!parent || Array.from(parent.children).some((child) => child.classList?.contains("internal-test-notice"))) return;
      const notice = document.createElement("p");
      notice.className = "internal-test-notice";
      notice.textContent = INTERNAL_TEST_NOTICE;
      title.insertAdjacentElement("afterend", notice);
    });
  }
  function scheduleInternalTestNotice() {
    if (noticeScheduled) return;
    noticeScheduled = true;
    const run = () => {
      noticeScheduled = false;
      ensureInternalTestNotice();
    };
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }
  function installPageChromeObserver() {
    scheduleInternalTestNotice();
    if (typeof MutationObserver !== "function" || !document.body) return;
    const observer = new MutationObserver((mutations) => {
      const shouldCheck = mutations.some((mutation) => Array.from(mutation.addedNodes || []).some((node) => {
        if (node.nodeType !== 1) return false;
        return node.matches?.(".page-title, .title-block, .system-page-title, main > section, main > .panel")
          || node.querySelector?.(".page-title h1, .title-block h1, .system-page-title h1, h1, h2");
      }));
      if (shouldCheck) scheduleInternalTestNotice();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  const chartColors = {
    "披露业绩": "#d32f2f",
    "模拟业绩": "#1565c0",
    "基准业绩": "#2e7d32",
    "沪深300业绩": "#6a1b9a",
    "调仓前仓位模拟": "#f57c00",
    "调仓后仓位实际": "#00897b"
  };
  function colorForSeries(name) {
    if (chartColors[name]) return chartColors[name];
    if (String(name).startsWith("全局基准")) return "#0f766e";
    return "#475467";
  }
  function returnFromBase(value, base, mode) {
    if (value === null || value === undefined || base === null || base === undefined) return null;
    if (mode === "return" || mode === "return_pct") {
      const denominator = 1 + Number(base) / 100;
      if (!denominator) return null;
      return ((1 + Number(value) / 100) / denominator - 1) * 100;
    }
    if (!Number(base)) return null;
    return (Number(value) / Number(base) - 1) * 100;
  }
  function rangeStartDate(points, range) {
    if (!points.length || range === "all") return null;
    const last = new Date(points[points.length - 1].日期);
    if (range === "ytd") return new Date(last.getFullYear(), 0, 1);
    const days = { "1y": 365, "6m": 183, "3m": 92, "1m": 31 }[range] || 0;
    return new Date(last.getTime() - days * 86400000);
  }
  function pointAtOrBefore(points, dateText) {
    let selected = null;
    for (const point of points) {
      if (String(point.日期) <= dateText) selected = point;
      else break;
    }
    return selected;
  }
  function transformSeries(seriesMap, range = "all", alreadyReturn = false, visibility = {}) {
    const raw = Object.entries(seriesMap || {}).filter(([name]) => visibility[name] !== false).map(([name, payload]) => {
      const points = Array.isArray(payload) ? payload : (payload?.points || []);
      const mode = payload?.模式 || points[0]?.模式 || "nav";
      const allTimeValueMode = range === "all" ? String(payload?.allTimeValueMode || "") : "";
      const start = alreadyReturn ? null : rangeStartDate(points, range);
      const filtered = points
        .filter((p) => p.日期 && p.数值 !== null && p.数值 !== undefined && (!start || new Date(p.日期) >= start))
        .map((p) => ({ 日期: String(p.日期), 数值: Number(p.数值) }))
        .filter((p) => Number.isFinite(p.数值))
        .sort((a, b) => a.日期.localeCompare(b.日期));
      return { name, points: filtered, mode: alreadyReturn ? "return" : mode, allTimeValueMode };
    }).filter((item) => item.points.length);
    if (!raw.length) return {};
    const commonStart = raw.map((item) => item.points[0].日期).sort().at(-1);
    const commonEnd = raw.map((item) => item.points[item.points.length - 1].日期).sort()[0];
    if (!commonStart || !commonEnd || commonStart > commonEnd) return {};
    const dateSet = new Set([commonStart, commonEnd]);
    raw.forEach((item) => item.points.forEach((point) => {
      if (point.日期 >= commonStart && point.日期 <= commonEnd) dateSet.add(point.日期);
    }));
    const dates = [...dateSet].sort();
    const entries = raw.map((item) => {
      if (item.allTimeValueMode) {
        const rows = item.points
          .filter((point) => point.日期 >= commonStart && point.日期 <= commonEnd)
          .map((point) => {
            const value = item.allTimeValueMode === "unit_nav"
              ? (Number(point.数值) - 1) * 100
              : Number(point.数值);
            return Number.isFinite(value) ? { 日期: point.日期, 数值: value } : null;
          })
          .filter(Boolean);
        return [item.name, rows];
      }
      const basePoint = pointAtOrBefore(item.points, commonStart) || item.points.find((point) => point.日期 >= commonStart);
      if (!basePoint) return [item.name, []];
      const rows = dates.map((date) => {
        const point = pointAtOrBefore(item.points, date) || item.points.find((candidate) => candidate.日期 >= date);
        if (!point) return null;
        const value = returnFromBase(point.数值, basePoint.数值, item.mode);
        return Number.isFinite(value) ? { 日期: date, 数值: value } : null;
      }).filter(Boolean);
      return [item.name, rows];
    });
    return Object.fromEntries(entries.filter(([, rows]) => rows.length));
  }
  function chartLegendHtml(names, visibility) {
    if (!names.length) return "";
    return `<div class="legend">${names.map((name) => {
      const checked = visibility[name] !== false ? "checked" : "";
      return `<label class="legend-item"><input class="legend-toggle" type="checkbox" data-series-name="${esc(name)}" ${checked}><i style="background:${colorForSeries(name)}"></i><span>${esc(name)}</span></label>`;
    }).join("")}</div>`;
  }
  function drawReturnChart(el, seriesMap, options = {}) {
    const range = options.range || "all";
    const names = Object.keys(seriesMap || {});
    const defaultVisibleSeries = Array.isArray(options.defaultVisibleSeries) ? new Set(options.defaultVisibleSeries) : null;
    const visibilityKey = `${names.join("|")}::${defaultVisibleSeries ? [...defaultVisibleSeries].join("|") : "all"}`;
    if (!el.__seriesVisibility || el.__seriesVisibilityKey !== visibilityKey) {
      el.__seriesVisibility = {};
      names.forEach((name) => { el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
      el.__seriesVisibilityKey = visibilityKey;
    } else {
      names.forEach((name) => { if (!(name in el.__seriesVisibility)) el.__seriesVisibility[name] = defaultVisibleSeries ? defaultVisibleSeries.has(name) : true; });
    }
    Object.keys(el.__seriesVisibility).forEach((name) => { if (!names.includes(name)) delete el.__seriesVisibility[name]; });
    const transformed = transformSeries(seriesMap, range, !!options.alreadyReturn, el.__seriesVisibility);
    const series = Object.entries(transformed);
    if (!series.length) {
      el.innerHTML = `${chartLegendHtml(names, el.__seriesVisibility)}<div class="empty">暂无可绘制曲线</div>`;
      el.querySelectorAll(".legend-toggle").forEach((input) => {
        input.addEventListener("change", () => {
          el.__seriesVisibility[input.dataset.seriesName] = input.checked;
          drawReturnChart(el, seriesMap, options);
        });
      });
      return;
    }
    const width = Math.max(920, Math.round(el.getBoundingClientRect().width || el.clientWidth || 960));
    const height = options.height || 310;
    const pad = { left: 44, right: 8, top: 22, bottom: 46 };
    const allValues = series.flatMap(([, rows]) => rows.map((row) => row.数值));
    const allDates = [...new Set(series.flatMap(([, rows]) => rows.map((row) => row.日期)))].sort();
    const minDate = new Date(allDates[0]).getTime();
    const maxDate = new Date(allDates[allDates.length - 1]).getTime();
    let min = Math.min(...allValues), max = Math.max(...allValues);
    if (min === max) { min -= 1; max += 1; }
    const yPad = Math.max(0.3, (max - min) * 0.12);
    min -= yPad; max += yPad;
    const xOf = (dateText) => {
      const t = new Date(dateText).getTime();
      return pad.left + (maxDate === minDate ? 0 : (t - minDate) / (maxDate - minDate)) * (width - pad.left - pad.right);
    };
    const yOf = (value) => height - pad.bottom - ((value - min) / (max - min)) * (height - pad.top - pad.bottom);
    const tickCount = Math.min(6, allDates.length);
    const tickDates = tickCount <= 1 ? allDates : Array.from({ length: tickCount }, (_, index) => allDates[Math.round(index * (allDates.length - 1) / (tickCount - 1))]).filter((date, index, arr) => arr.indexOf(date) === index);
    const multiYearAxis = new Date(allDates[0]).getFullYear() !== new Date(allDates[allDates.length - 1]).getFullYear();
    const tickLabel = (date) => multiYearAxis ? date.slice(0, 7) : date.slice(5);
    const xTicks = tickDates.map((date) => {
      const x = xOf(date);
      return `<line class="tick-line" x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}"/><text class="axis-text" x="${x}" y="${height - 15}" text-anchor="middle">${esc(tickLabel(date))}</text>`;
    }).join("");
    const grid = [0, .25, .5, .75, 1].map((ratio) => {
      const y = pad.top + ratio * (height - pad.top - pad.bottom);
      const value = max - ratio * (max - min);
      return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#edf1f5"/><text class="axis-text" x="8" y="${y + 4}">${value.toFixed(1)}%</text>`;
    }).join("");
    const zero = min < 0 && max > 0 ? `<line x1="${pad.left}" y1="${yOf(0)}" x2="${width - pad.right}" y2="${yOf(0)}" stroke="#cbd5e1" stroke-dasharray="4 4"/>` : "";
    const paths = series.map(([name, rows]) => {
      const color = colorForSeries(name);
      const d = rows.map((row, i) => `${i ? "L" : "M"}${xOf(row.日期).toFixed(1)},${yOf(row.数值).toFixed(1)}`).join(" ");
      return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
    }).join("");
    const legend = chartLegendHtml(names, el.__seriesVisibility);
    el.innerHTML = `${legend}<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.title || "收益曲线")}">${xTicks}${grid}${zero}<line x1="${pad.left}" y1="${height-pad.bottom}" x2="${width-pad.right}" y2="${height-pad.bottom}" stroke="#d0d7de"/>${paths}<g class="hover-layer" visibility="hidden"><line class="hover-line" x1="0" y1="${pad.top}" x2="0" y2="${height - pad.bottom}"/><g class="hover-points"></g></g></svg><div class="chart-tooltip" hidden></div>`;
    el.querySelectorAll(".legend-toggle").forEach((input) => {
      input.addEventListener("change", () => {
        el.__seriesVisibility[input.dataset.seriesName] = input.checked;
        drawReturnChart(el, seriesMap, options);
      });
    });
    const svg = el.querySelector("svg");
    const tip = el.querySelector(".chart-tooltip");
    const hoverLayer = svg.querySelector(".hover-layer");
    const hoverLine = svg.querySelector(".hover-line");
    const hoverPoints = svg.querySelector(".hover-points");
    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const viewX = (event.clientX - rect.left) / rect.width * width;
      const nearestDate = allDates.reduce((best, date) => Math.abs(xOf(date) - viewX) < Math.abs(xOf(best) - viewX) ? date : best, allDates[0]);
      const guideX = xOf(nearestDate);
      const rows = series.map(([name, values]) => {
        let nearest = values.find((row) => row.日期 === nearestDate) || pointAtOrBefore(values, nearestDate) || values[0];
        return { name, value: nearest?.数值, color: colorForSeries(name) };
      }).filter((row) => Number.isFinite(row.value));
      hoverLayer.setAttribute("visibility", "visible");
      hoverLine.setAttribute("x1", guideX.toFixed(1));
      hoverLine.setAttribute("x2", guideX.toFixed(1));
      hoverPoints.innerHTML = rows.map((row) => `<circle cx="${guideX.toFixed(1)}" cy="${yOf(row.value).toFixed(1)}" r="4" fill="#fff" stroke="${row.color}" stroke-width="2"/>`).join("");
      tip.innerHTML = `<strong>${esc(nearestDate)}</strong>${rows.map((row) => `<div class="chart-tip-row"><span><i class="chart-dot" style="background:${row.color}"></i>${esc(row.name)}</span><b class="${row.value >= 0 ? "ret-pos" : "ret-neg"}">${row.value.toFixed(2)}%</b></div>`).join("")}`;
      tip.hidden = false;
      const hostRect = el.getBoundingClientRect();
      const localX = event.clientX - hostRect.left;
      const localY = event.clientY - hostRect.top;
      tip.style.left = `${Math.min(localX + 16, el.clientWidth - 250)}px`;
      tip.style.top = `${Math.max(10, localY - 20)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; hoverLayer.setAttribute("visibility", "hidden"); });
  }
  const qualityScopeMap = {
    overview: ["首页", "负责人总览", "内网静态部署", "全局"],
    strategies: ["策略列表", "排名样本"],
    insights: ["数据洞察", "仓位分析", "调仓分析", "基金调仓榜单"],
    compare: ["策略对比"],
    ai_topic: ["主题分析", "AI选策略"],
    topic: ["主题分析", "AI选策略"],
    target_profit: ["目标盈分析"],
    ai: ["AI选策略"],
    strategy_detail: ["策略详情", "策略列表", "排名样本"],
    fund_detail: ["基金详情", "仓位分析", "主题分析"],
    quality: []
  };
  function qualityPack() {
    return window.__BASIC_DATA_QUALITY_PACK__ || state.dataQualityPack || {};
  }
  function pageQualityIssues(pageKey = "") {
    const pack = qualityPack();
    const checks = pack.checks || [];
    const scopes = qualityScopeMap[pageKey] || [];
    return checks.filter((row) => {
      const status = row.状态 || row.status;
      if (status !== "warn" && status !== "error" && status !== "bad") return false;
      if (!scopes.length) return true;
      const impact = String(row.影响页面 || "");
      return scopes.some((scope) => impact.includes(scope));
    });
  }
  function renderGlobalQualityGate(pageKey = "", targetId = "globalQualityGate") {
    const el = byId(targetId);
    if (!el) return;
    el.hidden = true;
    el.innerHTML = "";
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-field]");
    if (!button) return;
    const field = button.getAttribute("data-field");
    showInfoModal(field, businessFieldDescriptions[field] || dict()[field] || fallbackFieldDescription(field));
  });
  document.addEventListener("click", (event) => {
    if (event.target.id === "fieldModal" || event.target.id === "fieldModalClose") {
      byId("fieldModal").hidden = true;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && byId("fieldModal")) byId("fieldModal").hidden = true;
  });
  showPageLoading();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installPageChromeObserver, { once: true });
  } else {
    installPageChromeObserver();
  }
  window.addEventListener("load", () => {
    hidePageLoading();
    scheduleInternalTestNotice();
  });
  return { state, byId, esc, fmt, pct, pctSigned, valueHtml, toneClass, statusBadge, label, table, valueList, metricValue, metric, params, loadScript, showPageLoading, updatePageLoading, hidePageLoading, ensureInternalTestNotice, drawReturnChart, isDerivedField, fieldSourceText, showInfoModal, showHtmlModal, pageQualityIssues, renderGlobalQualityGate };
})();
"""


OVERVIEW_JS = r"""
(() => {
  const B = window.BasicData;
  const summary = B.state.summary;
  const root = B.byId("overviewPage");
  const overview = summary.overview;
  root.innerHTML = `
    <section class="page-title">
      <div>
        <h1>整体数据统计说明</h1>
        <p class="desc">只展示基础数据和核心口径，内部状态码和细碎字典值已归并为业务可读字段。点击任意字段名旁的问号查看口径说明。</p>
      </div>
      <span class="pill">生成时间 ${B.esc(overview.生成时间)}</span>
    </section>
    <section class="grid">
      ${B.metric("数据更新至", overview.数据更新至)}
      ${B.metric("策略总数", overview.策略总数)}
      ${B.metric("天天策略数", overview.天天策略数)}
      ${B.metric("纳入回放策略数", overview.纳入回放策略数)}
      ${B.metric("有历史调仓策略数", overview.有历史调仓策略数)}
      ${B.metric("有官方业绩策略数", overview.有官方业绩策略数)}
      ${B.metric("基金净值行数", overview.基金净值行数)}
      ${B.metric("策略基金净值缺失数", overview.策略基金净值缺失数, "为 0 表示当前策略依赖基金均有净值")}
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>渠道覆盖</h2><p class="desc">覆盖率按该渠道策略数为分母计算。</p></div></div>
      ${B.table(["渠道", "渠道类型", "策略数", "完整策略数", "官方业绩覆盖", "历史调仓覆盖", "当前持仓覆盖", "回放覆盖", "最新业绩日", "最新调仓日"], summary.channelStats, (row, h) => h.endsWith("覆盖") ? B.pct(row[h]) : B.fmt(row[h]))}
    </section>
    <section class="two-col">
      <section class="panel">
        <div class="panel-head"><h2>核心表记录数</h2></div>
        ${B.table(["表名", "记录数"], summary.tableCounts)}
      </section>
      <section class="panel">
        <div class="panel-head"><h2>页面字段口径字典</h2></div>
        <p class="desc">本页面、策略列表和策略详情共用同一份字段字典。实际使用时点击字段名旁的问号即可查看。</p>
        ${B.table(["表名", "记录数"], Object.keys(summary.fieldDictionary).map((name, index) => ({ 表名: name, 记录数: index + 1 })))}
      </section>
    </section>
  `;
})();
"""


STRATEGIES_JS = r"""
(() => {
  const B = window.BasicData;
  const summary = B.state.summary;
  const root = B.byId("strategyListPage");
  const allStrategies = summary.strategies || [];
  const isRegularRankStrategy = (item) => Number(item.是否纳入常规排名 ?? 1) === 1;
  const benchmarkBucketValue = (item) => item.基准风险资产权重 || "未分档";
  const isUnbucketed = (item) => benchmarkBucketValue(item) === "未分档";
  const listStrategies = allStrategies.filter((item) => (
    (item.数据完整性 === "完整" && isRegularRankStrategy(item)) || isUnbucketed(item) || Number(item.仅列表展示 ?? 0) === 1
  ));
  const unbucketedTotal = listStrategies.filter(isUnbucketed).length;
  const hiddenStrategyTotal = Math.max(0, allStrategies.length - listStrategies.length);
  const channels = [...new Set(listStrategies.map((item) => item.渠道))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const pools = [...new Set(listStrategies.map((item) => item.主可比池).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const regions = [...new Set(listStrategies.map((item) => item.市场地域).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const activePassiveOptions = [...new Set(listStrategies.map((item) => item.主动被动).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const risks = [...new Set(listStrategies.map((item) => item.风险等级).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const bucketOrder = Array.from({ length: 11 }, (_, index) => `L${index}`);
  const bucketSort = (a, b) => (bucketOrder.indexOf(a) === -1 ? 999 : bucketOrder.indexOf(a)) - (bucketOrder.indexOf(b) === -1 ? 999 : bucketOrder.indexOf(b)) || a.localeCompare(b, "zh-CN");
  const benchmarkBuckets = [...new Set(listStrategies.map(benchmarkBucketValue))].sort(bucketSort);
  const defaultChannel = channels.find((name) => name.includes("天天基金")) || "";
  const state = { page: 1, pageSize: 10, rows: [], sortField: "近一月", sortDir: "desc", showAllClassificationNotes: true, activeExplainDimension: "" };
  const returnHeaders = ["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率"];
  const listStats = summary.strategyListStats || {};
  const overview = B.state.summary?.overview || {};
  function formatDataSyncTime(value) {
    const text = String(value || "").trim();
    const match = text.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})/);
    if (!match) return text;
    return `${match[1]}年${String(match[2]).padStart(2, "0")}月${String(match[3]).padStart(2, "0")}日${String(match[4]).padStart(2, "0")}时${String(match[5]).padStart(2, "0")}分`;
  }
  const dataSyncTime = formatDataSyncTime(overview.数据刷新时间 || overview.生成时间);
  const classificationRules = {
    主可比池: {
      "目标日期/养老型": "互斥主排名池，优先级第1。名称、标签、描述或策略类型命中“目标日期/养老/退休/20[3-6][0-9]”等生命周期关键词即归属；无持仓权重阈值。",
      "目标盈系列产品": "互斥主排名池，优先级第2。名称、标签、描述或策略类型需命中目标盈/小目标/小盈加/智盈等品牌，或同时具备明确目标收益/达标止盈机制与期次、到期、赎回等生命周期证据才归属；普通止盈止损、预期兑现后止盈、目标日期到期时间不归入目标盈。",
      "海外/全球型": "互斥主排名池，优先级第3。QDII/海外基金权重 >= 30%，或海外业绩基准权重 >= 30%，或策略名称/标签明确命中“海外/QDII/港股/恒生/纳斯达克/标普/美国/印度/越南/日经”等境外市场词；通用投资范围、机构品牌、黄金/商品不触发，“MSCI沪深300/MSCI中国A股/MSCI中国”不单独触发。",
      "主题/行业型": "互斥主排名池，优先级第4。商品/黄金基金权重 >= 20%，或名称、标签、描述、业绩基准命中“主题/行业/科技/硬科技/AI/人工智能/医药/消费/军工/新能源/碳中和/环保/黄金/战略新兴/制造/半导体/港股互联网”等主题行业关键词。",
      "现金管理型": "互斥主排名池，优先级第5。货币基金权重 >= 80%，或基准货币权重 >= 80%；主要用于低波动、流动性类策略比较。",
      "纯债/短债型": "互斥主排名池，优先级第6。债券基金权重 + 货币基金权重 + 0.5 * 混合基金权重 >= 90%，且权益中枢 < 10%；若有基准拆分，则基准债券权重 + 基准货币权重 >= 90% 且基准权益权重 < 10% 也可触发。",
      "固收增强型": "互斥主排名池，优先级第7。债券基金权重 + 货币基金权重 + 0.5 * 混合基金权重 >= 70%，且权益中枢 < 40%；适合偏债增强类策略内部比较。",
      "偏股配置型": "互斥主排名池，优先级第8。权益基金权重 + 0.5 * 混合基金权重 >= 60%，或基准权益权重 >= 60%；以权益收益风险为核心评价。",
      "多资产配置型": "互斥主排名池，兜底优先级第9。未命中目标日期/养老、目标盈系列、海外/全球、主题/行业、现金管理、纯债/短债、固收增强、偏股配置时归属；用于跨资产配置能力比较。"
    },
    市场地域: {
      "国内": "辅助筛选维度。QDII/海外基金权重 < 10%，海外基准权重 < 10%，且策略名称/标签未出现明确境外市场证据。",
      "国内+海外": "辅助筛选维度。QDII/海外基金权重或海外基准权重介于 10% 到 30%，且未进入海外/全球主池；用于提示存在跨市场暴露。",
      "海外/全球": "辅助筛选维度。QDII/海外基金权重 >= 30%，海外基准权重 >= 30%，或策略名称/标签明确海外/全球/港股/美股等境外市场。"
    },
    主动被动: {
      "主动为主": "辅助筛选维度。主动基金权重 >= 60%，且指数/被动工具权重 < 40%。",
      "指数/被动为主": "辅助筛选维度。指数基金、ETF、ETF联接或指数增强基金权重 >= 70%。",
      "主动被动混合": "辅助筛选维度。主动基金权重 >= 30%，且指数/被动工具权重 >= 30%。",
      "未稳定识别": "辅助筛选维度。底层基金主动/被动属性覆盖不足，或主动基金权重 < 60%、指数/被动工具权重 < 70%、且二者未同时 >= 30%。"
    },
    风险等级: {
      "default": "渠道披露维度。直接使用 App/平台披露风险等级，不用系统算法重算；无系统阈值。适合作为合规展示和风险分层筛选，不作为业绩主排名池。"
    }
  };
  const classificationDimensionMeta = {
    主可比池: {
      title: "策略类型",
      summary: "互斥主排名池，用于正式评价和同类比较。系统按固定优先级只给每个策略分配一个策略类型，避免重复进入多个排名池。"
    },
    市场地域: {
      title: "市场地域",
      summary: "并列筛选维度，用于识别国内、海外/全球或国内+海外暴露。该维度不改变策略类型归属，只用于观察跨市场风险收益差异。"
    },
    主动被动: {
      title: "主动/被动",
      summary: "并列筛选维度，按底层基金主动基金权重和指数/ETF工具权重归并。适合观察策略实现方式和投研能力差异。"
    },
    风险等级: {
      title: "风险等级",
      summary: "渠道直接披露字段，不由本系统重算。适合合规展示、风险分层和客户适配，不作为业绩主排名池。"
    }
  };
  root.innerHTML = `
    <section class="page-title">
      <div>
        <h1>策略列表</h1>
        <p class="desc">展示数据完整的常规策略，并保留全部未分档策略供查询核对；未分档策略不自动进入正式同类比较。</p>
      </div>
      <div class="title-pills">
        <span class="pill">${listStrategies.length.toLocaleString("zh-CN")} 个可查询策略</span>
        <span class="pill">未分档 ${unbucketedTotal.toLocaleString("zh-CN")} 个</span>
        <span class="pill">其他非查询对象 ${hiddenStrategyTotal.toLocaleString("zh-CN")} 个</span>
        <span class="pill">数据更新至 ${B.esc(overview.数据更新至 || "未披露")}</span>
        <span class="pill">数据刷新时间 ${B.esc(overview.数据刷新时间 || overview.生成时间 || "未披露")}</span>
      </div>
    </section>
    <section class="panel">
      <div class="filters">
        <input id="searchInput" class="control" type="search" placeholder="搜索策略、机构、代码、渠道、分类">
        <select id="channelSelect" class="control"><option value="">全部渠道</option>${channels.map((x) => `<option ${x === defaultChannel ? "selected" : ""}>${B.esc(x)}</option>`).join("")}</select>
        <select id="benchmarkBucketSelect" class="control"><option value="">全部基准风险资产权重</option>${benchmarkBuckets.map((x) => `<option>${B.esc(x)}</option>`).join("")}</select>
        <div class="filter-field">
          <select id="poolSelect" class="control"><option value="">全部策略类型</option>${pools.map((x) => `<option>${B.esc(x)}</option>`).join("")}</select>
          <button class="classification-info-button" type="button" data-class-info="主可比池" title="查看策略类型口径">!</button>
        </div>
        <div class="filter-field">
          <select id="regionSelect" class="control"><option value="">全部市场地域</option>${regions.map((x) => `<option>${B.esc(x)}</option>`).join("")}</select>
          <button class="classification-info-button" type="button" data-class-info="市场地域" title="查看市场地域口径">!</button>
        </div>
        <div class="filter-field">
          <select id="activePassiveSelect" class="control"><option value="">全部主动/被动</option>${activePassiveOptions.map((x) => `<option>${B.esc(x)}</option>`).join("")}</select>
          <button class="classification-info-button" type="button" data-class-info="主动被动" title="查看主动/被动口径">!</button>
        </div>
        <div class="filter-field">
          <select id="riskSelect" class="control"><option value="">全部风险等级</option>${risks.map((x) => `<option>${B.esc(x)}</option>`).join("")}</select>
          <button class="classification-info-button" type="button" data-class-info="风险等级" title="查看风险等级口径">!</button>
        </div>
        <select id="sortSelect" class="control">
          <option value="name">按策略名称</option>
          <option value="return">按累计收益率</option>
          <option value="week">按近一周收益</option>
          <option value="month" selected>按近一月收益</option>
          <option value="performanceDate">按最新业绩日期</option>
          <option value="drawdown">按最大回撤</option>
          <option value="rebalance">按最近调仓日</option>
        </select>
        <button id="resetButton" class="control" type="button">重置</button>
      </div>
      <div id="classificationExplain" class="classification-explain"></div>
      <div class="pager">
        <p id="resultCount" class="desc"></p>
        <div class="pager-controls">
          <label class="small">每页
            <select id="pageSizeSelect" class="control" style="width:84px"><option>10</option><option>20</option><option>50</option><option>100</option></select>
          </label>
          <button id="prevPage" type="button">上一页</button>
          <span id="pageInfo" class="small"></span>
          <button id="nextPage" type="button">下一页</button>
        </div>
      </div>
      <div class="strategy-table-shell">
        <div id="topScrollbar" class="strategy-scrollbar"><div class="strategy-scrollbar-inner"></div></div>
        <div id="strategyTableWrap" class="strategy-table-wrap"></div>
      </div>
    </section>
  `;
  function sortValue(row, key) {
    const value = Number(row[key]);
    return Number.isFinite(value) ? value : -999999;
  }
  function sortHeader(h, cls = "") {
    const active = state.sortField === h;
    const arrow = active ? (state.sortDir === "asc" ? "▲" : "▼") : "↕";
    const label = h === "天天当前对客展示" ? B.esc("对客展示") : B.label(h);
    return `<th class="${cls}"><span class="sort-head ${active ? "is-active" : ""}" role="button" tabindex="0" data-sort-field="${B.esc(h)}">${label}<span class="sort-arrow">${arrow}</span></span></th>`;
  }
  function compareField(a, b, field) {
    if (returnHeaders.includes(field) || ["最大回撤", "调仓次数", "权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重"].includes(field)) {
      return sortValue(a, field) - sortValue(b, field);
    }
    if (field.includes("日") || field.includes("截至")) {
      return String(a[field] || "").localeCompare(String(b[field] || ""));
    }
    return String(a[field] || "").localeCompare(String(b[field] || ""), "zh-CN");
  }
  function applySortPreset(value) {
    const preset = {
      name: ["策略名称", "asc"],
      return: ["累计收益率", "desc"],
      week: ["近一周", "desc"],
      month: ["近一月", "desc"],
      performanceDate: ["最新业绩日期", "desc"],
      drawdown: ["最大回撤", "asc"],
      rebalance: ["最近调仓日", "desc"]
    }[value] || ["策略名称", "asc"];
    state.sortField = preset[0];
    state.sortDir = preset[1];
  }
  function syncScrollbars() {
    const wrap = B.byId("strategyTableWrap");
    const top = B.byId("topScrollbar");
    const inner = top.querySelector(".strategy-scrollbar-inner");
    inner.style.width = `${wrap.scrollWidth}px`;
    top.onscroll = () => { wrap.scrollLeft = top.scrollLeft; };
    wrap.onscroll = () => { top.scrollLeft = wrap.scrollLeft; };
  }
  function renderTable(rows) {
    const weightHeaders = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重"];
    const trailingHeaders = ["研报产品类型", "研报股票子类型", "业务分类", "市场地域", "主动被动", "披露策略类型", "天天当前对客展示", "基准可用状态"];
    const headers = ["策略名称", "渠道", "投顾机构", "主可比池", "风险等级", "基准风险资产权重", "业绩基准说明", "最新业绩日期", ...returnHeaders, "最大回撤", "天天展示状态", ...weightHeaders, "基础数据等级", "最新持仓日", "最近调仓日", "调仓次数", ...trailingHeaders];
    const head = headers.map((h, index) => {
      const cls = index === 0 ? "sticky-name" : index === 1 ? "sticky-channel" : returnHeaders.includes(h) || weightHeaders.includes(h) ? "narrow" : ["投顾机构", "主可比池", "主动被动", "基准可用状态", "业绩基准说明"].includes(h) ? "wide" : "";
      return sortHeader(h, cls);
    }).join("");
    const body = rows.length ? rows.map((row) => {
      return `<tr>${headers.map((h, index) => {
        const cls = index === 0 ? "sticky-name strategy-name-cell" : index === 1 ? "sticky-channel" : returnHeaders.includes(h) || weightHeaders.includes(h) ? "narrow" : ["投顾机构", "主可比池", "主动被动", "基准可用状态", "业绩基准说明"].includes(h) ? "wide" : "";
        let value;
        if (h === "策略名称") {
          value = `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID)}">${B.esc(row[h])}</a><div class="small">${B.label("策略代码")} ${B.esc(row.策略代码 || "未披露")}</div>`;
        } else if (returnHeaders.includes(h) || h === "最大回撤") {
          value = B.pctSigned(row[h]);
        } else if (weightHeaders.includes(h)) {
          value = B.pct(row[h]);
        } else if (h === "最近调仓日" && !row[h]) {
          value = '<span class="value-muted">无历史调仓事件</span>';
        } else if (h === "业绩基准说明") {
          value = row[h] ? `<span class="small">${B.esc(row[h])}</span>` : '<span class="value-muted">未披露</span>';
        } else if (h === "基准风险资产权重") {
          value = B.esc(benchmarkBucketValue(row));
        } else {
          value = B.fmt(row[h]);
        }
        return `<td class="${cls}">${value}</td>`;
      }).join("")}</tr>`;
    }).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    B.byId("strategyTableWrap").innerHTML = `<table class="strategy-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    B.byId("strategyTableWrap").querySelectorAll("[data-sort-field]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (event.target.closest("[data-field]")) return;
        const field = button.dataset.sortField;
        if (state.sortField === field) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        else {
          state.sortField = field;
          state.sortDir = returnHeaders.includes(field) || ["最大回撤", "调仓次数", "权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重"].includes(field) ? "desc" : "asc";
        }
        state.page = 1;
        render();
      });
    });
    requestAnimationFrame(syncScrollbars);
  }
  function selectedClassificationNotes() {
    const selected = [
      ["主可比池", B.byId("poolSelect").value],
      ["市场地域", B.byId("regionSelect").value],
      ["主动被动", B.byId("activePassiveSelect").value],
      ["风险等级", B.byId("riskSelect").value],
    ].filter(([, value]) => value);
    return selected.map(([dimension, value]) => {
      const rule = classificationRules[dimension]?.[value] || classificationRules[dimension]?.default || "该筛选维度来自当前分析库标准化字段。";
      return { dimension, value, rule };
    });
  }
  function dimensionTitle(dimension) {
    return classificationDimensionMeta[dimension]?.title || dimension;
  }
  function dimensionSummary(dimension) {
    return classificationDimensionMeta[dimension]?.summary || "该维度来自当前分析库标准化字段。";
  }
  function ruleCards(dimension, selectedValue = "") {
    const rules = classificationRules[dimension] || {};
    return Object.entries(rules).map(([value, rule]) => {
      const label = value === "default" ? "全部披露值" : value;
      const selected = selectedValue && (value === selectedValue || value === "default");
      return `<div class="classification-rule-card ${selected ? "is-selected" : ""}">
        <b>${B.esc(label)}</b>
        <span>${B.esc(rule)}</span>
      </div>`;
    }).join("");
  }
  function renderClassificationExplain() {
    const host = B.byId("classificationExplain");
    const notes = selectedClassificationNotes();
    const activeDimension = state.activeExplainDimension;
    if (!notes.length && !activeDimension) {
      host.innerHTML = '<div class="classification-empty">选择策略类型、市场地域、主动/被动或风险等级，或点击每个筛选项右侧的 ! 查看该分类口径。</div>';
      return;
    }
    const visible = state.showAllClassificationNotes ? notes : notes.slice(0, 1);
    const selectedHtml = notes.length ? `
      <div class="classification-note-head">
        <strong>当前筛选命中的分类规则</strong>
        ${notes.length > 1 ? `<button id="classificationExplainToggle" type="button">${state.showAllClassificationNotes ? "收起" : `展开全部 ${notes.length} 条`}</button>` : ""}
      </div>
      <div class="classification-note-lines">
        ${visible.map((item) => `<div class="classification-rule-card is-selected"><b>${B.esc(dimensionTitle(item.dimension))}｜${B.esc(item.value)}</b><span>${B.esc(item.rule)}</span></div>`).join("")}
      </div>` : "";
    const dimensionHtml = activeDimension ? `
      <div class="classification-note-head">
        <strong>${B.esc(dimensionTitle(activeDimension))}口径说明</strong>
      </div>
      <div class="classification-summary-card">${B.esc(dimensionSummary(activeDimension))}</div>
      <div class="classification-rule-grid">${ruleCards(activeDimension, B.byId({
        主可比池: "poolSelect",
        市场地域: "regionSelect",
        主动被动: "activePassiveSelect",
        风险等级: "riskSelect"
      }[activeDimension])?.value || "")}</div>` : "";
    host.innerHTML = `
      ${selectedHtml}
      ${selectedHtml && dimensionHtml ? '<div style="height:10px"></div>' : ""}
      ${dimensionHtml}`;
    const button = B.byId("classificationExplainToggle");
    if (button) {
      button.addEventListener("click", () => {
        state.showAllClassificationNotes = !state.showAllClassificationNotes;
        renderClassificationExplain();
      });
    }
  }
  function render() {
    const keyword = B.byId("searchInput").value.trim().toLowerCase();
    const channel = B.byId("channelSelect").value;
    const benchmarkBucket = B.byId("benchmarkBucketSelect").value;
    const pool = B.byId("poolSelect").value;
    const region = B.byId("regionSelect").value;
    const activePassive = B.byId("activePassiveSelect").value;
    const risk = B.byId("riskSelect").value;
    let rows = listStrategies.filter((item) => {
      if (channel && item.渠道 !== channel) return false;
      if (benchmarkBucket && benchmarkBucketValue(item) !== benchmarkBucket) return false;
      if (pool && item.主可比池 !== pool) return false;
      if (region && item.市场地域 !== region) return false;
      if (activePassive && item.主动被动 !== activePassive) return false;
      if (risk && item.风险等级 !== risk) return false;
      if (keyword && !String(item.searchText || "").toLowerCase().includes(keyword)) return false;
      return true;
    });
    rows.sort((a, b) => {
      const compared = compareField(a, b, state.sortField);
      return state.sortDir === "asc" ? compared : -compared;
    });
    state.rows = rows;
    const maxPage = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.min(state.page, maxPage);
    const start = (state.page - 1) * state.pageSize;
    const pageRows = rows.slice(start, start + state.pageSize);
    const resultCount = B.byId("resultCount");
    resultCount.textContent = `当前筛选 ${rows.length.toLocaleString("zh-CN")} 个策略，其中未分档 ${rows.filter(isUnbucketed).length.toLocaleString("zh-CN")} 个；未分档仅用于查询核对，不进入正式同类排名`;
    if (dataSyncTime) {
      const sync = document.createElement("span");
      sync.className = "strategy-data-sync-time";
      sync.style.cssText = "color:#b42318;font-weight:800;margin-left:10px;white-space:nowrap";
      sync.textContent = `最近一次数据同步：${dataSyncTime}`;
      resultCount.appendChild(sync);
    }
    B.byId("pageInfo").textContent = `${state.page} / ${maxPage}`;
    B.byId("prevPage").disabled = state.page <= 1;
    B.byId("nextPage").disabled = state.page >= maxPage;
    renderClassificationExplain();
    renderTable(pageRows);
  }
  function resetPageAndRender() {
    state.page = 1;
    state.showAllClassificationNotes = true;
    render();
  }
  ["searchInput", "channelSelect", "benchmarkBucketSelect", "poolSelect", "regionSelect", "activePassiveSelect", "riskSelect"].forEach((id) => B.byId(id).addEventListener("input", resetPageAndRender));
  root.querySelectorAll("[data-class-info]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeExplainDimension = button.dataset.classInfo || "";
      state.showAllClassificationNotes = true;
      renderClassificationExplain();
    });
  });
  B.byId("sortSelect").addEventListener("input", () => {
    applySortPreset(B.byId("sortSelect").value);
    resetPageAndRender();
  });
  B.byId("pageSizeSelect").addEventListener("change", () => {
    state.pageSize = Number(B.byId("pageSizeSelect").value);
    resetPageAndRender();
  });
  B.byId("prevPage").addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    render();
  });
  B.byId("nextPage").addEventListener("click", () => {
    state.page += 1;
    render();
  });
  B.byId("resetButton").addEventListener("click", () => {
    B.byId("searchInput").value = "";
    B.byId("channelSelect").value = defaultChannel;
    B.byId("benchmarkBucketSelect").value = "";
    B.byId("poolSelect").value = "";
    B.byId("regionSelect").value = "";
    B.byId("activePassiveSelect").value = "";
    B.byId("riskSelect").value = "";
    B.byId("sortSelect").value = "month";
    B.byId("pageSizeSelect").value = "10";
    applySortPreset("month");
    state.pageSize = 10;
    state.activeExplainDimension = "";
    resetPageAndRender();
  });
  render();
})();
"""


INSIGHTS_JS = r"""
(() => {
  const B = window.BasicData;
  const summary = B.state.summary || {};
  const root = B.byId("insightsPage");
  const allRows = summary.strategies || [];
  const completeRows = allRows.filter((row) => row.数据完整性 === "完整");
  const allRowById = new Map(allRows.map((row) => [row.统一策略ID, row]));
  const analysisChannelKeywords = ["天天基金/投顾", "广发基金"];
  function inAnalysisUniverse(row) {
    const channel = String(row?.渠道 || "");
    return analysisChannelKeywords.some((name) => channel.includes(name)) && Number(row?.是否纳入常规排名 ?? 1) === 1;
  }
  const analysisRows = completeRows.filter(inAnalysisUniverse);
  const analysisChannels = [...new Set(analysisRows.map((row) => row.渠道).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const gfChannel = analysisChannels.find((name) => name === "广发基金") || "广发基金";
  const channels = analysisChannels.includes(gfChannel) ? [gfChannel] : analysisChannels;
  const pools = [...new Set(analysisRows.map((row) => row.主可比池).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const regions = [...new Set(analysisRows.map((row) => row.市场地域).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const activePassiveOptions = [...new Set(analysisRows.map((row) => row.主动被动).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const broadBucketOrder = Array.from({ length: 11 }, (_, index) => `L${index}`);
  const broadEquityBuckets = [...new Set(analysisRows.map((row) => row.基准风险资产权重).filter(Boolean))].sort((a, b) => {
    const ai = broadBucketOrder.indexOf(a);
    const bi = broadBucketOrder.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.localeCompare(b, "zh-CN");
  });
  const returnFields = ["近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "累计收益率"];
  const turnoverOptions = [
    ["低换手", "低换手：年化换手率 < 30%"],
    ["中换手", "中换手：30% <= 年化换手率 < 120%"],
    ["高换手", "高换手：年化换手率 >= 120%"],
    ["未披露", "未披露：缺少可计算调仓或运作年数"],
  ];
  const volatilityOptions = [
    ["低波动", "低波动：年化波动率 < 5%"],
    ["中波动", "中波动：5% <= 年化波动率 < 15%"],
    ["高波动", "高波动：年化波动率 >= 15%"],
    ["未披露", "未披露：缺少可计算日收益序列"],
  ];
  const tabs = [
    ["overview", "经营总览"],
    ["focus", "经营重点"],
    ["week", "近一周总览"],
    ["structure", "产品结构"],
    ["performance", "收益风险"],
    ["rebalance", "调仓分析"],
    ["opportunity", "业务计划"],
  ];
  const defaultChannel = gfChannel;
  const query = new URLSearchParams(window.location.search);
  const initialTab = tabs.some(([key]) => key === query.get("tab")) ? query.get("tab") : "overview";
  const state = { tab: initialTab, channel: defaultChannel, metric: "近1年", pool: "", broadEquityBucket: "", region: "", activePassive: "", turnover: "", volatility: "" };

  function num(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function values(rows, field) {
    return rows.map((row) => num(row[field])).filter((value) => value !== null).sort((a, b) => a - b);
  }
  function median(list) {
    if (!list.length) return null;
    const mid = Math.floor(list.length / 2);
    return list.length % 2 ? list[mid] : (list[mid - 1] + list[mid]) / 2;
  }
  function quantile(list, ratio) {
    if (!list.length) return null;
    const index = Math.min(list.length - 1, Math.max(0, Math.ceil(list.length * ratio) - 1));
    return list[index];
  }
  function avg(list) {
    return list.length ? list.reduce((sum, value) => sum + value, 0) / list.length : null;
  }
  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }
  function countText(value) {
    return Number(value || 0).toLocaleString("zh-CN");
  }
  function pctText(value) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? "未披露" : `${Number(value).toFixed(2)}%`;
  }
  function ppText(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    const number = Number(value);
    const sign = number > 0 ? "+" : "";
    return `${sign}${number.toFixed(2)}pct`;
  }
  function signedPctText(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    const number = Number(value);
    const sign = number > 0 ? "+" : "";
    return `${sign}${number.toFixed(2)}%`;
  }
  function shortText(value, length = 48) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > length ? `${text.slice(0, length)}...` : text || "未披露";
  }
  function turnoverBucket(row) {
    const value = num(row.年化换手率);
    if (value === null) return "未披露";
    if (value < 30) return "低换手";
    if (value < 120) return "中换手";
    return "高换手";
  }
  function volatilityBucket(row) {
    const value = num(row.波动率);
    if (value === null) return "未披露";
    if (value < 5) return "低波动";
    if (value < 15) return "中波动";
    return "高波动";
  }
  function matchesFilters(row, includeChannel = true) {
    if (includeChannel && state.channel && row.渠道 !== state.channel) return false;
    if (state.pool && row.主可比池 !== state.pool) return false;
    if (state.broadEquityBucket && row.基准风险资产权重 !== state.broadEquityBucket) return false;
    if (state.region && row.市场地域 !== state.region) return false;
    if (state.activePassive && row.主动被动 !== state.activePassive) return false;
    if (state.turnover && turnoverBucket(row) !== state.turnover) return false;
    if (state.volatility && volatilityBucket(row) !== state.volatility) return false;
    return true;
  }
  function marketRows() {
    return analysisRows.filter((row) => matchesFilters(row, false));
  }
  function targetRows() {
    return analysisRows.filter((row) => matchesFilters(row, true));
  }
  function rankPercent(row, universe = marketRows()) {
    const value = num(row[state.metric]);
    const poolRows = universe.filter((item) => item.主可比池 === row.主可比池 && num(item[state.metric]) !== null);
    if (value === null || poolRows.length < 2) return null;
    const belowOrEqual = poolRows.filter((item) => num(item[state.metric]) <= value).length;
    return belowOrEqual / poolRows.length * 100;
  }
  function strategyLink(row) {
    return `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID)}">${B.esc(row.策略名称 || row.统一策略ID)}</a>`;
  }
  function renderKpis(items) {
    return `<section class="insight-hero">${items.map(([label, value, sub, tone]) => `
      <div class="insight-kpi ${tone || ""}"><span>${B.label(label)}</span><strong>${B.esc(value)}</strong><small>${B.esc(sub || "")}</small></div>
    `).join("")}</section>`;
  }
  function tableBlock(headers, rows, formatter) {
    return `<div class="insight-table">${B.table(headers, rows, formatter)}</div>`;
  }
  function dimensionRows(label, accessor, valuesList = null) {
    const universe = marketRows();
    const target = targetRows();
    const dimensionValues = valuesList || [...new Set(universe.map(accessor).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    return dimensionValues.map((value) => {
      const market = universe.filter((row) => accessor(row) === value);
      const channelRows = target.filter((row) => accessor(row) === value);
      const marketVals = values(market, state.metric);
      const channelVals = values(channelRows, state.metric);
      const marketMedian = median(marketVals);
      const channelMedian = median(channelVals);
      return {
        维度: label,
        类型: value,
        市场样本数: market.length,
        广发样本数: channelRows.length,
        广发覆盖率: market.length ? channelRows.length / market.length * 100 : null,
        广发中位收益: channelMedian,
        市场中位收益: marketMedian,
        中位差: channelMedian !== null && marketMedian !== null ? channelMedian - marketMedian : null,
        中位回撤: median(values(channelRows.length ? channelRows : market, "最大回撤")),
        中位波动: median(values(channelRows.length ? channelRows : market, "波动率")),
        高换手策略数: channelRows.filter((row) => turnoverBucket(row) === "高换手").length,
      };
    }).filter((row) => row.市场样本数 || row.广发样本数).sort((a, b) => b.市场样本数 - a.市场样本数);
  }
  function opportunityRows() {
    const universe = marketRows();
    const target = targetRows();
    return dimensionRows("策略类型", (row) => row.主可比池, pools).map((row) => {
      const market = universe.filter((item) => item.主可比池 === row.类型);
      const channelRows = target.filter((item) => item.主可比池 === row.类型);
      const marketVals = values(market, state.metric);
      const marketSorted = sortedByMetric(market);
      const gfSorted = sortedByMetric(channelRows);
      const gfTop3Avg = topAverage(channelRows, state.metric, 3);
      const gfTop5Avg = topAverage(channelRows, state.metric, 5);
      const marketTop3Avg = topAverage(market, state.metric, 3);
      const top3Gap = gfTop3Avg !== null && marketTop3Avg !== null ? gfTop3Avg - marketTop3Avg : null;
      const topThreshold = quantile(marketVals, 0.75);
      const best = gfSorted[0] ? num(gfSorted[0][state.metric]) : null;
      const headCount = topThreshold === null ? 0 : channelRows.filter((item) => {
        const value = num(item[state.metric]);
        return value !== null && value >= topThreshold;
      }).length;
      const leaderGap = topThreshold !== null && best !== null ? topThreshold - best : null;
      const targetDrawdown = median(values(channelRows, "最大回撤"));
      const marketDrawdown = median(values(market, "最大回撤"));
      const coverageScore = channelRows.length === 0 ? 34 : clamp((1 - Math.min(channelRows.length / Math.max(1, market.length * 0.08), 1)) * 24, 0, 24);
      const scaleScore = clamp(market.length / 12, 0, 20);
      const perfScore = channelRows.length === 0 ? 16 : clamp((top3Gap === null ? 0 : -top3Gap) * 1.4, 0, 24);
      const leaderScore = channelRows.length === 0 ? 12 : clamp((leaderGap || 0) * 1.2, 0, 16);
      const riskScore = targetDrawdown !== null && marketDrawdown !== null ? clamp((targetDrawdown - marketDrawdown) * 0.8, 0, 8) : 0;
      const score = Math.round(clamp(scaleScore + coverageScore + perfScore + leaderScore + riskScore, 0, 100));
      let suggestion = "维持观察";
      if (!channelRows.length && market.length >= 20) suggestion = "产品线空白，优先评估是否补齐";
      else if (channelRows.length <= 2 && market.length >= 30) suggestion = "覆盖偏薄，优先补齐策略梯度";
      else if (top3Gap !== null && top3Gap < -5 && headCount === 0) suggestion = "头部能力偏弱，复盘策略和标杆差异";
      else if (targetDrawdown !== null && marketDrawdown !== null && targetDrawdown > marketDrawdown + 5) suggestion = "回撤偏高，检查资产暴露与风控约束";
      else if (top3Gap !== null && top3Gap >= 0 && headCount > 0) suggestion = "已有头部优势，沉淀卖点和标杆话术";
      return {
        ...row,
        广发Top3平均收益: gfTop3Avg,
        广发Top5平均收益: gfTop5Avg,
        市场Top3平均收益: marketTop3Avg,
        广发Top3差距: top3Gap,
        头部策略数: headCount,
        头部差距: leaderGap,
        机会评分: score,
        复盘建议: suggestion,
      };
    }).sort((a, b) => b.机会评分 - a.机会评分 || b.市场样本数 - a.市场样本数);
  }
  function targetStrategyDiagnostics() {
    const universe = marketRows();
    return targetRows().map((row) => {
      const poolUniverse = universe.filter((item) => item.主可比池 === row.主可比池);
      const poolMedian = median(values(poolUniverse, state.metric));
      const poolDrawdown = median(values(poolUniverse, "最大回撤"));
      const poolVolatility = median(values(poolUniverse, "波动率"));
      const rank = rankPercent(row, universe);
      const ret = num(row[state.metric]);
      const dd = num(row.最大回撤);
      const volatility = num(row.波动率);
      const turnover = num(row.年化换手率);
      const issues = [];
      if (rank !== null && rank < 40) issues.push("收益分位低于40%");
      if (ret !== null && poolMedian !== null && ret < poolMedian - 3) issues.push("低于策略类型中位数3pct以上");
      if (dd !== null && poolDrawdown !== null && dd > poolDrawdown + 5) issues.push("回撤高于策略类型中位数5pct以上");
      if (volatility !== null && poolVolatility !== null && volatility > poolVolatility + 5) issues.push("波动率高于策略类型中位数5pct以上");
      if (turnover !== null && turnover >= 120) issues.push("年化换手率>=120%");
      if (row.基础数据等级 && row.基础数据等级 !== "A") issues.push(`基础数据等级${row.基础数据等级}`);
      const score = (rank === null ? 20 : Math.max(0, 55 - rank)) + issues.length * 8 + (turnover !== null && turnover >= 120 ? 8 : 0);
      return {
        ...row,
        策略类型: row.主可比池,
        所选收益: ret,
        池中位收益: poolMedian,
        排名分位: rank,
        诊断分数: Math.round(score),
        复盘建议: issues.length ? issues.join("；") : "表现或数据状态暂未触发预警",
      };
    }).sort((a, b) => b.诊断分数 - a.诊断分数 || (num(a.所选收益) || -999) - (num(b.所选收益) || -999));
  }
  function leaders(limit = 12) {
    const universe = marketRows();
    return universe
      .filter((row) => num(row[state.metric]) !== null)
      .sort((a, b) => num(b[state.metric]) - num(a[state.metric]))
      .slice(0, limit)
      .map((row) => ({ ...row, 策略类型: row.主可比池, 所选收益: num(row[state.metric]), 排名分位: rankPercent(row, universe) }));
  }
  function scoreClass(score) {
    if (score >= 65) return "";
    if (score >= 40) return "mid";
    return "low";
  }
  function opportunityList(stats) {
    return `<div class="opportunity-list">${stats.slice(0, 6).map((row) => `
      <div class="opportunity-row">
        <div class="score-badge ${scoreClass(row.机会评分)}">${row.机会评分}</div>
        <div>
          <strong>${B.esc(row.类型)}</strong>
          <p>${B.esc(row.复盘建议)}。广发样本 ${countText(row.广发样本数)}/${countText(row.市场样本数)}，Top3差距 ${ppText(row.广发Top3差距)}，头部差距 ${ppText(row.头部差距)}。</p>
        </div>
        <span class="insight-chip ${row.机会评分 >= 65 ? "bad" : row.机会评分 >= 40 ? "warn" : "good"}">${row.机会评分 >= 65 ? "优先" : row.机会评分 >= 40 ? "观察" : "维持"}</span>
      </div>
    `).join("")}</div>`;
  }
  function dimensionTable(title, rows) {
    return `<section class="panel">
      <div class="panel-head"><div><h2>${B.esc(title)}</h2><p class="desc">广发基金与当前筛选后的分析样本完整策略比较。</p></div></div>
      ${tableBlock(["类型", "市场样本数", "广发样本数", "广发覆盖率", "广发中位收益", "市场中位收益", "中位差", "中位回撤", "中位波动", "高换手策略数"], rows, (row, h) => {
        if (["广发中位收益", "市场中位收益", "中位回撤", "中位波动"].includes(h)) return h.includes("回撤") ? B.pctSigned(row[h]) : B.pctSigned(row[h]);
        if (h === "广发覆盖率") return pctText(row[h]);
        if (h === "中位差") return ppText(row[h]);
        return B.fmt(row[h]);
      })}
    </section>`;
  }
  function dimensionBenchmarkRows(label, accessor, valuesList = null) {
    const universe = marketRows();
    const target = targetRows();
    const dimensionValues = valuesList || [...new Set(universe.map(accessor).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    return dimensionValues.map((value) => {
      const market = universe.filter((row) => accessor(row) === value);
      const gf = target.filter((row) => accessor(row) === value);
      const marketSorted = sortedByMetric(market);
      const gfSorted = sortedByMetric(gf);
      const marketTop = marketSorted[0] || null;
      const gfBest = gfSorted[0] || null;
      const gfTop3Avg = topAverage(gf, state.metric, 3);
      const gfTop5Avg = topAverage(gf, state.metric, 5);
      const marketTop3Avg = topAverage(market, state.metric, 3);
      const marketTop5Avg = topAverage(market, state.metric, 5);
      const top3Gap = gfTop3Avg !== null && marketTop3Avg !== null ? gfTop3Avg - marketTop3Avg : null;
      const top5Gap = gfTop3Avg !== null && marketTop5Avg !== null ? gfTop3Avg - marketTop5Avg : null;
      const benchmarkGap = gfBest && marketTop && num(gfBest[state.metric]) !== null && num(marketTop[state.metric]) !== null ? num(gfBest[state.metric]) - num(marketTop[state.metric]) : null;
      const marketMedian = median(values(market, state.metric));
      const gfMedian = median(values(gf, state.metric));
      const medianGap = gfMedian !== null && marketMedian !== null ? gfMedian - marketMedian : null;
      const marketDrawdown = median(values(market, "最大回撤"));
      const gfDrawdown = median(values(gf, "最大回撤"));
      const drawdownGap = gfDrawdown !== null && marketDrawdown !== null ? gfDrawdown - marketDrawdown : null;
      const topThreshold = quantile(values(market, state.metric), 0.75);
      const headCount = topThreshold === null ? 0 : gf.filter((row) => {
        const value = num(row[state.metric]);
        return value !== null && value >= topThreshold;
      }).length;
      const gfTopRanks = gfSorted.slice(0, 3).map((row) => rankInSorted(row, marketSorted)).filter(Boolean);
      const gfTopAvgRank = avg(gfTopRanks);
      const gfTopAvgRankPct = gfTopAvgRank && marketSorted.length ? gfTopAvgRank / marketSorted.length * 100 : null;
      const rankText = gfTopAvgRank ? `平均第${gfTopAvgRank.toFixed(1)}/${marketSorted.length}，${rankPctText(gfTopAvgRankPct)}` : "未披露";
      let conclusion = "中性观察";
      let risk = "广发有样本但优势不突出，需要结合客户场景、风险和产品梯队判断经营资源。";
      let action = "跟踪市场Top5、广发Top3、中位收益和回撤，沉淀可解释的差异点。";
      let tone = "is-warn";
      if (!gf.length && market.length >= 10) {
        conclusion = "广发缺位";
        risk = "全市场已有供给但广发无完整可比产品，客户需求出现时承接不足。";
        action = "先判断该维度是否符合广发基金定位，符合则补齐至少一只可对标产品；不符合则明确不参与竞争。";
        tone = "is-bad";
      } else if (top5Gap !== null && top5Gap >= 0 && headCount > 0) {
        conclusion = "广发第一梯队";
        risk = "头部产品可比性较强，但仍需检查优势是否来自单一短区间或高波动暴露。";
        action = "把广发Top3列为重点经营名单，对照市场Top5补充收益、回撤、波动和换手话术。";
        tone = "is-good";
      } else if ((gfTopAvgRankPct !== null && gfTopAvgRankPct <= 25) || headCount > 0 || (top5Gap !== null && top5Gap > -2)) {
        conclusion = "头部可经营";
        risk = "相对市场Top1未必领先，但已接近第一梯队，关键在于选择合适产品和客群表达。";
        action = "重点包装进入市场前25%或接近Top5的广发产品，弱化同维度内长尾产品曝光。";
        tone = "is-good";
      } else if (medianGap !== null && medianGap >= 0 && (drawdownGap === null || drawdownGap <= 2)) {
        conclusion = "中位占优";
        risk = "头部爆发力不强，但整体表现不弱，适合稳健经营而非标榜冠军产品。";
        action = "用中位收益、回撤和波动稳定性构建持有体验话术，筛选其中排名靠前产品做重点展示。";
        tone = "is-good";
      } else if (gf.length < 3 && market.length >= 20) {
        conclusion = "广发梯队偏薄";
        risk = "产品数量不足，难覆盖不同风险偏好或客户场景。";
        action = "评估是否补齐风格梯度或风险层级，避免只靠单品承接需求。";
        tone = "is-warn";
      } else if (top5Gap !== null && top5Gap < -5 && headCount === 0 && (medianGap === null || medianGap < 0)) {
        conclusion = "广发头部落后";
        risk = "广发Top3与市场Top5差距较大，且没有产品进入市场前25%，直接推广会暴露产品竞争力不足。";
        action = "优先复盘资产配置、基金选择、调仓节奏和风险约束，未改善前不作为主推方向。";
        tone = "is-bad";
      } else if (top5Gap !== null && top5Gap < -2) {
        conclusion = "头部追赶";
        risk = "头部与第一梯队仍有差距，但不是简单缺位，需要看产品选择、风险暴露和渠道表达。";
        action = "把差距拆到具体广发Top3与市场Top5产品，优先复盘收益差距最大且回撤偏高的产品。";
        tone = "is-warn";
      }
      return {
        维度: label,
        类型: value,
        市场样本数: market.length,
        广发样本数: gf.length,
        广发覆盖率: market.length ? gf.length / market.length * 100 : null,
        广发Top3平均收益: gfTop3Avg,
        广发Top5平均收益: gfTop5Avg,
        市场Top3平均收益: marketTop3Avg,
        市场Top5平均收益: marketTop5Avg,
        广发Top3差距: top3Gap,
        广发Top3对Top5差距: top5Gap,
        头部达标数: headCount,
        中位差: medianGap,
        广发Top3平均排名: rankText,
        标杆差距: benchmarkGap,
        广发最佳产品: gfBest,
        广发Top3产品: gfSorted.slice(0, 3),
        标杆产品: marketTop,
        市场Top3产品: marketSorted.slice(0, 3),
        市场Top5产品: marketSorted.slice(0, 5),
        标杆机构: marketTop?.投顾机构 || "未披露",
        维度结论: conclusion,
        机会风险: risk,
        建议: action,
        市场排序: marketSorted,
        tone,
      };
    }).filter((row) => row.市场样本数 || row.广发样本数);
  }
  function multiDimensionBenchmarkRows() {
    const rows = [
      ...dimensionBenchmarkRows("策略类型", (row) => row.主可比池, pools),
      ...dimensionBenchmarkRows("市场地域", (row) => row.市场地域, regions),
      ...dimensionBenchmarkRows("主动/被动", (row) => row.主动被动, activePassiveOptions),
      ...dimensionBenchmarkRows("波动率", volatilityBucket, volatilityOptions.map((item) => item[0])),
      ...dimensionBenchmarkRows("换手率", turnoverBucket, turnoverOptions.map((item) => item[0])),
    ];
    const priority = { "广发头部落后": 6, "广发缺位": 5, "广发梯队偏薄": 4, "头部追赶": 3, "头部可经营": 2, "中位占优": 1, "广发第一梯队": 1, "中性观察": 0 };
    return rows.sort((a, b) => (priority[b.维度结论] || 0) - (priority[a.维度结论] || 0) || b.市场样本数 - a.市场样本数);
  }
  function dimensionBenchmarkTable(rows) {
    const headers = ["维度", "类型", "市场样本数", "广发样本数", "广发覆盖率", "市场Top5平均收益", "广发Top3对Top5差距", "头部达标数", "中位差", "广发Top3平均排名", "维度结论"];
    const head = headers.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `
      <tr>
        ${headers.map((h) => {
          if (h === "广发覆盖率") return `<td>${pctText(row[h])}</td>`;
          if (["市场Top5平均收益"].includes(h)) return `<td>${B.pctSigned(row[h])}</td>`;
          if (["广发Top3对Top5差距", "中位差"].includes(h)) return `<td>${ppText(row[h])}</td>`;
          if (h === "维度结论") return `<td><span class="insight-chip ${row.tone === "is-good" ? "good" : row.tone === "is-bad" ? "bad" : "warn"}">${B.esc(row[h])}</span></td>`;
          return `<td>${B.fmt(row[h])}</td>`;
        }).join("")}
      </tr>
      <tr class="insight-secondary-row"><td colspan="${headers.length}">
        ${rowDetailBlock(
          `${row.维度}｜${row.类型}：展开产品对比和业务判断`,
          [
            `机会风险：${row.机会风险}`,
            `建议：${row.建议}`,
            `参照口径：广发Top3平均收益对比市场Top5平均收益，辅助参考头部达标数、中位差和平均排名，不再只用市场Top1判断。`,
          ],
          row.广发Top3产品 || [],
          row.市场Top5产品 || row.市场Top3产品 || [],
          row.市场排序 || []
        )}
      </td></tr>
    `).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="insight-table"><div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
  }
  function dimensionBenchmarkPanel() {
    const rows = multiDimensionBenchmarkRows();
    const highlights = rows.filter((row) => row.维度结论 !== "中性观察").slice(0, 4);
    return `<section class="panel">
      <div class="panel-head"><div><h2>多维度标杆结论</h2><p class="desc">用市场Top5、广发Top3、前25%头部达标数、中位收益和风险指标综合判断，避免只和单一Top1比较导致结论失真。</p></div></div>
      ${highlights.length ? `<div class="insight-conclusion-grid">${highlights.map((row) => `
        <div class="insight-conclusion-card ${row.tone}">
          <strong>${B.esc(row.维度)}｜${B.esc(row.类型)}｜${B.esc(row.维度结论)}</strong>
          <p>广发 ${countText(row.广发样本数)}/${countText(row.市场样本数)}，Top3对市场Top5差距 ${ppText(row.广发Top3对Top5差距)}，头部达标 ${countText(row.头部达标数)} 个。${B.esc(row.建议)}</p>
        </div>
      `).join("")}</div>` : ""}
      ${dimensionBenchmarkTable(rows)}
    </section>`;
  }
  function barList(rows, labelKey, valueKey, subFormatter = null) {
    const max = Math.max(1, ...rows.map((row) => Number(row[valueKey] || 0)));
    return `<div class="insight-bar-list">${rows.map((row) => {
      const width = Number(row[valueKey] || 0) / max * 100;
      const sub = subFormatter ? subFormatter(row) : countText(row[valueKey]);
      return `<div class="insight-bar-row">
        <div class="insight-bar-label">${B.esc(row[labelKey])}</div>
        <div class="insight-bar-track"><div class="insight-bar-fill" style="width:${width.toFixed(2)}%"></div></div>
        <div>${B.esc(sub)}</div>
      </div>`;
    }).join("")}</div>`;
  }
  function gapChart(rows) {
    const maxAbs = Math.max(1, ...rows.map((row) => Math.abs(row.中位差 || 0)));
    return `<div class="insight-bar-list">${rows.map((row) => {
      const gap = row.中位差;
      const width = gap === null ? 0 : Math.abs(gap) / maxAbs * 50;
      const left = gap === null ? 50 : gap >= 0 ? 50 : 50 - width;
      const cls = gap !== null && gap >= 0 ? "is-target" : "";
      return `<div class="insight-bar-row">
        <div class="insight-bar-label">${B.esc(row.类型)}</div>
        <div class="insight-bar-track"><div class="insight-bar-fill ${cls}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></div></div>
        <div>${ppText(gap)}</div>
      </div>`;
    }).join("")}</div>`;
  }
  function overviewKpis(opps) {
    const universe = marketRows();
    const target = targetRows();
    const gfTop3Avg = topAverage(target, state.metric, 3);
    const marketTop3Avg = topAverage(universe, state.metric, 3);
    const top3Gap = gfTop3Avg !== null && marketTop3Avg !== null ? gfTop3Avg - marketTop3Avg : null;
    const coveredTypes = new Set(target.map((row) => row.主可比池).filter(Boolean)).size;
    const headCount = opps.reduce((sum, row) => sum + Number(row.头部策略数 || 0), 0);
    const reviewCount = targetStrategyDiagnostics().filter((row) => row.诊断分数 >= 35).length;
    return [
      ["广发基金投顾完整策略", countText(target.length), `当前筛选分析样本 ${countText(universe.length)} 个`, ""],
      ["广发覆盖率", universe.length ? pctText(target.length / universe.length * 100) : "未披露", "广发完整策略/分析样本完整策略", ""],
      ["覆盖策略类型数", `${coveredTypes}/${state.pool ? 1 : pools.length}`, "策略类型为互斥主归属", ""],
      ["广发头部产品数", countText(headCount), `按${state.metric}进入策略类型内前25%`, headCount ? "is-good" : ""],
      ["广发Top3差距", ppText(top3Gap), `${state.metric}：广发Top3均值减市场Top3均值`, top3Gap !== null && top3Gap < 0 ? "is-bad" : "is-good"],
      ["复盘策略数", countText(reviewCount), "收益/回撤/波动/换手或数据触发预警", reviewCount ? "is-warn" : ""],
    ];
  }
  function leaderTable(limit = 10) {
    return tableBlock(["策略名称", "渠道", "策略类型", "所选收益", "最大回撤", "波动率", "年化换手率", "排名分位"], leaders(limit), (row, h) => {
      if (h === "策略名称") return strategyLink(row);
      if (["所选收益", "最大回撤", "波动率", "年化换手率"].includes(h)) return h === "年化换手率" || h === "波动率" ? B.pct(row[h]) : B.pctSigned(row[h]);
      if (h === "排名分位") return pctText(row[h]);
      return B.fmt(row[h]);
    });
  }
  function reviewTable(limit = 12) {
    return tableBlock(["策略名称", "策略类型", "所选收益", "池中位收益", "最大回撤", "波动率", "年化换手率", "排名分位", "复盘建议"], targetStrategyDiagnostics().slice(0, limit), (row, h) => {
      if (h === "策略名称") return strategyLink(row);
      if (["所选收益", "池中位收益", "最大回撤", "波动率", "年化换手率"].includes(h)) return h === "年化换手率" || h === "波动率" ? B.pct(row[h]) : B.pctSigned(row[h]);
      if (h === "排名分位") return pctText(row[h]);
      return B.fmt(row[h]);
    });
  }
  function dataHealthBlock() {
    const target = targetRows();
    const total = Math.max(1, target.length);
    const gradeA = target.filter((row) => row.基础数据等级 === "A").length;
    const curveOnly = target.filter((row) => String(row.基准可用状态 || "").includes("仅曲线")).length;
    const feeMissing = target.filter((row) => String(row.费率状态 || "").includes("缺失")).length;
    const noRisk = target.filter((row) => !row.风险等级 || row.风险等级 === "未披露").length;
    return renderKpis([
      ["基础数据A级占比", pctText(gradeA / total * 100), "适合进入正式考核的数据基础", gradeA / total < 0.7 ? "is-warn" : ""],
      ["仅曲线基准占比", pctText(curveOnly / total * 100), "基准可看但公式解释不足", curveOnly / total > 0.3 ? "is-warn" : ""],
      ["费率缺失占比", pctText(feeMissing / total * 100), "影响费后收益和业务收益测算", feeMissing / total > 0.1 ? "is-warn" : ""],
      ["风险未披露占比", pctText(noRisk / total * 100), "影响风险分层和合规口径", noRisk / total > 0.1 ? "is-warn" : ""],
    ]);
  }
  function assetProfileBlock() {
    const rows = targetRows();
    const assetFields = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "主动基金权重"];
    return renderKpis(assetFields.map((field) => [field, pctText(median(values(rows, field))), "广发基金中位权重", ""]));
  }
  function riskQuadrantRows() {
    const universe = marketRows();
    const retMedian = median(values(universe, state.metric));
    const volMedian = median(values(universe, "波动率"));
    const drawMedian = median(values(universe, "最大回撤"));
    return targetRows().map((row) => {
      const ret = num(row[state.metric]);
      const vol = num(row.波动率);
      const draw = num(row.最大回撤);
      let quadrant = "信息不足";
      if (ret !== null && vol !== null && retMedian !== null && volMedian !== null) {
        if (ret >= retMedian && vol <= volMedian) quadrant = "稳健领先";
        else if (ret >= retMedian && vol > volMedian) quadrant = "高收益高波动";
        else if (ret < retMedian && draw !== null && drawMedian !== null && draw <= drawMedian) quadrant = "防御低收益";
        else quadrant = "待复盘";
      }
      return { ...row, 策略类型: row.主可比池, 所选收益: ret, 风险收益象限: quadrant };
    }).sort((a, b) => String(a.风险收益象限).localeCompare(String(b.风险收益象限), "zh-CN") || (num(b.所选收益) || -999) - (num(a.所选收益) || -999));
  }
  function dateValue(value) {
    const date = value ? new Date(`${String(value).slice(0, 10)}T00:00:00+08:00`) : null;
    return date && Number.isFinite(date.getTime()) ? date : null;
  }
  function daysFrom(anchor, value) {
    const date = dateValue(value);
    if (!anchor || !date) return Infinity;
    return Math.floor((anchor.getTime() - date.getTime()) / 86400000);
  }
  function rebalanceLogic(row) {
    const text = `${row.调仓标题 || ""} ${row.调仓原因 || ""} ${row.涉及资产 || ""}`;
    if (/基金经理|季报|一季报|半年报|年报|替换|调出|调入|产品/.test(text)) return "产品替换/基金优选";
    if (/债|久期|利率|信用|短债|中短债|流动性/.test(text)) return "固收久期/债券配置";
    if (/风险|回撤|防御|波动|止盈|约束|超出/.test(text)) return "风险控制/再平衡";
    if (/海外|全球|QDII|港股|美股|纳斯达克|标普|黄金|商品/.test(text)) return "海外/商品配置";
    if (/科技|成长|AI|人工智能|新能源|医药|消费|军工|半导体|周期/.test(text)) return "权益结构/主题切换";
    return "组合再平衡/常规调整";
  }
  function enrichedEvents() {
    return (summary.rebalanceEvents || []).map((event) => {
      const base = allRowById.get(event.统一策略ID) || {};
      const merged = { ...event, ...base };
      merged.调仓事件ID = event.调仓事件ID;
      merged.调仓日期 = event.调仓日期;
      merged.披露日期 = event.披露日期;
      merged.调仓标题 = event.调仓标题;
      merged.调仓原因 = event.调仓原因;
      merged.调仓基金数 = event.调仓基金数;
      merged.加仓权重合计 = event.加仓权重合计;
      merged.减仓权重合计 = event.减仓权重合计;
      merged.单次换手率 = event.单次换手率;
      merged.调后权重和 = event.调后权重和;
      merged.涉及资产 = event.涉及资产;
      merged.调仓超额 = event.调仓超额;
      merged.胜负 = event.胜负;
      merged.结果评价 = event.结果评价;
      merged.渠道 = base.渠道 || event.渠道;
      merged.策略名称 = base.策略名称 || event.策略名称;
      merged.投顾机构 = base.投顾机构 || event.投顾机构;
      merged.策略类型 = base.主可比池 || "未分类";
      merged.调仓逻辑 = rebalanceLogic(merged);
      return merged;
    });
  }
  function filteredEvents(includeChannel = false) {
    return enrichedEvents().filter((row) => inAnalysisUniverse(row) && matchesFilters(row, includeChannel));
  }
  function activeRebalanceEvents(rows) {
    return rows.filter((row) => ["主动为主", "主动被动混合"].includes(row.主动被动));
  }
  function latestEventAnchor(rows) {
    const times = rows.map((row) => dateValue(row.调仓日期)).filter(Boolean).map((date) => date.getTime());
    return times.length ? new Date(Math.max(...times)) : null;
  }
  function recentEvents(rows, days, anchor = latestEventAnchor(rows)) {
    return rows.filter((row) => daysFrom(anchor, row.调仓日期) <= days);
  }
  function groupBy(rows, keyFn) {
    const map = new Map();
    rows.forEach((row) => {
      const key = keyFn(row) || "未披露";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(row);
    });
    return [...map.entries()];
  }
  function isGuangfaEvent(row) {
    return row.渠道 === "广发基金";
  }
  function rebalanceOutcome(row) {
    const text = String(row.胜负 || "");
    if (!text || text.includes("不可")) return null;
    if (text.includes("胜")) return "胜";
    if (text.includes("负")) return "负";
    if (text.includes("平")) return "平";
    return null;
  }
  function rebalanceQualityStats(rows) {
    const assessedRows = rows.filter((row) => rebalanceOutcome(row));
    const wins = assessedRows.filter((row) => rebalanceOutcome(row) === "胜").length;
    const losses = assessedRows.filter((row) => rebalanceOutcome(row) === "负").length;
    const flats = assessedRows.filter((row) => rebalanceOutcome(row) === "平").length;
    return {
      事件数: rows.length,
      可评价事件数: assessedRows.length,
      胜数: wins,
      平数: flats,
      负数: losses,
      调仓胜率: assessedRows.length ? wins / assessedRows.length * 100 : null,
      平均调仓超额: avg(values(rows, "调仓超额")),
      中位单次换手率: median(values(rows, "单次换手率")),
      策略数: new Set(rows.map((row) => row.统一策略ID)).size,
      机构数: new Set(rows.map((row) => row.投顾机构).filter(Boolean)).size,
    };
  }
  function qualityScore(row) {
    const win = row.调仓胜率 === null ? -1 : row.调仓胜率;
    const excess = row.平均调仓超额 === null ? -99 : row.平均调仓超额;
    const confidence = Math.min(row.可评价事件数 || 0, 12) / 12 * 10;
    return win + confidence + Math.max(-10, Math.min(10, excess));
  }
  function qualityLabel(row, benchmark = null) {
    if (!row || !row.可评价事件数) return "样本不足";
    if (benchmark && row.调仓胜率 !== null && benchmark.调仓胜率 !== null && row.调仓胜率 >= benchmark.调仓胜率) return "优于标杆";
    if (row.调仓胜率 !== null && row.调仓胜率 >= 55 && (row.平均调仓超额 === null || row.平均调仓超额 >= 0)) return "质量较好";
    if (row.调仓胜率 !== null && row.调仓胜率 < 35) return "需要复盘";
    if (row.平均调仓超额 !== null && row.平均调仓超额 < 0) return "超额偏弱";
    return "中性观察";
  }
  function qualityTone(label) {
    if (["优于标杆", "质量较好", "广发调仓质量领先"].includes(label)) return "is-good";
    if (["需要复盘", "超额偏弱", "广发调仓胜率落后", "广发超额偏弱"].includes(label)) return "is-bad";
    return "is-warn";
  }
  function institutionQualityRows(rows, anchor, limit = null) {
    const raw = groupBy(rows, (row) => row.投顾机构).map(([institution, list]) => {
      const week = recentEvents(list, 7, anchor);
      const month = recentEvents(list, 30, anchor);
      const stats = rebalanceQualityStats(list);
      return {
        投顾机构: institution,
        是否广发: list.some(isGuangfaEvent),
        近一周主动调仓: week.length,
        近一月主动调仓: month.length,
        近一年事件数: list.length,
        覆盖策略数: stats.策略数,
        可评价事件数: stats.可评价事件数,
        平均单次换手率: avg(values(list, "单次换手率")),
        调仓胜率: stats.调仓胜率,
        平均调仓超额: stats.平均调仓超额,
        主要逻辑: groupBy(list, (row) => row.调仓逻辑).sort((a, b) => b[1].length - a[1].length)[0]?.[0] || "未披露",
      };
    }).sort((a, b) => {
      const assessedA = a.可评价事件数 || 0;
      const assessedB = b.可评价事件数 || 0;
      if ((assessedB >= 3) !== (assessedA >= 3)) return (assessedB >= 3 ? 1 : 0) - (assessedA >= 3 ? 1 : 0);
      return (b.调仓胜率 ?? -1) - (a.调仓胜率 ?? -1)
        || (b.平均调仓超额 ?? -99) - (a.平均调仓超额 ?? -99)
        || assessedB - assessedA
        || b.近一年事件数 - a.近一年事件数;
    }).map((row, index) => ({ ...row, 排名: index + 1 }));
    if (!limit) return raw;
    const top = raw.slice(0, limit);
    const gfRows = raw.filter((row) => row.是否广发 && !top.some((item) => item.投顾机构 === row.投顾机构));
    return [...top, ...gfRows];
  }
  function benchmarkInstitution(rows, anchor, minimum = 5) {
    return institutionQualityRows(rows, anchor).filter((row) => row.可评价事件数 >= minimum)[0] || institutionQualityRows(rows, anchor)[0] || null;
  }
  function rebalanceQualityConclusion(marketStats, gfStats, benchmark) {
    const gfWinRate = num(gfStats.调仓胜率);
    const marketWinRate = num(marketStats.调仓胜率);
    const benchmarkWinRate = benchmark ? num(benchmark.调仓胜率) : null;
    const marketGap = gfWinRate !== null && marketWinRate !== null ? gfWinRate - marketWinRate : null;
    const benchmarkGap = gfWinRate !== null && benchmarkWinRate !== null ? gfWinRate - benchmarkWinRate : null;
    let conclusion = "中性观察";
    let risk = "调仓样本尚可，但需要结合策略类型和调仓逻辑继续拆解。";
    let action = "跟踪胜率、平均超额和高频逻辑，优先复盘低胜率且超额为负的调仓场景。";
    if (!gfStats.可评价事件数) {
      conclusion = "广发样本不足";
      risk = "广发近期可评价调仓事件不足，无法稳定判断调仓质量。";
      action = "先补齐调仓评价样本和事件归因，再进入正式调仓考核。";
    } else if (benchmarkGap !== null && benchmarkGap >= 0 && (gfStats.平均调仓超额 === null || gfStats.平均调仓超额 >= 0)) {
      conclusion = "广发调仓质量领先";
      risk = "主要风险是样本期是否可持续，需要防止单阶段行情放大结论。";
      action = "提炼高胜率调仓逻辑，形成渠道话术和投研复盘模板。";
    } else if (benchmarkGap !== null && benchmarkGap > -10) {
      conclusion = "广发接近标杆";
      risk = "胜率接近标杆但尚未形成明显领先，需要继续比较调仓逻辑和超额质量。";
      action = "对照标杆机构的主要调仓逻辑，强化广发表现较好的场景，弱化不稳定场景。";
    } else if (marketGap !== null && marketGap < -10) {
      conclusion = "广发调仓胜率落后";
      risk = "调仓后表现弱于市场，可能影响客户对主动管理能力的感知。";
      action = "优先复盘负贡献事件，拆解资产方向、基金替换和调仓时点，短期不把低胜率逻辑作为主推卖点。";
    } else if (gfStats.平均调仓超额 !== null && gfStats.平均调仓超额 < 0) {
      conclusion = "广发超额偏弱";
      risk = "胜率可能不低，但调仓贡献为负，说明单次错误调仓的损失可能偏大。";
      action = "检查大权重调仓和高波动资产调仓，增加单次调仓风险约束。";
    }
    return { 调仓质量结论: conclusion, 调仓质量风险: risk, 调仓质量建议: action, 胜率差距: benchmarkGap, 市场胜率差距: marketGap, tone: qualityTone(conclusion) };
  }
  function logicTable(rows) {
    const data = groupBy(rows, (row) => row.调仓逻辑).map(([logic, list]) => {
      const stats = rebalanceQualityStats(list);
      return {
        调仓逻辑: logic,
        事件数: list.length,
        可评价事件数: stats.可评价事件数,
        策略数: stats.策略数,
        机构数: stats.机构数,
        调仓胜率: stats.调仓胜率,
        中位单次换手率: stats.中位单次换手率,
        平均调仓超额: stats.平均调仓超额,
        示例原因: shortText(list.find((row) => row.调仓原因)?.调仓原因 || list[0]?.调仓标题, 64),
      };
    }).sort((a, b) => b.事件数 - a.事件数 || (b.调仓胜率 ?? -1) - (a.调仓胜率 ?? -1));
    return tableBlock(["调仓逻辑", "事件数", "可评价事件数", "策略数", "机构数", "调仓胜率", "中位单次换手率", "平均调仓超额", "示例原因"], data, (row, h) => {
      if (h === "中位单次换手率" || h === "调仓胜率") return B.pct(row[h]);
      if (h === "平均调仓超额") return ppText(row[h]);
      return B.fmt(row[h]);
    });
  }
  function institutionRebalanceTable(rows, anchor) {
    const benchmark = benchmarkInstitution(rows, anchor);
    const data = institutionQualityRows(rows, anchor, 20).map((row) => ({ ...row, 调仓质量结论: qualityLabel(row, benchmark) }));
    return tableBlock(["排名", "投顾机构", "可评价事件数", "近一周主动调仓", "近一月主动调仓", "近一年事件数", "覆盖策略数", "平均单次换手率", "调仓胜率", "平均调仓超额", "主要逻辑", "调仓质量结论"], data, (row, h) => {
      if (h === "投顾机构" && row.是否广发) return `<strong>${B.esc(row[h])}</strong><span class="small">广发基金</span>`;
      if (h === "平均单次换手率" || h === "调仓胜率") return pctText(row[h]);
      if (h === "平均调仓超额") return ppText(row[h]);
      if (h === "调仓质量结论") {
        const cls = row[h] === "优于标杆" || row[h] === "质量较好" ? "good" : row[h] === "需要复盘" || row[h] === "超额偏弱" ? "bad" : "warn";
        return `<span class="insight-chip ${cls}">${B.esc(row[h])}</span>`;
      }
      return B.fmt(row[h]);
    });
  }
  function rebalanceQualitySummaryPanel(marketRows, gfRows, anchor) {
    const marketStats = rebalanceQualityStats(marketRows);
    const gfStats = rebalanceQualityStats(gfRows);
    const benchmark = benchmarkInstitution(marketRows, anchor);
    const conclusion = rebalanceQualityConclusion(marketStats, gfStats, benchmark);
    const cards = [
      {
        title: "调仓质量结论",
        value: conclusion.调仓质量结论,
        text: `${conclusion.调仓质量风险} ${conclusion.调仓质量建议}`,
        tone: conclusion.tone,
      },
      {
        title: "广发 vs 全市场",
        value: `胜率差 ${ppText(conclusion.市场胜率差距)}`,
        text: `广发胜率 ${pctText(gfStats.调仓胜率)}，全市场胜率 ${pctText(marketStats.调仓胜率)}；广发平均超额 ${ppText(gfStats.平均调仓超额)}。`,
        tone: conclusion.市场胜率差距 !== null && conclusion.市场胜率差距 >= 0 ? "is-good" : "is-warn",
      },
      {
        title: "广发 vs 标杆机构",
        value: benchmark ? `${benchmark.投顾机构}` : "未披露",
        text: benchmark ? `标杆胜率 ${pctText(benchmark.调仓胜率)}，广发相对标杆 ${ppText(conclusion.胜率差距)}；标杆主要逻辑：${benchmark.主要逻辑}。` : "当前筛选下缺少可评价机构标杆。",
        tone: conclusion.胜率差距 !== null && conclusion.胜率差距 >= 0 ? "is-good" : "is-warn",
      },
    ];
    return `<section class="panel">
      <div class="panel-head"><div><h2>广发调仓质量对标结论</h2><p class="desc">胜率按胜/负/平可评价事件计算，不可评估事件不进分母；同时比较平均调仓超额，避免只看胜率。</p></div></div>
      <div class="insight-conclusion-grid">${cards.map((card) => `
        <div class="insight-conclusion-card ${card.tone}">
          <strong>${B.esc(card.title)}｜${B.esc(card.value)}</strong>
          <p>${B.esc(card.text)}</p>
        </div>
      `).join("")}</div>
      ${tableBlock(["对象", "事件数", "可评价事件数", "策略数", "机构数", "调仓胜率", "平均调仓超额", "中位单次换手率"], [
        { 对象: "全市场", ...marketStats },
        { 对象: "广发基金", ...gfStats },
        benchmark ? { 对象: `标杆机构：${benchmark.投顾机构}`, 事件数: benchmark.近一年事件数, 可评价事件数: benchmark.可评价事件数, 策略数: benchmark.覆盖策略数, 机构数: 1, 调仓胜率: benchmark.调仓胜率, 平均调仓超额: benchmark.平均调仓超额, 中位单次换手率: benchmark.平均单次换手率 } : null,
      ].filter(Boolean), (row, h) => {
        if (["调仓胜率", "中位单次换手率"].includes(h)) return pctText(row[h]);
        if (h === "平均调仓超额") return ppText(row[h]);
        return B.fmt(row[h]);
      })}
    </section>`;
  }
  function rebalanceLogicQualityRows(marketRows, gfRows, anchor) {
    return groupBy(marketRows, (row) => row.调仓逻辑).map(([logic, marketList]) => {
      const gfList = gfRows.filter((row) => row.调仓逻辑 === logic);
      const marketStats = rebalanceQualityStats(marketList);
      const gfStats = rebalanceQualityStats(gfList);
      const benchmark = benchmarkInstitution(marketList, anchor, 3);
      const conclusion = rebalanceQualityConclusion(marketStats, gfStats, benchmark);
      return {
        调仓逻辑: logic,
        市场事件数: marketStats.事件数,
        广发事件数: gfStats.事件数,
        可评价事件数: gfStats.可评价事件数,
        市场调仓胜率: marketStats.调仓胜率,
        广发调仓胜率: gfStats.调仓胜率,
        胜率差距: conclusion.市场胜率差距,
        广发平均超额: gfStats.平均调仓超额,
        标杆机构: benchmark?.投顾机构 || "未披露",
        标杆机构胜率: benchmark?.调仓胜率 ?? null,
        调仓质量结论: conclusion.调仓质量结论,
        调仓质量风险: conclusion.调仓质量风险,
        调仓质量建议: conclusion.调仓质量建议,
        tone: conclusion.tone,
      };
    }).sort((a, b) => {
      const priority = { "广发调仓胜率落后": 5, "广发超额偏弱": 4, "广发样本不足": 3, "广发接近标杆": 2, "广发调仓质量领先": 1, "中性观察": 0 };
      return (priority[b.调仓质量结论] || 0) - (priority[a.调仓质量结论] || 0) || b.市场事件数 - a.市场事件数;
    });
  }
  function rebalanceLogicQualityTable(marketRows, gfRows, anchor) {
    const rows = rebalanceLogicQualityRows(marketRows, gfRows, anchor);
    return tableBlock(["调仓逻辑", "市场事件数", "广发事件数", "可评价事件数", "市场调仓胜率", "广发调仓胜率", "胜率差距", "广发平均超额", "标杆机构", "标杆机构胜率", "调仓质量结论", "调仓质量风险", "调仓质量建议"], rows, (row, h) => {
      if (["市场调仓胜率", "广发调仓胜率", "标杆机构胜率"].includes(h)) return pctText(row[h]);
      if (["胜率差距", "广发平均超额"].includes(h)) return h === "广发平均超额" ? ppText(row[h]) : ppText(row[h]);
      if (h === "调仓质量结论") return `<span class="insight-chip ${row.tone === "is-good" ? "good" : row.tone === "is-bad" ? "bad" : "warn"}">${B.esc(row[h])}</span>`;
      if (["调仓质量风险", "调仓质量建议"].includes(h)) return `<span class="small">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }
  function recentEventTable(rows) {
    const data = rows.slice().sort((a, b) => String(b.调仓日期 || "").localeCompare(String(a.调仓日期 || ""))).slice(0, 24).map((row) => ({
      调仓日期: row.调仓日期,
      策略名称: row.策略名称,
      统一策略ID: row.统一策略ID,
      投顾机构: row.投顾机构,
      策略类型: row.策略类型,
      主动被动: row.主动被动,
      单次换手率: row.单次换手率,
      涉及资产: shortText(row.涉及资产, 24),
      调仓逻辑: row.调仓逻辑,
      调仓原因: shortText(row.调仓原因 || row.调仓标题, 58),
      调仓超额: row.调仓超额,
    }));
    return tableBlock(["调仓日期", "策略名称", "投顾机构", "策略类型", "主动被动", "单次换手率", "涉及资产", "调仓逻辑", "调仓原因", "调仓超额"], data, (row, h) => {
      if (h === "策略名称") return `<a class="link" href="./strategy.html?id=${encodeURIComponent(row.统一策略ID)}">${B.esc(row.策略名称 || "未披露")}</a>`;
      if (h === "单次换手率") return B.pct(row[h]);
      if (h === "调仓超额") return ppText(row[h]);
      if (h === "调仓逻辑") return `<span class="logic-chip">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }
  function strategyReturnCell(row) {
    if (!row) return '<span class="value-muted">未披露</span>';
    const value = num(row[state.metric]);
    const sub = value === null ? "收益未披露" : B.pctSigned(value);
    return `${strategyLink(row)}<span class="small">${sub}</span>`;
  }
  function sortedByMetric(rows, field = state.metric) {
    return rows.filter((row) => num(row[field]) !== null).sort((a, b) => num(b[field]) - num(a[field]));
  }
  function topAverage(rows, field = state.metric, limit = 3) {
    const top = sortedByMetric(rows, field).slice(0, limit).map((row) => num(row[field])).filter((value) => value !== null);
    return avg(top);
  }
  function rankInSorted(row, sortedRows) {
    if (!row) return null;
    const index = sortedRows.findIndex((item) => item.统一策略ID === row.统一策略ID);
    return index >= 0 ? index + 1 : null;
  }
  function rankLabel(row, sortedRows) {
    const rank = rankInSorted(row, sortedRows);
    return rank ? `第${rank}/${sortedRows.length}` : "未排名";
  }
  function rankPctText(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "未披露";
    return `前${Number(value).toFixed(1)}%`;
  }
  function productLine(row, sortedRows) {
    if (!row) return '<span class="value-muted">未披露</span>';
    const ret = num(row[state.metric]);
    const rank = rankLabel(row, sortedRows);
    const risk = [
      `收益 ${ret === null ? "未披露" : pctText(ret)}`,
      `回撤 ${pctText(row.最大回撤)}`,
      `波动 ${pctText(row.波动率)}`
    ].join("，");
    return `${strategyLink(row)}<span class="small">${B.esc(rank)}｜${B.esc(risk)}</span>`;
  }
  function topProductsCell(rows, marketSorted, limit = 3) {
    const top = rows.slice(0, limit);
    if (!top.length) return '<span class="value-muted">无广发可计算产品</span>';
    return top.map((row, index) => `<div class="small"><b>${index + 1}.</b> ${productLine(row, marketSorted)}</div>`).join("");
  }
  function productCompareTable(gfRows, marketRows, marketSorted) {
    const rows = [
      ...gfRows.slice(0, 3).map((row, index) => ({ scope: "gf", 范围: `广发Top${index + 1}`, row })),
      ...marketRows.slice(0, 5).map((row, index) => ({ scope: "market", 范围: `市场Top${index + 1}`, row })),
    ].filter((item) => item.row);
    if (!rows.length) return '<div class="empty">暂无可比较产品。</div>';
    const headers = ["范围", "产品", "机构", `收益(${state.metric})`, "市场排名", "最大回撤", "波动率", "年化换手率"];
    return `<div class="product-compare-table"><table><thead><tr>${headers.map((h) => `<th>${B.label(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((item) => {
      const row = item.row;
      const scopeCls = item.scope === "gf" ? "gf" : "market";
      return `<tr>
        <td><span class="compare-scope ${scopeCls}">${B.esc(item.范围)}</span></td>
        <td>${strategyLink(row)}</td>
        <td>${B.esc(row.投顾机构 || "未披露")}</td>
        <td>${B.pctSigned(row[state.metric])}</td>
        <td>${B.esc(rankLabel(row, marketSorted))}</td>
        <td>${pctText(row.最大回撤)}</td>
        <td>${pctText(row.波动率)}</td>
        <td>${pctText(row.年化换手率)}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
  }
  function rowDetailBlock(summary, notes, gfRows, marketRows, marketSorted) {
    const noteHtml = notes.filter(Boolean).map((item) => `<div>${B.esc(item)}</div>`).join("");
    return `<details class="row-detail">
      <summary>${B.esc(summary)}</summary>
      <div class="row-detail-body">
        ${noteHtml ? `<div class="product-compare-note">${noteHtml}</div>` : ""}
        ${productCompareTable(gfRows || [], marketRows || [], marketSorted || [])}
      </div>
    </details>`;
  }
  function bestVsBenchmarkCell(best, benchmark) {
    if (!best || !benchmark) return '<span class="value-muted">缺少可比标杆</span>';
    const retGap = num(best[state.metric]) !== null && num(benchmark[state.metric]) !== null ? num(best[state.metric]) - num(benchmark[state.metric]) : null;
    const drawGap = num(best.最大回撤) !== null && num(benchmark.最大回撤) !== null ? num(best.最大回撤) - num(benchmark.最大回撤) : null;
    const volGap = num(best.波动率) !== null && num(benchmark.波动率) !== null ? num(best.波动率) - num(benchmark.波动率) : null;
    const turnoverGap = num(best.年化换手率) !== null && num(benchmark.年化换手率) !== null ? num(best.年化换手率) - num(benchmark.年化换手率) : null;
    return [
      `收益差 ${ppText(retGap)}`,
      `回撤差 ${ppText(drawGap)}`,
      `波动差 ${ppText(volGap)}`,
      `换手差 ${ppText(turnoverGap)}`
    ].join("；");
  }
  function deepFinding(row) {
    if (!row.广发样本数) return "广发暂无完整可比产品，无法形成对客竞争话术，机会在于先判断该类型是否值得补线。";
    if (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 >= 0) return "广发头部产品已经达到或超过市场Top5平均水平，业务重点应放在把头部产品包装成可复制卖点。";
    if (row.头部达标数 > 0 || (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 > -2)) return "广发虽然未必超过市场Top1，但已接近第一梯队，短板更可能在产品筛选、标杆话术和渠道曝光。";
    if (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 < -5 && !row.头部达标数) return "广发头部产品相对市场第一梯队存在明显差距，单纯推广难以解决，优先需要投研复盘和策略定位校准。";
    if (row.广发样本数 >= 5 && row.中位差 !== null && row.中位差 < 0 && row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 > -3) return "广发头部产品尚可，但长尾产品拉低整体观感，适合做产品分层和重点名单收敛。";
    return "广发有一定可比产品，但相对优势不够突出，需要围绕收益、回撤、波动和换手寻找更清晰的客户场景。";
  }
  function businessRisk(row) {
    if (!row.广发样本数) return "业务风险：该类型在市场有样本但广发缺位，客户咨询时缺少可承接产品。";
    if (row.广发最佳 && row.市场标杆 && num(row.广发最佳.最大回撤) !== null && num(row.市场标杆.最大回撤) !== null && num(row.广发最佳.最大回撤) > num(row.市场标杆.最大回撤) + 5) return "业务风险：广发最佳产品收益未必差，但回撤明显高于标杆，容易在客户持有体验上吃亏。";
    if (row.广发最佳 && num(row.广发最佳.年化换手率) !== null && num(row.广发最佳.年化换手率) >= 120) return "业务风险：广发头部产品换手偏高，需要解释交易逻辑和稳定性，否则容易被理解为风格漂移。";
    if (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 < -5 && !row.头部达标数) return "业务风险：头部产品能力不够，继续放大销售会暴露与天天同类第一梯队的业绩差距。";
    return "业务风险：主要在于优势表达不充分或产品梯队不清晰，需避免平均口径掩盖头部亮点。";
  }
  function businessAction(row) {
    if (!row.广发样本数) return "动作：先做立项判断，若该类型符合战略，再补齐一个可对标市场前列的策略；若不符合，明确不参与竞争。";
    if (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 >= 0) return "动作：把广发Top3列为重点经营名单，沉淀客户场景、收益回撤对比和持有期话术。";
    if (row.头部达标数 > 0 || (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 > -2)) return "动作：围绕进入市场前25%或接近市场Top5的广发产品做标杆追赶话术，补充对标产品差异、持仓逻辑和适配客群。";
    if (row.广发Top3对Top5差距 !== null && row.广发Top3对Top5差距 < -5 && !row.头部达标数) return "动作：组织投研复盘，拆解市场Top5产品资产配置、基金选择、调仓节奏和风险控制，不建议直接作为主推。";
    return "动作：收敛展示重点，优先推广排名靠前且风险指标可解释的产品，弱势产品进入观察或优化池。";
  }
  function typeConclusionRows() {
    const universe = marketRows();
    const target = targetRows();
    return pools.map((pool) => {
      const market = universe.filter((row) => row.主可比池 === pool);
      const gf = target.filter((row) => row.主可比池 === pool);
      const marketSorted = sortedByMetric(market);
      const gfSorted = sortedByMetric(gf);
      const marketMedian = median(values(market, state.metric));
      const gfMedian = median(values(gf, state.metric));
      const gap = gfMedian !== null && marketMedian !== null ? gfMedian - marketMedian : null;
      const gfTop3Avg = topAverage(gf, state.metric, 3);
      const gfTop5Avg = topAverage(gf, state.metric, 5);
      const marketTop3Avg = topAverage(market, state.metric, 3);
      const marketTop5Avg = topAverage(market, state.metric, 5);
      const gfTop3Gap = gfTop3Avg !== null && marketTop3Avg !== null ? gfTop3Avg - marketTop3Avg : null;
      const gfTop3Top5Gap = gfTop3Avg !== null && marketTop5Avg !== null ? gfTop3Avg - marketTop5Avg : null;
      const marketTop = marketSorted[0] || null;
      const gfBest = gfSorted[0] || null;
      const gfWeak = gfSorted.length ? gfSorted[gfSorted.length - 1] : null;
      const benchmarkGap = gfBest && marketTop && num(gfBest[state.metric]) !== null && num(marketTop[state.metric]) !== null ? num(gfBest[state.metric]) - num(marketTop[state.metric]) : null;
      const topThreshold = quantile(values(market, state.metric), 0.75);
      const gfHeadCount = topThreshold === null ? 0 : gf.filter((row) => {
        const value = num(row[state.metric]);
        return value !== null && value >= topThreshold;
      }).length;
      const gfTopRanks = gfSorted.slice(0, 3).map((row) => rankInSorted(row, marketSorted)).filter(Boolean);
      const gfTopAvgRank = avg(gfTopRanks);
      const gfTopAvgRankPct = gfTopAvgRank && marketSorted.length ? gfTopAvgRank / marketSorted.length * 100 : null;
      const gfTopRankText = gfTopAvgRank ? `平均第${gfTopAvgRank.toFixed(1)}/${marketSorted.length}，${rankPctText(gfTopAvgRankPct)}` : "未披露";
      let judgment = "中性观察";
      let tone = "is-warn";
      if (!gf.length && market.length >= 10) {
        judgment = "广发空白";
        tone = "is-bad";
      } else if (gfTop3Top5Gap !== null && gfTop3Top5Gap >= 0 && gfHeadCount > 0) {
        judgment = "第一梯队";
        tone = "is-good";
      } else if ((gfTopAvgRankPct !== null && gfTopAvgRankPct <= 25) || gfHeadCount > 0 || (gfTop3Top5Gap !== null && gfTop3Top5Gap > -2)) {
        judgment = "头部可经营";
        tone = "is-good";
      } else if (gap !== null && gap >= 0) {
        judgment = "中位占优";
        tone = "is-good";
      } else if (gf.length < 3 && market.length >= 20) {
        judgment = "梯队不足";
        tone = "is-warn";
      } else if (gfTop3Top5Gap !== null && gfTop3Top5Gap < -5 && gfHeadCount === 0 && (gap === null || gap < 0)) {
        judgment = "头部能力落后";
        tone = "is-bad";
      } else if (gf.length >= 5 && gap !== null && gap < 0 && gfTop3Top5Gap !== null && gfTop3Top5Gap > -3) {
        judgment = "头部可打长尾拖累";
        tone = "is-warn";
      } else if (gfTop3Top5Gap !== null && gfTop3Top5Gap < -2) {
        judgment = "头部追赶";
        tone = "is-warn";
      }
      const result = {
        类型: pool,
        市场样本数: market.length,
        广发样本数: gf.length,
        广发中位收益: gfMedian,
        市场中位收益: marketMedian,
        中位差: gap,
        广发Top3平均收益: gfTop3Avg,
        广发Top5平均收益: gfTop5Avg,
        市场Top3平均收益: marketTop3Avg,
        市场Top5平均收益: marketTop5Avg,
        广发Top3差距: gfTop3Gap,
        广发Top3对Top5差距: gfTop3Top5Gap,
        广发Top3产品: gfSorted.slice(0, 3),
        市场Top3产品: marketSorted.slice(0, 3),
        市场Top5产品: marketSorted.slice(0, 5),
        市场排序: marketSorted,
        广发最好: gfBest,
        广发最佳: gfBest,
        广发较弱: gfWeak,
        市场标杆: marketTop,
        相对标杆: bestVsBenchmarkCell(gfBest, marketTop),
        标杆差距: benchmarkGap,
        头部策略数: gfHeadCount,
        头部达标数: gfHeadCount,
        广发Top3平均排名: gfTopRankText,
        判断: judgment,
        tone,
      };
      result.深层结论 = deepFinding(result);
      result.业务风险 = businessRisk(result);
      result.业务动作 = businessAction(result);
      result.业务机会 = result.业务动作;
      return result;
    }).filter((row) => row.市场样本数 || row.广发样本数).sort((a, b) => {
      const priority = { "头部能力落后": 7, "广发空白": 6, "梯队不足": 5, "头部追赶": 4, "头部可打长尾拖累": 3, "头部可经营": 2, "中位占优": 1, "第一梯队": 1, "中性观察": 0 };
      return (priority[b.判断] || 0) - (priority[a.判断] || 0) || b.市场样本数 - a.市场样本数;
    });
  }
  function typeConclusionTable(rows) {
    const headers = ["类型", "市场样本数", "广发样本数", "广发Top3平均收益", "广发Top5平均收益", "市场Top5平均收益", "广发Top3对Top5差距", "头部达标数", "广发Top3平均排名", "判断"];
    const head = headers.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `
      <tr>
        ${headers.map((h) => {
          if (["广发Top3平均收益", "广发Top5平均收益", "市场Top5平均收益"].includes(h)) return `<td>${B.pctSigned(row[h])}</td>`;
          if (h === "广发Top3对Top5差距") return `<td>${ppText(row[h])}</td>`;
          if (h === "判断") return `<td><span class="insight-chip ${row.tone === "is-good" ? "good" : row.tone === "is-bad" ? "bad" : "warn"}">${B.esc(row[h])}</span></td>`;
          return `<td>${B.fmt(row[h])}</td>`;
        }).join("")}
      </tr>
      <tr class="insight-secondary-row"><td colspan="${headers.length}">
        ${rowDetailBlock(
          `${row.类型}：展开产品对比、风险和动作`,
          [
            `深层结论：${row.深层结论}`,
            `业务风险：${row.业务风险}`,
            `业务动作：${row.业务动作}`,
            `相对标杆：${String(row.相对标杆 || "缺少可比标杆").replace(/<[^>]+>/g, "")}`,
          ],
          row.广发Top3产品 || [],
          row.市场Top5产品 || row.市场Top3产品 || [],
          row.市场排序 || []
        )}
      </td></tr>
    `).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="insight-table"><div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
  }
  function typeConclusionPanel() {
    const rows = typeConclusionRows();
    const highlights = rows.filter((row) => row.判断 !== "中性观察").slice(0, 3);
    return `<section class="panel">
      <div class="panel-head"><div><h2>各策略类型结论</h2><p class="desc">全市场口径限定为天天基金/投顾 + 广发基金完整策略；评价对象固定为广发基金，重点比较广发Top3与市场Top5，并用折叠行查看具体产品对比。</p></div></div>
      ${highlights.length ? `<div class="insight-conclusion-grid">${highlights.map((row) => `
        <div class="insight-conclusion-card ${row.tone}">
          <strong>${B.esc(row.类型)}｜${B.esc(row.判断)}</strong>
          <p>全市场 ${countText(row.市场样本数)} 个，广发 ${countText(row.广发样本数)} 个，Top3对市场Top5差距 ${ppText(row.广发Top3对Top5差距)}，头部达标 ${countText(row.头部达标数)} 个。${B.esc(row.业务动作)}</p>
        </div>
      `).join("")}</div>` : ""}
      ${typeConclusionTable(rows)}
    </section>`;
  }
  function weeklyStats(rows) {
    const calculable = rows.filter((row) => num(row.近一周) !== null);
    const vals = values(rows, "近一周");
    return {
      样本数: rows.length,
      可计算策略数: calculable.length,
      中位收益: median(vals),
      Top5平均收益: topAverage(rows, "近一周", 5),
      Bottom5平均收益: avg(vals.slice(0, Math.min(vals.length, 5))),
      上涨占比: calculable.length ? calculable.filter((row) => num(row.近一周) >= 0).length / calculable.length * 100 : null,
      中位回撤: median(values(rows, "最大回撤")),
      中位波动: median(values(rows, "波动率")),
      高波动策略数: rows.filter((row) => volatilityBucket(row) === "高波动").length,
      高换手策略数: rows.filter((row) => turnoverBucket(row) === "高换手").length,
    };
  }
  function weeklyPosition(gap, headCount, gfCount) {
    if (!gfCount) return { label: "广发缺位", tone: "is-bad" };
    if (headCount > 0 || (gap !== null && gap >= 0.2)) return { label: "短期可经营", tone: "is-good" };
    if (gap !== null && gap <= -0.2) return { label: "短期落后", tone: "is-bad" };
    return { label: "中性观察", tone: "is-warn" };
  }
  function weeklyAction(position, marketCount) {
    if (position === "广发缺位") return marketCount >= 10 ? "评估是否符合广发基金定位，若符合则纳入产品补齐或合作供给讨论。" : "样本较少，先观察需求和同类供给是否持续。";
    if (position === "短期可经营") return "筛选广发进入同类前25%或近一周领先的产品，补充风险、回撤和持有体验话术。";
    if (position === "短期落后") return "复盘近一周资产暴露、底层基金表现和调仓节奏，短期不把该类作为主推卖点。";
    return "继续跟踪近一月和近三月表现，避免只因单周波动调整长期经营排序。";
  }
  function weeklyNarrativeCards(marketStats, gfStats, recentWeek, recentGfWeek, recentWeekStats, gap) {
    const breadthTone = marketStats.上涨占比 !== null && marketStats.上涨占比 >= 60 && marketStats.中位收益 !== null && marketStats.中位收益 >= 0 ? "is-good" : marketStats.上涨占比 !== null && marketStats.上涨占比 < 40 ? "is-bad" : "is-warn";
    const gfTone = gap !== null && gap >= 0.2 ? "is-good" : gap !== null && gap <= -0.2 ? "is-bad" : "is-warn";
    const rebalanceTone = recentGfWeek.length && recentWeekStats.调仓胜率 !== null && recentWeekStats.调仓胜率 >= 50 ? "is-good" : recentGfWeek.length ? "is-warn" : "is-warn";
    const riskTone = gfStats.高波动策略数 || gfStats.高换手策略数 ? "is-warn" : "is-good";
    const cards = [
      {
        title: "全市场短期温度",
        tone: breadthTone,
        fact: `近一周可计算 ${countText(marketStats.可计算策略数)} 只，中位收益 ${pctText(marketStats.中位收益)}，上涨占比 ${pctText(marketStats.上涨占比)}。`,
        view: marketStats.上涨占比 !== null && marketStats.上涨占比 >= 60 ? "短期正收益面较宽，可观察哪些策略类型贡献了热度。" : marketStats.上涨占比 !== null && marketStats.上涨占比 < 40 ? "短期压力偏大，周度经营应先控制风险表达。" : "市场短期分化，适合用同类分组而不是全市场总榜判断。",
        logic: "用近一周收益中位数和上涨占比判断市场宽度，再用策略类型拆解热点来源。",
        action: "周会先讲市场状态，再进入广发相对位置和产品名单，避免直接从单品榜单下结论。",
      },
      {
        title: "广发相对位置",
        tone: gfTone,
        fact: `广发近一周中位收益 ${pctText(gfStats.中位收益)}，相对全市场 ${ppText(gap)}，可计算 ${countText(gfStats.可计算策略数)} 只。`,
        view: gap !== null && gap >= 0.2 ? "广发短期中位表现占优，可从领先产品中提炼可经营卖点。" : gap !== null && gap <= -0.2 ? "广发短期中位表现落后，需要先定位拖累来源。" : "广发整体接近市场中位，重点看具体类型和头部产品。",
        logic: "周度领先/落后阈值暂定为广发中位收益相对市场中位收益 ±0.20pct，同时结合同类前25%产品数。",
        action: gap !== null && gap <= -0.2 ? "优先复盘短期落后的策略类型和弱势产品，检查权益/债券/海外暴露是否集中拖累。" : "把广发Top5和同类头部达标产品作为重点沟通对象，同时说明周度口径的短期性。",
      },
      {
        title: "主动调仓信号",
        tone: rebalanceTone,
        fact: `近一周全市场主动调仓 ${countText(recentWeek.length)} 次，广发 ${countText(recentGfWeek.length)} 次，全市场可评价胜率 ${pctText(recentWeekStats.调仓胜率)}。`,
        view: recentWeek.length ? "近期机构动作可作为市场方向和投研关注点观察，但胜率需要用更长周期验证。" : "当前筛选下近一周没有可展示主动调仓事件。",
        logic: "主动调仓限定主动为主或主动被动混合策略，近一周按最新调仓日向前7天取数。",
        action: recentGfWeek.length ? "逐条复核广发调仓原因和调仓后表现，胜率较好的逻辑可沉淀成投研复盘材料。" : "若广发本周无调仓，应说明是策略纪律、风险约束还是数据披露滞后导致。",
      },
      {
        title: "风险与经营边界",
        tone: riskTone,
        fact: `广发高波动策略 ${countText(gfStats.高波动策略数)} 只，高换手策略 ${countText(gfStats.高换手策略数)} 只，中位回撤 ${pctText(gfStats.中位回撤)}。`,
        view: gfStats.高波动策略数 || gfStats.高换手策略数 ? "部分产品需要配套风险解释，不能只按短期收益排序。" : "当前筛选下广发风险暴露未触发高波动或高换手集中预警。",
        logic: "高波动阈值为年化波动率>=15%，高换手阈值为年化换手率>=120%。",
        action: "对高波动/高换手且短期落后的产品建立复盘清单，渠道侧减少单周收益驱动的误导性表达。",
      },
    ];
    return `<div class="insight-panel-stack">${cards.map((card) => `
      <div class="insight-conclusion-card ${card.tone}">
        <strong>${B.esc(card.title)}</strong>
        <div class="fvla-grid">
          <div class="fvla-item"><b>${B.label("事实")}</b>${B.esc(card.fact)}</div>
          <div class="fvla-item"><b>${B.label("观点")}</b>${B.esc(card.view)}</div>
          <div class="fvla-item"><b>${B.label("逻辑")}</b>${B.esc(card.logic)}</div>
          <div class="fvla-item"><b>${B.label("动作")}</b>${B.esc(card.action)}</div>
        </div>
      </div>
    `).join("")}</div>`;
  }
  function weeklyTypeRows() {
    const universe = marketRows();
    const target = targetRows();
    return pools.map((pool) => {
      const market = universe.filter((row) => row.主可比池 === pool);
      const gf = target.filter((row) => row.主可比池 === pool);
      const marketStats = weeklyStats(market);
      const gfStats = weeklyStats(gf);
      const gap = gfStats.中位收益 !== null && marketStats.中位收益 !== null ? gfStats.中位收益 - marketStats.中位收益 : null;
      const marketSorted = sortedByMetric(market, "近一周");
      const gfSorted = sortedByMetric(gf, "近一周");
      const threshold = quantile(values(market, "近一周"), 0.75);
      const headCount = threshold === null ? 0 : gf.filter((row) => {
        const value = num(row.近一周);
        return value !== null && value >= threshold;
      }).length;
      const position = weeklyPosition(gap, headCount, gf.length);
      const gfBest = gfSorted[0] || null;
      const marketTop = marketSorted[0] || null;
      return {
        策略类型: pool,
        市场样本数: market.length,
        广发样本数: gf.length,
        近一周全市场中位收益: marketStats.中位收益,
        近一周广发中位收益: gfStats.中位收益,
        近一周广发相对差: gap,
        近一周上涨占比: gfStats.上涨占比,
        头部达标数: headCount,
        广发最佳产品: gfBest,
        标杆产品: marketTop,
        广发周度位置: position.label,
        tone: position.tone,
        事实: `市场${countText(market.length)}只，广发${countText(gf.length)}只，广发同类前25%达标${countText(headCount)}只。`,
        观点: position.label === "短期可经营" ? "广发在该类已有短期可经营产品。" : position.label === "短期落后" ? "广发该类短期弱于市场中位，需要复盘拖累。" : position.label === "广发缺位" ? "市场有供给但广发缺少完整可比样本。" : "短期表现接近市场，需要看更长周期。",
        逻辑: `按近一周广发中位减市场中位，差距 ${ppText(gap)}；领先/落后阈值为 ±0.20pct，另看是否进入市场前25%。`,
        动作: weeklyAction(position.label, market.length),
      };
    }).filter((row) => row.市场样本数 || row.广发样本数).sort((a, b) => {
      const priority = { "短期落后": 5, "广发缺位": 4, "短期可经营": 3, "中性观察": 1 };
      return (priority[b.广发周度位置] || 0) - (priority[a.广发周度位置] || 0) || b.市场样本数 - a.市场样本数;
    });
  }
  function weeklyTypeTable(rows) {
    const headers = ["策略类型", "市场样本数", "广发样本数", "近一周全市场中位收益", "近一周广发中位收益", "近一周广发相对差", "头部达标数", "广发周度位置", "事实", "观点", "动作"];
    return tableBlock(headers, rows, (row, h) => {
      if (["近一周全市场中位收益", "近一周广发中位收益"].includes(h)) return B.pctSigned(row[h]);
      if (h === "近一周广发相对差") return ppText(row[h]);
      if (h === "广发周度位置") return `<span class="insight-chip ${row.tone === "is-good" ? "good" : row.tone === "is-bad" ? "bad" : "warn"}">${B.esc(row[h])}</span>`;
      return B.fmt(row[h]);
    });
  }
  function weeklyProductLine(row, marketSorted) {
    if (!row) return '<span class="value-muted">未披露</span>';
    return `${strategyLink(row)}<span class="small">${B.esc(row.主可比池 || "未分类")}｜近一周 ${B.pctSigned(row.近一周)}｜市场${B.esc(rankLabel(row, marketSorted))}｜回撤 ${pctText(row.最大回撤)}｜波动 ${pctText(row.波动率)}</span>`;
  }
  function weeklyRankCards() {
    const universe = marketRows();
    const target = targetRows();
    const marketSorted = sortedByMetric(universe, "近一周");
    const gfSorted = sortedByMetric(target, "近一周");
    const gfTop = gfSorted.slice(0, 5);
    const gfWeak = gfSorted.slice(-5).reverse();
    const marketTop = marketSorted.slice(0, 5);
    const list = (rows, emptyText) => rows.length ? `<ol>${rows.map((row) => `<li>${weeklyProductLine(row, marketSorted)}</li>`).join("")}</ol>` : `<div class="empty">${B.esc(emptyText)}</div>`;
    return `<div class="weekly-rank-list">
      <div class="weekly-rank-card"><h3>${B.label("近一周广发Top5")}</h3>${list(gfTop, "当前筛选下没有广发近一周可计算产品。")}</div>
      <div class="weekly-rank-card"><h3>近一周广发需复盘产品</h3>${list(gfWeak, "当前筛选下没有广发近一周可计算产品。")}</div>
      <div class="weekly-rank-card"><h3>${B.label("近一周市场Top5")}</h3>${list(marketTop, "当前筛选下没有市场近一周可计算产品。")}</div>
      <div class="weekly-rank-card"><h3>周度名单使用口径</h3><ol>
        <li>Top5 只用于发现短期热点和话术候选，不直接作为考核排名。</li>
        <li>需复盘产品按近一周收益从低到高列示，优先检查是否存在高波动、高换手或数据断点。</li>
        <li>正式产品经营建议需要结合近一月、近三月、近1年、最大回撤和波动率。</li>
      </ol></div>
    </div>`;
  }
  function weeklyRebalanceLogicRows(recentWeek, recentGfWeek) {
    const gfByLogic = new Map(groupBy(recentGfWeek, (row) => row.调仓逻辑));
    return groupBy(recentWeek, (row) => row.调仓逻辑).map(([logic, list]) => {
      const gfList = gfByLogic.get(logic) || [];
      const stats = rebalanceQualityStats(list);
      const gfStats = rebalanceQualityStats(gfList);
      const gap = gfStats.调仓胜率 !== null && stats.调仓胜率 !== null ? gfStats.调仓胜率 - stats.调仓胜率 : null;
      return {
        调仓逻辑: logic,
        全市场事件数: list.length,
        广发事件数: gfList.length,
        策略数: stats.策略数,
        机构数: stats.机构数,
        市场调仓胜率: stats.调仓胜率,
        广发调仓胜率: gfStats.调仓胜率,
        胜率差距: gap,
        平均调仓超额: stats.平均调仓超额,
        事实: `全市场${countText(list.length)}次，广发${countText(gfList.length)}次，可评价${countText(stats.可评价事件数)}次。`,
        动作: gfList.length ? "复核广发同逻辑调仓后收益和原因，胜率高则提炼投研方法，胜率低则限制对客表达。" : "广发未参与该逻辑，观察是否属于能力圈外、纪律约束或数据缺失。",
      };
    }).sort((a, b) => b.全市场事件数 - a.全市场事件数 || (b.市场调仓胜率 ?? -1) - (a.市场调仓胜率 ?? -1));
  }
  function weeklyRebalanceLogicTable(rows) {
    return tableBlock(["调仓逻辑", "全市场事件数", "广发事件数", "策略数", "机构数", "市场调仓胜率", "广发调仓胜率", "胜率差距", "平均调仓超额", "事实", "动作"], rows, (row, h) => {
      if (["市场调仓胜率", "广发调仓胜率"].includes(h)) return pctText(row[h]);
      if (["胜率差距", "平均调仓超额"].includes(h)) return ppText(row[h]);
      return B.fmt(row[h]);
    });
  }
  function weeklyFrameworkRows() {
    return [
      {
        平台对标参考: "天天基金/投顾策略地图",
        公开资料: [
          ["上海证券基金投顾年度报告", "https://stock.cnstock.com/stock/smk_jjdx/202302/5016709.htm"],
          ["上海证券报投顾调仓报道", "https://paper.cnstock.com/html/2025-01/13/content_2017674.htm"],
        ],
        可借鉴点: "把策略按客户场景、风险收益层级和组合类型组织，适合业务人员快速找到同类产品和可替代产品。",
        本页落地: "保留互斥策略类型，并叠加市场地域、主动/被动、波动率、换手率等并列维度做经营拆解。",
        下一步补强: "若能稳定获取天天策略地图原始分层，可把外部场景标签作为软标签加入筛选和洞察。",
      },
      {
        平台对标参考: "国内基金投顾研究/FOF分类",
        公开资料: [
          ["晨星基金分类", "https://www.morningstar.cn/help/data/fundcategory.html"],
          ["开源金工基金投顾解析", "https://bigquant.com/square/paper/4aa7cdb9-fd5a-4bf4-bd0e-180d7f36c2a2"],
        ],
        可借鉴点: "更强调权益、债券、货币、QDII等资产权重和基准口径，适合做可比池和正式考核分组。",
        本页落地: "主可比池、市场地域、主动/被动和风险收益指标都基于可量化字段或标准基金分类加工。",
        下一步补强: "继续补齐权威基金标准分类、基准资产权重和投顾费率，提高正式考核稳定性。",
      },
      {
        平台对标参考: "Betterment / Wealthfront",
        公开资料: [
          ["SEC Robo-Advisers", "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-45"],
          ["Wealthfront TLH", "https://www.wealthfront.com/tax-loss-harvesting"],
          ["Betterment TLH", "https://www.betterment.com/resources/understanding-tax-loss-harvesting/"],
        ],
        可借鉴点: "围绕目标、风险画像、组合表现、自动再平衡、税务优化和费用透明做客户层面的投顾评估。",
        本页落地: "把收益、波动、回撤、调仓胜率、换手率和费率状态放在同一业务视图里。",
        下一步补强: "后续如有客户持仓和申赎数据，可增加目标达成率、客户留存、风险错配和持有体验分析。",
      },
      {
        平台对标参考: "Vanguard / Schwab 智能投顾",
        公开资料: [
          ["Vanguard rebalancing", "https://investor.vanguard.com/investing/portfolio-management/rebalance"],
          ["Vanguard Digital Advisor", "https://ownyourfuture.vanguard.com/content/iig-psx/us/en/advice/resources/insights/caring-for-your-portfolio.html"],
          ["Schwab Intelligent Portfolios", "https://www.schwab.com/intelligent-portfolios"],
        ],
        可借鉴点: "用目标组合、风险承受能力、资产配置偏离和再平衡纪律解释投顾价值。",
        本页落地: "调仓分析页已按主动调仓事件、逻辑、胜率和超额拆解机构能力。",
        下一步补强: "增加目标配置偏离、再平衡触发阈值和调仓前后风险暴露变化，区分纪律性再平衡与主动择时。",
      },
    ];
  }
  function publicSourceLinks(links) {
    if (!Array.isArray(links) || !links.length) return "未披露";
    return links.map(([label, url]) => `<a class="link" href="${B.esc(url)}" target="_blank" rel="noopener">${B.esc(label)}</a>`).join("<br>");
  }
  function focusDecisionFor(row, weekRow = null) {
    if (isTargetProfitType(row)) {
      if (!row.广发样本数 && row.市场样本数 >= 8) return "产品补齐";
      const risk = targetProfitRiskSummary(row);
      if (risk.可包装) return "头部可包装";
      return "需要复盘";
    }
    if (!row.广发样本数 && row.市场样本数 >= 8) return "产品补齐";
    if (row.判断 === "梯队不足") return "产品补齐";
    if (row.判断 === "头部能力落后" && num(row.广发Top3对Top5差距) !== null && num(row.广发Top3对Top5差距) <= -5 && !row.头部达标数) return "暂不主推";
    if (["头部能力落后", "头部追赶", "头部可打长尾拖累"].includes(row.判断)) return "需要复盘";
    if (row.判断 === "第一梯队") return "重点经营";
    if (["头部可经营", "中位占优"].includes(row.判断)) return "头部可包装";
    if (weekRow?.广发周度位置 === "短期落后" && num(row.中位差) !== null && num(row.中位差) < 0) return "需要复盘";
    return "观察跟踪";
  }
  function focusTone(decision) {
    if (["重点经营", "头部可包装"].includes(decision)) return "is-good";
    if (["需要复盘", "产品补齐", "观察跟踪"].includes(decision)) return "is-warn";
    return "is-bad";
  }
  function focusPriority(decision) {
    return { "重点经营": 6, "需要复盘": 5, "暂不主推": 4, "产品补齐": 3, "头部可包装": 2, "观察跟踪": 1 }[decision] || 0;
  }
  function isTargetProfitType(row) {
    return String(row?.类型 || row?.主可比池 || row || "").includes("目标盈");
  }
  function targetProfitRiskSummary(row) {
    const pool = row?.类型 || "目标盈系列产品";
    const market = marketRows().filter((item) => item.主可比池 === pool);
    const gf = targetRows().filter((item) => item.主可比池 === pool);
    const gfDraw = median(values(gf, "最大回撤"));
    const marketDraw = median(values(market, "最大回撤"));
    const gfVol = median(values(gf, "波动率"));
    const marketVol = median(values(market, "波动率"));
    const drawOk = gfDraw !== null && marketDraw !== null ? gfDraw <= marketDraw + 1 : null;
    const volOk = gfVol !== null && marketVol !== null ? gfVol <= marketVol + 1 : null;
    const riskKnown = drawOk !== null || volOk !== null;
    return {
      广发样本数: gf.length,
      市场样本数: market.length,
      广发回撤: gfDraw,
      市场回撤: marketDraw,
      广发波动: gfVol,
      市场波动: marketVol,
      可包装: gf.length > 0 && riskKnown && (drawOk !== false || volOk !== false),
      风险可比: riskKnown,
    };
  }
  function focusProductName(row) {
    return row?.策略名称 || row?.统一策略ID || "未披露产品";
  }
  function focusProductNames(rows) {
    return rows.map((row) => focusProductName(row)).filter(Boolean).join("、") || "暂无明确产品";
  }
  function focusRiskScore(row) {
    const draw = num(row?.最大回撤);
    const vol = num(row?.波动率);
    const ret = num(row?.[state.metric]);
    return (draw ?? 999) * 2 + (vol ?? 999) - (ret ?? -999) * 0.05;
  }
  function focusSuitabilityScore(row, typeRow) {
    const sorted = typeRow?.市场排序 || [];
    const rank = rankInSorted(row, sorted);
    const rankScore = rank && sorted.length ? (sorted.length - rank + 1) / sorted.length * 100 : (rankPercent(row) ?? 0);
    const draw = num(row?.最大回撤);
    const vol = num(row?.波动率);
    const turnover = num(row?.年化换手率);
    const drawPenalty = draw === null ? 10 : draw * 1.1;
    const volPenalty = vol === null ? 5 : vol * 0.35;
    const turnoverPenalty = turnover !== null && turnover > 120 ? (turnover - 120) * 0.04 : 0;
    return rankScore - drawPenalty - volPenalty - turnoverPenalty;
  }
  function focusCandidateProducts(typeRow, limit = 2) {
    const pool = typeRow?.类型 || typeRow?.主可比池 || "";
    if (isTargetProfitType(typeRow)) {
      return targetRows()
        .filter((row) => row.主可比池 === pool)
        .sort((a, b) => focusRiskScore(a) - focusRiskScore(b))
        .slice(0, limit);
    }
    return (typeRow?.广发Top3产品 || [])
      .slice()
      .sort((a, b) => focusSuitabilityScore(b, typeRow) - focusSuitabilityScore(a, typeRow))
      .slice(0, limit);
  }
  function focusProblemProduct(typeRow) {
    return typeRow?.广发较弱 || typeRow?.广发最佳 || (typeRow?.广发Top3产品 || [])[0] || null;
  }
  function focusRankText(product, typeRow) {
    const sorted = typeRow?.市场排序 || [];
    return sorted.length ? rankLabel(product, sorted) : "未排名";
  }
  function focusTargetRiskCompare(product, typeRow) {
    const risk = targetProfitRiskSummary(typeRow);
    const draw = num(product?.最大回撤);
    const vol = num(product?.波动率);
    const drawOk = draw !== null && risk.市场回撤 !== null ? draw <= risk.市场回撤 + 1 : null;
    const volOk = vol !== null && risk.市场波动 !== null ? vol <= risk.市场波动 + 1 : null;
    if (drawOk === true && volOk === true) return "回撤和波动低于或接近市场中位";
    if (drawOk === true) return "回撤低于或接近市场中位，但波动还要单独解释";
    if (volOk === true) return "波动低于或接近市场中位，但回撤还要单独解释";
    return "风险指标没有明显优于市场中位，不能只靠短期收益包装";
  }
  function focusRiskBoundaryText(product) {
    const notes = [];
    const draw = num(product?.最大回撤);
    const turnover = num(product?.年化换手率);
    if (draw !== null && draw >= 30) notes.push(`回撤 ${pctText(draw)} 偏高`);
    if (turnover !== null && turnover >= 120) notes.push(`换手 ${pctText(turnover)} 偏高`);
    return notes.length ? `风险边界：${notes.join("，")}，更适合高风险客户或单独解释。` : "风险边界相对更容易解释。";
  }
  function focusProductReason(product, typeRow, decision) {
    if (!product) return typeRow?.业务重点 || "暂无明确产品可讲。";
    const name = focusProductName(product);
    const ret = `${state.metric} ${signedPctText(product[state.metric])}`;
    const risk = `回撤 ${pctText(product.最大回撤)}、波动 ${pctText(product.波动率)}`;
    const rank = focusRankText(product, typeRow);
    if (isTargetProfitType(typeRow)) {
      return `可讲 ${name}，${risk}，${focusTargetRiskCompare(product, typeRow)}；目标盈按“稳不稳、能不能达标”讲，还要补目标达成率。`;
    }
    if (decision === "重点经营") {
      return `可推 ${name}，${ret}，同类${rank}，${risk}；原因是广发头部产品已经进入或接近市场第一梯队，且${focusRiskBoundaryText(product)}`;
    }
    if (decision === "头部可包装") {
      return `可推 ${name}，${ret}，同类${rank}，${risk}；原因是这只产品表现靠前，且${focusRiskBoundaryText(product)} 不能把整类都说成优势。`;
    }
    if (decision === "需要复盘") {
      return `先复盘 ${name}，${ret}，同类${rank}，${risk}；看差距来自资产配置、基金选择还是调仓。`;
    }
    if (decision === "暂不主推") {
      return `先别推 ${name}，${ret}，同类${rank}，${risk}；当前收益、风险或数据不支撑主推。`;
    }
    return `${name}：${ret}，同类${rank}，${risk}。`;
  }
  function focusBusinessPoint(row, decision, weekRow = null) {
    const gap = ppText(row.广发Top3对Top5差距);
    const weekText = weekRow ? `周度位置：${weekRow.广发周度位置}，周度差 ${ppText(weekRow.近一周广发相对差)}` : "周度信号未披露";
    if (isTargetProfitType(row)) {
      const risk = targetProfitRiskSummary(row);
      const riskText = `广发回撤 ${pctText(risk.广发回撤)}、市场中位 ${pctText(risk.市场回撤)}；广发波动 ${pctText(risk.广发波动)}、市场中位 ${pctText(risk.市场波动)}。`;
      if (decision === "产品补齐") return `目标盈看“能不能稳稳达标”，不是和权益、固收比谁收益高。广发${countText(row.广发样本数)}只、市场${countText(row.市场样本数)}只，供给偏少。`;
      const product = focusCandidateProducts(row, 1)[0];
      if (decision === "头部可包装" && product) return `${focusProductReason(product, row, decision)} 同类整体风险：${riskText}`;
      if (decision === "头部可包装") return `目标盈重点看回撤和波动。${riskText}可以作为目标收益类产品单独包装，但还缺目标达成率。`;
      return `目标盈先看风险和达标体验。${riskText}当前缺目标达成率，先别按收益榜直接下结论。`;
    }
    if (decision === "重点经营") return focusProductReason(focusCandidateProducts(row, 1)[0], row, decision);
    if (decision === "头部可包装") return focusProductReason(focusCandidateProducts(row, 1)[0], row, decision);
    if (decision === "需要复盘") return `${focusProductReason(focusProblemProduct(row), row, decision)} 广发头部比市场Top5低${gap}，${weekText}。`;
    if (decision === "产品补齐") return `市场有${countText(row.市场样本数)}只，广发只有${countText(row.广发样本数)}只，可能接不住客户需求。`;
    if (decision === "暂不主推") return focusProductReason(focusProblemProduct(row), row, decision);
    return `没有明显动作，继续观察。`;
  }
  function focusNextAction(row, decision) {
    const candidates = focusCandidateProducts(row, 2);
    const names = focusProductNames(candidates);
    if (isTargetProfitType(row)) {
      if (decision === "产品补齐") return "先判断目标盈是不是要做重点产品线；要做就补产品，不做就明确不主推。";
      if (decision === "头部可包装") return `把 ${names} 作为目标盈单品包装，主讲低回撤、低波动和持有体验；补目标达成率后再形成销售话术。`;
      return "补目标达成率、持有期、止盈/到期数据，再决定是否主推。";
    }
    if (decision === "重点经营") return `把 ${names} 放入主推候选，准备同类排名、收益回撤和标杆产品对比。`;
    if (decision === "头部可包装") return `只推 ${names} 这些表现靠前的产品，不把${row.类型}整类包装成优势。`;
    if (decision === "需要复盘") return `先复盘 ${focusProductName(focusProblemProduct(row))}，拆资产配置、基金选择和调仓贡献。`;
    if (decision === "产品补齐") return "先判断要不要做这个方向，要做就补产品。";
    if (decision === "暂不主推") return `先不推 ${focusProductName(focusProblemProduct(row))}，等复盘或数据补齐后再看。`;
    return "继续观察。";
  }
  function focusDecisionRows() {
    const weekMap = new Map(weeklyTypeRows().map((row) => [row.策略类型, row]));
    return typeConclusionRows().map((row) => {
      const weekRow = weekMap.get(row.类型) || null;
      const decision = focusDecisionFor(row, weekRow);
      return {
        ...row,
        经营判断: decision,
        周度位置: weekRow?.广发周度位置 || "未披露",
        周度差: weekRow?.近一周广发相对差 ?? null,
        业务重点: focusBusinessPoint(row, decision, weekRow),
        下一步动作: focusNextAction(row, decision),
        focusTone: focusTone(decision),
      };
    }).sort((a, b) => focusPriority(b.经营判断) - focusPriority(a.经营判断) || b.市场样本数 - a.市场样本数);
  }
  function focusKpis(rows) {
    const diagnostics = targetStrategyDiagnostics();
    const dataIssues = diagnostics.filter((row) => row.基础数据等级 && row.基础数据等级 !== "A").length;
    const highRisk = diagnostics.filter((row) => volatilityBucket(row) === "高波动" || turnoverBucket(row) === "高换手").length;
    return [
      ["重点经营类型", countText(rows.filter((row) => row.经营判断 === "重点经营").length), "可以优先讲", "is-good"],
      ["头部可包装类型", countText(rows.filter((row) => row.经营判断 === "头部可包装").length), "只讲好产品", "is-good"],
      ["需要复盘类型", countText(rows.filter((row) => row.经营判断 === "需要复盘").length), "先找原因", "is-warn"],
      ["产品补齐方向", countText(rows.filter((row) => row.经营判断 === "产品补齐").length), "可能缺产品", "is-warn"],
      ["暂不主推类型", countText(rows.filter((row) => row.经营判断 === "暂不主推").length), "先别推", "is-bad"],
      ["风险/数据问题", `${countText(highRisk)} / ${countText(dataIssues)}`, "高风险 / 数据问题", highRisk || dataIssues ? "is-warn" : ""],
    ];
  }
  function focusDecisionCards(rows) {
    const groups = [
      [["重点经营", "头部可包装"], "可以讲什么", "可推/可包装", "is-good"],
      [["需要复盘"], "先查什么问题", "先复盘", "is-warn"],
      [["产品补齐"], "缺什么产品", "可补产品", "is-warn"],
      [["暂不主推"], "什么先别推", "先别推", "is-bad"],
    ];
    return `<div class="focus-decision-list">${groups.map(([decisions, title, label, tone]) => {
      const list = rows.filter((row) => decisions.includes(row.经营判断)).slice(0, 4);
      const body = list.length
        ? list.map((row) => `${row.类型}：${row.业务重点}`).join("；")
        : "当前筛选口径下没有触发该动作分类。";
      const action = list.length ? list.map((row) => `${row.类型}：${row.下一步动作}`).join("；") : "保持观察。";
      return `<div class="focus-decision-card ${tone}">
        <strong>${B.esc(title)}｜${B.esc(label)}</strong>
        <p><b>${B.label("事实")}</b> ${B.esc(body)}</p>
        <p><b>${B.label("动作")}</b> ${B.esc(action)}</p>
      </div>`;
    }).join("")}</div>`;
  }
  function focusMatrixTable(rows) {
    const headers = ["经营判断", "类型", "市场样本数", "广发样本数", "广发Top3对Top5差距", "头部达标数", "周度位置", "业务重点", "下一步动作"];
    return tableBlock(headers, rows, (row, h) => {
      if (h === "经营判断") return `<span class="insight-chip ${row.focusTone === "is-good" ? "good" : row.focusTone === "is-bad" ? "bad" : "warn"}">${B.esc(row[h])}</span>`;
      if (isTargetProfitType(row) && h === "广发Top3对Top5差距") return "看回撤/波动";
      if (isTargetProfitType(row) && h === "头部达标数") return "不按收益排名";
      if (h === "广发Top3对Top5差距") return ppText(row[h]);
      return B.fmt(row[h]);
    });
  }
  function uniqueByStrategy(rows) {
    const seen = new Set();
    return rows.filter((item) => {
      const id = item.row?.统一策略ID || item.title || "";
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }
  function focusProductItem(row, reason, sourceRow = null) {
    return {
      row,
      titleHtml: strategyLink(row),
      meta: `${row.主可比池 || sourceRow?.类型 || "未分类"}｜${state.metric} ${signedPctText(row[state.metric])}｜回撤 ${pctText(row.最大回撤)}｜波动 ${pctText(row.波动率)}｜换手 ${pctText(row.年化换手率)}`,
      reason,
    };
  }
  function focusPromoteProducts(rows) {
    const source = rows.filter((row) => ["重点经营", "头部可包装"].includes(row.经营判断));
    const items = source.flatMap((typeRow) => focusCandidateProducts(typeRow, 2).map((row) => focusProductItem(row, focusProductReason(row, typeRow, typeRow.经营判断), typeRow)));
    return uniqueByStrategy(items).sort((a, b) => (num(b.row[state.metric]) ?? -999) - (num(a.row[state.metric]) ?? -999)).slice(0, 8);
  }
  function focusReviewProducts() {
    return targetStrategyDiagnostics().filter((row) => {
      if (isTargetProfitType(row)) return row.基础数据等级 && row.基础数据等级 !== "A";
      return row.诊断分数 >= 35;
    }).slice(0, 8).map((row) => {
      if (isTargetProfitType(row)) {
        return focusProductItem(row, "目标盈不按收益分位复盘；先补目标达成率、持有期和基础数据，再看回撤、波动。");
      }
      return focusProductItem(row, row.复盘建议);
    });
  }
  function focusNoPromoteProducts() {
    const diagnostics = targetStrategyDiagnostics();
    return diagnostics.filter((row) => {
      if (isTargetProfitType(row)) return false;
      const rank = num(row.排名分位);
      return (rank !== null && rank < 35 && (volatilityBucket(row) === "高波动" || turnoverBucket(row) === "高换手"))
        || (rank !== null && rank < 25 && row.基础数据等级 && row.基础数据等级 !== "A");
    }).slice(0, 8).map((row) => focusProductItem(row, `暂不主推：${row.复盘建议}`));
  }
  function focusSupplyDirections(rows) {
    return rows.filter((row) => row.经营判断 === "产品补齐").slice(0, 8).map((row) => ({
      title: row.类型,
      meta: `市场${countText(row.市场样本数)}只｜广发${countText(row.广发样本数)}只｜Top3对Top5差距${ppText(row.广发Top3对Top5差距)}`,
      reason: row.下一步动作,
    }));
  }
  function focusListCard(title, items, emptyText) {
    return `<div class="weekly-rank-card"><h3>${B.label(title)}</h3>
      <div class="focus-list">${items.length ? items.map((item) => `<div class="focus-list-item">
        <strong>${item.titleHtml || B.esc(item.title || "")}</strong>
        <span>${B.esc(item.meta || "")}</span>
        <span class="small">${B.esc(item.reason || "")}</span>
      </div>`).join("") : `<div class="empty">${B.esc(emptyText)}</div>`}</div>
    </div>`;
  }
  function renderFocusInsights() {
    const rows = focusDecisionRows();
    const matrixRows = rows.filter((row) => row.经营判断 !== "观察跟踪").slice(0, 12);
    const promote = focusPromoteProducts(rows);
    const review = focusReviewProducts();
    const supply = focusSupplyDirections(rows);
    const noPromote = focusNoPromoteProducts();
    return `
      ${renderKpis(focusKpis(rows))}
      <section class="insight-panel-stack">
        <section class="panel">
          <div class="panel-head"><div><h2>先看结论</h2><p class="desc">只回答四件事：讲什么、查什么、缺什么、先别推什么。</p></div></div>
          ${focusDecisionCards(rows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>策略类型怎么处理</h2><p class="desc">每类策略给一个动作，方便业务直接分工。</p></div></div>
          ${focusMatrixTable(matrixRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>${B.label("重点名单")}</h2><p class="desc">把产品直接分到清单里。</p></div></div>
          <div class="focus-section-grid">
            ${focusListCard("可主推产品", promote, "当前筛选下没有明确可主推产品。")}
            ${focusListCard("需复盘产品", review, "当前筛选下没有触发复盘阈值的产品。")}
            ${focusListCard("产品补齐方向", supply, "当前筛选下没有产品补齐方向。")}
            ${focusListCard("暂不主推产品", noPromote, "当前筛选下没有明确暂不主推产品。")}
          </div>
        </section>
        <section class="panel">
          <div class="source-method"><strong>${B.label("经营重点")}</strong> 本页是业务入口，不是正式考核表。正式考核仍回到同类策略里看近三月、近1年、回撤、波动、换手、调仓、费率和数据完整性。目标盈系列单独看回撤、波动、持有期和目标达成情况，不和固收或权益产品直接比收益率。</div>
        </section>
      </section>`;
  }
  function renderWeeklyOverview() {
    const universe = marketRows();
    const target = targetRows();
    const marketStats = weeklyStats(universe);
    const gfStats = weeklyStats(target);
    const gap = gfStats.中位收益 !== null && marketStats.中位收益 !== null ? gfStats.中位收益 - marketStats.中位收益 : null;
    const marketEventRows = filteredEvents(false);
    const targetEventRows = filteredEvents(true);
    const activeMarket = activeRebalanceEvents(marketEventRows);
    const activeTarget = activeRebalanceEvents(targetEventRows);
    const anchor = latestEventAnchor(activeMarket);
    const recentWeek = recentEvents(activeMarket, 7, anchor);
    const recentGfWeek = recentEvents(activeTarget, 7, anchor);
    const recentWeekStats = rebalanceQualityStats(recentWeek);
    const weekRows = weeklyTypeRows();
    const kpis = [
      ["近一周全市场中位收益", pctText(marketStats.中位收益), `可计算 ${countText(marketStats.可计算策略数)} 只，上涨占比 ${pctText(marketStats.上涨占比)}`, ""],
      ["近一周广发中位收益", pctText(gfStats.中位收益), `可计算 ${countText(gfStats.可计算策略数)} 只，上涨占比 ${pctText(gfStats.上涨占比)}`, gap !== null && gap >= 0.2 ? "is-good" : gap !== null && gap <= -0.2 ? "is-bad" : ""],
      ["近一周广发相对差", ppText(gap), "广发中位收益 - 全市场中位收益", gap !== null && gap >= 0.2 ? "is-good" : gap !== null && gap <= -0.2 ? "is-bad" : "is-warn"],
      ["近一周市场Top5均值", pctText(marketStats.Top5平均收益), "观察市场热点上限", ""],
      ["近一周全市场主动调仓", countText(recentWeek.length), `锚点 ${anchor ? anchor.toISOString().slice(0, 10) : "未披露"}`, ""],
      ["近一周广发主动调仓", countText(recentGfWeek.length), `全市场胜率 ${pctText(recentWeekStats.调仓胜率)}`, recentGfWeek.length ? "is-warn" : ""],
    ];
    return `
      ${renderKpis(kpis)}
      <section class="insight-panel-stack">
        <section class="panel">
          <div class="panel-head"><div><h2>近一周经营结论</h2><p class="desc">按事实、观点、逻辑、动作拆解全市场状态、广发相对位置、调仓信号和风险边界。</p></div></div>
          ${weeklyNarrativeCards(marketStats, gfStats, recentWeek, recentGfWeek, recentWeekStats, gap)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>近一周策略类型分解</h2><p class="desc">领先/落后阈值为广发中位收益相对市场中位收益 ±0.20pct；同时看广发产品是否进入同类市场前25%。</p></div></div>
          ${weeklyTypeTable(weekRows)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>近一周重点产品名单</h2><p class="desc">一侧看广发可经营产品，一侧看需复盘产品，同时列出全市场Top5作为热点参照。</p></div></div>
          ${weeklyRankCards()}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>近一周主动调仓雷达</h2><p class="desc">按调仓逻辑看全市场动作、广发参与情况、胜率和超额，用于发现机构行为和广发调仓质量。</p></div></div>
          ${weeklyRebalanceLogicTable(weeklyRebalanceLogicRows(recentWeek, recentGfWeek))}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>产品分析挖掘框架补充</h2><p class="desc">对标天天投顾和海外 robo-advisor 后，当前最有价值的补充方向不是只加榜单，而是把场景、目标、风险、调仓和费用放进同一经营框架。</p></div></div>
          ${tableBlock(["平台对标参考", "公开资料", "可借鉴点", "本页落地", "下一步补强"], weeklyFrameworkRows(), (row, h) => h === "公开资料" ? publicSourceLinks(row[h]) : B.fmt(row[h]))}
        </section>
        <section class="panel">
          <div class="source-method"><strong>${B.label("近一周经营总览")}</strong> 近一周收益基于 App 或渠道披露单位净值计算，近一周调仓以当前筛选下最新调仓日期为锚点向前 7 天。周度结论仅用于经营雷达和周会复盘，正式产品评价仍应以同策略类型内的近三月、近1年、最大回撤、波动率、调仓质量、费率和数据完整性共同判断。</div>
        </section>
      </section>`;
  }
  function renderOverview() {
    const opps = opportunityRows();
    const typeRows = dimensionRows("策略类型", (row) => row.主可比池, pools);
    const conclusions = typeConclusionRows();
    const strengths = conclusions.filter((row) => ["第一梯队", "头部可经营", "中位占优"].includes(row.判断)).slice(0, 3);
    const gaps = conclusions.filter((row) => ["头部能力落后", "广发空白", "梯队不足", "头部追赶"].includes(row.判断)).slice(0, 3);
    return `
      ${renderKpis(overviewKpis(opps))}
      <section class="insight-panel-stack">
        ${typeConclusionPanel()}
        <section class="panel">
          <div class="panel-head"><div><h2>关键机会优先级</h2><p class="desc">综合样本规模、广发覆盖缺口、广发Top3差距、头部差距和风险差异排序。</p></div></div>
          ${opportunityList(opps)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>策略类型竞争位置</h2><p class="desc">广发基金与天天基金/投顾 + 广发基金完整策略按 ${B.esc(state.metric)} 对比。</p></div></div>
          ${tableBlock(["类型", "市场样本数", "广发样本数", "广发Top3平均收益", "市场Top3平均收益", "广发Top3差距", "头部策略数", "头部差距", "机会评分", "复盘建议"], opps, (row, h) => {
            if (["广发Top3平均收益", "市场Top3平均收益"].includes(h)) return B.pctSigned(row[h]);
            if (["广发Top3差距", "头部差距"].includes(h)) return ppText(row[h]);
            if (h === "机会评分") return `<span class="insight-chip ${row[h] >= 65 ? "bad" : row[h] >= 40 ? "warn" : "good"}">${row[h]}</span>`;
            return B.fmt(row[h]);
          })}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>产品覆盖结构</h2><p class="desc">深色为全市场样本数，右侧为广发/全市场数量。</p></div></div>
          ${barList(typeRows, "类型", "市场样本数", (row) => `${countText(row.广发样本数)} / ${countText(row.市场样本数)}`)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>收益中位辅助观察</h2><p class="desc">右侧为正，表示广发中位收益高于同策略类型全市场中位数；正式业务判断以上方Top3和标杆比较为主。</p></div></div>
          ${gapChart(typeRows)}
        </section>
        <section class="panel">
          <div class="insight-callout"><strong>广发产品特点：</strong>${strengths.length ? strengths.map((row) => `${row.类型}：${row.判断}，Top3对Top5差距${ppText(row.广发Top3对Top5差距)}`).join("；") : "当前筛选下暂未识别出第一梯队、头部可经营或中位占优的策略类型"}。<br><strong>业务关注点：</strong>${gaps.length ? gaps.map((row) => `${row.类型}：${row.业务动作}`).join("；") : "当前筛选下未触发高优先级缺口"}。</div>
        </section>
      </section>`;
  }
  function renderStructure() {
    return `
      ${assetProfileBlock()}
      <section class="insight-panel-stack">
        ${dimensionBenchmarkPanel()}
      </section>
      <section class="insight-dimension-grid">
        ${dimensionTable("策略类型分布", dimensionRows("策略类型", (row) => row.主可比池, pools))}
        ${dimensionTable("市场地域分布", dimensionRows("市场地域", (row) => row.市场地域, regions))}
        ${dimensionTable("主动/被动分布", dimensionRows("主动被动", (row) => row.主动被动, activePassiveOptions))}
        ${dimensionTable("波动率分层", dimensionRows("波动率分层", volatilityBucket, volatilityOptions.map((item) => item[0])))}
        ${dimensionTable("换手率分层", dimensionRows("换手率分层", turnoverBucket, turnoverOptions.map((item) => item[0])))}
      </section>
      <section class="panel">
        <div class="source-method"><strong>${B.label("分类口径")}</strong> 策略类型使用互斥主归属；市场地域、主动/被动、波动率分层、换手率分层为并列观察维度。波动率阈值：低于5%、5%-15%、15%及以上；换手率阈值：低于30%、30%-120%、120%及以上。</div>
      </section>`;
  }
  function renderPerformance() {
    const universe = marketRows();
    const target = targetRows();
    const kpis = [
      ["广发基金投顾中位收益", pctText(median(values(target, state.metric))), `指标：${state.metric}`, ""],
      ["全市场中位收益", pctText(median(values(universe, state.metric))), "当前筛选完整策略", ""],
      ["广发基金投顾中位回撤", pctText(median(values(target, "最大回撤"))), "越低越稳", ""],
      ["广发基金投顾中位波动率", pctText(median(values(target, "波动率"))), "年化波动率", ""],
      ["高波动策略数", countText(target.filter((row) => volatilityBucket(row) === "高波动").length), "波动率>=15%", "is-warn"],
      ["高换手策略数", countText(target.filter((row) => turnoverBucket(row) === "高换手").length), "年化换手率>=120%", "is-warn"],
    ];
    return `
      ${renderKpis(kpis)}
      <section class="insight-layout">
        <div class="insight-panel-stack">
          <section class="panel">
            <div class="panel-head"><div><h2>风险收益象限</h2><p class="desc">按当前筛选全市场中位收益和中位波动划分，辅助识别稳健领先、待复盘和高波动产品。</p></div></div>
            ${tableBlock(["策略名称", "策略类型", "风险收益象限", "所选收益", "最大回撤", "波动率", "夏普比率", "年化换手率"], riskQuadrantRows().slice(0, 18), (row, h) => {
              if (h === "策略名称") return strategyLink(row);
              if (["所选收益", "最大回撤", "波动率", "年化换手率"].includes(h)) return h === "波动率" || h === "年化换手率" ? B.pct(row[h]) : B.pctSigned(row[h]);
              return B.fmt(row[h]);
            })}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>存量策略复盘清单</h2><p class="desc">收益分位、回撤、波动率、换手率和基础数据共同触发。</p></div></div>
            ${reviewTable(16)}
          </section>
        </div>
        <div class="insight-panel-stack">
          <section class="panel">
            <div class="panel-head"><div><h2>全市场标杆策略</h2><p class="desc">当前筛选口径下按 ${B.esc(state.metric)} 排序。</p></div></div>
            ${leaderTable(12)}
          </section>
          ${dimensionTable("波动率分层", dimensionRows("波动率分层", volatilityBucket, volatilityOptions.map((item) => item[0])))}
          ${dimensionTable("换手率分层", dimensionRows("换手率分层", turnoverBucket, turnoverOptions.map((item) => item[0])))}
        </div>
      </section>`;
  }
  function renderRebalance() {
    const marketEventRows = filteredEvents(false);
    const targetEventRows = filteredEvents(true);
    const activeMarket = activeRebalanceEvents(marketEventRows);
    const activeTarget = activeRebalanceEvents(targetEventRows);
    const anchor = latestEventAnchor(activeMarket);
    const recentWeek = recentEvents(activeMarket, 7, anchor);
    const recentMonth = recentEvents(activeMarket, 30, anchor);
    const targetMonth = recentEvents(activeTarget, 30, anchor);
    const marketStats = rebalanceQualityStats(activeMarket);
    const gfStats = rebalanceQualityStats(activeTarget);
    const benchmark = benchmarkInstitution(activeMarket, anchor);
    const quality = rebalanceQualityConclusion(marketStats, gfStats, benchmark);
    const latestText = anchor ? anchor.toISOString().slice(0, 10) : "未披露";
    const kpis = [
      ["最新调仓日期", latestText, "以当前筛选下全市场主动调仓事件为锚点", ""],
      ["市场调仓胜率", pctText(marketStats.调仓胜率), `可评价事件 ${countText(marketStats.可评价事件数)}`, ""],
      ["广发调仓胜率", pctText(gfStats.调仓胜率), `可评价事件 ${countText(gfStats.可评价事件数)}`, quality.tone],
      ["标杆机构胜率", pctText(benchmark?.调仓胜率), benchmark ? `${benchmark.投顾机构}，可评价事件 ${countText(benchmark.可评价事件数)}` : "未披露", ""],
      ["胜率差距", ppText(quality.胜率差距), "广发调仓胜率减标杆机构胜率", quality.胜率差距 !== null && quality.胜率差距 >= 0 ? "is-good" : "is-warn"],
      ["近一周主动调仓", countText(recentWeek.length), `全市场策略数 ${countText(new Set(recentWeek.map((row) => row.统一策略ID)).size)}`, ""],
      ["近一月广发调仓", countText(targetMonth.length), "广发基金主动调仓", ""],
      ["近一月主动调仓", countText(recentMonth.length), `全市场机构数 ${countText(new Set(recentMonth.map((row) => row.投顾机构).filter(Boolean)).size)}`, ""],
    ];
    return `
      ${renderKpis(kpis)}
      <section class="insight-panel-stack">
        ${rebalanceQualitySummaryPanel(activeMarket, activeTarget, anchor)}
        <section class="panel">
          <div class="panel-head"><div><h2>机构调仓质量排名</h2><p class="desc">优先按调仓胜率排序；可评价事件少于3个的机构排在后面。广发基金即使不在前20也会保留展示。</p></div></div>
          ${institutionRebalanceTable(activeMarket, anchor)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>调仓逻辑质量对比</h2><p class="desc">按调仓逻辑拆解广发相对全市场和标杆机构的胜率、超额、风险与建议。</p></div></div>
          ${rebalanceLogicQualityTable(activeMarket, activeTarget, anchor)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>近一月主动调仓逻辑</h2><p class="desc">按调仓标题、原因和涉及资产关键词归因，展示全市场近期主动调仓方向和胜率。</p></div></div>
          ${logicTable(recentMonth)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>广发近期调仓</h2><p class="desc">只展示广发基金在近一月的主动调仓，用于回看具体策略和调仓原因。</p></div></div>
          ${recentEventTable(targetMonth)}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>近一月全市场调仓明细</h2><p class="desc">用于查看市场近期具体策略、机构、调仓逻辑和调仓后超额。</p></div></div>
          ${recentEventTable(recentMonth)}
        </section>
        <section class="panel">
          <div class="source-method"><strong>${B.label("调仓分析口径")}</strong> 主动调仓口径限定为主动为主或主动被动混合策略；近一周为最新调仓日期向前7天，近一月为向前30天。调仓胜率=胜数/可评价事件数，可评价事件仅包含胜、负、平，不含不可评估。调仓逻辑由调仓标题、调仓原因和涉及资产关键词归因，属于解释性分组，不替代人工复核。</div>
        </section>
      </section>`;
  }
  function renderOpportunity() {
    const opps = opportunityRows();
    const dataIssues = targetStrategyDiagnostics().filter((row) => row.基础数据等级 && row.基础数据等级 !== "A");
    const highRisk = targetStrategyDiagnostics().filter((row) => volatilityBucket(row) === "高波动" || turnoverBucket(row) === "高换手").slice(0, 8);
    const missingCoverage = opps.filter((row) => row.广发样本数 === 0).slice(0, 8);
    return `
      ${dataHealthBlock()}
      <section class="insight-layout">
        <div class="insight-panel-stack">
          <section class="panel">
            <div class="panel-head"><div><h2>业务计划候选方向</h2><p class="desc">按机会评分生成优先级，供业务负责人讨论产品补齐、定位强化或策略复盘。</p></div></div>
            ${tableBlock(["类型", "市场样本数", "广发样本数", "广发Top3差距", "头部策略数", "机会评分", "复盘建议"], opps, (row, h) => {
              if (h === "广发Top3差距") return ppText(row[h]);
              if (h === "机会评分") return `<span class="insight-chip ${row[h] >= 65 ? "bad" : row[h] >= 40 ? "warn" : "good"}">${row[h]}</span>`;
              return B.fmt(row[h]);
            })}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>高波动/高换手关注清单</h2><p class="desc">适合投研复盘、风险沟通和组合约束检查。</p></div></div>
            ${tableBlock(["策略名称", "策略类型", "所选收益", "最大回撤", "波动率", "年化换手率", "复盘建议"], highRisk, (row, h) => {
              if (h === "策略名称") return strategyLink(row);
              if (["所选收益", "最大回撤", "波动率", "年化换手率"].includes(h)) return h === "波动率" || h === "年化换手率" ? B.pct(row[h]) : B.pctSigned(row[h]);
              return B.fmt(row[h]);
            })}
          </section>
        </div>
        <div class="insight-panel-stack">
          <section class="panel">
            <div class="panel-head"><div><h2>产品空白与补齐方向</h2><p class="desc">广发基金无完整策略、但全市场样本较多的策略类型。</p></div></div>
            ${missingCoverage.length ? barList(missingCoverage, "类型", "市场样本数", (row) => `${countText(row.市场样本数)} 个市场样本`) : '<div class="empty">当前筛选下没有广发基金完全空白的策略类型。</div>'}
          </section>
          <section class="panel">
            <div class="panel-head"><div><h2>基础数据治理清单</h2><p class="desc">正式评价前应优先补齐基准、费率、风险等级和持仓链路。</p></div></div>
            ${tableBlock(["策略名称", "策略类型", "基础数据等级", "基准可用状态", "费率状态", "复盘建议"], dataIssues.slice(0, 12), (row, h) => h === "策略名称" ? strategyLink(row) : B.fmt(row[h]))}
          </section>
          <section class="panel">
            <div class="insight-callout"><strong>业务计划解释：</strong>优先级高不等于必须新发产品，先判断该类型是否符合广发基金战略、是否有可持续投研能力、是否能通过现有策略调优补齐。低覆盖但全市场样本大的方向适合做产品线评估；广发Top3明显落后的方向适合做策略复盘；数据缺口方向先进入数据治理。</div>
          </section>
        </div>
      </section>`;
  }
  function renderCurrentTab() {
    if (state.tab === "focus") return renderFocusInsights();
    if (state.tab === "week") return renderWeeklyOverview();
    if (state.tab === "structure") return renderStructure();
    if (state.tab === "performance") return renderPerformance();
    if (state.tab === "rebalance") return renderRebalance();
    if (state.tab === "opportunity") return renderOpportunity();
    return renderOverview();
  }
  function render() {
    root.innerHTML = `
      <section class="page-title">
        <div>
          <h1>数据洞察</h1>
          <p class="desc">面向广发基金业务负责人的产品格局、收益风险、交易调仓、数据治理和业务计划视图。分析样本限定为天天基金/投顾 + 广发基金，评价对象固定为广发基金。</p>
        </div>
        <div class="title-pills">
          <span class="pill">数据更新至 ${B.esc(summary.overview?.数据更新至 || "未披露")}</span>
          <span class="pill">数据刷新时间 ${B.esc(summary.overview?.数据刷新时间 || "未披露")}</span>
        </div>
      </section>
      <section class="panel">
        <div class="insight-filters">
          <select id="insightChannel" class="control">${channels.map((name) => `<option value="${B.esc(name)}" ${name === state.channel ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>
          <select id="insightMetric" class="control">${returnFields.map((name) => `<option value="${B.esc(name)}" ${name === state.metric ? "selected" : ""}>按${B.esc(name)}洞察</option>`).join("")}</select>
          <select id="insightPool" class="control"><option value="">全部策略类型</option>${pools.map((name) => `<option value="${B.esc(name)}" ${name === state.pool ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>
          <select id="insightBroadEquityBucket" class="control"><option value="">全部基准风险资产权重</option>${broadEquityBuckets.map((name) => `<option value="${B.esc(name)}" ${name === state.broadEquityBucket ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>
          <select id="insightRegion" class="control"><option value="">全部市场地域</option>${regions.map((name) => `<option value="${B.esc(name)}" ${name === state.region ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>
          <select id="insightActivePassive" class="control"><option value="">全部主动/被动</option>${activePassiveOptions.map((name) => `<option value="${B.esc(name)}" ${name === state.activePassive ? "selected" : ""}>${B.esc(name)}</option>`).join("")}</select>
          <select id="insightTurnover" class="control"><option value="">全部换手率</option>${turnoverOptions.map(([value, label]) => `<option value="${B.esc(value)}" ${value === state.turnover ? "selected" : ""}>${B.esc(label)}</option>`).join("")}</select>
          <select id="insightVolatility" class="control"><option value="">全部波动率</option>${volatilityOptions.map(([value, label]) => `<option value="${B.esc(value)}" ${value === state.volatility ? "selected" : ""}>${B.esc(label)}</option>`).join("")}</select>
        </div>
        <div class="insight-tabs">${tabs.map(([key, label]) => `<button type="button" class="insight-tab-button ${state.tab === key ? "is-active" : ""}" data-insight-tab="${key}">${B.esc(label)}</button>`).join("")}</div>
        <div class="source-method"><strong>${B.label("筛选口径")}</strong> 分析样本为天天基金/投顾 + 广发基金完整策略；策略类型为互斥主归属，市场地域、主动/被动、波动率和换手率为并列分析维度。当前筛选后广发基金完整策略 ${countText(targetRows().length)} 个，分析样本完整策略 ${countText(marketRows().length)} 个。</div>
      </section>
      ${renderCurrentTab()}
      <section class="panel">
        <div class="source-method"><strong>${B.label("数据洞察来源")}</strong> 当前页使用本地导出的策略摘要、全局分类字段、清洗后披露收益、回放风险指标和调仓事件数据；洞察中的全市场特指天天基金/投顾 + 广发基金分析样本，机会评分与调仓逻辑均为业务辅助排序口径，不直接等同正式考核排名。</div>
      </section>
    `;
    B.byId("insightChannel").addEventListener("change", (event) => { state.channel = event.target.value; render(); });
    B.byId("insightMetric").addEventListener("change", (event) => { state.metric = event.target.value; render(); });
    B.byId("insightPool").addEventListener("change", (event) => { state.pool = event.target.value; render(); });
    B.byId("insightBroadEquityBucket").addEventListener("change", (event) => { state.broadEquityBucket = event.target.value; render(); });
    B.byId("insightRegion").addEventListener("change", (event) => { state.region = event.target.value; render(); });
    B.byId("insightActivePassive").addEventListener("change", (event) => { state.activePassive = event.target.value; render(); });
    B.byId("insightTurnover").addEventListener("change", (event) => { state.turnover = event.target.value; render(); });
    B.byId("insightVolatility").addEventListener("change", (event) => { state.volatility = event.target.value; render(); });
    root.querySelectorAll("[data-insight-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        state.tab = button.dataset.insightTab;
        render();
      });
    });
  }
  render();
})();
"""


STRATEGY_DETAIL_JS = r"""
(async () => {
  const B = window.BasicData;
  const root = B.byId("strategyDetailPage");
  const id = B.params().get("id");
  const item = B.state.summary.strategies.find((row) => row.统一策略ID === id);
  if (!item) {
    root.innerHTML = '<section class="panel"><div class="empty">未找到策略，请从策略列表进入。</div></section>';
    return;
  }
  await B.loadScript(item.detailFile);
  const detail = B.state.details[id];
  if (!detail) {
    root.innerHTML = '<section class="panel"><div class="empty">策略详情文件加载失败。</div></section>';
    return;
  }
  const overview = B.state.summary?.overview || {};
  const dataRefreshTime = overview.数据刷新时间 || overview.生成时间 || "";
  const ranges = [
    ["all", "成立以来"],
    ["1y", "近1年"],
    ["6m", "近6月"],
    ["3m", "近3月"],
    ["1m", "近1月"],
    ["ytd", "今年以来"]
  ];
  const intervalHeaders = ["口径", "近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "成立以来"];
  const curveRows = ["披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"];
  const holdingHeaders = ["基金代码", "基金名称", "二级分类", "权重", "上次调仓后权重", "权重变化", "基金净值", "净值日期", "日涨幅", "调仓后收益率", "调仓后收益贡献"];
  const snapshots = detail.positionSnapshots || [];
  const globalBenchmarks = B.state.summary?.globalBenchmarks || [];
  let activeRange = "all";
  let activePerformanceTab = "interval";
  let activeSnapshotIndex = 0;
  let holdingSortField = "权重";
  let holdingSortDir = "desc";
  let selectedGlobalBenchmarkCode = "";
  let selectedContributionGlobalBenchmarkCode = "";

  function heroKpi(labelName, rawValue, formatter = null) {
    const tone = B.toneClass(labelName, rawValue);
    const value = formatter && !String(labelName).includes("回撤") ? formatter(rawValue) : B.valueHtml(labelName, rawValue);
    return `<div class="hero-kpi ${tone}"><span>${B.label(labelName)}</span><strong>${value}</strong></div>`;
  }
  function topFact(labelName, value, extraClass = "") {
    return `<div class="date-card ${extraClass}"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function isBlank(value) {
    return value === null || value === undefined || value === "" || value === "未披露";
  }
  function mapFields(rows) {
    return Object.fromEntries((rows || []).map((row) => [row.字段, row.值]));
  }
  const profileMap = mapFields(detail.profileFields);
  const performanceMap = mapFields(detail.performanceFields);
  const classificationMap = mapFields(detail.classificationFields);
  function pairCard(title, rows) {
    const body = rows.map(([labelName, value, formatter]) => `
      <div class="pair-row"><span>${B.label(labelName)}</span><strong>${formatter ? formatter(value) : B.valueHtml(labelName, value)}</strong></div>
    `).join("");
    return `<div class="paired-card"><h3>${B.esc(title)}</h3>${body}</div>`;
  }
  function coreLine(title, rows) {
    return `<section class="core-line"><h4>${B.esc(title)}</h4><div class="core-line-values">${rows.map(([labelName, value, formatter]) => B.metricValue(labelName, value, formatter)).join("")}</div></section>`;
  }
  function coreMetrics() {
    return `<div class="core-metric-board">
      ${coreLine("披露业绩", [
        ["官方单位净值", performanceMap.官方单位净值],
        ["官方累计收益", detail.summary.官方累计收益, B.pctSigned],
        ["近1年", detail.summary.近1年, B.pctSigned],
        ["最大回撤", detail.summary.最大回撤, B.pctSigned]
      ])}
      ${coreLine("评价口径", [
        ["主可比池", classificationMap.主可比池],
        ["基准可用状态", classificationMap.基准可用状态],
        ["基础数据等级", classificationMap.基础数据等级],
        ["可比记录数", performanceMap.可比记录数]
      ])}
      ${coreLine("分类指标", [
        ["权益基金权重", classificationMap.权益基金权重, B.pct],
        ["债券基金权重", classificationMap.债券基金权重, B.pct],
        ["QDII权重", classificationMap.QDII权重, B.pct],
        ["指数基金权重", classificationMap.指数基金权重, B.pct]
      ])}
      ${coreLine("风险交易", [
        ["波动率", performanceMap.波动率, B.pct],
        ["夏普比率", performanceMap.夏普比率],
        ["年化换手率", performanceMap.年化换手率, B.pct],
        ["调仓频率", performanceMap.调仓频率]
      ])}
    </div>`;
  }
  function selectedRows(rows, names) {
    const byName = mapFields(rows);
    return names.filter((name) => !isBlank(byName[name])).map((name) => ({ 字段: name, 值: byName[name] }));
  }
  function otherRows() {
    const primary = new Set(["统一策略ID", "策略代码", "策略名称", "渠道", "投顾机构", "策略类型", "风险等级", "成立日期", "运作天数", "运作状态", "官方单位净值", "自建单位净值", "费前单位净值", "费后单位净值", "官方累计收益", "自建累计收益", "与官方偏差", "年化收益", "最大回撤", "波动率", "夏普比率", "官方对比口径", "可比记录数", "建议持有时长", "起投金额", "投顾费率", "业绩基准", "业绩基准说明", "标签", "策略概念"]);
    return [...(detail.profileFields || []), ...(detail.performanceFields || [])].filter((row) => !primary.has(row.字段));
  }
  function compactInfoRows() {
    const byName = mapFields(detail.profileFields || []);
    return ["策略代码", "策略类型", "建议持有时长", "起投金额", "标签", "策略概念"].map((name) => ({ 字段: name, 值: byName[name] ?? "未披露" }));
  }
  function classificationInfoRows() {
    const names = ["主可比池", "基准风险资产权重", "基准风险资产权重_百分比", "基准风险资产权重说明", "权益中枢", "固收中枢", "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "配置风格标签", "市场地域", "主动被动", "特殊标签", "策略实现标签", "权益基金权重", "债券基金权重", "货币基金权重", "混合基金权重", "QDII权重", "指数基金权重", "主动基金权重", "基准权益权重", "基准债券权重", "基准货币权重", "基准结构类型", "非权益比较轨道", "正式可比池", "可比池样本资格", "可比池说明", "基准互斥权重合计_百分比", "基准港股权益权重", "基准海外权益权重", "是否多元策略", "多元策略标签", "基准映射置信度", "基准资产已映射权重", "基准资产未映射权重", "基准资产大类-权益", "基准资产大类-债券", "基准资产大类-现金", "基准资产大类-商品", "基准资产大类-另类", "基准资产大类-其他", "基准资产类别-A股", "基准资产类别-港股", "基准资产类别-海外权益", "基准资产类别-债券", "基准资产类别-商品", "基准资产类别-现金", "基准资产类别-其他", "基准可用状态", "基础数据等级", "分类依据"];
    return names.map((name) => ({ 字段: name, 值: classificationMap[name] ?? "未披露" }));
  }
  function classChip(labelName, value, main = false) {
    return `<div class="class-chip ${main ? "is-main" : ""}"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classMetric(labelName, value) {
    return `<div class="class-metric"><span>${B.label(labelName)}</span><strong>${B.valueHtml(labelName, value)}</strong></div>`;
  }
  function classificationSummary() {
    const holdingWeights = ["权益基金权重", "债券基金权重", "货币基金权重", "QDII权重", "指数基金权重", "主动基金权重"];
    const benchmarkWeights = ["基准风险资产权重_百分比", "基准权益权重", "基准债券权重", "基准货币权重"];
    const benchmarkMajorWeights = ["基准资产大类-权益", "基准资产大类-债券", "基准资产大类-现金", "基准资产大类-商品", "基准资产大类-另类", "基准资产大类-其他"];
    const benchmarkCategoryWeights = ["基准资产类别-A股", "基准资产类别-港股", "基准资产类别-海外权益", "基准资产类别-债券", "基准资产类别-商品", "基准资产类别-现金", "基准资产类别-其他"];
    return `<div class="classification-summary">
      <div class="class-chip-grid">
        ${classChip("主可比池", classificationMap.主可比池, true)}
        ${classChip("市场地域", classificationMap.市场地域)}
        ${classChip("主动被动", classificationMap.主动被动)}
        ${classChip("特殊标签", classificationMap.特殊标签)}
        ${classChip("策略实现标签", classificationMap.策略实现标签)}
        ${classChip("基准风险资产权重", classificationMap.基准风险资产权重)}
        ${classChip("配置风格标签", classificationMap.配置风格标签)}
        ${classChip("多元策略标签", classificationMap.多元策略标签 || (classificationMap.是否多元策略 ? "是" : "否"))}
        ${classChip("基准可用状态", classificationMap.基准可用状态)}
      </div>
      <div class="class-section-title">持仓分类权重</div>
      <div class="class-metric-grid">${holdingWeights.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
      <div class="class-section-title">基准拆分</div>
      <div class="class-metric-grid">${benchmarkWeights.map((name) => classMetric(name, classificationMap[name])).join("")}${classMetric("基础数据等级", classificationMap.基础数据等级)}</div>
      <div class="class-section-title">基准资产大类</div>
      <div class="class-metric-grid">${benchmarkMajorWeights.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
      <div class="class-section-title">基准资产类别</div>
      <div class="class-metric-grid">${benchmarkCategoryWeights.map((name) => classMetric(name, classificationMap[name])).join("")}</div>
      <div class="class-basis"><strong>${B.label("分类依据")}</strong><span>${B.esc(classificationMap.分类依据 || "未披露")}</span></div>
    </div>`;
  }
  function benchmarkInfo() {
    const text = profileMap.业绩基准说明 ?? profileMap.业绩基准 ?? "未披露";
    return `<div class="benchmark-strip"><strong>${B.label("业绩基准说明")}</strong><span>${B.esc(text)}</span></div>`;
  }
  function latestRebalanceText() {
    if (!isBlank(detail.summary.最近调仓日)) return B.fmt(detail.summary.最近调仓日);
    const history = snapshots.find((snap) => snap.id !== "current" && !isBlank(snap.日期));
    if (history) return B.fmt(history.日期);
    if (!isBlank(detail.holdingMeta.最新持仓日)) return `${B.esc(detail.holdingMeta.最新持仓日)}（无历史调仓事件）`;
    return "无历史调仓事件";
  }
  function sourceCards() {
    const sources = detail.curveSources || {};
    const meta = detail.benchmarkMeta || {};
    const metaText = meta.基准公式解析 ? `${meta.基准公式解析}${(meta.缺失组件 || []).length ? `；缺失：${meta.缺失组件.join("、")}` : ""}` : "";
    const selected = selectedGlobalBenchmark();
    const globalText = selected ? `<p><b>全局基准：</b>${B.esc(selected.name)}（${B.esc(selected.code)}），区间 ${B.esc(selected.start || "未披露")} 至 ${B.esc(selected.end || "未披露")}；数据来源：${B.esc(selected.source || "指数日度行情")}</p>` : "";
    const warnings = (detail.curveWarnings || []).map((text) => `<p class="warn"><b>${B.label("曲线数据提示")}：</b>${B.esc(text)}</p>`).join("");
    return `<div class="source-note-list">${warnings}${curveRows.map((name) => `<p><b>${B.esc(name)}：</b>${B.esc(sources[name] || "未生成来源说明")}</p>`).join("")}${globalText}${metaText ? `<p><b>基准公式解析：</b>${B.esc(metaText)}</p>` : ""}</div>`;
  }
  function rangeButtons() {
    return `<div class="range-tabs">${ranges.map(([key, text]) => `<button type="button" data-range="${key}" class="${key === activeRange ? "is-active" : ""}">${B.esc(text)}</button>`).join("")}</div>`;
  }
  function selectedGlobalBenchmark() {
    return globalBenchmarks.find((row) => row.code === selectedGlobalBenchmarkCode) || null;
  }
  function selectedContributionGlobalBenchmark() {
    return globalBenchmarks.find((row) => row.code === selectedContributionGlobalBenchmarkCode) || null;
  }
  function selectedGlobalBenchmarkSeriesName() {
    const selected = selectedGlobalBenchmark();
    return selected ? `全局基准：${selected.name}` : "";
  }
  function selectedContributionGlobalBenchmarkSeriesName() {
    const selected = selectedContributionGlobalBenchmark();
    return selected ? `全局基准：${selected.name}` : "";
  }
  function mainChartSeries() {
    const series = { ...(detail.curves || {}) };
    const strategyId = String(detail.summary?.统一策略ID || "");
    if (strategyId.startsWith("gfbank_cgb__")) {
      ["披露业绩", "基准业绩"].forEach((name) => {
        const payload = series[name];
        const points = Array.isArray(payload) ? payload : (payload?.points || []);
        const mode = payload?.模式 || points[0]?.模式 || "nav";
        if (!points.length) return;
        const allTimeValueMode = ["return", "return_pct"].includes(mode)
          ? "return_pct"
          : (mode === "nav" && Number(points[0]?.数值) > 0 && Number(points[0]?.数值) <= 10 ? "unit_nav" : "");
        if (!allTimeValueMode) return;
        series[name] = Array.isArray(payload)
          ? { 模式: mode, points: payload, allTimeValueMode }
          : { ...payload, allTimeValueMode };
      });
    }
    const selected = selectedGlobalBenchmark();
    if (selected && Array.isArray(selected.points) && selected.points.length) {
      series[selectedGlobalBenchmarkSeriesName()] = { 模式: "nav", points: selected.points };
    }
    return series;
  }
  function globalBenchmarkSelectHtml() {
    return `<select id="globalBenchmarkSelect" class="control benchmark-select"><option value="">选择全局基准</option>${globalBenchmarks.map((row) => `<option value="${B.esc(row.code)}" ${row.code === selectedGlobalBenchmarkCode ? "selected" : ""}>${B.esc(row.name)}｜${B.esc(row.code)}</option>`).join("")}</select>`;
  }
  function contributionGlobalBenchmarkSelectHtml() {
    return `<select id="contributionGlobalBenchmarkSelect" class="control benchmark-select"><option value="">选择全局基准</option>${globalBenchmarks.map((row) => `<option value="${B.esc(row.code)}" ${row.code === selectedContributionGlobalBenchmarkCode ? "selected" : ""}>${B.esc(row.name)}｜${B.esc(row.code)}</option>`).join("")}</select>`;
  }
  function intervalMatrixTable() {
    const byName = Object.fromEntries((detail.intervalMatrix || []).map((row) => [row.口径, row]));
    const rows = curveRows.map((name) => byName[name] || { 口径: name });
    const head = intervalHeaders.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.map((row) => `<tr>${intervalHeaders.map((h) => {
      if (h === "口径") return `<td><strong>${B.esc(row[h])}</strong></td>`;
      return `<td>${B.pctSigned(row[h])}</td>`;
    }).join("")}</tr>`).join("");
    return `<div class="table-wrap interval-matrix"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function annualPerformanceTable() {
    const headers = ["年度", "披露业绩", "模拟业绩", "基准业绩", "沪深300业绩"];
    const rows = detail.annualMatrix || [];
    const head = headers.map((h) => `<th>${B.label(h)}</th>`).join("");
    const body = rows.length ? rows.map((row) => `<tr>${headers.map((h) => {
      if (h === "年度") return `<td><strong>${B.esc(row[h])}</strong></td>`;
      return `<td>${B.pctSigned(row[h])}</td>`;
    }).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}"><div class="empty">暂无年度业绩</div></td></tr>`;
    return `<div class="table-wrap interval-matrix"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function performanceTabsHtml() {
    return `<div class="data-tabs"><button type="button" data-performance-tab="interval" class="${activePerformanceTab === "interval" ? "is-active" : ""}">常用区间</button><button type="button" data-performance-tab="annual" class="${activePerformanceTab === "annual" ? "is-active" : ""}">年度业绩</button></div>`;
  }
  function renderPerformanceTable() {
    B.byId("performanceTable").innerHTML = activePerformanceTab === "annual" ? annualPerformanceTable() : intervalMatrixTable();
  }
  function renderPerformanceTabs() {
    B.byId("performanceTabs").innerHTML = performanceTabsHtml();
    B.byId("performanceTabs").querySelectorAll("[data-performance-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        activePerformanceTab = button.dataset.performanceTab;
        renderPerformanceTabs();
        renderPerformanceTable();
      });
    });
    renderPerformanceTable();
  }
  function holdingValue(row, h) {
    if (h === "基金代码") return `<strong>${B.esc(row[h] || "")}</strong>`;
    if (["权重", "上次调仓后权重"].includes(h)) return B.pct(row[h]);
    if (["权重变化", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(h)) return B.pctSigned(row[h]);
    return B.fmt(row[h]);
  }
  function holdingSortHeader(h) {
    const active = holdingSortField === h;
    const arrow = active ? (holdingSortDir === "asc" ? "▲" : "▼") : "↕";
    return `<th><span class="sort-head ${active ? "is-active" : ""}" role="button" tabindex="0" data-holding-sort="${B.esc(h)}">${B.label(h)}<span class="sort-arrow">${arrow}</span></span></th>`;
  }
  function compareHolding(a, b, h) {
    if (["权重", "上次调仓后权重", "权重变化", "基金净值", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(h)) {
      const av = Number(a[h]);
      const bv = Number(b[h]);
      return (Number.isFinite(av) ? av : -999999) - (Number.isFinite(bv) ? bv : -999999);
    }
    if (h.includes("日期")) return String(a[h] || "").localeCompare(String(b[h] || ""));
    return String(a[h] || "").localeCompare(String(b[h] || ""), "zh-CN");
  }
  function holdingTable(rows) {
    const sortedRows = [...rows].sort((a, b) => {
      const compared = compareHolding(a, b, holdingSortField);
      return holdingSortDir === "asc" ? compared : -compared;
    });
    const head = holdingHeaders.map((h) => holdingSortHeader(h)).join("");
    const body = sortedRows.length ? sortedRows.map((row) => `<tr>${holdingHeaders.map((h) => `<td>${holdingValue(row, h)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${holdingHeaders.length}"><div class="empty">暂无持仓明细</div></td></tr>`;
    return `<div class="table-wrap"><table class="compact-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function renderMainChart() {
    const selectedName = selectedGlobalBenchmarkSeriesName();
    const officialPoints = detail.curves?.披露业绩?.points || [];
    const simulatedPoints = detail.curves?.模拟业绩?.points || [];
    const hasDrawableStrategyCurve = officialPoints.length >= 2 || simulatedPoints.length >= 2;
    if (!hasDrawableStrategyCurve) {
      B.byId("navChart").innerHTML = `
        <div class="empty">
          <strong>暂无真实业绩走势图</strong><br/>
          当前渠道尚未提供可验证的结构化逐日净值或收益序列；页面仅展示已取得的官方区间收益，不使用截图或图像反推点替代。
        </div>`;
      const sourceHost = B.byId("sourceCards");
      if (sourceHost) sourceHost.innerHTML = sourceCards();
      return;
    }
    const defaultSeries = selectedName ? ["披露业绩", selectedName] : ["披露业绩"];
    B.drawReturnChart(B.byId("navChart"), mainChartSeries(), { range: activeRange, title: "净值曲线", defaultVisibleSeries: defaultSeries });
    const sourceHost = B.byId("sourceCards");
    if (sourceHost) sourceHost.innerHTML = sourceCards();
  }
  function renderRangeTabs() {
    B.byId("rangeTabs").innerHTML = rangeButtons();
    B.byId("rangeTabs").querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        activeRange = button.dataset.range;
        renderRangeTabs();
        renderMainChart();
      });
    });
  }
  function renderSnapshotList() {
    const list = B.byId("rebalanceList");
    list.innerHTML = snapshots.length ? snapshots.map((snap, index) => `
      <button class="rebalance-item ${index === activeSnapshotIndex ? "is-active" : ""}" type="button" data-snapshot-index="${index}">
        <strong>${B.esc(snap.类型 || "")}｜${B.esc(snap.日期 || "未披露日期")}</strong>
        <span>${B.esc(snap.标题 || "")}</span>
        <span>${B.esc(snap.说明 || "")}</span>
      </button>`).join("") : '<div class="empty">暂无仓位快照</div>';
    list.querySelectorAll("[data-snapshot-index]").forEach((button) => {
      button.addEventListener("click", () => {
        activeSnapshotIndex = Number(button.dataset.snapshotIndex);
        renderPositions();
      });
    });
  }
  function contributionFor(snapshot) {
    const curves = detail.contributionCurves || {};
    const drawable = (payload) => {
      const series = payload?.series || {};
      return (series?.调仓前仓位模拟?.points || []).length >= 2
        && (series?.调仓后仓位实际?.points || []).length >= 2;
    };
    if (snapshot && snapshot.id && drawable(curves[String(snapshot.id)])) {
      return { snapshot, payload: curves[String(snapshot.id)] };
    }
    const fallback = snapshots.find((item) => item.id !== "current" && drawable(curves[String(item.id)]));
    return fallback ? { snapshot: fallback, payload: curves[String(fallback.id)] } : { snapshot: null, payload: null };
  }
  function renderContribution(snapshot) {
    const target = contributionFor(snapshot);
    const desc = B.byId("contributionDesc");
    if (!target.payload) {
      desc.textContent = "暂无可用于绘制调仓贡献曲线的调仓质量评估数据。";
      B.drawReturnChart(B.byId("contributionChart"), {}, { alreadyReturn: false, title: "调仓贡献曲线" });
      return;
    }
    const meta = target.payload || {};
    const selected = selectedContributionGlobalBenchmark();
    const selectedName = selectedContributionGlobalBenchmarkSeriesName();
    const series = { ...(target.payload.series || {}) };
    if (selected && Array.isArray(selected.points) && selected.points.length) {
      series[selectedName] = { 模式: "nav", points: selected.points };
    }
    const fallbackText = snapshot?.id && target.snapshot?.id && snapshot.id !== target.snapshot.id
      ? `；所选快照尚不可评估，已回退到最近可评估调仓 ${target.snapshot?.日期 || ""}`
      : "";
    desc.textContent = `${meta.起始日期 || target.snapshot?.日期 || ""} 至 ${meta.结束日期 || "最新"}，默认展示调仓前后仓位曲线${fallbackText}；基准、沪深300和全局基准可在图例中勾选。`;
    const defaultVisible = selectedName ? ["调仓前仓位模拟", "调仓后仓位实际", selectedName] : ["调仓前仓位模拟", "调仓后仓位实际"];
    B.drawReturnChart(B.byId("contributionChart"), series, { alreadyReturn: false, title: "调仓贡献曲线", height: 280, defaultVisibleSeries: defaultVisible });
  }
  function renderPositions() {
    activeSnapshotIndex = Math.max(0, Math.min(activeSnapshotIndex, Math.max(0, snapshots.length - 1)));
    const snap = snapshots[activeSnapshotIndex] || { holdings: [] };
    renderSnapshotList();
    B.byId("holdingHead").innerHTML = `
      <div>
        <h3>${B.esc(snap.标题 || "当前仓位")}</h3>
        <p>${B.esc(snap.类型 || "")}｜${B.esc(snap.日期 || "未披露日期")}｜${B.esc(snap.说明 || "")}</p>
      </div>
      <span class="pill">${(snap.holdings || []).length.toLocaleString("zh-CN")} 只基金</span>`;
    B.byId("holdingTable").innerHTML = holdingTable(snap.holdings || []);
    B.byId("holdingTable").querySelectorAll("[data-holding-sort]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (event.target.closest("[data-field]")) return;
        const field = button.dataset.holdingSort;
        if (holdingSortField === field) holdingSortDir = holdingSortDir === "asc" ? "desc" : "asc";
        else {
          holdingSortField = field;
          holdingSortDir = ["权重", "上次调仓后权重", "基金净值", "日涨幅", "调仓后收益率", "调仓后收益贡献"].includes(field) ? "desc" : "asc";
        }
        renderPositions();
      });
    });
    renderContribution(snap);
  }

  root.innerHTML = `
    <section class="page-title">
      <div>
        <a class="link" href="./strategies.html">返回策略列表</a>
        <h1>策略详情</h1>
        <p class="desc">同屏查看基础信息、披露业绩、模拟业绩、仓位和调仓贡献。</p>
      </div>
      <span class="pill">${B.label("统一策略ID")} ${B.esc(detail.id)}</span>
    </section>
    <section class="panel hero-panel">
      <div class="strategy-hero">
        <div>
          <div class="hero-title">
            <h1>${B.esc(detail.summary.策略名称)}</h1>
            ${B.statusBadge(detail.summary.数据完整性)}
          </div>
          <div class="hero-meta">
            <span class="pill">${B.esc(detail.summary.渠道)}</span>
            <span class="pill">${B.esc(classificationMap.主可比池 || "未分类")}</span>
            <span class="pill">${B.esc(detail.summary.策略类型 || "未披露类型")}</span>
            <span class="pill">${B.esc(detail.summary.运作状态 || "未披露运作状态")}</span>
          </div>
          <div class="hero-dates">
            ${topFact("成立日期", detail.summary.成立日期, "is-date")}
            ${topFact("最新业绩日期", detail.summary.最新业绩日期 || detail.summary.收益数据截至 || "未披露", "is-date")}
            ${topFact("数据刷新时间", dataRefreshTime || "未披露")}
            <div class="date-card is-date"><span>${B.label("运作天数")}</span><strong>${B.fmt(detail.summary.运作天数, " 天")}</strong></div>
            ${topFact("投顾机构", profileMap.投顾机构 || detail.summary.投顾机构 || "未披露")}
            ${topFact("风险等级", profileMap.风险等级 || detail.summary.风险等级 || "未披露")}
            ${topFact("投顾费率", profileMap.投顾费率 || "未披露")}
            ${topFact("市场地域", classificationMap.市场地域 || "未披露")}
            ${topFact("主动被动", classificationMap.主动被动 || "未披露")}
          </div>
          <p class="desc">${B.esc(detail.summary.运作状态 || "未披露运作状态")}｜最新业绩日 ${B.esc(detail.summary.最新业绩日期 || detail.summary.收益数据截至 || "未披露")}｜最新持仓日 ${B.esc(detail.holdingMeta.最新持仓日 || "未披露")}｜持仓来源 ${B.esc(detail.holdingMeta.持仓来源 || "未披露")}</p>
        </div>
        <div class="hero-kpis">
          ${heroKpi("官方累计收益", detail.summary.官方累计收益, B.pctSigned)}
          ${heroKpi("近1年", detail.summary.近1年, B.pctSigned)}
          ${heroKpi("最大回撤", detail.summary.最大回撤, B.pctSigned)}
          ${heroKpi("主可比池", classificationMap.主可比池 || "未披露")}
          ${heroKpi("持仓基金数", detail.holdingMeta.持仓基金数)}
          ${heroKpi("最近调仓日", latestRebalanceText())}
        </div>
      </div>
      <div class="hero-support profile-compact">
        <div class="profile-block strategy-info-block">
          <h3>策略基本信息</h3>
          ${B.valueList(compactInfoRows())}
        </div>
        <div class="profile-block classification-block">
          <h3>分类影响指标</h3>
          ${classificationSummary()}
        </div>
        <div class="profile-block evaluation-block">
          <h3>评价核心数据</h3>
          ${coreMetrics()}
        </div>
        ${benchmarkInfo()}
      </div>
    </section>
    <section class="panel chart-panel">
      <div class="panel-head">
        <div>
          <h2>净值曲线</h2>
          <p class="desc">默认成立以来，切换区间后各曲线均按该区间起点归零展示相对收益率。</p>
        </div>
        <div class="chart-actions">
          ${globalBenchmarkSelectHtml()}
          <div id="rangeTabs"></div>
        </div>
      </div>
      <div id="navChart" class="chart"></div>
      <div id="sourceCards">${sourceCards()}</div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div><h2>区间业绩</h2><p class="desc">常用区间优先采用官方披露区间；年度业绩按上年末或年初前最近可用点到该年度最后可用点计算。</p></div>
        <div id="performanceTabs"></div>
      </div>
      <div id="performanceTable"></div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>仓位</h2>
          <p class="desc">左侧为当前仓位和历史调仓列表，点击后右侧切换对应基金仓位明细。</p>
        </div>
        <span class="pill">${B.esc(detail.holdingMeta.稽核结论 || "未生成稽核")}</span>
      </div>
      <div class="position-layout">
        <div id="rebalanceList" class="rebalance-list"></div>
        <div class="position-detail">
          <div id="holdingHead" class="holding-head"></div>
          <div id="holdingTable"></div>
        </div>
      </div>
    </section>
    <section class="panel chart-panel">
      <div class="panel-head">
        <div>
          <h2>调仓贡献曲线</h2>
          <p id="contributionDesc" class="desc"></p>
        </div>
        <div class="chart-actions">
          ${contributionGlobalBenchmarkSelectHtml()}
        </div>
      </div>
      <div id="contributionChart" class="chart"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>数据质量与其他信息</h2><p class="desc">保留原详情页的质量检查、持仓口径和低覆盖字段；低覆盖或空值较多的字段默认折叠。</p></div></div>
      <div class="quality-grid">
        ${(detail.qualityChecks || []).map((row) => `<div class="quality-card"><h3>${B.esc(row.项目)}</h3>${B.statusBadge(row.结论)}<p>${B.esc(row.说明)}</p></div>`).join("")}
      </div>
      <details class="fold-block">
        <summary>持仓口径与其他字段</summary>
        ${B.valueList(Object.entries(detail.holdingMeta || {}).map(([字段, 值]) => ({ 字段, 值 })))}
        ${B.valueList(otherRows())}
      </details>
    </section>
  `;
  const globalBenchmarkSelect = B.byId("globalBenchmarkSelect");
  if (globalBenchmarkSelect) {
    globalBenchmarkSelect.addEventListener("change", () => {
      selectedGlobalBenchmarkCode = globalBenchmarkSelect.value;
      renderMainChart();
    });
  }
  const contributionGlobalBenchmarkSelect = B.byId("contributionGlobalBenchmarkSelect");
  if (contributionGlobalBenchmarkSelect) {
    contributionGlobalBenchmarkSelect.addEventListener("change", () => {
      selectedContributionGlobalBenchmarkCode = contributionGlobalBenchmarkSelect.value;
      renderContribution(snapshots[activeSnapshotIndex] || null);
    });
  }
  renderRangeTabs();
  renderPerformanceTabs();
  renderMainChart();
  renderPositions();
})();
"""


FUND_DETAIL_JS = r"""
(() => {
  const B = window.BasicData;
  const root = B.byId("fundDetailPage");
  const pack = window.__BASIC_DATA__?.fundDetailPack;
  const query = new URLSearchParams(window.location.search);
  const requestedCode = (query.get("code") || "").trim();
  const requestedName = (query.get("name") || "").trim();

  function empty(message) {
    root.innerHTML = `<section class="panel"><div class="empty">${B.esc(message)}</div></section>`;
  }

  if (!pack || !Array.isArray(pack.funds)) {
    empty("未找到基金详情数据包，请先运行报告数据包重建步骤。");
    return;
  }

  const fundFields = pack.fundFields || [];
  const holdingFields = pack.holdingFields || [];
  const monthlyFields = pack.monthlyFields || [];
  const funds = pack.funds || [];
  const holdings = pack.holdings || [];
  const monthly = pack.monthly || [];

  function toObject(fields, row) {
    return Object.fromEntries((fields || []).map((field, index) => [field, row?.[index] ?? ""]));
  }

  const codeField = fundFields[0] || "基金代码";
  const nameField = fundFields[1] || "基金名称";
  const fundObjects = funds.map((row, index) => ({ index, row, data: toObject(fundFields, row) }));
  const matched = fundObjects.find((item) => {
    const code = String(item.data[codeField] || "").trim();
    const name = String(item.data[nameField] || "").trim();
    return (requestedCode && code === requestedCode) || (requestedName && name === requestedName);
  });

  if (!matched) {
    empty("未找到匹配的基金。");
    return;
  }

  function isPctField(field) {
    return /权重|收益|增配|减配|比例|占比|中位|近\d|今年以来|成立以来|涨跌幅|回撤|波动率|投顾费率/.test(String(field || ""));
  }

  function isTextField(field) {
    return /代码|ID|编号/.test(String(field || ""));
  }

  function valueHtml(field, value) {
    if (value === null || value === undefined || value === "") return '<span class="value-muted">未披露</span>';
    if (isTextField(field)) return B.esc(String(value));
    const number = Number(value);
    if (Number.isFinite(number) && isPctField(field)) return B.pctSigned(number);
    if (Number.isFinite(number)) return B.fmt(number);
    return B.esc(value);
  }

  function factGrid(fields, data) {
    return `<div class="stat-grid">${fields.map((field) => `
      <div class="metric-card">
        <span>${B.label(field)}</span>
        <strong>${valueHtml(field, data[field])}</strong>
      </div>
    `).join("")}</div>`;
  }

  function table(fields, rows, formatter) {
    const head = fields.map((field) => `<th>${B.label(field)}</th>`).join("");
    const body = rows.length
      ? rows.map((row) => `<tr>${fields.map((field) => `<td>${formatter ? formatter(row, field) : valueHtml(field, row[field])}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${fields.length}"><div class="empty">暂无数据</div></td></tr>`;
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function strategyHref(row) {
    const id = String(row?.[holdingFields[1]] || "").trim();
    return id ? `./strategy.html?id=${encodeURIComponent(id)}` : "";
  }

  function holdingValueHtml(row, field) {
    if (field === holdingFields[2]) {
      const href = strategyHref(row);
      const value = row[field];
      if (href && value !== null && value !== undefined && value !== "") {
        return `<a class="link" href="${href}">${B.esc(value)}</a>`;
      }
    }
    return valueHtml(field, row[field]);
  }

  const fundData = matched.data;
  const fundIndex = matched.index;
  const fundHoldings = holdings
    .filter((row) => Number(row?.[0]) === fundIndex)
    .map((row) => toObject(holdingFields, row))
    .sort((a, b) => Number(b[holdingFields[12]] || 0) - Number(a[holdingFields[12]] || 0));
  const fundMonthly = monthly
    .filter((row) => Number(row?.[0]) === fundIndex)
    .map((row) => toObject(monthlyFields, row))
    .sort((a, b) => String(b[monthlyFields[1]] || "").localeCompare(String(a[monthlyFields[1]] || "")));

  const summaryFields = [0, 1, 2, 3, 6, 7, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22]
    .map((index) => fundFields[index])
    .filter(Boolean);
  const holdingDisplayFields = [2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]
    .map((index) => holdingFields[index])
    .filter(Boolean);
  const monthlyDisplayFields = monthlyFields.slice(1);
  document.title = `${fundData[nameField] || "基金详情"}｜基金详情`;

  root.innerHTML = `
    <section class="panel hero-panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">底层基金详情</p>
          <h1>${B.esc(fundData[nameField] || "未命名基金")}</h1>
          <p class="desc">${B.esc(fundData[codeField] || "未披露代码")}｜${B.esc(fundData[fundFields[2]] || "未披露基金公司")}｜${B.esc(fundData[fundFields[3]] || "未披露类型")}</p>
        </div>
        <a class="link" href="./insights.html">返回数据洞察</a>
      </div>
      <h2>基金基础信息</h2>
      ${factGrid(summaryFields, fundData)}
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>持仓策略</h2>
          <p class="desc">按期末持仓比例排序，共 ${fundHoldings.length.toLocaleString("zh-CN")} 条策略持仓记录。</p>
        </div>
      </div>
      ${table(holdingDisplayFields, fundHoldings, holdingValueHtml)}
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>月度调仓</h2>
          <p class="desc">展示该基金在策略调仓中的净增配、加仓和减仓权重。</p>
        </div>
      </div>
      ${table(monthlyDisplayFields, fundMonthly.slice(0, 60))}
    </section>
  `;
})();
"""


def write_shell_files(site: Path) -> None:
    basic_common_source = load_template_asset_text("assets/basic-common.js", COMMON_JS)
    strategies_source = load_template_asset_text("assets/strategies.js", STRATEGIES_JS)
    insights_source = load_template_asset_text("assets/insights.js", INSIGHTS_JS)
    strategy_detail_source = load_template_asset_text("assets/strategy-detail.js", STRATEGY_DETAIL_JS)
    write_text(site / "index.html", '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=./institutions.html"><title>投顾数据天眼</title><a href="./institutions.html">进入机构总览</a>')
    write_text(site / "strategies.html", backup_style_strategies_html())
    write_text(site / "institutions.html", page_html("机构总览", "institutions", "institutionPage", "institutions.js"))
    write_text(site / "compare.html", template_text("compare.html", page_html("策略对比", "compare", "insightsPage", "insights.js")))
    write_text(site / "mixed-performance-scatter.html", page_html("投顾基金全市场产品排名", "mixed_performance_scatter", "mixedPerformanceScatterPage", "mixed-performance-scatter.js"))
    write_text(site / "ai-strategy.html", template_text("ai-strategy.html", page_html("AI选策略", "ai", "aiStrategyPage", "ai-strategy.js")))
    write_text(site / "strategy.html", template_text("strategy.html", strategy_page_html()))
    write_text(site / "fund.html", template_text("fund.html", fund_page_html()))
    write_text(site / "assets" / "basic.css", template_text("assets/basic.css", CSS))
    write_text(site / "assets" / "basic-common.js", basic_common_source)
    write_text(site / "assets" / "strategies.js", template_text("assets/strategies.js", strategies_source))
    write_text(site / "assets" / "institutions.js", template_text("assets/institutions.js", ""))
    write_text(site / "assets" / "ai-strategy-config.js", template_text("assets/ai-strategy-config.js", ""))
    write_text(site / "assets" / "ai-strategy.js", template_text("assets/ai-strategy.js", ""))
    write_text(site / "assets" / "mixed-performance-scatter.js", template_text("assets/mixed-performance-scatter.js", ""))
    write_text(site / "assets" / "insights.js", template_text("assets/insights.js", insights_source))
    write_text(site / "assets" / "strategy-detail.js", template_text("assets/strategy-detail.js", strategy_detail_source))
    write_text(site / "assets" / "fund-detail.js", template_text("assets/fund-detail.js", FUND_DETAIL_JS))
    config_dir = site / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    write_text(config_dir / "模型服务配置.js", template_text("config/模型服务配置.js", "window.__AI_STRATEGY_LOCAL_CONFIG__ = {};\n"))
    write_text(config_dir / "ai-strategy-local-config.js", template_text("config/ai-strategy-local-config.js", "window.__AI_STRATEGY_LOCAL_CONFIG__ = {};\n"))
    write_text(config_dir / "模型配置说明.md", template_text("config/模型配置说明.md", ""))
    proxy_dir = site.parent / "scripts"
    for script_name in (
        "serve_basic_data_site.py",
        "ai_strategy_codex_proxy.mjs",
        "start_ai_strategy_codex_proxy.ps1",
        "start_ai_strategy_codex_proxy.cmd",
        "install_ai_strategy_codex_proxy_startup.ps1",
        "install_ai_strategy_codex_proxy_startup.cmd",
        "uninstall_ai_strategy_codex_proxy_startup.ps1",
        "uninstall_ai_strategy_codex_proxy_startup.cmd",
    ):
        source = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / script_name
        if source.exists():
            write_text(proxy_dir / script_name, source.read_text(encoding="utf-8-sig"))


def slim_ai_topic_evidence_pack(pack: dict[str, Any]) -> dict[str, Any]:
    ai_themes = [theme for theme in (pack.get("themes") or []) if theme.get("id") == "ai_core"]
    return {
        "version": pack.get("version"),
        "generatedAt": pack.get("generatedAt"),
        "dataUpdatedTo": pack.get("dataUpdatedTo"),
        "window": pack.get("window"),
        "sourceEntityIndex": pack.get("sourceEntityIndex"),
        "themes": [
            {
                "id": theme.get("id"),
                "name": theme.get("name"),
                "threshold": theme.get("threshold"),
                "summary": theme.get("summary"),
                "logic": theme.get("logic"),
                "selected": theme.get("selected") or [],
                "points": theme.get("points") or [],
                "fundEvidence": theme.get("fundEvidence") or [],
            }
            for theme in ai_themes
        ],
    }


def write_topic_analysis_pack(site: Path, db_path: Path) -> None:
    from build_topic_analysis_pack import build_pack, js_assignment, write_split_topic_packs

    pack = build_pack(db_path=db_path, site_dir=site)
    js_assignment(site / "data" / "topic_analysis_pack.js", "window.__BASIC_TOPIC_ANALYSIS_PACK__", pack)
    write_split_topic_packs(site, pack)
    js_assignment(
        site / "data" / "ai_topic_evidence_pack.js",
        "window.__BASIC_AI_TOPIC_EVIDENCE_PACK__",
        slim_ai_topic_evidence_pack(pack),
    )
    themes = pack.get("themes") or []
    selected = 0
    ai_theme = next((theme for theme in themes if theme.get("id") == "ai_core"), themes[0] if themes else {})
    if ai_theme:
        selected = ((ai_theme.get("summary") or {}).get("入选策略数") or 0)
    log_progress(f"write topic analysis pack: themes={len(themes)}, ai_selected={selected}")


def write_target_profit_analysis_pack(site: Path, db_path: Path) -> None:
    from build_target_profit_analysis_pack import build_pack, js_assignment

    pack = build_pack(db_path=db_path, site_dir=site)
    js_assignment(site / "data" / "target_profit_analysis_pack.js", "window.__BASIC_TARGET_PROFIT_ANALYSIS_PACK__", pack)
    overview = pack.get("overview") or {}
    log_progress(
        "write target profit analysis pack: "
        f"periods={overview.get('目标盈期次数')}, series={overview.get('目标盈系列数')}"
    )


def write_fund_enrichment_pack(site: Path, db_path: Path) -> None:
    script = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_fund_page_enrichment_pack.py"
    fund_pack = site / "data" / "fund_detail_pack.js"
    if not fund_pack.exists():
        log_progress(f"skip fund enrichment pack: missing {fund_pack}")
        return
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(script),
            "--db-path",
            project_arg(db_path),
            "--site-dir",
            project_arg(site),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(f"fund enrichment pack build failed: exit={completed.returncode}")


def write_fund_economic_exposure_pack(site: Path, db_path: Path) -> None:
    script = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "同步基金经济暴露到页面包.py"
    fund_pack = site / "data" / "fund_detail_pack.js"
    if not fund_pack.exists():
        log_progress(f"skip fund economic exposure sync: missing {fund_pack}")
        return
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(script),
            "--db-path",
            project_arg(db_path),
            "--site-dir",
            project_arg(site),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(f"fund economic exposure sync failed: exit={completed.returncode}")


def write_site_files(args: argparse.Namespace, summary: dict[str, Any], context: dict[str, dict[str, Any]], conn: sqlite3.Connection) -> None:
    site = args.site_dir
    log_progress(f"write shell files: {site}")
    write_shell_files(site)
    log_progress("write basic summary packs")
    write_basic_summary_packs(site, summary, write_full=True)
    strategy_count = len(summary.get("strategies") or [])
    summary.clear()
    summary["strategies"] = [None] * strategy_count
    gc.collect()
    log_progress("load detail maps")
    detail_maps = load_detail_maps(conn, args.algorithm_version)
    log_progress("detail maps loaded")
    details_dir = site / "data" / "details"
    details_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    total_details = len(context)
    semantic_details: list[dict[str, Any]] = []
    for index, (strategy_id, ctx) in enumerate(context.items(), start=1):
        filename = f"{safe_filename(strategy_id)}.js"
        expected.add(filename)
        detail = strategy_detail(strategy_id, ctx, detail_maps)
        semantic_details.append(detail)
        write_js_assignment(details_dir / filename, f'window.__BASIC_DATA__.details["{strategy_id}"]', detail)
        if index % 100 == 0 or index == total_details:
            log_progress(f"write detail files: {index}/{total_details}")
    for path in details_dir.glob("*.js"):
        if path.name not in expected:
            path.unlink()
    if isinstance(summary.get("overview"), dict):
        summary["overview"]["数据刷新时间"] = format_beijing_minute()
        write_js_assignment(site / "data" / "basic_summary.js", "window.__BASIC_DATA__.summary", summary)
    semantic_count = write_ai_semantic_index(site, semantic_details)
    log_progress(f"write ai semantic index: holdings={semantic_count}")
    write_topic_analysis_pack(site, args.db_path)
    write_fund_economic_exposure_pack(site, args.db_path)
    log_progress("write site files completed")


def main() -> None:
    args = parse_args()
    if args.static_only:
        write_shell_files(args.site_dir)
        write_basic_summary_packs_from_existing(args.site_dir)
        semantic_count = write_ai_semantic_index_from_detail_files(args.site_dir)
        write_topic_analysis_pack(args.site_dir, args.db_path)
        write_fund_economic_exposure_pack(args.site_dir, args.db_path)
        write_fund_enrichment_pack(args.site_dir, args.db_path)
        print(
            json.dumps(
                {
                    "输出目录": str(args.site_dir),
                    "mode": "static_only",
                    "ai_semantic_holding_rows": semantic_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    conn = connect(args.db_path)
    try:
        log_progress("build payload")
        summary, context = build_payload(conn, args)
        log_progress(f"payload ready: strategies={len(context)}")
        write_site_files(args, summary, context, conn)
    finally:
        conn.close()
    print(
        json.dumps(
            {
                "输出目录": str(args.site_dir),
                "最小发布集入口": str(args.site_dir / "index.html"),
                "策略列表页": str(args.site_dir / "strategies.html"),
                "策略详情页": str(args.site_dir / "strategy.html"),
                "策略数": len(summary["strategies"]),
                "字段口径数": len(FIELD_DICTIONARY),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
