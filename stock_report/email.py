from email.message import EmailMessage
from email.header import Header
from email.utils import formataddr, formatdate, make_msgid
import logging
import smtplib
import ssl

from .config import Settings

logger = logging.getLogger(__name__)


def send(settings: Settings, subject: str, text: str, html: str) -> None:
    message = EmailMessage()
    # Build RFC 2047 encoded headers explicitly. This is required for the
    # default Chinese display name on Python 3.12/GitHub's SMTP path.
    message["Subject"] = str(Header(subject, "utf-8"))
    encoded_name = str(Header(settings.sender_name, "utf-8"))
    message["From"] = formataddr((encoded_name, settings.sender))
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
        try:
            server.login(settings.sender, settings.password)
        except UnicodeEncodeError as exc:
            raise RuntimeError(
                "SMTP 认证参数包含非 ASCII 字符，请检查 EMAIL_SENDER 和 EMAIL_PASSWORD；"
                "邮箱显示名称不会参与认证"
            ) from exc
        logger.info("发送收盘报告")
        try:
            refused = server.send_message(message, from_addr=settings.sender, to_addrs=list(settings.receivers))
        except UnicodeEncodeError as exc:
            raise RuntimeError("邮件头或收件人地址编码失败，请检查邮箱地址和发件人名称") from exc
        if refused:
            raise RuntimeError("部分收件人被拒收，请检查邮箱配置（不自动重发以避免重复邮件）")
    logger.info("邮件发送成功")
