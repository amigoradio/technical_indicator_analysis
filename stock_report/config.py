"""All runtime settings come from environment variables (Actions Secrets)."""

from dataclasses import dataclass, field
import os

from .models import Stock


SMTP_PRESETS = {
    "qq.com": ("smtp.qq.com", 465),
    "foxmail.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
    "gmail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587),
    "sina.com": ("smtp.sina.com", 465),
    "sohu.com": ("smtp.sohu.com", 465),
    "aliyun.com": ("smtp.aliyun.com", 465),
    "139.com": ("smtp.139.com", 465),
}


def value(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    result = int(value(name, str(default)))
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return result


@dataclass(frozen=True)
class Settings:
    stocks: tuple[Stock, ...]
    sender: str
    password: str = field(repr=False)
    receivers: tuple[str, ...]
    sender_name: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    request_timeout: int
    attempts: int
    history_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        raw_stocks = value("STOCK_LIST") or value("stock_list")
        stocks = tuple(dict.fromkeys(Stock.parse(s) for s in raw_stocks.split(";") if s.strip()))
        if not stocks:
            raise ValueError("请在 Secrets 中配置 STOCK_LIST（分号分隔）")
        sender, password = value("EMAIL_SENDER"), value("EMAIL_PASSWORD")
        receivers = tuple(dict.fromkeys(s.strip() for s in value("EMAIL_RECEIVERS").split(",") if s.strip())) or (sender,)
        if not sender or not password:
            raise ValueError("EMAIL_SENDER 和 EMAIL_PASSWORD 为必填参数")
        for address in (sender, *receivers):
            if address.count("@") != 1 or any(c.isspace() for c in address):
                raise ValueError("邮箱格式无效")
        preset = SMTP_PRESETS.get(sender.rsplit("@", 1)[1].lower())
        host = value("EMAIL_SMTP_HOST", preset[0] if preset else "")
        if not host:
            raise ValueError("该邮箱域名需要配置 EMAIL_SMTP_HOST")
        port = integer("EMAIL_SMTP_PORT", preset[1] if preset else 465, 1, 65535)
        security = value("EMAIL_SMTP_SECURITY", "ssl" if port == 465 else "starttls").lower()
        if security not in ("ssl", "starttls"):
            raise ValueError("EMAIL_SMTP_SECURITY 仅支持 ssl 或 starttls")
        return cls(stocks, sender, password, receivers, value("EMAIL_SENDER_NAME", "可爱炒股小秘"),
                   host, port, security, integer("DATA_REQUEST_TIMEOUT", 60, 5, 300),
                   integer("DATA_RETRY_ATTEMPTS", 2, 1, 3), integer("HISTORY_DAYS", 120, 30, 365))
