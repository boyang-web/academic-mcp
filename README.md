# academic-mcp

**以武汉大学图书馆为主入口的学术资源 MCP 网关。** 先通过图书馆完成身份认证、珞珈学术搜索和数据库分流，再在已有权限范围内读取论文、下载 PDF。知网和国泰安作为网关内部适配器，统一从 `whu_*` 调用。

```text
AI / MCP 客户端
    └─ whu_* 武大图书馆主入口
        ├─ 官网数据库导航 / 珞珈 Primo 检索
        ├─ 武大身份认证 / 全文中转 / 出版商页面
        └─ 正文读取 / PDF 本地下载与校验

        └─ whu_call_database
            ├─ cnki 内部引擎：检索 / PDF
            └─ csmar 内部引擎：目录 / 预览 / 导出
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

## WorkBuddy 连接与旧配置迁移

WorkBuddy 的本地 `~/.workbuddy/mcp.json` 可使用以下配置（替换解释器为实际绝对路径）：

```json
{
  "mcpServers": {
    "academic-data": {
      "command": "/absolute/path/.venv/bin/python",
      "args": ["-m", "academic_data_mcp", "serve"],
      "env": {"ADM_PROVIDERS": "whu"}
    }
  }
}
```

修改已有配置时保留其他服务。旧配置 `ADM_PROVIDERS=cnki,csmar,csda` 中的 `csda` 已删除，会使服务启动失败，客户端可能只显示 `Connection closed`；请将此项目的列表改为 `whu`。旧 `cnki` / `csmar` 名称可迁移，但不应继续使用已删除的 CSDA。

先用配置中同一解释器执行 `python -m pip install -e /absolute/path/to/repo`，保证在任意工作目录都能导入模块，不能只依靠终端激活虚拟环境。保存后在 WorkBuddy 中重新连接该服务；若仍使用旧进程/配置，重启客户端。

可以直接验证客户端真实配置，而非只测仓库内的开发环境：

```bash
.venv/bin/python scripts/verify_client_config.py --config ~/.workbuddy/mcp.json --server academic-data
```

脚本使用配置中的 command/args/env，在仓库外启动，检查协议握手、13 个 WHU 工具和一次无需网络的能力调用；不会注入项目 PYTHONPATH、打开浏览器或触发下载。通过仅证明服务连接和工具调用正常，数据库访问仍需网络与本人权限。

## 推荐使用流程

1. 使用 `whu_search` 查论文，或用 `whu_list_databases` 查询完整数据库目录；`whu_get_database` 核实订阅状态与已购说明。
2. 优先沿结果中的 `preferred_url` / `access_routes`，使用 `whu_read_page` 读取；指定数据库可用 `whu_open_database` 打开。
3. 需要认证时调用 `whu_open_login_page(url=原始入口)`，人工完成武大统一认证、CARSI 或验证码，再调用 `whu_finish_login`。
4. 重试原操作，从页面返回的 `pdf_urls` 中选择真实 PDF 地址，调用 `whu_download_pdf`。保留 `metaersp` / `ersp.lib.whu.edu.cn` 地址，不能擅自剥离图书馆代理。
5. 需要知网或国泰安库内操作时，先调用 `whu_database_capabilities(database="cnki" 或 "csmar")` 获取参数，再调用 `whu_call_database(database=..., operation=..., arguments={...})`。站点账号仍需独立认证；统一入口不会自动转移凭据或触发国泰安导出。

例如：`whu_call_database(database="csmar", operation="list_series")`。登录操作依次使用同一工具的 `open_login_page`、`finish_login`、`login_status`。

校内 IP、VPN、机构认证和具体订购范围共同决定权限。个人浏览器已登录不等于工具浏览器已登录；能检索、能看到“全文”标签也不代表能下载。

## 工具

默认只启用 `whu`，共 **13 个公共工具**，不再注册 `cnki_*` / `csmar_*`。

| 工具 | 用途 |
|---|---|
| `whu_search` | 珞珈检索、元数据、DOI 和实际全文路径 |
| `whu_list_databases` | 完整官网目录，可按名称或 ID 筛选；本轮 280 项 |
| `whu_get_database` | 武大订阅状态、已购说明、年份和子库关系；不等于实测可下载 |
| `whu_open_database` | 打开目录中唯一匹配的官方入口 |
| `whu_access_plan` | 保留机构链接，给出下一步及内部适配器建议 |
| `whu_database_capabilities` | 库内操作列表和参数结构，无需启动浏览器 |
| `whu_call_database` | 统一调用知网 / 国泰安内部适配器 |
| `whu_read_page` / `whu_download_pdf` | 页面读取、PDF 落盘和校验 |
| `whu_login_status` / `whu_open_login_page` / `whu_finish_login` / `whu_close_browser` | 图书馆和出版商会话管理 |

## 配置

优先级：CLI > `ADM_*` 环境变量 > 默认值，完整示例见 [.env.example](.env.example)。普通 `.env` 不自动加载；旧用法可通过 `ADM_DOTENV` 显式指定文件。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADM_PROVIDERS` | `whu` | 统一入口；旧 cnki/csmar 配置名迁移为 whu |
| `ADM_DATA_DIR` | 平台应用数据目录 | 本地 profile、认证状态和缓存 |
| `ADM_DOWNLOADS_DIR` | 系统下载目录 | 默认导出到 `academic-data-mcp/<入口名>/` |
| `ADM_BROWSER_CHANNEL` | `auto` | `chrome` / `msedge` / `chromium` |
| `ADM_WHU_SHOW_BROWSER` | `1` | 默认可见，供认证及出版商验证；设 `0` 可后台运行，部分站点可能拒绝 |
| `ADM_WHU_TIMEOUT` | `30` | 普通调用总时限（秒，1–120），含排队、启动、跳转和解析 |
| `ADM_WHU_DOWNLOAD_TIMEOUT` | `60` | 下载总时限（秒，1–300） |
| `ADM_WHU_MAX_DOWNLOAD_BYTES` | `104857600` | 单文件上限，默认 100 MiB |
| `ADM_WHU_PROXY` | 空 | 可选 HTTP 代理，浏览器与下载共用；不自动配置 VPN |
| `ADM_WHU_DIRECTORY_URL` / `ADM_WHU_LIBRARY_CODE` | `https://whu.metaersp.cn/databaseList` / `131001` | 完整目录及武大机构范围 |
| `ADM_WHU_BASE_URL` / `ADM_WHU_VIEW_ID` | 武大 Primo / `86WHU` | 珞珈检索后端 |

