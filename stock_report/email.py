from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
import logging
import smtplib
import ssl

from .config import Settings

logger = logging.getLogger(__name__)


def send(settings: Settings, subject: str, text: str, html: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.sender_name, settings.sender))
    message["To"] = ", ".join(settings.receivers)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    transport = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    logger.info("连接邮件服务器，模式 %s，收件人数 %d", settings.smtp_security, len(settings.receivers))
    kwargs = {"context": context} if settings.smtp_security == "ssl" else {}
    with transport(settings.smtp_host, settings.smtp_port, timeout=30, **kwargs) as server:
        if settings.smtp_security == "starttls":
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        logger.info("验证发件邮箱身份")
        server.login(settings.sender, settings.password)
        logger.info("发送收盘报告")
        refused = server.send_message(message, from_addr=settings.sender, to_addrs=list(settings.receivers))
        if refused:
            raise RuntimeError("部分收件人被拒收，请检查邮箱配置（不自动重发以避免重复邮件）")
    logger.info("邮件发送成功")
