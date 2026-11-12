# 每日收盘任务

## 时间与运行方式

“每日 6 点”按收盘后的北京时间 **18:00** 实现。工作流 `.github/workflows/daily-close.yml` 的 cron 为 `0 10 * * 1-5`（UTC），周一至周五运行。GitHub 定时任务可能排队延迟，不保证整点送达；工作流必须位于默认分支。公开仓库长期无活动时 GitHub 可能停用 schedule，需在 Actions 中重新启用。

定时任务只处理运行当天：周末跳过，工作日 18:00 前报错且不发送。若定时任务延迟至次日凌晨，入口会拒绝处理，而不是把前一天的数据当作当天收盘。

手动测试：进入 **Actions → Daily closing report → Run workflow**，选择分支并执行。与参考项目一样提供布尔输入 `force_run`，默认不勾选；勾选后可在周末或 18:00 前执行完整抓取、分析及邮件流程，日志显示“手动强制测试”，邮件主题和正文标记“手动测试”。不勾选则遵守正常任务的时间检查。定时触发始终使用正常模式。

强制运行仍只查询运行当天，并保留数据校验、重试与回退。盘中返回的当日日 K 可能尚未收盘；周末、节假日可能没有当天数据，届时发送缺失状态邮件并按原规则标记任务失败，便于查看日志。不会为了测试而伪造当日收盘或替换成上一交易日数据。Run workflow 按钮需工作流文件已推送至默认分支且 Actions 已启用。

任务按股票串行处理，每次请求前随机间隔 2–5 秒，每源默认尝试两次。SDK 请求在可终止的独立进程中执行，默认 60 秒硬超时，包含连接和初始化时间，防止第三方 SDK 挂死后无法回退。Actions 整个 job 最长 120 分钟，包含安装依赖；很大的股票清单或很长的超时可能超出此上限。

## Actions Secrets

所有配置在仓库 `Settings → Secrets and variables → Actions` 的 **Secrets** 中设置。GitHub Secret 名称不区分大小写，工作流统一写 `STOCK_LIST`，因此界面中原有的 `stock_list` 同样适用。本地环境兼容小写 `stock_list`，优先使用 `STOCK_LIST`。

| Secret | 必填 / 默认值 | 用途 |
| --- | --- | --- |
| `STOCK_LIST` | 必填 | 英文分号分隔，如 `600519;000001;hk00700`；自动去空白、去重；港股必须有 `hk` 前缀，`hk700` 自动补齐到 `hk00700` |
| `EMAIL_SENDER` | 必填 | 发件人邮箱 |
| `EMAIL_PASSWORD` | 必填 | 邮箱 SMTP 授权码；邮箱需开启 SMTP 服务 |
| `EMAIL_RECEIVERS` | 收件人邮箱 | 英文逗号分隔；空字符串发给自己 |
| `EMAIL_SENDER_NAME` | `可爱炒股小秘` | 发件人显示名称 |
| `EMAIL_SMTP_HOST` | 常用域名自动识别 | 自定义 SMTP 主机；企业邮箱等未知域名必须设置 |
| `EMAIL_SMTP_PORT` | 常用域名默认端口，否则 465 | 端口号 |
| `EMAIL_SMTP_SECURITY` | 465 用 `ssl`，其余用 `starttls` | 仅允许加密 SMTP；可显式覆盖 |
| `DATA_REQUEST_TIMEOUT` | `60` | 单次请求硬超时秒数，范围 5–300 |
| `DATA_RETRY_ATTEMPTS` | `2` | 每股票每数据源的总尝试次数，范围 1–3 |
| `HISTORY_DAYS` | `120` | 查询当天及此前日历天数，范围 30–365，用于计算均线与涨跌幅 |

自动识别：QQ / Foxmail、163、阿里云、默认 SSL 465；Gmail、Outlook、Hotmail、Live 默认 STARTTLS 587。以邮箱服务商实际 SMTP 配置为准；可通过 SMTP Secrets 覆盖。多个收件人共享同一份邮件，`To` 中可见彼此地址。

## Longbridge 配置

参考 `daily_stock_analysis-main/data_provider/longbridge_fetcher.py` 及其 Actions 参数，支持以下两种认证。认证成功不代表具有全部市场行情权限，需确认长桥账户已开通所需 A 股 / 港股日 K 权限。未配置长桥时不访问长桥；配置不完整、无权限、超时或无当日日 K 时记录失败并继续 Akshare。

