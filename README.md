# academic-mcp

**以武汉大学图书馆为主入口的学术资源 MCP 网关。** 先通过图书馆完成身份认证、珞珈学术搜索和数据库分流，再在已有权限范围内读取论文、下载 PDF。知网和国泰安保留为补充直连接口。

```text
AI / MCP 客户端
    └─ whu_* 武大图书馆主入口
        ├─ 官网数据库导航 / 珞珈 Primo 检索
        ├─ 武大身份认证 / 全文中转 / 出版商页面
        └─ 正文读取 / PDF 本地下载与校验

需要专库能力时，显式选择补充入口：
    ├─ cnki_*   知网检索、机构登录与 PDF 下载
    └─ csmar_*  国泰安目录、字段、预览与数据导出
```

工具支持人工认证和验证码：在工具打开的可见浏览器中完成操作，然后继续使用同一会话。不会自动绕过验证，也不会将超时、拒绝访问或错误页当成零条结果或下载成功。

## 安装与运行

Python ≥ 3.11，推荐已安装 Chrome / Edge。

```bash
python -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
academic-data-mcp providers
academic-data-mcp serve                  # 默认 stdio
```

未安装系统浏览器时可运行 `playwright install chromium`。浏览器依赖不会随 Python 分发包打包。

```bash
academic-data-mcp serve --providers whu   # 仅图书馆主入口
academic-data-mcp doctor                 # 依赖与配置诊断（凭据脱敏）
```

HTTP 模式可选：`pip install '.[http]'`，然后运行 `academic-data-mcp serve --transport http --port 7791`，默认仅监听 `127.0.0.1`。

MCP 客户端配置示例（将路径替换为本机绝对路径）：

```json
{
  "mcpServers": {
    "academic": {
      "command": "/absolute/path/.venv/bin/python",
      "args": ["-m", "academic_data_mcp", "serve"]
    }
  }
}
```

Windows 使用 `.venv\\Scripts\\python.exe`。更新后需重启 MCP 客户端，重新发现工具。

## 推荐使用流程

1. 使用 `whu_search` 查论文，或用 `whu_list_databases` 查找官网常用数据库入口。
2. 优先沿结果中的 `preferred_url` / `access_routes`，使用 `whu_read_page` 读取；指定数据库可用 `whu_open_database` 打开。
3. 需要认证时调用 `whu_open_login_page(url=原始入口)`，人工完成武大统一认证、CARSI 或验证码，再调用 `whu_finish_login`。
4. 重试原操作，从页面返回的 `pdf_urls` 中选择真实 PDF 地址，调用 `whu_download_pdf`。保留 `metaersp` / `ersp.lib.whu.edu.cn` 地址，不能擅自剥离图书馆代理。
5. 图书馆入口不可用或需要专库检索时，参考 `whu_access_plan`，显式使用 `cnki_*` 或 `csmar_*`。补充入口的登录态独立；不会自动触发消耗配额的国泰安导出。

校内 IP、VPN、机构认证和具体订购范围共同决定权限。个人浏览器已登录不等于工具浏览器已登录；能检索、能看到“全文”标签也不代表能下载。

## 工具

默认启用 `whu,cnki,csmar`，共 **26 个工具**。

| 层级 | 工具 | 用途 |
|---|---|---|
| 主入口 | `whu_search` | 珞珈 Primo 检索，返回元数据、DOI、实际全文链接和优先访问路径 |
| 主入口 | `whu_list_databases` / `whu_open_database` | 读取官网常用数据库导航并打开真实入口；非完整订购清单 |
| 主入口 | `whu_access_plan` | 保留原始链接，给出图书馆优先及补充直连建议 |
| 主入口 | `whu_read_page` / `whu_download_pdf` | 页面文字、引用元数据、PDF 链接、本地下载与校验 |
| 主入口 | `whu_login_status` / `whu_open_login_page` / `whu_finish_login` / `whu_close_browser` | 图书馆及出版商会话管理 |
| 补充 | `cnki_*`（6 个） | 登录、期刊首屏检索（最多 20 条）、PDF 下载 |
| 补充 | `csmar_*`（10 个） | 登录、数据库目录、字段、预览（最多 100 行）及单表完整导出 |

