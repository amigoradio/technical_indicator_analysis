"""Render per-instrument technical analysis."""
from datetime import date
import base64
from io import BytesIO
from html import escape

from .analysis import analyze
from .data import FetchResult
from .margin import MarginInfo


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _margin_number(value: object) -> str:
    if value is None:
        return "—"
    try:
        if value != value:
            return "—"
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return escape(str(value))


def _margin_chart(rows: tuple[tuple[date, float, float], ...]) -> str | None:
    if not rows:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        import numpy as np

        labels = [day.strftime("%m-%d") for day, _, _ in rows]
        balances = [float(balance) for _, balance, _ in rows]
        changes = [float(change) if change == change else 0.0 for _, _, change in rows]
        positions = np.arange(len(rows))
        chart_font = next(
            (font for font in font_manager.fontManager.ttflist
             if any(token in font.name.lower() for token in
                    ("noto sans cjk", "source han", "microsoft yahei", "simhei",
                     "wenquanyi", "droid sans fallback"))),
            None,
        )
        if chart_font:
            plt.rcParams["font.family"] = chart_font.name
            balance_label = "融资余额"
            change_label = "融资净增量"
            axis_label = "亿元"
            title = "最近十个交易日融资余额与融资净增量"
        else:
            # A runner without a CJK font would render Chinese as empty boxes.
            balance_label = "Margin balance"
            change_label = "Net margin change"
            axis_label = "CNY 100m"
            title = "Margin balance and net change (10 sessions)"
        figure, axis = plt.subplots(figsize=(7.2, 3.6), dpi=150)
        width = 0.36
        axis.bar(positions - width / 2, balances, width, label=balance_label, color="#f2c94c")
        axis.bar(positions + width / 2, changes, width, label=change_label, color="#9bd7f5")
        axis.set_xticks(positions, labels)
        axis.set_ylabel(axis_label)
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.25)
        axis.legend()
        figure.tight_layout()
        output = BytesIO()
        figure.savefig(output, format="png", facecolor="white")
        plt.close(figure)
        return base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        return None


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


def _colored_action(value: str) -> str:
    if value == "做多":
        return "<span style='color:#d00000'>做多</span>"
    if value == "做空":
        return "<span style='color:#008000'>做空</span>"
    return escape(value)


def _colored_indicator_text(value: str) -> str:
    """Color only indicator state words; surrounding explanation stays black."""
    escaped = escape(value)
    return (escaped.replace("顶背离", "<span style='color:#008000'>顶背离</span>")
            .replace("底背离", "<span style='color:#d00000'>底背离</span>")
            .replace("超买", "<span style='color:#d00000'>超买</span>")
            .replace("超卖", "<span style='color:#008000'>超卖</span>"))


def _color_indicator_markup(value: str) -> str:
    """Color indicator labels in an already escaped HTML fragment."""
    labels = (
        ("顶背离", "#008000"),
        ("底背离", "#d00000"),
        ("超买", "#d00000"),
        ("超卖", "#008000"),
    )
    for label, color in labels:
        value = value.replace(label, f"<span style='color:{color}'>{label}</span>")
    return value


def _colored_sar_text(value: str) -> str:
    """Color the complete SAR state, including a recent reversal label."""
    color = "#008000" if "绿色" in value else "#d00000" if "红色" in value else None
    escaped = escape(value)
    return f"<span style='color:{color}'>{escaped}</span>" if color else escaped


