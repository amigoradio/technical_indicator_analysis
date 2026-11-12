"""Render market sections with technical indicators and strategy signals."""
from datetime import date
from html import escape
from .analysis import analyze
from .data import FetchResult

def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"

def render(results: list[FetchResult], target: date, errors: tuple[str, ...] = (), range_days: int = 60) -> tuple[str, str, str]:
    subject = f"股票日K收盘报告 | {target}"
    groups = {"CN": [], "HK": []}
    for result in results: groups[result.stock.market].append(result)
    text, html = [subject, "一、A股股票 / ETF 分析"], [f"<h2>{escape(subject)}</h2><h3>一、A股股票 / ETF 分析</h3>"]
    for market, label in (("CN", "A股"), ("HK", "港股")):
        if market == "HK": text.append("\n二、港股分析"); html.append("<h3>二、港股分析</h3>")
        if not groups[market]: text.append("无"); html.append("<p>无配置标的</p>"); continue
        for result in groups[market]:
            name = next((bar.instrument_name for bar in result.bars if bar.instrument_name), "名称暂缺")
            display = f"{result.stock.code}（{name}）"
            text.append(f"\n[{display}]\n类型: {result.stock.asset_type}\n数据来源: {result.source or '无'}")
            html.append(f"<section><h4>{escape(display)}</h4><p>类型：{escape(result.stock.asset_type)}；数据来源：{escape(result.source or '无')}</p>")
            if not result.bars:
                error = result.error or "未能获得数据"
                if "未能获得数据" not in error:
                    error = "未能获得数据；" + error
                text.append(f"个股错误: {error}"); html.append(f"<p class='error'>个股错误：{escape(error)}</p></section>"); continue
            try:
                a = analyze(result.bars, range_days); b = a.latest
                text.extend([f"收盘: {number(b.close)}，涨跌: {number(a.change_pct)}%",
                             f"今日量能高于前120日均量: {'是' if a.volume_above_ma120 else '否' if a.volume_above_ma120 is not None else '数据不足'}；连续3日: {'是' if a.volume_above_ma120_three_days else '否' if a.volume_above_ma120_three_days is not None else '数据不足'}",
                             f"均线: MA5 {number(a.moving_averages[5])} / MA10 {number(a.moving_averages[10])} / MA20 {number(a.moving_averages[20])}",
                             f"MACD: {a.macd_crossover}，MACD {number(a.macd)}，信号线 {number(a.macd_signal)}，柱 {number(a.macd_histogram)}",
                             f"{a.range_days}日位置: {number(a.range_percentile)}%（{a.range_position}）",
                             f"做多: {a.long_score}/5；{'、'.join(a.long_reasons) or '未满足条件'}",
                             f"做空: {a.short_score}/5；{'、'.join(a.short_reasons) or '未满足条件'}"])
                html.append(f"<p>收盘：{number(b.close)}；涨跌：{number(a.change_pct)}%</p>"
                            f"<p>今日量能高于前120日均量：{'是' if a.volume_above_ma120 else '否' if a.volume_above_ma120 is not None else '数据不足'}；连续3日：{'是' if a.volume_above_ma120_three_days else '否' if a.volume_above_ma120_three_days is not None else '数据不足'}</p>"
                            f"<p>均线：MA5 {number(a.moving_averages[5])} / MA10 {number(a.moving_averages[10])} / MA20 {number(a.moving_averages[20])}</p>"
                            f"<p>MACD：{escape(a.macd_crossover)}；MACD {number(a.macd)}；信号线 {number(a.macd_signal)}；柱 {number(a.macd_histogram)}</p>"
                            f"<p>{a.range_days}日位置：{number(a.range_percentile)}%（{escape(a.range_position)}）</p>"
                            f"<p><strong>做多 {a.long_score}/5</strong>：{escape('、'.join(a.long_reasons) or '未满足条件')}；<strong>做空 {a.short_score}/5</strong>：{escape('、'.join(a.short_reasons) or '未满足条件')}</p></section>")
            except Exception as exc:
                text.append(f"个股错误: 技术分析失败：{type(exc).__name__}"); html.append(f"<p class='error'>个股错误：技术分析失败：{type(exc).__name__}</p></section>")
    if errors:
        text.extend(["\n三、整体错误信息", *[f"- {e}" for e in errors]])
        html.append("<h3>三、整体错误信息</h3><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errors) + "</ul>")
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>.error{color:#b00020}section{margin:12px 0 20px}</style><body>' + ''.join(html) + "</body></html>"
    return subject, "\n".join(text), html
