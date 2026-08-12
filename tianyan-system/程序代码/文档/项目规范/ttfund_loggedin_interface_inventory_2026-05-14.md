# 天天基金登录态接口/采集方式清单（2026-05-14）

## 结论

登录天天基金 App 后，进入投顾策略详情页，可以稳定从 App 外部缓存直接拿到详情 JSON。  
这条链路已经实测覆盖：

- 组合基础信息的大部分字段
- 组合区间业绩快照和基准快照
- 当前基金持仓明细
- 机构标识、策略标识、财富号标识

当前仍未直接确认到：

- 组合日频完整收益序列
- 基准日频完整收益序列
- 指数日频完整收益序列
- 官方调仓历史事件列表接口

因此，当前最可靠的结论是：

1. 天天基金登录态详情缓存是可直接采集的。
2. 首页登录态缓存可以批量枚举大量 `strategyId`。
3. 深层“日频曲线/官方调仓”接口本轮未从抓包侧稳定恢复出来。

## 已确认入口

### 1. 策略详情页运行时路由

- Activity:
  - `com.eastmoney.android.fund/com.eastmoney.android.libwxcomp.FundWeexActivity`
- 页面包：
  - `appId = funda91a99886abf7e`
- 实测路由样例：
  - `/pages/strategyDetail/index?partnerId=469&id=XQUM09A&showKycPopup=1`

说明：

- `id` 即策略主键，可视作 `strategyId`
- `partnerId` 即投顾机构编码

### 2. 策略详情缓存目录

手机路径：

```text
/sdcard/Android/data/com.eastmoney.android.fund/files/.ttjj_cache/
```

已命中的详情缓存文件样例：

```text
strategyDetailPageDataXQUM09A_funda91a99886abf7e.0
ttfund-layout-cache-advicer-strategy-detail-matter-XQUM09A-datas_66b75339ff4441f597928a381c7a5f1d_funda91a99886abf7e.0
kyc-result-XQUM09A_funda91a99886abf7e.0
```

其中：

- 前两个文件内容一致，都是详情主数据
- 第三个文件仅为 KYC 标记，不是主数据

本地样例文件：

- [strategyDetailPageDataXQUM09A_funda91a99886abf7e.0](</E:/AI工作区/投顾数据处理/data/raw/device_cache/XQUM09A/strategyDetailPageDataXQUM09A_funda91a99886abf7e.0>)
- [ttfund-layout-cache-advicer-strategy-detail-matter-XQUM09A-datas_66b75339ff4441f597928a381c7a5f1d_funda91a99886abf7e.0](</E:/AI工作区/投顾数据处理/data/raw/device_cache/XQUM09A/ttfund-layout-cache-advicer-strategy-detail-matter-XQUM09A-datas_66b75339ff4441f597928a381c7a5f1d_funda91a99886abf7e.0>)

### 3. 投顾首页批量枚举缓存

已命中的首页主缓存文件：

- [layout_tougu-scroll-viewc27d7b9732ddc0510e43075147757bd9_noprefix.0](</E:/AI工作区/投顾数据处理/data/raw/device_cache/fund704db2fb905941/layout_tougu-scroll-viewc27d7b9732ddc0510e43075147757bd9_noprefix.0>)
- [home-vuex_66b75339ff4441f597928a381c7a5f1d.0](</E:/AI工作区/投顾数据处理/data/raw/device_cache/fund704db2fb905941/home-vuex_66b75339ff4441f597928a381c7a5f1d.0>)

实测结果：

- `layout_tougu-scroll-view...` 中可正则提取出至少 `272` 个唯一 `strategyId`
- 文件中同时包含：
  - `strategyId`
  - `partnerId`
  - `strategyName`
  - `styleName`
  - `holdLimit`
  - `shTime`
  - `latestYearProfit`
  - 部分 `skipUrl`
- 文件中还包含 `AdvicerLicense` 区块，可拿到投顾机构编码列表，实测至少 `30` 家机构编码

这意味着：

- 可以先从首页缓存批量拿策略 ID
- 再逐个打开策略详情页
- 再从详情缓存批量落详情数据

## 可采字段清单

### A. 组合基础信息

来源：详情缓存 JSON 的 `tgExtendInfo`

可拿字段：

- 机构：`logoName`
- 策略名：`tgName` / `name`
- 风险等级：`risk`
- 成立天数：`estabed`
- 建议持有期：`investTerm`
- 起投金额：`minBuy`
- 服务费率：`strategyRate` + `provisionType`
- 标签：`label1` / `label2` / `label3`
- 策略说明：
  - `strategy.strategyConcept1`
  - `strategy.strategyConcept2`
  - `strategy.strategyConcept3`
- 基准说明：`basicCalFormulaRemark`
- 机构编码：`partnerId`
- 财富号/机构内部标识：`wealthNo`

