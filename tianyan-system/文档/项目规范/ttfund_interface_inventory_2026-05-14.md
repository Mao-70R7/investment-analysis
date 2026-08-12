# 天天基金 App 投顾接口清单（2026-05-14）

## 结论

基于 APK 静态分析和 2026-05-14 在线实测，天天基金这条线当前可以稳定拿到的，是：

- 官网公开的 9 个推荐投顾策略目录。
- 每个策略的最新交易日收益快照和多个区间收益指标。
- 策略详情页的 App 路由线索。

当前拿不到或未在匿名接口中暴露的，是：

- 组合日度业绩完整时间序列。
- 基准收益、指数收益。
- 官方调仓事件。
- 当前基金持仓明细。
- 风险等级、建议持有期、费率等深层基础字段。

这部分数据更像是放在 App 运行时动态拉取的 `strategyDetail` 详情页接口里，而不是直接随 APK 离线包公开。

## 实测摘要

- 实测日期：2026-05-14
- 官网投顾页：`https://fund.eastmoney.com/tg/`
- 当前公开策略数：9
- 官网页面日期：2026-05-13
- APK：`data/apk/ttfund_6.8.6_appchina.apk`

## 字段覆盖结论

| 目标数据 | 天天基金匿名公开接口 | 天天基金动态详情页/登录态 | 其他可采集方式 |
| --- | --- | --- | --- |
| 机构 | 可取 | 可补充 | 无需补充 |
| 策略名 | 可取 | 可补充 | 无需补充 |
| 策略类型 | 部分可取 | 可补充 | 机构侧页面可补 |
| 风险等级 | 未发现 | 高概率可取 | 机构侧详情页/H5 |
| 成立时间 | 可取 | 可补充 | 无需补充 |
| 建议持有期 | 未发现 | 高概率可取 | 机构侧详情页/H5 |
| 起投金额 | 部分可取 | 可补充 | 机构侧详情页/H5 |
| 费率 | 未发现 | 高概率可取 | 机构侧详情页/H5 |
| 标签 | 部分可取 | 可补充 | 机构侧详情页/H5 |
| 策略说明 | 部分可取 | 高概率可取 | 机构侧详情页/H5 |
| 组合收益（日最新） | 可取 | 可补充 | 无需补充 |
| 组合收益（日历史序列） | 未发现 | 高概率可取 | 机构侧公开 API |
| 基准收益 | 未发现 | 高概率可取 | 机构侧公开 API |
| 指数收益 | 未发现 | 高概率可取 | 机构侧公开 API |
| 官方调仓事件 | 未发现 | 高概率可取 | 机构侧公开 API / 公告 |
| 当前基金持仓 | 未发现 | 高概率可取 | 机构侧公开 API |
| 基金单位净值/净值日期 | 未发现组合级直出 | 可用持仓基金代码二次补齐 | 基金公开净值接口 |

## 接口清单

### 1. 官网推荐投顾页

- URL: `https://fund.eastmoney.com/tg/`
- 认证：无需登录
- 实测结果：成功，当前 9 个策略
- 可取字段：
  - `TGCODE`（从页面 `data-id` 提取）
  - 策略名
  - 机构名/出品方
  - 页面分组文案，如“追求高收益 / 稳健理财 / 养老储蓄”
  - 起投金额
  - 部分区间收益展示
- 局限：
  - 只是推荐策略页，不是全市场投顾库。
  - 风险等级、费率、建议持有期、完整说明不全。

### 2. 投顾收益快照接口

- URL: `https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/getTGQuoteByFavor`
- 方法：`POST`
- Content-Type: `application/x-www-form-urlencoded`
- 入参示例：

```text
tgCodeWithDateStr=SF1016_2026-05-13,JQNQMI3_2026-05-13,TRPPXDI_2026-05-13
```

- 认证：无需登录
- 实测结果：成功
- 可取字段：
  - `TGCODE`
  - `TGNAME`
  - `LOGO_NAME`
  - `LOGO_URL`
  - `RUN_DATE`
  - `ANNSYL_LN`
  - `SYL_D`
  - `SYL_Z`
  - `SYL_Y`
  - `SYL_3Y`
  - `SYL_6Y`
  - `SYL_JN`
  - `SYL_1N`
  - `SYL_2N`
  - `SYL_3N`
  - `SYL_LN`
  - `ESTABDATE`
  - `SYRQ`
  - `JZRQ`
  - `SALE_DATE`
  - `SALE_END_DATE`
- 关键结论：
  - 这条接口只给“最新交易日快照 + 区间收益”。
  - 日期参数不能回溯历史。把 `SF1016_2019-01-21`、`JQNQMI3_2017-09-05` 传进去，返回的 `SYRQ/JZRQ` 仍然是 `2026-05-13`，只有 `ADD_DATE` 会回填成你传入的日期。
- 不能取到：
  - 日维度完整收益曲线
  - 基准收益
  - 指数收益
  - 调仓事件
  - 当前持仓

### 3. 投顾自选补充接口

- URL: `https://fundts.1234567.com.cn/apphome/show/getTgOptional`
- 方法：`POST`
- Content-Type: `application/json`
- 入参示例：

```json
{}
```

- 认证：未登录可访问
- 实测结果：成功，但未登录返回空数组
- 返回示例特征：

```json
{"data":[],"errorCode":0,"success":true,"totalCount":0}
```

- 用途判断：
  - 这是“用户自选投顾”列表，不是全市场投顾目录。
  - 登录并有自选投顾后，可能返回 `fcode` 等字段，再拼给 `getTGQuoteByFavor` 做收益补充。

### 4. 投顾自选管理接口

