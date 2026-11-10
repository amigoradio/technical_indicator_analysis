"""Render the report in market sections and keep per-stock errors local."""
from datetime import date
from html import escape
from .analysis import analyze
from .data import FetchResult

def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"

def render(results: list[FetchResult], target: date,
           errors: tuple[str, ...] = ()) -> tuple[str, str, str]:
    subject = f"股票日 K 收盘报告 | {target}"
    intro = "价格为不复权原始价格；成交量单位为股，成交额为当地货币。"
    groups = {"CN": [], "HK": []}
    for result in results:
        groups[result.stock.market].append(result)
    text = [subject, "一、A股股票 / ETF 分析"]
    html = [f"<h2>{escape(subject)}</h2><h3>一、A股股票 / ETF 分析</h3>"]
    headings = ("日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "成交量（股）", "成交额", "MA5", "MA10", "MA20")
    for market, label in (("CN", "A股"), ("HK", "港股")):
        if market == "HK":
            text.append("\n二、港股股票分析")
            html.append("<h3>二、港股股票分析</h3>")
        if not groups[market]:
            text.append("无")
            html.append("<p>无配置标的</p>")
            continue
        for result in groups[market]:
            text.append(f"\n[{result.stock.code}]")
            html.append(f"<section><h4>{escape(result.stock.code)}</h4>")
            text.append(f"数据来源：{result.source or '无'}")
            html.append(f"<p><strong>数据来源：</strong>{escape(result.source or '无')}</p>")
            if not result.bars:
                error = f"未能获得数据：{result.error or '未知错误'}"
                text.append(f"个股错误：{error}")
                html.append(f"<p class='error'><strong>个股错误：</strong>{escape(error)}</p></section>")
                continue
            try:
                analysis = analyze(result.bars)
                bar = analysis.latest
                values = (str(bar.date), number(bar.open), number(bar.high), number(bar.low), number(bar.close),
                          number(analysis.change_pct) + "%" if analysis.change_pct is not None else "—",
                          number(bar.volume), number(bar.amount), *(number(analysis.moving_averages[p]) for p in (5, 10, 20)))
                text.append("；".join(f"{k}: {v}" for k, v in zip(headings, values)))
                html.append('<table border="1" cellpadding="6" cellspacing="0"><tbody>' +
                            "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in zip(headings, values)) +
                            "</tbody></table></section>")
            except Exception as exc:
                error = f"技术分析失败：{type(exc).__name__}"
                text.append(f"个股错误：{error}")
                html.append(f"<p class='error'><strong>个股错误：</strong>{escape(error)}</p></section>")
    if errors:
        text.extend(["\n三、整体错误信息", *[f"- {error}" for error in errors]])
        html.append("<h3>三、整体错误信息</h3><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errors) + "</ul>")
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<style>.error{color:#b00020}section{margin:12px 0 20px}table{border-collapse:collapse}</style><body>' +
            "".join(html) + f"<p>{escape(intro)} 历史不足时均线显示 —。</p></body></html>")
    return subject, "\n".join(text), html
