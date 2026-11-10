"""Render reports separately from analysis and SMTP transport."""

from datetime import date
from html import escape

from .analysis import analyze
from .data import FetchResult


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def render(results: list[FetchResult], target: date) -> tuple[str, str, str]:
    subject = f"股票日 K 收盘报告 | {target}"
    intro = ("价格为不复权原始价格；成交量单位为股，成交额为当地货币。"
             "涨跌幅与均线基于不复权收盘价，除权除息可能造成跳变。")
    lines = [subject, intro]
    rows = []
    headings = ("股票", "日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "成交量（股）",
                "成交额", "MA5", "MA10", "MA20", "数据源 / 状态")
    for result in results:
        if not result.bars:
            reason = f"未能获得数据：{result.error}"
            lines.append(f"{result.stock.code} | {reason}")
            rows.append(f"<tr><td>{escape(result.stock.code)}</td>"
                        f'<td colspan="12">{escape(reason)}</td></tr>')
            continue
        analysis = analyze(result.bars)
        bar = analysis.latest
        values = (result.stock.code, str(bar.date), number(bar.open), number(bar.high),
                  number(bar.low), number(bar.close),
                  number(analysis.change_pct) + "%" if analysis.change_pct is not None else "—",
                  number(bar.volume), number(bar.amount),
                  *(number(analysis.moving_averages[p]) for p in (5, 10, 20)), result.source)
        lines.append(" | ".join(f"{key}: {val}" for key, val in zip(headings, values)))
        rows.append("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in values) + "</tr>")
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>'
            f"<h2>{escape(subject)}</h2><p>{escape(intro)}</p>"
            '<table border="1" cellpadding="6" cellspacing="0"><thead><tr>'
            + "".join(f"<th>{escape(h)}</th>" for h in headings)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p>无当日日 K 时不以历史数据替代；可能原因包括休市、停牌、接口延迟或抓取失败。"
            "历史不足时均线显示 —。</p></body></html>")
    return subject, "\n\n".join(lines), html
