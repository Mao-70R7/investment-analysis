# 招商基金公开采集 Skill

## 目标

采集招商基金/招财乐投顾官网 SSR 页面中公开披露的精选策略卡片、FAQ 和服务入口，并记录未公开的基金级数据缺口。

## 复跑命令

```powershell
python .\scripts\collect_official_apps_public.py --apps cmfchina
```

## 公开来源

- `https://www.cmfchina.com/web/investmentadvisory/index.html`
- 页面引用的 Nuxt/服务端渲染公开数据。

公开页面中观察到的服务端路径包括：

- `/ws-business-server/otherBusin/getTrustProductList`
- `/ws-base-server/publish/publish/getArticleContentList`
- `/ws-business-server/common/picList`

## 标准输出

- `official_apps/cmfchina/outputs/strategy_master.csv`
- `official_apps/cmfchina/outputs/app_public_entry.csv`
- `official_apps/cmfchina/outputs/latest_summary.json`
- `official_apps/cmfchina/outputs/coverage_check.json`
- `official_apps/cmfchina/outputs/source_inventory.json`
- `official_apps/cmfchina/outputs/raw_manifest.json`

## 当前结果

最近一次成功运行：`20260523T224046+0800`。

- 公开精选策略：4 个，分别为乐钱包、乐稳健、乐均衡、乐进取。
- 当前基金级持仓：0 行。
- 调仓事件：0 个。

## 校验

```powershell
Get-Content -Raw -Encoding UTF8 .\official_apps\cmfchina\outputs\coverage_check.json
```

`strategy_master_ok` 应为 `true`，`holding_penetration_status` 应为 `blocked_app_or_encrypted_api_required`。

## 已知口径

官网 SSR 数据只含精选策略卡片、FAQ 和服务入口。基金级持仓、业绩曲线和调仓记录没有在公开 HTML 中披露；底层 API 有加密/签名封装，当前公开采集不模拟 App 加密请求。