| Secret | 用途 |
| --- | --- |
| `LONGBRIDGE_APP_KEY` | 传统 API Key 三件套之一 |
| `LONGBRIDGE_APP_SECRET` | 传统 API Key 三件套之一 |
| `LONGBRIDGE_ACCESS_TOKEN` | 传统 API Key 三件套之一 |
| `LONGBRIDGE_OAUTH_CLIENT_ID` | OAuth 认证的公开 client ID |
| `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64` | 已授权 SDK token 缓存文件的 Base64 文本，供无交互任务恢复 |
| `LONGBRIDGE_REGION` | 默认hk 覆盖接入点；SDK 会按网络自动选择，默认 hk，若判断不正确可设置（如 cn、hk） |
| `LONGBRIDGE_HTTP_URL` | HTTP 接口地址（默认 https://openapi.longbridge.com） |
| `LONGBRIDGE_QUOTE_WS_URL` | 行情 WebSocket 地址（默认 wss://openapi-quote.longbridge.com/v2） |
| `LONGBRIDGE_TRADE_WS_URL` | 交易 WebSocket 地址（默认 wss://openapi-trade.longbridge.com/v2） |

LONGBRIDGE_STATIC_INFO_TTL_SECONDS	长桥 static_info 进程内缓存秒数（默认 86400，0=不缓存）
LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS	长桥连接关闭类异常后的冷却秒数（默认 15；冷却期内临时跳过 Longbridge，避免频繁重连）
LONGBRIDGE_ENABLE_OVERNIGHT	设置为 false
LONGBRIDGE_PUSH_CANDLESTICK_MODE	K 线推送模式：realtime 或 confirmed（默认 realtime）

传统认证需同时提供三个 Secret。OAuth 需同时提供 client ID 与缓存 Secret；如果只有 App Key 且无 Access Token，兼容将 App Key 视为 OAuth client ID。两种认证均提供时优先 OAuth，初始化失败再尝试完整三件套。

OAuth 缓存来自已完成授权的长桥 SDK，原始文件位置为 `~/.longbridge/openapi/tokens/<client_id>`。可使用参考项目的 `scripts/generate_longbridge_oauth_token.py` 在本地生成，然后将该文件内容转换为 Base64，填入 `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64`。任务会校验 JSON 格式并恢复至 SDK 约定路径，不会打开浏览器或等待交互授权。

缓存包含敏感令牌，不能提交到仓库或上传为 artifact。任务内保留 SDK 刷新后的缓存，后续股票请求复用；GitHub 托管 runner 每次全新启动，不会将刷新结果自动写回 Secret。若缓存过期或 refresh token 轮换，需重新授权并更新缓存 Secret。推荐先用传统三件套完成首次验证。

工作流使用 Ubuntu 24.04 + Python 3.12 + Longbridge 4.x（`>=4.0.5,<5`），匹配参考项目对 Linux wheel / glibc 和 OAuth SDK 的要求。未配置的 URL Secret 产生的空环境变量会在 SDK 初始化前移除，避免空地址导致连接失败。

## 数据与失败语义

- A 股：Longbridge（配置后）→ Akshare → Baostock。
- 港股：Longbridge（配置后）→ Akshare；失败显示“未能获得数据”，不调用 Baostock。
- A 股 ETF（`51xxxx`、`52xxxx`、`56xxxx`、`58xxxx`、`15xxxx`、`16xxxx`、`18xxxx`）在 Akshare 使用 `fund_etf_hist_em` 专用接口；普通 A 股使用 `stock_zh_a_hist`。Baostock 仅作为普通沪深 A 股的兜底，不用于 ETF 或港股。
- GitHub Runner 的公共出口 IP 可能触发 Akshare Eastmoney `Push2RateLimitError`。ETF 遇到该错误时会尝试 Akshare 的新浪 ETF 历史接口（上海使用 `sh` 前缀、深圳使用 `sz` 前缀）；两者均失败才记录 ETF 数据源错误。ETF 不会再调用 Baostock。
- 统一模型会将这些代码标记为 `asset_type=ETF`，并设置 `supports_fundamentals=false`、`adjustment=none`。后续增加财务、估值或分红复权功能时，必须先检查该标记：ETF 跳过 PE、EPS、净利润、分红和股票复权逻辑，只使用 ETF 的行情与技术指标。
- 所有数据源使用不复权日线，以展示实际成交收盘价。MA5/10/20 和相邻交易记录涨跌幅也使用不复权价格，除权除息时可能跳变，与行情软件的复权指标可能不同。
- 标准模型统一 `date/open/high/low/close/volume/amount`。成交量单位为股：Akshare A 股历史接口的手转换为股，港股历史接口直接使用股；Longbridge、Baostock 使用股。成交额为当地货币（A 股人民币、港股港元）。
- 必须含北京时间当天日 K，且价格、成交量、成交额和高低价关系有效，才接受该源结果。空数据、缺当天记录、重复日期或无效价格均触发重试及回退。不同源的历史数据不会混合拼接。
- 不把上一交易日收盘价伪装为当日数据。节假日、停牌、接口延迟或不可用均可能缺少当天 K 线；报告会展示缺失状态。只有存在有效历史记录时才能给出最近日期，无法仅凭缺失记录准确区分休市与数据源故障。
- 单股票失败不会阻止其他股票和邮件；全部股票失败仍发状态邮件，之后以退出码 1 标记 Actions 失败。节假日全部缺数据也采用此行为。部分失败会在报告与日志中展示，任务仍成功。
- 运行采用错误收集模式：无效股票条目会被跳过并写入报告；可选配置错误使用安全默认值并写入报告；单只股票、数据源、超时、分析或报告行错误都会记录后继续下一项。任务会尽量走到 SMTP 发送步骤。只有发件人、授权码、邮箱地址或 SMTP 参数本身无效时，无法建立发送通道，邮件无法发送；此时只能依靠 Actions 日志修复配置。
- 少于两根 K 线时涨跌幅显示 `—`，不足相应周期时均线显示 `—`。
- 邮件固定分为三部分：第一部分逐只列出 A 股股票 / ETF 的数据源、K 线和技术分析及个股错误；第二部分逐只列出港股相同内容；第三部分只显示跨股票的整体错误（例如配置、数据服务初始化或报告生成失败），没有整体错误时隐藏该部分。
- 邮件含 HTML 表格与纯文本正文，HTML 内容会转义。SMTP 认证失败或部分收件人被拒收会导致任务失败；不自动重发邮件，避免部分收件人重复收到。

