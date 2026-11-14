# 武大图书馆通路抽样（2026-11-14）

本表记录当前实现、当前账号/网络及指定论文的结果，不是武大订购数据库清单，也不是整库权限结论。测试通过真实 stdio MCP 执行；每个平台最多下载一篇，失败不自动绕过验证。

本表保留 0.3 的八个平台样本证据。0.4 已改用完整目录，核实 280 项详情，252 项标为订阅，见[完整访问表](access-matrix.md)及[订阅范围](subscriptions.md)。Springer 已在 0.4 重新完成检索、机构代理读取及 PDF 落盘。

| 平台 | 目标 DOI | 目标定位 | 出版商页 | PDF 落盘 | 观察 |
|---|---|---|---|---|---|
| Springer | 10.1007/s00146-025-02700-4 | 是 | 是 | 是 | 已下载并校验 |
| ScienceDirect | 10.1016/j.irfa.2023.102876 | 是 | 是 | 否 | PDF 失败：pdf_link_required |
| Wiley | 10.1002/asi.23552 | 否 | 否 | 否 | 未定位到目标 DOI，不能判断目标库权限 |
| Taylor & Francis | 10.1080/15481603.2024.2380126 | 是 | 否 | 否 | 未发现该平台全文入口（不能用其他平台的副本充数） |
| Nature | 10.1038/nature14539 | 是 | 否 | 否 | 未发现该平台全文入口（不能用其他平台的副本充数） |
| Oxford | 10.1093/bib/bbad467 | 是 | 否 | 否 | 页面失败：access_denied |
| IEEE | 10.1109/TIFS.2026.3725564 | 是 | 是 | 否 | PDF 失败：pdf_link_required |
| JSTOR | 10.2307/1879431 | 是 | 否 | 否 | 页面失败：timeout |

本轮 8 个平台样本中，Springer 1 个完成下载校验；ScienceDirect 和 IEEE 到达机构代理页面但 PDF 传输失败。其余停在定位、入口发现、拒绝访问或超时，不能归结为武大没有订购。

Wiley 标题检索返回一条同名记录，但该记录未提供目标 DOI；本轮按严格 DOI 匹配未将其算作目标定位成功。Taylor & Francis 的记录详情超时，未取得可验证入口；Nature 未从本次记录取得该站全文入口；Oxford 返回拒绝访问；JSTOR 详情/跳转超时。

Springer 在中转页一度超时后，另一次相同路径调用恢复并成功下载，说明网络状态会影响结果，单次失败不能当作永久不可用。重构后的 `whu` 主入口已另行复验通过：PDF 458,076 字节，SHA256 `2bda7d34559bc88053eae9443bfd901ea2f706edd2faa149ca167064b68af5ca`。

## 重复测试

```bash
python scripts/sample_whu_coverage.py
python scripts/sample_whu_coverage.py --only Springer,IEEE --output verification/recheck
python scripts/verify_whu.py --login
```

原始逐步报告和样本保存在本机 `verification/`，不提交登录态、会话 URL、完整论文或数据。后续优先处理动态 PDF 入口和出版商验证，再扩大覆盖；不能用公开副本代替目标平台来提高覆盖率。