## 配置

优先级：CLI > `ADM_*` 环境变量 > 默认值，完整示例见 [.env.example](.env.example)。普通 `.env` 不自动加载；旧用法可通过 `ADM_DOTENV` 显式指定文件。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADM_PROVIDERS` | `whu,cnki,csmar` | 启用入口；WHU 为主，另外两个为补充 |
| `ADM_DATA_DIR` | 平台应用数据目录 | 本地 profile、认证状态和缓存 |
| `ADM_DOWNLOADS_DIR` | 系统下载目录 | 默认导出到 `academic-data-mcp/<入口名>/` |
| `ADM_BROWSER_CHANNEL` | `auto` | `chrome` / `msedge` / `chromium` |
| `ADM_WHU_SHOW_BROWSER` | `1` | 默认可见，供认证及出版商验证；设 `0` 可后台运行，部分站点可能拒绝 |
| `ADM_WHU_TIMEOUT` | `30` | 普通调用总时限（秒，1–120），含排队、启动、跳转和解析 |
| `ADM_WHU_DOWNLOAD_TIMEOUT` | `60` | 下载总时限（秒，1–300） |
| `ADM_WHU_MAX_DOWNLOAD_BYTES` | `104857600` | 单文件上限，默认 100 MiB |
| `ADM_WHU_PROXY` | 空 | 可选 HTTP 代理，浏览器与下载共用；不自动配置 VPN |
| `ADM_WHU_LIBRARY_URL` | `https://www.lib.whu.edu.cn/` | 官网导航来源 |
| `ADM_WHU_BASE_URL` / `ADM_WHU_VIEW_ID` | 武大 Primo / `86WHU` | 珞珈检索后端 |

失败返回 `success=false`、`error_code` 和 `message`，区分网络错误、超时、待认证、验证码、无权限、解析失败及文件过大。PDF 校验文件头和 SHA256；HTML 不会保存为 PDF，目标文件不覆盖，失败的临时文件会清理。

## 项目结构

```text
src/academic_data_mcp/
├─ gateways/whu/        # 主入口：检索、官网数据库导航、访问路径与身份认证
├─ connectors/cnki/     # 补充：知网专用接口及保留出处的上游后端
├─ connectors/csmar/    # 补充：国泰安专用接口
├─ browser/             # 会话、profile 进程锁、超时与下载校验
├─ base.py              # 工具契约及 primary/supplementary 角色
├─ registry.py          # 内置和 entry point 发现
├─ config.py            # 配置及登录态迁移兼容
├─ server.py            # 图书馆优先的 MCP 指令与工具组装
└─ cli.py               # serve / providers / doctor
```

架构与扩展见 [docs/architecture.md](docs/architecture.md)。`0.3.0` 将旧的 `luojia_*` 工具统一改为 `whu_*`；保留 `ADM_LUOJIA_*` 和 `--providers luojia` 的配置兼容，并优先复用已有 `luojia` profile，不要求因重命名重新认证。

## 验证与通路覆盖

```bash
pytest
python scripts/verify_stdio.py
python scripts/verify_whu.py --login
python scripts/sample_whu_coverage.py
python -m build --no-isolation
```

离线测试与协议握手不要求联网或启动浏览器。真实通路测试需要网络及本人的机构权限；原始报告、样本 PDF 和认证资料不进入 Git 或分发包。

目前已验证 Springer 样本的完整图书馆检索 → 认证代理 → 页面读取 → PDF 落盘。**不能因此宣称武大订购的数据库全部可用。** ScienceDirect 等平台的动态页面、验证机制和权限差异仍需逐项处理。抽样方法与已确认边界见 [docs/coverage.md](docs/coverage.md)。

## 来源

知网检索与下载后端改编自 [wuruiqi/cnki-mcp](https://github.com/wuruiqi/cnki-mcp)，保留其 MIT 许可和版权声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目仅访问本人已有权限的内容；人工认证不代表可以绕过平台授权。