## 日志与排查

生产 Actions 只执行依赖安装和四个业务步骤：配置验证 → 数据抓取 → 分析与报告 → 邮件发送，不以离线单元测试作为行情任务的前置门禁。这样测试断言或模拟接口的变化不会阻止真实任务发送状态邮件。数据日志包含股票、源、日期范围、请求间隔、尝试次数、耗时、返回 K 线数量与回退原因。

SDK 原始异常可能包含带凭证的 URL，所以日志只记录 SDK 异常类型与检查提示；自行构造的验证错误会显示具体原因。邮件正文也不会包含原始 SDK 异常。日志不打印授权码、令牌、整个环境或配置对象；长桥行情包打印被关闭。

首次部署后，手动运行 `Daily closing report`，验证真实行情权限与 SMTP 投递；周末或 18:00 前需勾选 `force_run`。点击本次运行，再打开 `report` job 的各步骤可查看日志，业务日志位于 `Fetch, analyze and email daily close` 步骤。此项目的离线测试使用模拟接口，不访问真实行情服务、不发邮件。若登录失败，先检查授权码、SMTP 开关、服务商限制和长桥行情权限。

## 本地开发与扩展

建议 Python 3.12，安装依赖后设置同名环境变量：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:STOCK_LIST = '600519;hk00700'
$env:EMAIL_SENDER = 'your-email@qq.com'
$env:EMAIL_PASSWORD = 'your-smtp-authorization-code'
.\.venv\Scripts\python -m stock_report
```

`.env.example` 是配置清单，程序不会自动加载 `.env`。请使用当前 shell 环境变量或 IDE 的安全环境配置。

```text
stock_report/
  config.py          环境变量验证与 SMTP 默认值
  models.py          Stock / DailyBar 统一模型与日 K 校验
  providers/         Akshare / Baostock / Longbridge 适配器
  data.py            源优先级、市场路由、进程超时、重试、回退
  analysis.py        纯计算，输入 DailyBar，不依赖数据源
  report.py          HTML 与纯文本报告
  email.py           SMTP 发送
  __main__.py        任务编排与退出码
```

新增源：增加适配器模块，实现 `fetch(stock, start, end) -> list[DailyBar]`，统一不复权价格、日期、成交量单位，再在 `data.py` 的 `PROVIDERS` 按优先级注册模块路径和市场。无需修改分析或邮件逻辑。新增凭证时同步更新工作流 env、`.env.example` 与本文档。

新增指标：在 `analysis.py` 编写基于统一 K 线模型的纯计算，再在 `report.py` 展示结果。MACD / RSI / SAR / 布林线 / 背离等以及周线分析尚未实现。

本地开发验证：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q stock_report tests
```

测试覆盖优先级、回退、过期数据拒绝、进程超时与异常脱敏、成交量转换、SMTP SSL / STARTTLS、默认收件人、HTML 转义、均线，以及全失败 / 部分成功 / 周末和收盘前的任务行为。测试不会访问真实行情服务，也不会发送真实邮件；生产工作流不会自动执行该测试步骤。