失败返回 `success=false`、`error_code` 和 `message`，区分网络错误、超时、待认证、验证码、无权限、解析失败及文件过大。PDF 校验文件头和 SHA256；HTML 不会保存为 PDF，目标文件不覆盖，失败的临时文件会清理。

## 项目结构

```text
src/academic_data_mcp/
├─ gateways/whu/        # 主入口：检索、官网数据库导航、访问路径与身份认证
├─ connectors/cnki/     # 内部：知网专用引擎及上游出处
├─ connectors/csmar/    # 内部：国泰安专用引擎
├─ browser/             # 会话、profile 进程锁、超时与下载校验
├─ base.py              # 工具契约
├─ registry.py          # 内置和 entry point 发现
├─ config.py            # 配置及登录态迁移兼容
├─ server.py            # 图书馆优先的 MCP 指令与工具组装
└─ cli.py               # serve / providers / doctor
```

架构与扩展见 [docs/architecture.md](docs/architecture.md)。`0.4.0` 移除专库公共入口，保留内部能力和登录态。`0.3.0` 将旧的 `luojia_*` 工具统一改为 `whu_*`；保留 `ADM_LUOJIA_*` 和 `--providers luojia` 的配置兼容，并优先复用已有 `luojia` profile，不要求因重命名重新认证。

## 验证与通路覆盖

```bash
pytest
python scripts/verify_stdio.py
python scripts/verify_whu.py --login
python scripts/inventory_whu.py --output verification/inventory.json
python scripts/sample_whu_coverage.py
python -m build
```

离线测试与协议握手不要求联网或启动浏览器。真实通路测试需要网络及本人的机构权限；原始报告、样本 PDF 和认证资料不进入 Git 或分发包。

当前完整目录的元数据核实为 **280/280**，其中官方标为订阅 **252** 项。元数据通过不等于全文通过。知网统一调用已完成检索和 PDF 落盘，Springer 机构通路有成功样本；其他平台及子库逐项记录。

- [完整访问表（280 项）](docs/access-matrix.md)：✅ 仅表示对应步骤已验证，未测不会打勾。
- [订阅与子库范围](docs/subscriptions.md)：知网 16 项、国泰安 19 系列及授权边界。
- [论文通路抽样](docs/coverage.md)：8 个出版商样本的具体结果。
- [共创验证与工作量](docs/contributing-access.md)：按需求逐库验证并提交脱敏结果。

## 来源

知网检索与下载后端改编自 [wuruiqi/cnki-mcp](https://github.com/wuruiqi/cnki-mcp)，保留其 MIT 许可和版权声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目仅访问本人已有权限的内容；人工认证不代表可以绕过平台授权。