def render(results: list[FetchResult], target: date, errors: tuple[str, ...] = (), range_days: int = 60,
           frequency: str = "daily", margin: MarginInfo | None = None) -> tuple[str, str, str]:
    period_name = "周K" if frequency == "weekly" else "日K"
    subject = f"股票{period_name}收盘报告 | {target}"
    groups = {"CN": [], "HK": []}
    summary_rows = []
    for result in results:
        name = next((bar.instrument_name for bar in result.bars if bar.instrument_name), "名称暂缺")
        analysis = None
        analysis_error = ""
        if result.bars:
            try:
                analysis = analyze(result.bars, range_days, frequency=frequency)
            except Exception as exc:
                analysis_error = f"技术分析失败：{type(exc).__name__}"
        action = "不操作"
        second_action = "不操作"
        divergence = "无"
        change_text = "—"
        if analysis is not None:
            action = "做多" if analysis.long_score else "做空" if analysis.short_score else "不操作"
            second_action = analysis.strategy_two.action
            if analysis.change_pct is not None:
                change = round(analysis.change_pct, 2)
                change_text = f"{change:+.2f}%" if change else "0.00%"
            if analysis.macd_divergences or analysis.rsi_divergences:
                divergence = "背离"
        groups[result.stock.market].append((result, name, analysis, analysis_error, action))
        summary_rows.append((result.stock.code, name, change_text, divergence, action, second_action))
    text = [subject]
    html = [f"<h2>{escape(subject)}</h2>"]
    if frequency == "daily" and margin is not None:
        if margin.latest_date is None:
            text.extend(["基本面信息", "融资信息：无法获取数据"])
            html.append("<h3>基本面信息</h3><p>融资信息：无法获取数据</p>")
        else:
            status = f"；状态：{margin.status}" if margin.status else ""
            chart = _margin_chart(margin.recent)
            text.extend([
                "基本面信息",
                f"融资信息：交易日期 {margin.latest_date}；融资余额 { _margin_number(margin.balance) } 亿元；"
                f"融资净增量 { _margin_number(margin.change) } 亿元；"
                f"融资余额3年百分比 { _margin_number(margin.percentile_3y) }%；"
                f"融资余额5年百分比 { _margin_number(margin.percentile_5y) }%{status}",
            ])
            text.append("最近十个交易日融资数据（日期 | 融资余额(亿) | 融资净增量）")
            text.extend(
                f"{day} | {_margin_number(balance)} | {_margin_number(change)}"
                for day, balance, change in margin.recent
            )
            html.append(
                "<h3>基本面信息</h3>"
                f"<p>融资信息：交易日期 {escape(str(margin.latest_date))}；"
                f"融资余额 {_margin_number(margin.balance)} 亿元；"
                f"融资净增量 {_margin_number(margin.change)} 亿元；"
                f"融资余额3年百分比 {_margin_number(margin.percentile_3y)}%；"
                f"融资余额5年百分比 {_margin_number(margin.percentile_5y)}%"
                f"{f'；状态：{escape(margin.status)}' if margin.status else ''}</p>"
            )
            if chart:
                html.append(
                    "<p><img alt='最近十个交易日融资数据柱状图' "
                    f"style='max-width:100%;height:auto' src='data:image/png;base64,{chart}'></p>"
                )
    text.append("总结")
    text.append("代码 | 名称 | 涨跌情况 | 背离情况 | 策略一（MACD） | 策略二（SAR与均线）")
    text.extend(" | ".join(row) for row in summary_rows)
    html.append("<h3>总结</h3>"
            "<table border='1' cellpadding='6' cellspacing='0'><thead><tr>"
            "<th>代码</th><th>名称</th><th>涨跌情况</th><th>背离情况</th><th>策略一（MACD）</th>"
            "<th>策略二（SAR与均线）</th></tr></thead><tbody>")
    html.extend(
        f"<tr><td>{escape(code)}</td><td>{escape(name)}</td><td>{change_text}</td><td>{divergence}</td>"
        f"<td>{_colored_action(action)}</td><td>{_colored_action(second_action)}</td></tr>"
        for code, name, change_text, divergence, action, second_action in summary_rows
    )
    html.append("</tbody></table><h3>一、A股股票 / ETF 分析</h3>")
    for market in ("CN", "HK"):
        if market == "HK":
            text.append("\n二、港股分析")
            html.append("<h3>二、港股分析</h3>")
        if not groups[market]:
            text.append("无")
            html.append("<p>无配置标的</p>")
            continue
        for result, name, analysis, analysis_error, action in groups[market]:
            display = f"{result.stock.code}（{name}）"
            text.append(f"\n[{display}]\n类型: {result.stock.asset_type}\n数据来源: {result.source or '无'}")
            html.append(
                f"<section><h4 style='font-size:18px;margin-bottom:4px'>{escape(display)} "
                f"<span style='font-size:14px;font-weight:normal'>类型：{escape(result.stock.asset_type)}；"
                f"数据来源：{escape(result.source or '无')}</span></h4>"
            )
            if not result.bars:
                error = result.error or "未能获得数据"
                if "未能获得数据" not in error:
                    error = "未能获得数据；" + error
                text.append(f"个股错误: {error}")
                html.append(f"<p class='error'>个股错误：{escape(error)}</p></section>")
                continue
            if analysis is None:
                text.append(f"个股错误: {analysis_error}")
                html.append(f"<p class='error'>个股错误：{escape(analysis_error)}</p></section>")
                continue
            try:
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
                if analysis.macd_divergences:
                    macd_text = f"{macd_text}{chr(12289)}{chr(12289).join(analysis.macd_divergences)}"
                macd_text = " ".join(macd_signals + list(analysis.macd_divergences))
                rsi_details = []
                rsi = analysis.trend.rsi
                if rsi is not None:
                    if rsi >= 70:
                        rsi_details.append(f"RSI(14) 超买（{number(rsi)}）")
                    elif rsi <= 30:
                        rsi_details.append(f"RSI(14) 超卖（{number(rsi)}）")
                if analysis.rsi_divergences:
                    rsi_details.append(f"RSI 与价格：{'、'.join(analysis.rsi_divergences)}")
                sar_text = f"SAR：{analysis.sar.direction}"
                if analysis.sar.recent_reversal != "无":
                    sar_text += f"（{analysis.sar.recent_reversal}）"
                position_text = _price_position_text(analysis.range_percentile)
                strategy_two_summary = f"策略二（SAR与均线）结论: {analysis.strategy_two.action}"
                strategy_one_html = (f"<p><strong>策略一（MACD）结论：</strong>"
                                     f"{_colored_action(action)}</p>")
                strategy_two_html = (f"<p><strong>策略二（SAR与均线）结论：</strong>"
                                     f"{_colored_action(analysis.strategy_two.action)}</p>")
                if analysis.strategy_two.action in ("做多", "做空"):
                    strategy_two_summary += (f"；可信度: {analysis.strategy_two.confidence}/5；"
                                             f"{analysis.strategy_two.reason}")
                    strategy_two_html = (
                        f"<p><strong>策略二（SAR与均线）结论：</strong>{_colored_action(analysis.strategy_two.action)}"
                        f"（可信度 {analysis.strategy_two.confidence}/5）："
                        f"{escape(analysis.strategy_two.reason)}</p>"
                    )
                text.extend([
                    f"收盘: {number(analysis.latest.close)}，涨跌: {number(analysis.change_pct)}%",
                    f"MACD: {macd_text}",
                    *([analysis.nine_turn] if analysis.nine_turn else []),
                    f"{analysis.range_days}日股价位置百分比: {number(analysis.range_percentile)}%（{position_text}）",
                    f"今日量能高于前120日均量: {_volume_text(analysis.volume_above_ma120)}；连续3日放量: {_volume_text(analysis.volume_above_ma120_three_days)}",
                    f"策略一（MACD）结论: {action}",
                    sar_text,
                    f"均线排列: {analysis.alignment.direction}；K线位置: {analysis.alignment.candle_position}",
                    *rsi_details,
                    strategy_two_summary,
                ])
                html.append("".join([
                    f"<p>收盘：{number(analysis.latest.close)}；涨跌：{number(analysis.change_pct)}%</p>",
                    f"<p>MACD：{escape(macd_text)}</p>",
                    (f"<p><span style='color:#1565c0'>{escape(analysis.nine_turn)}</span></p>"
                     if analysis.nine_turn else ""),
                    f"<p>{analysis.range_days}日股价位置百分比：{number(analysis.range_percentile)}%（{position_text}）</p>",
                    f"<p>今日量能高于前120日均量：{_volume_text(analysis.volume_above_ma120)}；连续3日放量：{_volume_text(analysis.volume_above_ma120_three_days)}</p>",
                    strategy_one_html,
                    f"<p>{_colored_sar_text(sar_text)}</p>",
                    f"<p>均线排列：{analysis.alignment.direction}；K线位置：{analysis.alignment.candle_position}</p>",
                    *(f"<p>{_colored_indicator_text(detail)}</p>" for detail in rsi_details),
                    strategy_two_html,
                    "</section>",
                ]))
            except Exception as exc:
                error = f"技术分析失败：{type(exc).__name__}"
                text.append(f"个股错误: {error}")
                html.append(f"<p class='error'>个股错误：{escape(error)}</p></section>")
    if errors:
        text.extend(["\n三、整体错误信息", *[f"- {error}" for error in errors]])
        html.append("<h3>三、整体错误信息</h3><ul>" + "".join(f"<li>{escape(error)}</li>" for error in errors) + "</ul>")
    html = [_color_indicator_markup(fragment) if "MACD：" in fragment else fragment for fragment in html]
    body = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>.error{color:#b00020}section{margin:12px 0 20px}</style><body>" + "".join(html) + "</body></html>"
    return subject, "\n".join(text), body
