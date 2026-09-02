PRAGMA foreign_keys = ON;

-- 当前可落地版分析层表结构
-- 设计原则：
-- 1. 只覆盖当前已经拿到的数据；
-- 2. 字段名全部使用中文；
-- 3. 把“当前持仓”和“历史调仓”分开，避免语义混淆；
-- 4. 为后续补“底层基金日度收益时间序列”和“可回放的历史持仓时间序列”预留扩展空间。

CREATE TABLE IF NOT EXISTS "渠道信息" (
    "渠道ID" TEXT PRIMARY KEY,
    "渠道名称" TEXT NOT NULL,
    "渠道类型" TEXT,
    "官方站点" TEXT,
    "登录要求" TEXT,
    "备注" TEXT,
    "创建时间" TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "更新时间" TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS "策略信息" (
    "统一策略ID" TEXT PRIMARY KEY,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "策略名称" TEXT NOT NULL,
    "投顾机构" TEXT,
    "策略类型" TEXT,
    "风险等级" TEXT,
    "成立日期" TEXT,
    "建议持有时长" TEXT,
    "起投金额" REAL,
    "投顾费率" TEXT,
    "业绩基准" TEXT,
    "标签JSON" TEXT,
    "策略状态" TEXT,
    "策略描述" TEXT,
    "原始来源URL" TEXT,
    "原始快照ID" TEXT,
    "首次入库时间" TEXT,
    "最近入库时间" TEXT,
    FOREIGN KEY ("渠道ID") REFERENCES "渠道信息"("渠道ID")
);

CREATE UNIQUE INDEX IF NOT EXISTS "idx_策略信息_渠道_策略"
ON "策略信息"("渠道ID", "渠道策略ID");

-- 渠道直接披露的结构化业绩基准成分。指数代码和权重必须保留，不能只把
-- 基准降级成文本后再做模糊匹配，否则同名/近似名称会映射到错误指数。
CREATE TABLE IF NOT EXISTS "策略业绩基准成分" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "指数代码" TEXT NOT NULL,
    "指数名称" TEXT,
    "指数类型" TEXT,
    "权重_百分比" REAL NOT NULL,
    "是否精确拆分" INTEGER NOT NULL DEFAULT 0,
    "置信度" TEXT,
    "原始快照ID" TEXT,
    "最近入库时间" TEXT,
    PRIMARY KEY ("统一策略ID", "指数代码"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略业绩基准成分_渠道_策略"
ON "策略业绩基准成分"("渠道ID", "渠道策略ID");

CREATE TABLE IF NOT EXISTS "策略关系" (
    "子策略ID" TEXT PRIMARY KEY,
    "母策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "关系类型" TEXT NOT NULL,
    "官方业绩策略ID" TEXT,
    "持仓策略ID" TEXT,
    "调仓策略ID" TEXT,
    "置信度" TEXT NOT NULL,
    "置信分" REAL NOT NULL,
    "关系状态" TEXT NOT NULL,
    "证据JSON" TEXT NOT NULL,
    "规则版本" TEXT NOT NULL,
    "连续不一致次数" INTEGER NOT NULL DEFAULT 0,
    "首次识别时间" TEXT NOT NULL,
    "最近复核时间" TEXT NOT NULL,
    FOREIGN KEY ("子策略ID") REFERENCES "策略信息"("统一策略ID"),
    FOREIGN KEY ("母策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略关系_母策略"
ON "策略关系"("母策略ID", "关系状态");

CREATE INDEX IF NOT EXISTS "idx_策略关系_官方业绩"
ON "策略关系"("官方业绩策略ID", "关系状态");

CREATE TABLE IF NOT EXISTS "策略日度业绩" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "交易日期" TEXT NOT NULL,
    "单位净值" REAL,
    "日收益率_百分比" REAL,
    "累计收益率_百分比" REAL,
    "基准收益率_百分比" REAL,
    "指数收益率_百分比" REAL,
    "最大回撤_百分比" REAL,
    "业绩区段名称" TEXT,
    "业绩区段类型" TEXT,
    "原始快照ID" TEXT,
    PRIMARY KEY ("统一策略ID", "交易日期"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略日度业绩_渠道_日期"
ON "策略日度业绩"("渠道ID", "交易日期");

CREATE TABLE IF NOT EXISTS "策略区间业绩" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "统计日期" TEXT NOT NULL,
    "区间代码" TEXT NOT NULL,
    "区间名称" TEXT NOT NULL,
    "策略收益率_百分比" REAL,
    "基准收益率_百分比" REAL,
    "原始快照ID" TEXT,
    PRIMARY KEY ("统一策略ID", "统计日期", "区间代码"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略区间业绩_渠道_统计日期"
ON "策略区间业绩"("渠道ID", "统计日期");

CREATE TABLE IF NOT EXISTS "策略披露风险指标" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "统计日期" TEXT,
    "区间代码" TEXT NOT NULL,
    "区间名称" TEXT,
    "官方收益率_百分比" REAL,
    "官方最大回撤_百分比" REAL,
    "官方波动率_百分比" REAL,
    "官方夏普" REAL,
    "官方基准收益率_百分比" REAL,
    "数据来源字段" TEXT NOT NULL,
    "原始快照ID" TEXT,
    PRIMARY KEY ("统一策略ID", "区间代码", "数据来源字段"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略披露风险指标_渠道_统计日期"
ON "策略披露风险指标"("渠道ID", "统计日期");

CREATE TABLE IF NOT EXISTS "策略当前持仓分组" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "持仓日期" TEXT NOT NULL,
    "披露日期" TEXT,
    "分组名称" TEXT NOT NULL,
    "分组权重_百分比" REAL,
    "基金数量" INTEGER,
    "原始快照ID" TEXT,
    PRIMARY KEY ("统一策略ID", "持仓日期", "分组名称"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE TABLE IF NOT EXISTS "策略当前持仓" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "持仓日期" TEXT NOT NULL,
    "披露日期" TEXT,
    "基金代码" TEXT,
    "基金名称" TEXT NOT NULL,
    "资产类型" TEXT,
    "分组名称" TEXT,
    "基金权重_百分比" REAL,
    "分组权重_百分比" REAL,
    "基金净值" REAL,
    "基金净值日期" TEXT,
    "最新日涨幅_百分比" REAL,
    "操作标记" TEXT,
    "是否精确权重" INTEGER NOT NULL DEFAULT 0,
    "置信度" TEXT,
    "访问级别" TEXT,
    "原始快照ID" TEXT,
    PRIMARY KEY ("统一策略ID", "持仓日期", "基金名称"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略当前持仓_渠道_持仓日期"
ON "策略当前持仓"("渠道ID", "持仓日期");

CREATE INDEX IF NOT EXISTS "idx_策略当前持仓_基金代码"
ON "策略当前持仓"("基金代码");

-- 历史仓位是官方完整快照事实，不与当前持仓或局部交易指令混用。
-- 同一历史快照可关联普通调仓事件，也可来自信号类组合披露的完整调前/调后仓位。
CREATE TABLE IF NOT EXISTS "策略历史持仓" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "历史快照ID" TEXT NOT NULL,
    "持仓日期" TEXT NOT NULL,
    "披露日期" TEXT,
    "快照阶段" TEXT,
    "来源事件ID" TEXT,
    "基金代码" TEXT,
    "基金名称" TEXT NOT NULL,
    "资产类型" TEXT,
    "基金权重_百分比" REAL,
    "是否精确权重" INTEGER NOT NULL DEFAULT 0,
    "置信度" TEXT,
    "访问级别" TEXT,
    "原始记录哈希" TEXT,
    "原始来源URL" TEXT,
    "采集批次ID" TEXT,
    PRIMARY KEY ("统一策略ID", "历史快照ID", "基金名称"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略历史持仓_渠道_持仓日期"
ON "策略历史持仓"("渠道ID", "持仓日期");

CREATE INDEX IF NOT EXISTS "idx_策略历史持仓_策略_持仓日期"
ON "策略历史持仓"("统一策略ID", "持仓日期");

CREATE INDEX IF NOT EXISTS "idx_策略历史持仓_基金代码"
ON "策略历史持仓"("基金代码");

CREATE TABLE IF NOT EXISTS "策略调仓事件" (
    "调仓事件ID" TEXT PRIMARY KEY,
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "调仓日期" TEXT NOT NULL,
    "上次仓位日期" TEXT,
    "本次仓位日期" TEXT,
    "披露日期" TEXT,
    "调仓标题" TEXT,
    "调仓原因" TEXT,
    "上次仓位日期是否推断" INTEGER,
    "事件序号" INTEGER,
    "事件时间" TEXT,
    "载荷类型" TEXT,
    "置信度" TEXT,
    "原始快照ID" TEXT,
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略调仓事件_渠道_调仓日期"
ON "策略调仓事件"("渠道ID", "调仓日期");

CREATE TABLE IF NOT EXISTS "策略调仓明细" (
    "调仓明细ID" TEXT PRIMARY KEY,
    "调仓事件ID" TEXT NOT NULL,
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "调仓日期" TEXT NOT NULL,
    "披露日期" TEXT,
    "调仓标题" TEXT,
    "基金代码" TEXT,
    "基金名称" TEXT NOT NULL,
    "分组名称" TEXT,
    "调前权重_百分比" REAL,
    "调后权重_百分比" REAL,
    "权重变化_百分比" REAL,
    "调仓动作" TEXT,
    "基金代码匹配状态" TEXT,
    "分组调前权重_百分比" REAL,
    "分组调后权重_百分比" REAL,
    "原始快照ID" TEXT,
    FOREIGN KEY ("调仓事件ID") REFERENCES "策略调仓事件"("调仓事件ID"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_策略调仓明细_渠道_调仓日期"
ON "策略调仓明细"("渠道ID", "调仓日期");

CREATE INDEX IF NOT EXISTS "idx_策略调仓明细_基金代码"
ON "策略调仓明细"("基金代码");

-- 发车/买卖信号是局部交易指令，不等同于普通组合完整调仓。
-- 指令比例与组合存量权重分列保存，避免把新增资金分配比例误作仓位。
CREATE TABLE IF NOT EXISTS "信号策略事件" (
    "信号事件ID" TEXT PRIMARY KEY,
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "投顾机构" TEXT,
    "信号日期" TEXT,
    "信号时间" TEXT,
    "信号标题" TEXT,
    "信号原因" TEXT,
    "信号摘要" TEXT,
    "预计确认日" TEXT,
    "买入模式" TEXT,
    "买入金额" REAL,
    "转换模式" TEXT,
    "是否精确调前仓位" INTEGER,
    "是否精确调后仓位" INTEGER,
    "调前权重合计_百分比" REAL,
    "调后权重合计_百分比" REAL,
    "官方换手率_百分比" REAL,
    "原始事件ID" TEXT,
    "原始信号ID" TEXT,
    "访问级别" TEXT,
    "置信度" TEXT,
    "原始快照路径" TEXT,
    "原始事件序号" INTEGER,
    "指令数" INTEGER,
    "买入指令数" INTEGER,
    "卖出指令数" INTEGER,
    "加仓指令数" INTEGER,
    "减仓指令数" INTEGER,
    "净买入权重_百分点" REAL,
    "总调整强度_百分点" REAL,
    "可评价指令数_1月" INTEGER,
    "胜率_1月" REAL,
    "加权方向收益_1月" REAL,
    "可评价指令数_3月" INTEGER,
    "胜率_3月" REAL,
    "加权方向收益_3月" REAL,
    "可评价指令数_6月" INTEGER,
    "胜率_6月" REAL,
    "加权方向收益_6月" REAL,
    "可评价指令数_1年" INTEGER,
    "胜率_1年" REAL,
    "加权方向收益_1年" REAL,
    "信号评价结论" TEXT,
    "生成时间" TEXT,
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_信号策略事件_渠道_日期"
ON "信号策略事件"("渠道ID", "信号日期");

CREATE TABLE IF NOT EXISTS "信号策略基金指令" (
    "信号指令ID" TEXT PRIMARY KEY,
    "信号事件ID" TEXT NOT NULL,
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "信号日期" TEXT,
    "信号时间" TEXT,
    "基金代码" TEXT,
    "基金名称" TEXT,
    "分组名称" TEXT,
    "天天基金资产类型" TEXT,
    "指令方向" TEXT,
    "调前权重_百分比" REAL,
    "调后权重_百分比" REAL,
    "权重变化_百分点" REAL,
    "指令强度_百分点" REAL,
    "指令金额" REAL,
    "新增资金分配比例_百分比" REAL,
    "指令比例口径" TEXT,
    "组合权重来源" TEXT,
    "目标基金代码" TEXT,
    "目标基金名称" TEXT,
    "原始动作文本" TEXT,
    "原始记录哈希" TEXT,
    "置信度" TEXT,
    "原始动作码" INTEGER,
    "基金收益率_1月" REAL,
    "方向收益_1月" REAL,
    "评价_1月" TEXT,
    "收益开始日期_1月" TEXT,
    "收益结束日期_1月" TEXT,
    "基金收益率_3月" REAL,
    "方向收益_3月" REAL,
    "评价_3月" TEXT,
    "收益开始日期_3月" TEXT,
    "收益结束日期_3月" TEXT,
    "基金收益率_6月" REAL,
    "方向收益_6月" REAL,
    "评价_6月" TEXT,
    "收益开始日期_6月" TEXT,
    "收益结束日期_6月" TEXT,
    "基金收益率_1年" REAL,
    "方向收益_1年" REAL,
    "评价_1年" TEXT,
    "收益开始日期_1年" TEXT,
    "收益结束日期_1年" TEXT,
    "数据状态" TEXT,
    "生成时间" TEXT,
    FOREIGN KEY ("信号事件ID") REFERENCES "信号策略事件"("信号事件ID"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

CREATE INDEX IF NOT EXISTS "idx_信号策略基金指令_渠道_日期"
ON "信号策略基金指令"("渠道ID", "信号日期");

CREATE INDEX IF NOT EXISTS "idx_信号策略基金指令_基金代码"
ON "信号策略基金指令"("基金代码");

CREATE TABLE IF NOT EXISTS "基金信息" (
    "基金代码" TEXT PRIMARY KEY,
    "基金名称" TEXT NOT NULL,
    "基金公司" TEXT,
    "基金类型" TEXT,
    "跟踪指数" TEXT,
    "主题标签JSON" TEXT,
    "最新净值" REAL,
    "最新净值日期" TEXT,
    "基金状态" TEXT,
    "数据来源" TEXT,
    "最近更新时间" TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS "基金名称映射" (
    "映射名称" TEXT PRIMARY KEY,
    "基金代码" TEXT NOT NULL,
    "标准基金名称" TEXT,
    "匹配方式" TEXT NOT NULL,
    "匹配来源" TEXT NOT NULL,
    "置信度" TEXT NOT NULL,
    "更新时间" TEXT NOT NULL,
    FOREIGN KEY ("基金代码") REFERENCES "基金信息"("基金代码")
);

CREATE TABLE IF NOT EXISTS "数据来源清单" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT NOT NULL,
    "文件类型" TEXT NOT NULL,
    "文件路径" TEXT NOT NULL,
    "采集批次ID" TEXT,
    "采集时间" TEXT,
    PRIMARY KEY ("统一策略ID", "文件类型"),
    FOREIGN KEY ("统一策略ID") REFERENCES "策略信息"("统一策略ID")
);

-- 分析常用视图：策略横截面概览
CREATE VIEW IF NOT EXISTS "视图_策略横截面概览" AS
SELECT
    s."统一策略ID",
    s."渠道ID",
    c."渠道名称",
    s."投顾机构",
    s."策略名称",
    s."策略类型",
    s."风险等级",
    s."成立日期",
    s."建议持有时长",
    s."起投金额",
    s."投顾费率",
    s."业绩基准"
FROM "策略信息" s
LEFT JOIN "渠道信息" c
    ON s."渠道ID" = c."渠道ID";

-- 分析常用视图：当前持仓风格分析底表
CREATE VIEW IF NOT EXISTS "视图_当前持仓分析底表" AS
SELECT
    h."统一策略ID",
    s."策略名称",
    h."渠道ID",
    c."渠道名称",
    s."投顾机构",
    h."持仓日期",
    h."披露日期",
    h."基金代码",
    h."基金名称",
    h."资产类型",
    h."分组名称",
    h."基金权重_百分比",
    h."分组权重_百分比",
    h."是否精确权重",
    f."基金公司",
    f."基金类型",
    f."跟踪指数"
FROM "策略当前持仓" h
LEFT JOIN "策略信息" s
    ON h."统一策略ID" = s."统一策略ID"
LEFT JOIN "渠道信息" c
    ON h."渠道ID" = c."渠道ID"
LEFT JOIN "基金信息" f
    ON h."基金代码" = f."基金代码";

-- 分析常用视图：官方调仓分析底表
CREATE VIEW IF NOT EXISTS "视图_官方调仓分析底表" AS
SELECT
    d."调仓明细ID",
    d."调仓事件ID",
    d."统一策略ID",
    s."策略名称",
    d."渠道ID",
    c."渠道名称",
    s."投顾机构",
    e."上次仓位日期",
    e."本次仓位日期",
    d."调仓日期",
    d."披露日期",
    d."基金代码",
    d."基金名称",
    d."分组名称",
    d."调前权重_百分比",
    d."调后权重_百分比",
    d."权重变化_百分比",
    d."调仓动作",
    d."基金代码匹配状态"
FROM "策略调仓明细" d
LEFT JOIN "策略调仓事件" e
    ON d."调仓事件ID" = e."调仓事件ID"
LEFT JOIN "策略信息" s
    ON d."统一策略ID" = s."统一策略ID"
LEFT JOIN "渠道信息" c
    ON d."渠道ID" = c."渠道ID";

CREATE TABLE IF NOT EXISTS "基金日度净值" (
    "基金代码" TEXT NOT NULL,
    "交易日期" TEXT NOT NULL,
    "基金名称" TEXT,
    "基金类型" TEXT,
    "基金公司" TEXT,
    "净值口径" TEXT NOT NULL,
    "单位净值" REAL,
    "累计净值" REAL,
    "日收益率_百分比" REAL,
    "每万份收益" REAL,
    "七日年化收益率_百分比" REAL,
    "净值图分红送配" TEXT,
    "是否货币基金" INTEGER NOT NULL DEFAULT 0,
    "数据来源" TEXT NOT NULL,
    "原始净值快照ID" TEXT,
    "采集时间" TEXT NOT NULL,
    PRIMARY KEY ("基金代码", "交易日期"),
    FOREIGN KEY ("基金代码") REFERENCES "基金信息"("基金代码")
);

CREATE INDEX IF NOT EXISTS "idx_基金日度净值_交易日期"
ON "基金日度净值"("交易日期");

CREATE INDEX IF NOT EXISTS "idx_基金日度净值_基金类型_日期"
ON "基金日度净值"("基金类型", "交易日期");

CREATE TABLE IF NOT EXISTS "基金季报原始快照" (
  "原始快照ID" TEXT PRIMARY KEY,
  "基金代码" TEXT NOT NULL,
  "数据类型" TEXT NOT NULL,
  "数据来源" TEXT NOT NULL,
  "来源URL" TEXT NOT NULL,
  "报告期" TEXT,
  "抓取时间" TEXT NOT NULL,
  "HTTP状态" INTEGER,
  "内容哈希" TEXT NOT NULL,
  "原始路径" TEXT NOT NULL,
  "解析状态" TEXT NOT NULL,
  "错误信息" TEXT
);

CREATE TABLE IF NOT EXISTS "基金季度资产配置" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "披露日期" TEXT,
  "股票占比_百分比" REAL,
  "债券占比_百分比" REAL,
  "现金占比_百分比" REAL,
  "基金占比_百分比" REAL,
  "商品占比_百分比" REAL,
  "存托凭证占比_百分比" REAL,
  "其他占比_百分比" REAL,
  "净资产_亿元" REAL,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "数据来源")
);

CREATE TABLE IF NOT EXISTS "股票行业映射" (
  "股票代码" TEXT PRIMARY KEY,
  "股票名称" TEXT,
  "市场代码" TEXT,
  "东财行业" TEXT,
  "行业一级" TEXT,
  "行业二级" TEXT,
  "地区板块" TEXT,
  "数据来源" TEXT NOT NULL,
  "更新时间" TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "基金季度股票持仓" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "股票代码" TEXT NOT NULL,
  "股票名称" TEXT,
  "市场代码" TEXT,
  "占基金净值比例_百分比" REAL,
  "持股数_万股" REAL,
  "持仓市值_万元" REAL,
  "行业一级" TEXT,
  "行业二级" TEXT,
  "行业来源" TEXT,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "股票代码", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金季度债券持仓" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "债券代码" TEXT NOT NULL,
  "债券名称" TEXT,
  "占基金净值比例_百分比" REAL,
  "持债数量" REAL,
  "持仓市值_万元" REAL,
  "债券类型" TEXT,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "债券代码", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金季度行业配置" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "行业一级" TEXT NOT NULL,
  "占基金净值比例_百分比" REAL,
  "股票持仓样本数" INTEGER,
  "数据来源" TEXT NOT NULL,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "行业一级", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金分类快照" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "披露日期" TEXT,
  "基金名称" TEXT,
  "基金公司" TEXT,
  "基金类型" TEXT,
  "二级分类" TEXT,
  "资产暴露JSON" TEXT,
  "行业暴露JSON" TEXT,
  "主题标签JSON" TEXT,
  "分类来源" TEXT NOT NULL,
  "是否估算" INTEGER NOT NULL DEFAULT 0,
  "覆盖状态" TEXT NOT NULL,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期")
);

CREATE TABLE IF NOT EXISTS "基金穿透数据质量" (
  "运行ID" TEXT NOT NULL,
  "指标名" TEXT NOT NULL,
  "指标值" REAL,
  "指标文本" TEXT,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("运行ID", "指标名")
);

CREATE INDEX IF NOT EXISTS "idx_基金季报原始快照_基金类型时间"
ON "基金季报原始快照"("基金代码", "数据类型", "抓取时间");

CREATE INDEX IF NOT EXISTS "idx_基金季度资产配置_报告期"
ON "基金季度资产配置"("报告期");

CREATE INDEX IF NOT EXISTS "idx_基金季度股票持仓_报告期"
ON "基金季度股票持仓"("报告期");

CREATE INDEX IF NOT EXISTS "idx_基金分类快照_基金代码报告期"
ON "基金分类快照"("基金代码", "报告期");

CREATE TABLE IF NOT EXISTS "基金分红送配" (
  "基金代码" TEXT NOT NULL,
  "权益登记日" TEXT NOT NULL,
    "除息日" TEXT,
    "基金名称" TEXT,
    "年份" TEXT,
    "每份分红" TEXT NOT NULL,
    "分红发放日" TEXT,
    "数据来源" TEXT NOT NULL,
    "原始分红快照ID" TEXT,
    "采集时间" TEXT NOT NULL,
    PRIMARY KEY ("基金代码", "权益登记日", "每份分红"),
    FOREIGN KEY ("基金代码") REFERENCES "基金信息"("基金代码")
);

CREATE INDEX IF NOT EXISTS "idx_基金分红送配_除息日"
ON "基金分红送配"("除息日");

CREATE TABLE IF NOT EXISTS "基金净值概况" (
    "基金代码" TEXT PRIMARY KEY,
    "基金名称" TEXT,
    "基金类型" TEXT,
    "基金公司" TEXT,
    "净值口径" TEXT NOT NULL,
    "是否货币基金" INTEGER NOT NULL DEFAULT 0,
    "历史起始日期" TEXT,
    "历史结束日期" TEXT,
    "历史记录数" INTEGER NOT NULL DEFAULT 0,
    "分红事件数" INTEGER NOT NULL DEFAULT 0,
    "最新单位净值" REAL,
    "最新累计净值" REAL,
    "最新日收益率_百分比" REAL,
    "最新每万份收益" REAL,
    "最新七日年化收益率_百分比" REAL,
    "数据来源" TEXT NOT NULL,
    "原始净值快照ID" TEXT,
    "原始分红快照ID" TEXT,
    "最近采集时间" TEXT NOT NULL,
    FOREIGN KEY ("基金代码") REFERENCES "基金信息"("基金代码")
);

CREATE TABLE IF NOT EXISTS "策略模拟净值" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "交易日期" TEXT NOT NULL,
    "模拟单位净值" REAL NOT NULL,
    "日收益率_百分比" REAL NOT NULL,
    "累计收益率_百分比" REAL NOT NULL,
    "最大回撤_百分比" REAL NOT NULL,
    "费前单位净值" REAL,
    "费前日收益率_百分比" REAL,
    "费前累计收益率_百分比" REAL,
    "费前最大回撤_百分比" REAL,
    "模拟总资产_元" REAL,
    "费前总资产_元" REAL,
    "当日投顾费_元" REAL,
    "累计投顾费_元" REAL,
    "投顾费率_年化_百分比" REAL,
    "初始资产_元" REAL,
    "调仓事件ID" TEXT,
    "调仓日期" TEXT,
    "区间序号" INTEGER,
    "成分基金数" INTEGER,
    "权重和_百分比" REAL,
    "现金权重_百分比" REAL,
    "算法版本" TEXT NOT NULL,
    "质量等级" TEXT NOT NULL,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("统一策略ID", "交易日期", "算法版本")
);

CREATE INDEX IF NOT EXISTS "idx_策略模拟净值_日期"
ON "策略模拟净值"("交易日期");

CREATE INDEX IF NOT EXISTS "idx_策略模拟净值_渠道_日期"
ON "策略模拟净值"("渠道ID", "交易日期");

CREATE TABLE IF NOT EXISTS "策略模拟净值区间" (
    "统一策略ID" TEXT NOT NULL,
    "区间序号" INTEGER NOT NULL,
    "算法版本" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "调仓事件ID" TEXT,
    "调仓日期" TEXT,
    "下一调仓日期" TEXT,
    "区间开始日期" TEXT,
    "区间结束日期" TEXT,
    "区间结束类型" TEXT,
    "区间是否有效" INTEGER NOT NULL DEFAULT 0,
    "是否纳入模拟" INTEGER NOT NULL,
    "质量等级" TEXT NOT NULL,
    "问题类型" TEXT,
    "问题说明" TEXT,
    "修复说明" TEXT,
    "明细行数" INTEGER,
    "正权重明细行数" INTEGER,
    "唯一基金数" INTEGER,
    "缺失代码数" INTEGER,
    "重复基金代码数" INTEGER,
    "重复权重行数" INTEGER,
    "权重和_百分比" REAL,
    "归一化倍数" REAL,
    "缺净值基金数" INTEGER,
    "起始覆盖不足基金数" INTEGER,
    "结束覆盖不足基金数" INTEGER,
    "区间交易日数" INTEGER,
    "缺失日收益填补点数" INTEGER,
    "区间收益率_百分比" REAL,
    "区间费前收益率_百分比" REAL,
    "区间投顾费_元" REAL,
    "区间费率拖累_百分点" REAL,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("统一策略ID", "区间序号", "算法版本")
);

CREATE INDEX IF NOT EXISTS "idx_策略模拟净值区间_质量"
ON "策略模拟净值区间"("算法版本", "是否纳入模拟", "区间是否有效", "质量等级");

-- 调仓质量事实必须在每次策略模拟净值重建后原子重建。
-- 严格以当前调仓事件 ID 为主键，避免增量入库后继续引用旧事件 ID。
CREATE TABLE IF NOT EXISTS "调仓质量事件分析" (
    "调仓事件ID" TEXT PRIMARY KEY,
    "统一策略ID" TEXT NOT NULL,
    "策略名称" TEXT,
    "投顾机构" TEXT,
    "渠道ID" TEXT NOT NULL,
    "调仓日期" TEXT,
    "下次调仓日期" TEXT,
    "区间结束锚点日期" TEXT,
    "区间结束是否封闭" INTEGER NOT NULL DEFAULT 0,
    "评估层级" TEXT NOT NULL,
    "评估状态" TEXT NOT NULL,
    "评估说明" TEXT,
    "调仓明细行数" INTEGER NOT NULL DEFAULT 0,
    "已补码行数" INTEGER NOT NULL DEFAULT 0,
    "未补码行数" INTEGER NOT NULL DEFAULT 0,
    "有净值覆盖行数" INTEGER NOT NULL DEFAULT 0,
    "调前权重和_百分比" REAL,
    "调后权重和_百分比" REAL,
    "调前仓位收益率_百分比" REAL,
    "调后仓位收益率_百分比" REAL,
    "调仓超额_百分比" REAL,
    "胜负" TEXT,
    "结果评价" TEXT,
    "买入加仓收益率_百分比" REAL,
    "卖出减仓收益率_百分比" REAL,
    "方向性超额_百分比" REAL,
    "策略区间收益率_百分比" REAL,
    "最优贡献基金" TEXT,
    "最差贡献基金" TEXT,
    "调仓标题" TEXT,
    "调仓原因" TEXT
);

CREATE INDEX IF NOT EXISTS "idx_调仓质量事件分析_策略日期"
ON "调仓质量事件分析"("统一策略ID", "调仓日期");

CREATE TABLE IF NOT EXISTS "调仓质量基金明细" (
    "调仓明细分析ID" TEXT PRIMARY KEY,
    "调仓事件ID" TEXT NOT NULL,
    "统一策略ID" TEXT NOT NULL,
    "策略名称" TEXT,
    "渠道ID" TEXT NOT NULL,
    "调仓日期" TEXT,
    "基金代码_原始" TEXT,
    "基金代码_分析" TEXT,
    "基金代码解析状态" TEXT NOT NULL,
    "基金名称" TEXT,
    "调仓动作_分析" TEXT,
    "调前权重_百分比" REAL,
    "调后权重_百分比" REAL,
    "基金区间收益率_百分比" REAL,
    "调前收益贡献_百分比" REAL,
    "调后收益贡献_百分比" REAL,
    "调仓贡献变化_百分比" REAL,
    "评估层级" TEXT NOT NULL,
    "基金收益起始日期" TEXT,
    "基金收益结束日期" TEXT
);

CREATE INDEX IF NOT EXISTS "idx_调仓质量基金明细_事件"
ON "调仓质量基金明细"("调仓事件ID");

CREATE TABLE IF NOT EXISTS "调仓质量策略汇总" (
    "统一策略ID" TEXT PRIMARY KEY,
    "策略名称" TEXT,
    "投顾机构" TEXT,
    "渠道ID" TEXT NOT NULL,
    "历史调仓事件数" INTEGER NOT NULL DEFAULT 0,
    "有效调仓事件数" INTEGER NOT NULL DEFAULT 0,
    "全组合有效事件数" INTEGER NOT NULL DEFAULT 0,
    "调仓子集有效事件数" INTEGER NOT NULL DEFAULT 0,
    "不可评估事件数" INTEGER NOT NULL DEFAULT 0,
    "胜事件数" INTEGER NOT NULL DEFAULT 0,
    "负事件数" INTEGER NOT NULL DEFAULT 0,
    "平事件数" INTEGER NOT NULL DEFAULT 0,
    "胜率_有效事件_百分比" REAL,
    "胜率_全组合事件_百分比" REAL,
    "平均调仓超额_百分比" REAL,
    "中位数调仓超额_百分比" REAL,
    "累计调仓超额_百分比" REAL,
    "平均正超额_百分比" REAL,
    "平均负超额_百分比" REAL,
    "赔率" REAL,
    "最近一次调仓日期" TEXT,
    "最近一次调仓评价" TEXT,
    "完整性说明" TEXT,
    "历史评价" TEXT
);

CREATE TABLE IF NOT EXISTS "调仓质量完整性概览" (
    "对象类型" TEXT NOT NULL,
    "对象ID" TEXT NOT NULL,
    "渠道ID" TEXT,
    "策略名称" TEXT,
    "指标名称" TEXT NOT NULL,
    "指标值" REAL,
    "指标文本" TEXT,
    PRIMARY KEY ("对象类型", "对象ID", "指标名称")
);

CREATE TABLE IF NOT EXISTS "调仓质量构建状态" (
    "构建ID" TEXT PRIMARY KEY,
    "算法版本" TEXT NOT NULL,
    "生成时间" TEXT NOT NULL,
    "排除渠道JSON" TEXT NOT NULL,
    "源事件数" INTEGER NOT NULL,
    "质量事件数" INTEGER NOT NULL,
    "源最新调仓日期" TEXT,
    "质量最新调仓日期" TEXT,
    "基金净值最新日期" TEXT,
    "缺失事件数" INTEGER NOT NULL,
    "孤立事件数" INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS "策略模拟净值质量" (
    "统一策略ID" TEXT NOT NULL,
    "算法版本" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "投顾机构" TEXT,
    "原始调仓事件数" INTEGER NOT NULL,
    "折叠后调仓日期数" INTEGER NOT NULL,
    "同日重复事件数" INTEGER NOT NULL,
    "同日不同仓位日期数" INTEGER NOT NULL,
    "有效区间数" INTEGER NOT NULL,
    "无效区间数" INTEGER NOT NULL,
    "是否纳入模拟" INTEGER NOT NULL,
    "质量等级" TEXT NOT NULL,
    "首个问题日期" TEXT,
    "首个问题类型" TEXT,
    "问题说明" TEXT,
    "修复说明" TEXT,
    "模拟起始日期" TEXT,
    "模拟结束日期" TEXT,
    "模拟交易日数" INTEGER,
    "模拟区间年数" REAL,
    "初始资产_元" REAL,
    "投顾费率_年化_百分比" REAL,
    "缺失投顾费率按0处理" INTEGER,
    "模拟期末总资产_元" REAL,
    "模拟单位净值_期末" REAL,
    "模拟费前单位净值_期末" REAL,
    "模拟累计投顾费_元" REAL,
    "模拟投顾费拖累_百分点" REAL,
    "模拟累计收益率_百分比" REAL,
    "模拟费前累计收益率_百分比" REAL,
    "模拟年化收益率_百分比" REAL,
    "模拟最大回撤_百分比" REAL,
    "模拟波动率_年化_百分比" REAL,
    "模拟夏普_年化无风险0" REAL,
    "官方可比记录数" INTEGER,
    "官方起始日期" TEXT,
    "官方结束日期" TEXT,
    "官方区间收益率_百分比" REAL,
    "模拟同区间收益率_百分比" REAL,
    "模拟官方收益差_百分点" REAL,
    "模拟费前同区间收益率_百分比" REAL,
    "模拟费前官方收益差_百分点" REAL,
    "官方更接近口径" TEXT,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("统一策略ID", "算法版本")
);

CREATE INDEX IF NOT EXISTS "idx_策略模拟净值质量_纳入"
ON "策略模拟净值质量"("算法版本", "是否纳入模拟", "渠道ID");

CREATE TABLE IF NOT EXISTS "策略模拟净值校验" (
    "统一策略ID" TEXT NOT NULL,
    "算法版本" TEXT NOT NULL,
    "校验项" TEXT NOT NULL,
    "校验状态" TEXT NOT NULL,
    "校验数值" REAL,
    "阈值" REAL,
    "问题说明" TEXT,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("统一策略ID", "算法版本", "校验项")
);

CREATE TABLE IF NOT EXISTS "策略官方偏差分析" (
    "统一策略ID" TEXT NOT NULL,
    "算法版本" TEXT NOT NULL,
    "渠道ID" TEXT NOT NULL,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "质量等级" TEXT,
    "官方可比记录数" INTEGER,
    "官方起始日期" TEXT,
    "官方结束日期" TEXT,
    "官方区间收益率_百分比" REAL,
    "费后模拟同区间收益率_百分比" REAL,
    "费前模拟同区间收益率_百分比" REAL,
    "费后官方偏差_百分点" REAL,
    "费前官方偏差_百分点" REAL,
    "费后官方绝对偏差_百分点" REAL,
    "费前官方绝对偏差_百分点" REAL,
    "费前相对费后改善_百分点" REAL,
    "官方更接近口径" TEXT,
    "偏差方向" TEXT,
    "推断原因" TEXT,
    "优化建议" TEXT,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("统一策略ID", "算法版本")
);

CREATE TABLE IF NOT EXISTS "渠道官方偏差分析" (
    "渠道ID" TEXT NOT NULL,
    "算法版本" TEXT NOT NULL,
    "可比策略数" INTEGER NOT NULL,
    "官方样本充足策略数" INTEGER NOT NULL,
    "费后更接近策略数" INTEGER NOT NULL,
    "费前更接近策略数" INTEGER NOT NULL,
    "费前费后接近策略数" INTEGER NOT NULL,
    "费后绝对偏差均值_百分点" REAL,
    "费后绝对偏差中位数_百分点" REAL,
    "费前绝对偏差均值_百分点" REAL,
    "费前绝对偏差中位数_百分点" REAL,
    "最优口径绝对偏差均值_百分点" REAL,
    "费前相对费后平均改善_百分点" REAL,
    "推荐官方口径" TEXT,
    "渠道算法判断" TEXT,
    "下一步优化建议" TEXT,
    "生成时间" TEXT NOT NULL,
    PRIMARY KEY ("渠道ID", "算法版本")
);

CREATE TABLE IF NOT EXISTS "策略官方算法候选评估" (
    "统一策略ID" TEXT NOT NULL,
    "渠道ID" TEXT,
    "渠道策略ID" TEXT,
    "策略名称" TEXT,
    "算法版本" TEXT NOT NULL,
    "候选算法ID" TEXT NOT NULL,
    "候选算法名称" TEXT,
    "调仓生效口径" TEXT,
    "费用口径" TEXT,
    "官方可比记录数" INTEGER,
    "官方起始日期" TEXT,
    "官方结束日期" TEXT,
    "官方区间收益率_百分比" REAL,
    "模拟同区间收益率_百分比" REAL,
    "模拟官方收益差_百分点" REAL,
    "绝对偏差_百分点" REAL,
    "是否本策略最优" INTEGER,
    "生成时间" TEXT,
    PRIMARY KEY ("统一策略ID", "算法版本", "候选算法ID")
);

CREATE TABLE IF NOT EXISTS "渠道官方算法候选评估" (
    "渠道ID" TEXT NOT NULL,
    "算法版本" TEXT NOT NULL,
    "候选算法ID" TEXT NOT NULL,
    "候选算法名称" TEXT,
    "调仓生效口径" TEXT,
    "费用口径" TEXT,
    "可比策略数" INTEGER,
    "胜出策略数" INTEGER,
    "绝对偏差均值_百分点" REAL,
    "绝对偏差中位数_百分点" REAL,
    "绝对偏差P90_百分点" REAL,
    "较基准改善_百分点" REAL,
    "是否渠道最优" INTEGER,
    "渠道算法判断" TEXT,
    "下一步优化建议" TEXT,
    "生成时间" TEXT,
    PRIMARY KEY ("渠道ID", "算法版本", "候选算法ID")
);
