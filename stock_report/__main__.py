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

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票日 K 收盘邮件任务")
    parser.add_argument("--force-run", action="store_true", help="手动测试，跳过周末和18点前检查")
    args = parser.parse_args(argv if argv is not None else [])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout, force=True)
    try:
        logger.info("步骤 1/4：读取并验证 Secret 配置")
        settings = Settings.from_env()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        logger.info("运行模式：%s；北京时间：%s", "手动强制测试" if args.force_run else "正常收盘任务", now.isoformat())
        if args.force_run:
            logger.warning("已跳过周末和18点前检查；当天数据可能未收盘或不存在，邮件将标记为手动测试")
        if not args.force_run and now.weekday() >= 5:
            logger.info("北京时间 %s 为周末，跳过任务", now.isoformat())
            return 0
        runtime_errors = []
        if not args.force_run and now.hour < 18:
            warning = "当前时间早于北京时间 18:00，行情可能尚未收盘"
            runtime_errors.append(warning)
            logger.warning("%s；仍继续执行并发送状态邮件", warning)
        target = now.date()
        logger.info("步骤 2/4：抓取 %s 日 K，共 %d 只股票", target, len(settings.stocks))
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
                try:
                    results.append(service.fetch(stock, target))
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
            notice = "手动测试：当天日 K 可能尚未收盘，数据仅用于验证抓取和邮件流程。"
            subject = "[手动测试] " + subject
            text = notice + "\n\n" + text
            html = html.replace("<body>", f"<body><p>{notice}</p>", 1)
        logger.info("步骤 4/4：发送邮件（包含失败股票的状态）")
        send(settings, subject, text, html)
        logger.info("任务完成")
        return 0
    except ValueError as exc:
        logger.error("配置或数据验证失败：%s", exc)
        return 1
    except Exception as exc:
        logger.error("任务失败（%s），请检查网络、数据源权限或邮箱配置；不打印可能含凭证的异常内容", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
