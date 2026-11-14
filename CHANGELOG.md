# Changelog

## 0.3.0 — 2026-11-14

- 武大图书馆改为主入口；新增官网数据库导航、打开数据库及访问路径规划。
- 组织为 `gateways/whu`、`connectors/cnki`、`connectors/csmar` 和公共 `browser` 层。
- 工具统一为 `whu_*`，知网、国泰安标记为补充入口；保留旧 WHU 配置和 profile 的迁移兼容。
- 增加调用总时限、人工验证状态、PDF 校验和不覆盖落盘；修正空白页面及部分 200 错误页误判。
- 提供真实 MCP 通路验证与出版商抽样脚本；公开结果仅声明实际验证到的能力。
- 移除 CSDA；不包含 Google Scholar。
