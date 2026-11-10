# Technical Indicators Analysis
基于技术指标对股票日线、周线K线进行分析，辅助个人交易决策。

> ⚠️ **风险提示**：本项目所有工具、指标计算结果仅作为个人学习研究参考，项目代码99%是AI编写，**不构成任何投资建议**。股市有风险，投资需谨慎。

## 📌 项目简介
本项目实现常见技术指标的计算与行情分析，读取K线数据，自动识别信号，用于辅助观察标的日线、周线级别走势，为个人买卖决策提供参考。

## 每日收盘邮件

已实现 Python + GitHub Actions 定时任务：周一至周五北京时间 **18:00** 获取 A 股、港股当日日 K，发送包含开高低收、涨跌幅、成交量、成交额及 MA5/10/20 的邮件。数据源、分析、报告与邮件发送独立分层。

1. 将本项目推送到 GitHub 仓库的默认分支，并启用 Actions。
2. 在 `Settings → Secrets and variables → Actions → New repository secret` 配置 `STOCK_LIST`、`EMAIL_SENDER`、`EMAIL_PASSWORD`。例如 `STOCK_LIST=600519;000001;hk00700`。
3. 按需配置 `EMAIL_RECEIVERS`（逗号分隔，留空发给发件人）及 `EMAIL_SENDER_NAME`（默认“可爱炒股小秘”）。
4. 进入 Actions → Daily closing report → Run workflow 手动验证；如需在周末或 18:00 前测试，勾选 `force_run`，报告会标记为“手动测试”。之后自动定时运行。

未配置长桥时，A 股使用 Akshare → Baostock，港股使用 Akshare。配置 Longbridge 后优先使用长桥。失败股票在邮件中显示“未能获得数据”，其余股票正常展示。

完整配置、Longbridge OAuth、日志、休市处理和扩展方法见 [运行与开发说明](docs/daily-close.md)。

## 技术指标规划

当前已实现日线 MA5/10/20；以下指标及周线分析为后续规划：
- MACD（金叉 / 死叉识别）
- RSI（超买 / 超卖）
- SAR（翻红 / 翻绿）
- 布林线（上中下轨）
- 均线（多头排列 / 空头排列）
- 顶背离 / 底背离
- 九转序列
- 一阳穿多均，断头铡刀

## 📋 功能规划
- 读取标的日K、周K行情数据
- 计算上述技术指标
- 识别指标信号、输出分析结果
- 指标可视化（后续迭代）

## 🤝 参与与反馈
欢迎提交 Issue 反馈问题、提出想法，也欢迎提交 PR 贡献代码。
