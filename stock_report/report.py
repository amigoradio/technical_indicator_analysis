"""Render per-instrument technical analysis."""
from datetime import date
from html import escape

from .analysis import analyze
from .data import FetchResult


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _volume_text(value: bool | None) -> str:
    return "是" if value else "否" if value is not None else "数据不足"


def _price_position_text(percentile: float | None) -> str:
    if percentile is None:
        return "数据不足"
    if percentile <= 15:
        return "底部"
    if percentile <= 30:
        return "中下部"
    if percentile < 70:
        return "中部"
    if percentile < 85:
        return "中上部"
    return "顶部"


def render(results: list[FetchResult], target: date, errors: tuple[str, ...] = (), range_days: int = 60) -> tuple[str, str, str]:
    subject = f"股票日K收盘报告 | {target}"
    groups = {"CN": [], "HK": []}
    for result in results:
        groups[result.stock.market].append(result)
    text = [subject, "一、A股股票 / ETF 分析"]
    html = [f"<h2>{escape(subject)}</h2><h3>一、A股股票 / ETF 分析</h3>"]
    for market in ("CN", "HK"):
        if market == "HK":
            text.append("\n二、港股分析")
            html.append("<h3>二、港股分析</h3>")
        if not groups[market]:
            text.append("无")
            html.append("<p>无配置标的</p>")
            continue
        for result in groups[market]:
            name = next((bar.instrument_name for bar in result.bars if bar.instrument_name), "名称暂缺")
            display = f"{result.stock.code}（{name}）"
            text.append(f"\n[{display}]\n类型: {result.stock.asset_type}\n数据来源: {result.source or '无'}")
            html.append(f"<section><h4>{escape(display)}</h4><p>类型：{escape(result.stock.asset_type)}；数据来源：{escape(result.source or '无')}</p>")
            if not result.bars:
                error = result.error or "未能获得数据"
                if "未能获得数据" not in error:
                    error = "未能获得数据；" + error
                text.append(f"个股错误: {error}")
                html.append(f"<p class='error'>个股错误：{escape(error)}</p></section>")
                continue
            try:
                analysis = analyze(result.bars, range_days)
                action = "做多" if analysis.long_score else "做空" if analysis.short_score else "不操作"
                ma5 = analysis.moving_averages[5]
                previous_ma5 = sum(bar.close for bar in result.bars[-6:-1]) / 5 if len(result.bars) >= 6 else None
                macd_signals = [analysis.macd_zone]
                if analysis.macd_cross != "无":
                    macd_signals.append(analysis.macd_cross)
                    if ma5 is not None and analysis.latest.close > ma5 and analysis.macd_cross == "金叉":
                        macd_signals.append("站上5日线")
                    if ma5 is not None and analysis.latest.close < ma5 and analysis.macd_cross == "死叉":
                        macd_signals.append("跌破5日线")
                    if ma5 is not None and previous_ma5 is not None and ((
                            analysis.macd_cross == "金叉" and analysis.latest.close > ma5 and ma5 > previous_ma5) or (
                            analysis.macd_cross == "死叉" and analysis.latest.close < ma5 and ma5 < previous_ma5)):
                        macd_signals.append("5日线拐头")
                macd_text = "，".join(macd_signals)
                position_text = _price_position_text(analysis.range_percentile)
                text.extend([
                    f"收盘: {number(analysis.latest.close)}，涨跌: {number(analysis.change_pct)}%",
                    f"MACD: {macd_text}",
                    f"{analysis.range_days}日股价位置百分比: {number(analysis.range_percentile)}%（{position_text}）",
                    f"今日量能高于前120日均量: {_volume_text(analysis.volume_above_ma120)}；连续3日放量: {_volume_text(analysis.volume_above_ma120_three_days)}",
                    f"策略结论: {action}",
                ])
                html.append(
                    f"<p>收盘：{number(analysis.latest.close)}；涨跌：{number(analysis.change_pct)}%</p>"
                    f"<p>MACD：{escape(macd_text)}</p>"
                    f"<p>{analysis.range_days}日股价位置百分比：{number(analysis.range_percentile)}%（{position_text}）</p>"
                    f"<p>今日量能高于前120日均量：{_volume_text(analysis.volume_above_ma120)}；连续3日放量：{_volume_text(analysis.volume_above_ma120_three_days)}</p>"
                    f"<p><strong>策略结论：{action}</strong></p></section>"
                )
            except Exception as exc:
                error = f"技术分析失败：{type(exc).__name__}"
                text.append(f"个股错误: {error}")
                html.append(f"<p class='error'>个股错误：{escape(error)}</p></section>")
    if errors:
        text.extend(["\n三、整体错误信息", *[f"- {error}" for error in errors]])
        html.append("<h3>三、整体错误信息</h3><ul>" + "".join(f"<li>{escape(error)}</li>" for error in errors) + "</ul>")
    body = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>.error{color:#b00020}section{margin:12px 0 20px}</style><body>" + "".join(html) + "</body></html>"
    return subject, "\n".join(text), body