说明：

- `estabed` 当前拿到的是“成立天数”，不是原始成立日期
- 如果需要“成立日期”，需要再结合页面展示值、其他缓存，或继续抓真实接口

### B. 组合业绩

来源：`tgExtendInfo.stageListAll` 和 `subtitleParam`

可拿字段：

- 区间：`period`
- 组合收益：`rate`
- 基准收益：`basic`
- 页面主展示收益：
  - `subtitleParam.num1`
  - `subtitleParam.num2`

当前能力边界：

- 这是区间收益快照，不是日频时序
- 本轮未拿到完整日度 `date -> strategy_return / benchmark_return / index_return`

### C. 当前基金持仓

来源：`holdWareHouseInfo`

可拿字段：

- 持仓日期：`holdWareHouseInfo.date`
- 资产类型：`holdTypeList[].type`
- 资产类型占比：`holdTypeList[].rate`
- 基金代码：`fundList[].fundCode`
- 基金名称：`fundList[].fundName`
- 基金占比：`fundList[].rate`
- 单位净值：`fundList[].netAssetValue`
- 净值日期：`fundList[].date`
- 当日涨跌：`fundList[].increaseRate`
- 页面动作标记：`fundList[].operationType`

说明：

- `operationType` 目前可见值如“新增/加仓/减仓”
- 这更像“当前持仓相对上一期的动作标记”，不是完整官方调仓历史

### D. 官方调仓事件

当前状态：

- 本轮未从详情缓存中拿到“调仓事件列表”
- 也未恢复出稳定可复用的“官方调仓接口”

因此以下字段暂未直接确认：

- 调仓日
- 上一仓位日
- 成分基金调仓前后比例

## 匹配你原始需求的覆盖结论

### 1. 组合基础信息

- 已覆盖：
  - 机构
  - 策略名
  - 风险等级
  - 建议持有期
  - 起投金额
  - 费率
  - 标签
  - 策略说明
- 部分覆盖：
  - 成立时间：当前拿到成立天数，未直接拿到原始日期
- 未确认：
  - 策略类型

### 2. 组合日度业绩

- 已覆盖：
  - 区间组合收益
  - 区间基准收益
- 未覆盖：
  - 日维度组合收益序列
  - 日维度基准收益序列
  - 日维度指数收益序列

### 3. 官方调仓事件

- 未覆盖

### 4. 当前基金持仓

- 已覆盖：
  - 基金代码
  - 基金名称
  - 资产类型
  - 基金占比
  - 单位净值
  - 净值日期

## 补充：匿名公开接口

这部分不依赖登录，可作为补充源：

### 1. 推荐投顾目录页

- `https://fund.eastmoney.com/tg/`

可拿：

- 部分策略目录
- 策略名
- 机构名
- 起投金额
- 页面展示收益快照

### 2. 投顾收益快照接口

- `POST https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/getTGQuoteByFavor`

可拿：

- `TGCODE`
- `TGNAME`
- `LOGO_NAME`
- `RUN_DATE`
- `ESTABDATE`
- `SYL_D`
- 多个区间收益字段

限制：

- 返回的是最新收益快照
- 不是完整日频历史曲线

## 推荐采集方案

### 方案 A：登录态缓存采集

适合目标：

- 要覆盖策略详情和当前持仓
- 接受“页面落缓存后再采”

步骤：

1. 登录天天基金 App
2. 进入投顾首页
3. 从首页缓存提取批量 `strategyId`
4. 逐个打开详情页
5. 拉取 `.ttjj_cache` 下对应详情文件
6. 解析 `tgExtendInfo` 和 `holdWareHouseInfo`

### 方案 B：匿名接口 + 登录态缓存混合

适合目标：

- 先快速批量扫目录
- 再用登录态补深层字段

步骤：

1. 匿名接口拿目录和收益快照
2. 登录态缓存补：
   - 风险等级
   - 费率
   - 持有期
   - 标签
   - 策略说明
   - 当前持仓

## 当前最准确的判断

到 2026-05-14 为止，天天基金这条线已经不应再判断为“只能拿目录和收益快照”。  
更准确的结论是：

- 登录态详情缓存已经足以覆盖大部分组合基础信息
- 登录态详情缓存已经足以覆盖当前基金持仓
- 登录态详情缓存只能覆盖区间收益快照，不能替代日频业绩接口
- 官方调仓事件仍然是缺口

相关长文：

- [ttfund_loggedin_cache_findings_2026-05-14.md](</E:/AI工作区/投顾数据处理/docs/ttfund_loggedin_cache_findings_2026-05-14.md>)
- [ttfund_interface_inventory_2026-05-14.md](</E:/AI工作区/投顾数据处理/docs/ttfund_interface_inventory_2026-05-14.md>)
