import argparse
from datetime import datetime
from html import escape
import logging
import sys
from zoneinfo import ZoneInfo

from .config import Settings
from .data import DataService
from .email import send
from .report import render
from .trading_calendar import completed_session

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票日 K 收盘邮件任务")
    parser.add_argument("--force-run", action="store_true", help="手动测试标记，仍只获取已收盘交易日数据")
    args = parser.parse_args(argv if argv is not None else [])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout, force=True)
    settings = None
    try:
        logger.info("步骤 1/4：读取并验证 Secret 配置")
        settings = Settings.from_env()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        logger.info("运行模式：%s；北京时间：%s", "手动强制测试" if args.force_run else "正常收盘任务", now.isoformat())
        runtime_errors = []
        targets = {}
        for market in dict.fromkeys(stock.market for stock in settings.stocks):
            try:
                targets[market] = completed_session(now, market)
                logger.info("%s 最近已收盘交易日：%s", market, targets[market])
            except Exception as exc:
                runtime_errors.append(f"{market} 交易日历不可用：{type(exc).__name__}，跳过该市场抓取")
                logger.error("%s", runtime_errors[-1])
        target = now.date()
        logger.info("步骤 2/4：按各市场最近已收盘交易日抓取日 K，共 %d 只股票", len(settings.stocks))
        target = min(targets.values()) if targets else now.date()
        try:
            service = DataService(settings)
        except Exception as exc:
            logger.exception("数据服务初始化失败，跳过抓取并继续生成邮件")
            service = None
            runtime_errors.append(f"数据服务初始化失败：{type(exc).__name__}")
        from .data import FetchResult
        results = []
        if service is not None:
            for stock in settings.stocks:
                if stock.market not in targets:
                    results.append(FetchResult(stock, [], error="交易日历不可用，未抓取"))
                    continue
                try:
                    results.append(service.fetch(stock, targets[stock.market]))
                except Exception as exc:
                    logger.exception("股票 %s 未处理完成，继续下一只", stock.code)
                    results.append(FetchResult(stock, [], error=f"任务处理失败：{type(exc).__name__}"))
        logger.info("步骤 3/4：分析并生成报告，成功 %d/%d", sum(bool(r.bars) for r in results), len(results))
        report_errors = getattr(settings, "config_errors", ()) + tuple(runtime_errors)
        try:
            subject, text, html = render(results, target, report_errors)
        except Exception as exc:
            logger.exception("报告生成失败，使用最小错误报告继续发送")
            report_errors += (f"报告生成失败：{type(exc).__name__}",)
            subject = f"股票日 K 收盘报告（报告生成异常）| {target}"
            text = "报告生成失败，错误信息：\n" + "\n".join(report_errors)
            html = "<html><body><h2>报告生成失败</h2><ul>" + "".join(
                f"<li>{escape(error)}</li>" for error in report_errors) + "</ul></body></html>"
        if args.force_run:
            notice = "手动测试：按各市场最近已收盘交易日取数，报告日期以个股 K 线日期为准。"
            subject = "[手动测试] " + subject
            text = notice + "\n\n" + text
            html = html.replace("<body>", f"<body><p>{notice}</p>", 1)
        logger.info("步骤 4/4：发送邮件（包含失败股票的状态）")
        send(settings, subject, text, html)
        logger.info("任务完成")
        return 0
    except UnicodeEncodeError as exc:
        logger.error("邮件编码失败：请检查邮箱地址、授权码或发件人显示名称（%s）", type(exc).__name__)
        return 1
    except ValueError as exc:
        logger.error("配置或数据验证失败：%s", exc)
        return 1
    except Exception as exc:
        logger.error("任务失败（%s），请检查网络、数据源权限或邮箱配置；不打印可能含凭证的异常内容", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
