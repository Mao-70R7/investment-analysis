# 实现位置

正式实现复用 `节点脚本/_共享组件/python_src/advisor_monitor/collectors/official_apps_public.py` 的 `gfsec_fima` 渠道，节点仅负责调度和结果封装。

`gfsec_fima_position_history.py` 是不进入每日 DAG 的手工可复跑分析器。它只读历史原始快照和基金日度净值，生成官方当前模型仓位的观测历史、全部相邻状态差分及明确标注的配置变化候选；不写主库、正式调仓表或页面包。
