import argparse
from datetime import datetime
from html import escape
import logging
import sys
from zoneinfo import ZoneInfo

from .config import Settings
from .data import DataService, FetchResult
from .email import send
from .report import render

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="股票日K收盘邮件任务")
    parser.add_argument("--force-run", action="store_true")
    args = parser.parse_args(argv if argv is not None else [])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout, force=True)
    try:
        logger.info("步骤 1/4：读取并验证 Secret 配置")
        settings = Settings.from_env()
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        target = now.date()
        logger.info("运行时间：%s；直接请求数据源实际交易日", now.isoformat())
        logger.info("步骤 2/4：抓取最近 120 个有效历史交易日，共 %d 只股票", len(settings.stocks))
        runtime_errors: list[str] = []
        try:
            service = DataService(settings)
        except Exception as exc:
            service = None
            runtime_errors.append(f"数据服务初始化失败：{type(exc).__name__}")
        results: list[FetchResult] = []
        for stock in settings.stocks:
            if service is None:
                results.append(FetchResult(stock, [], error="数据服务不可用"))
                continue
            try:
                results.append(service.fetch(stock, target))
            except Exception as exc:
                logger.exception("股票 %s 处理失败，继续下一只", stock.code)
                results.append(FetchResult(stock, [], error=f"任务处理失败：{type(exc).__name__}"))
        logger.info("步骤 3/4：分析并生成报告，成功 %d/%d", sum(bool(r.bars) for r in results), len(results))
        report_errors = tuple(getattr(settings, "config_errors", ())) + tuple(runtime_errors)
        try:
            subject, text, html = render(results, target, report_errors, settings.range_days)
        except Exception as exc:
            report_errors += (f"报告生成失败：{type(exc).__name__}",)
            subject = f"股票日K收盘报告（报告生成异常）| {target}"
            text = "报告生成失败：\n" + "\n".join(report_errors)
            html = "<html><body><h2>报告生成失败</h2><ul>" + "".join(f"<li>{escape(e)}</li>" for e in report_errors) + "</ul></body></html>"
        if args.force_run:
            subject = "[手动测试] " + subject
        logger.info("步骤 4/4：发送邮件")
        send(settings, subject, text, html)
        logger.info("任务完成")
        return 0
    except UnicodeEncodeError:
        logger.error("邮件编码失败，请检查邮箱配置")
        return 1
    except ValueError as exc:
        logger.error("配置验证失败：%s", exc)
        return 1
    except Exception as exc:
        logger.error("任务失败：%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
