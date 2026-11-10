"""Render market-grouped stock reports independently from data and SMTP layers."""

from datetime import date
from html import escape

from .analysis import analyze
from .data import FetchResult


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _stock_content(result: FetchResult) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    if not result.bars:
        error = f"未能获得数据：{result.error or '未知错误'}"
        return (f"数据来源：{result.source or '无（所有数据源均失败）'}\n个股错误：{error}",
                f"<p><strong>数据来源：</strong>{escape(result.source or '无（所有数据源均失败）')}</p>"
                f"<p class=\"error\"><strong>个股错误：</strong>{escape(error)}</p>", [error])
    try:
        analysis = analyze(result.bars)
    except Exception as exc:
        error = f"技术分析失败：{type(exc).__name__}"
        return (f"数据来源：{result.source}\n个股错误：{error}",
                f"<p><strong>数据来源：</strong>{escape(result.source)}</p>"
                f"<p class=\"error\"><strong>个股错误：</strong>{escape(error)}</p>", [error])
    bar = analysis.latest
    values = {
        "日期": str(bar.date), "开盘": number(bar.open), "最高": number(bar.high),
        "最低": number(bar.low), "收盘": number(bar.close),
        "涨跌幅": number(analysis.change_pct) + "%" if analysis.change_pct is not None else "—",
        "成交量（股）": number(bar.volume), "成交额": number(bar.amount),
        "MA5": number(analysis.moving_averages[5]),
        "MA10": number(analysis.moving_averages[10]), "MA20": number(analysis.moving_averages[20]),
    }
    text = f"数据来源：{result.source}\n" + "；".join(f"{k}: {v}" for k, v in values.items())
    html = (f"<p><strong>数据来源：</strong>{escape(result.source)}</p><table border=\"1\" "
            "cellpadding=\"6\" cellspacing=\"0\"><tbody>"
            + "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in values.items())
            + "</tbody></table>")
    return text, html, errors


def render(results: list[FetchResult], target: date,
           errors: tuple[str, ...] = ()) -> tuple[str, str, str]:
    subject = f"股票日 K 收盘报告 | {target}"
    intro = ("价格为不复权原始价格；成交量单位为股，成交额为当地货币。"
             "涨跌幅与均线基于不复权收盘价，除权除息可能造成跳变。")
    grouped = {"CN": [], "HK": []}
    for result in results:
        grouped[result.stock.market].append(result)
    text_parts = [subject, intro]
    html_parts = [f"<h2>{escape(subject)}</h2><p>{escape(intro)}</p>"]
    for market, label in (("CN", "一、A股股票 / ETF 分析"), ("HK", "二、港股股票分析")):
        text_parts.append(f"\n{label}")
        html_parts.append(f"<h3>{escape(label)}</h3>")
        if not grouped[market]:
            text_parts.append("无")
            html_parts.append("<p>无配置标的</p>")
            continue
        for result in grouped[market]:
            plain, html, _ = _stock_content(result)
            text_parts.append(f"\n[{result.stock.code}]\n{plain}")
            html_parts.append(f"<section><h4>{escape(result.stock.code)}</h4>{html}</section>")
    if errors:
        text_parts.append("\n三、整体错误信息\n" + "\n".join(f"- {e}" for e in errors))
        html_parts.append("<h3>三、整体错误信息</h3><ul>" + "".join(
            f"<li>{escape(e)}</li>" for e in errors) + "</ul>")
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<style>.error{color:#b00020} section{margin:12px 0 20px} '
            'table{border-collapse:collapse} th{background:#f2f2f2;text-align:left}</style>'
            '<body>' + "".join(html_parts) +
            '<p>无当日日 K 时不以历史数据替代；可能原因包括休市、停牌、接口延迟或抓取失败。'
            '历史不足时均线显示 —。</p></body></html>')
    return subject, "\n".join(text_parts), html
