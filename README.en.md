# Technical Indicators Analysis
Analyze daily and weekly stock K-lines based on technical indicators to assist personal trading decisions.

> ⚠️ **Risk Warning**: All tools and indicator calculation results in this project are for personal learning and research reference only. About 99% of the project code was written by AI, and **it does not constitute any investment advice**. The stock market involves risks; invest with caution.

## 📌 Project Overview
This project implements the calculation and market analysis of common technical indicators. It reads K-line data and automatically identifies signals, helping to observe the daily and weekly trends of target instruments and providing reference for personal buy/sell decisions.

## Daily closing email

The Python application runs through GitHub Actions at **18:00 Asia/Shanghai, Monday–Friday**, and emails daily OHLC, percentage change, volume, turnover and MA5/10/20 for A-shares and Hong Kong stocks.

1. Push this project to the default branch of a GitHub repository and enable Actions.
2. Add repository Actions Secrets: `STOCK_LIST` (for example `600519;000001;hk00700`), `EMAIL_SENDER`, and `EMAIL_PASSWORD` (SMTP authorization code).
3. Optionally set `EMAIL_RECEIVERS` (comma separated; defaults to the sender) and `EMAIL_SENDER_NAME` (defaults to `可爱炒股小秘`).
4. Run **Actions → Daily closing report → Run workflow** to verify delivery. Select `force_run` to test on weekends or before 18:00 China time; the email will be marked as a manual test.

Routing: configured Longbridge first, then Akshare, then Baostock for mainland stocks only. Missing current-day data is explicitly reported without substituting an older close. Data adapters, analysis, report rendering and SMTP delivery are separate modules.

See the [configuration and development guide (Chinese)](docs/daily-close.md) for all Secrets, OAuth, failure handling and extension points.

## Planned indicators

Daily MA5/10/20 is implemented. The following indicators and weekly analysis remain planned:
- MACD (golden cross / death cross identification)
- RSI (overbought / oversold)
- SAR (red flip / green flip)
- Bollinger Bands (upper, middle, and lower bands)
- Moving Averages (bullish alignment / bearish alignment)
- Top divergence / bottom divergence
- Nine-Turn Sequence
- One bullish candle piercing multiple moving averages; Death Guillotine

## 📋 Feature Roadmap
- Read daily and weekly K-line market data for target instruments
- Calculate the technical indicators listed above
- Identify indicator signals and output analysis results
- Indicator visualization (future iteration)

## 🤝 Contributing and Feedback
You are welcome to submit Issues to report problems or share ideas, and you are also welcome to submit PRs to contribute code.