- URL 组：
  - `https://fundfavorapi.eastmoney.com/favor/tg/getAll`
  - `https://fundfavorapi.eastmoney.com/favor/tg/getAllWithSetTopStatus`
  - `https://fundfavorapi.eastmoney.com/favor/tg/add`
  - `https://fundfavorapi.eastmoney.com/favor/tg/del`
  - `https://fundfavorapi.eastmoney.com/favor/tg/settop`
  - `https://fundfavorapi.eastmoney.com/favor/tg/updateOrder`
- 认证：需要登录态/权限
- 实测结果：

```json
{"data":null,"errorCode":502,"firstError":"您没有权限","success":false}
```

- 用途判断：
  - 只适合登录态下管理用户自选。
  - 不适合作为匿名全量投顾采集源。

### 5. 策略详情页动态路由

- Deeplink：

```text
fund://mp.1234567.com.cn/weex/funda91a99886abf7e/pages/strategyDetail/index?id={TGCODE}
```

- 已定位用途：
  - 投顾策略详情页
  - 最可能承载深层字段：风险等级、费率、建议持有期、完整策略说明、收益图表、持仓、调仓
- 当前问题：
  - 对应的离线包 `funda91a99886abf7e` 不在 APK 内置资源里。
  - 以下静态路径 2026-05-14 实测都是 `404`：
    - `https://mp.1234567.com.cn/weex/funda91a99886abf7e/pages.json`
    - `https://mp.1234567.com.cn/weex/funda91a99886abf7e.zip`
    - `https://mp.1234567.com.cn/weex/funda91a99886abf7e/pages/strategyDetail/index.js`
- 结论：
  - 详情页大概率是 App 运行时动态拉包。
  - 仅靠离线 APK 静态解包，拿不到详情接口响应。

### 6. 投顾资产/发车详情路由

- Deeplink：

```text
fund://mp.1234567.com.cn/weex/b0381a3b634440379d330b69f09d3f8e/pages/assets/index
fund://mp.1234567.com.cn/weex/b0381a3b634440379d330b69f09d3f8e/pages/departureDetail/index?id={TGCODE}&saleDate={SALE_DATE}
```

- 用途判断：
  - `assets/index` 更像“我的投顾/投顾资产”页。
  - `departureDetail/index` 更像“发车中”详情页。
- 结论：
  - 可能包含持有态数据、买入态数据、发车计划数据。
  - 不属于匿名公开目录接口，需运行 App 再抓。

## 针对你要的 4 类数据，当前能拿多少

### 1. 组合基础信息

当前天天基金匿名侧可直接拿到：

- 机构
- 策略名
- 成立时间
- 起投金额
- 部分标签/分组
- 部分策略说明文案

当前拿不到：

- 风险等级
- 建议持有期
- 费率
- 完整策略说明

建议采集方式：

1. 先用官网页 + `getTGQuoteByFavor` 建基础表。
2. 再从 `strategyDetail` 动态详情页补足。
3. 如果天天基金详情页仍然不直出，就转投顾机构自己的 H5/API。

### 2. 组合日度业绩

当前天天基金匿名侧可直接拿到：

- 最新交易日组合收益：`SYL_D`
- 多个区间收益：`SYL_Z/SYL_Y/SYL_3Y/SYL_6Y/SYL_JN/SYL_1N/SYL_2N/SYL_3N/SYL_LN`
- 最新收益日期：`SYRQ/JZRQ`

当前拿不到：

- 日维度完整时间序列
- 基准收益
- 指数收益

建议采集方式：

1. 抓 `strategyDetail` 页的图表接口。
2. 对已有机构公开源的策略，直接走机构侧接口。

### 3. 官方调仓事件

当前天天基金匿名侧结论：

- 未发现公开接口。
- APK 内可确认有详情页路由，但拿不到离线详情包。

建议采集方式：

1. 运行 App 打开策略详情页，抓调仓模块请求。
2. 优先用机构侧公开调仓接口或公告。
3. 若仍无官方记录，再考虑用持仓快照差分做 `inferred` 结果。

### 4. 当前基金持仓

当前天天基金匿名侧结论：

- 未发现公开接口。
- 组合级持仓明细、基金代码、基金占比没有在匿名接口里返回。

建议采集方式：

1. 抓 `strategyDetail` 页或资产页请求。
2. 从机构侧公开接口补齐组合持仓。
3. 拿到基金代码后，再用基金净值公开接口补齐单位净值和净值日期。

## 其他可采集方式

### 1. 机构侧公开接口

这条线比天天基金本身更重要。天天基金更像聚合展示层，深层数据很可能来自投顾机构自身系统。

本仓库里已验证的一条：

- 中欧财富投顾 / 钱滚滚
  - 对应天天基金策略：`JQNQMI3`
  - 已验证可公开获取：
    - 基础信息
    - 日度收益
    - 净值
    - 当前持仓
    - 官方调仓
  - 见：
    - `docs/zocaifu_feasibility_2026-05-14.md`

### 2. App 动态抓包

如果目标是“尽量从天天基金 App 本身拿全”，下一步应该做的不是继续离线反编译，而是：

1. 安装 APK 到测试机或模拟器。
2. 打开 9 个策略的 `strategyDetail` deeplink。
3. 抓动态下发的 Weex 包和 HTTPS 请求。
4. 优先筛这些关键词：
   - `strategyDetail`
   - `investAdviser`
   - `holding`
   - `position`
   - `rebalance`
   - `ratio`
   - `benchmark`
   - `chart`

## 最终判断

就“天天基金匿名公开接口”本身，当前上限是：

- 能做：推荐投顾目录 + 最新收益快照。
- 不能做：完整日频业绩、基准/指数、官方调仓、当前持仓。

就“天天基金 App 整体可采集性”来说，深层数据仍有机会，但前提是进入动态抓包阶段。只做离线 APK 反编译，拿不到你要的完整 4 类数据。
