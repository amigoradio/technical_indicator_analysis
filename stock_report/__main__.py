import argparse
from datetime import datetime
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
        if not args.force_run and now.hour < 18:
            raise ValueError("正常任务仅在北京时间 18:00 后运行；手动测试可勾选 force_run")
        target = now.date()
        logger.info("步骤 2/4：抓取 %s 日 K，共 %d 只股票", target, len(settings.stocks))
        service = DataService(settings)
        results = [service.fetch(stock, target) for stock in settings.stocks]
        logger.info("步骤 3/4：分析并生成报告，成功 %d/%d", sum(bool(r.bars) for r in results), len(results))
        subject, text, html = render(results, target)
        if args.force_run:
            notice = "手动测试：当天日 K 可能尚未收盘，数据仅用于验证抓取和邮件流程。"
            subject = "[手动测试] " + subject
            text = notice + "\n\n" + text
            html = html.replace("<body>", f"<body><p>{notice}</p>", 1)
        logger.info("步骤 4/4：发送邮件（包含失败股票的状态）")
        send(settings, subject, text, html)
        if all(not r.bars for r in results):
            logger.error("所有股票均未获取到当日数据；状态邮件已发送，任务标记失败")
            return 1
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
