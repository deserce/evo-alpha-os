"""SMTP SSL email delivery for Watch Engine alerts.

The implementation intentionally mirrors the small, proven notifier used by
wx-research-hub while keeping EvoAlpha configuration and credentials isolated.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol

from app.core.config import settings


logger = logging.getLogger(__name__)


class EmailConfig(Protocol):
    ALERT_EMAIL_ENABLED: bool
    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_SENDER: str
    SMTP_AUTH_CODE: str
    SMTP_RECIPIENTS: str
    SMTP_SUBJECT_PREFIX: str


@dataclass(frozen=True)
class EmailDeliveryResult:
    success: bool
    skipped: bool = False
    error: str | None = None


def _recipients(config: EmailConfig) -> list[str]:
    return [item.strip() for item in config.SMTP_RECIPIENTS.split(",") if item.strip()]


def _build_message(
    subject: str,
    body: str,
    *,
    is_html: bool,
    config: EmailConfig,
    recipients: list[str],
) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    message["From"] = config.SMTP_SENDER
    message["To"] = ", ".join(recipients)
    message["Subject"] = f"{config.SMTP_SUBJECT_PREFIX} {subject}".strip()
    message.attach(MIMEText(body, "html" if is_html else "plain", "utf-8"))
    return message


def send_notification(
    subject: str,
    body: str,
    *,
    is_html: bool = False,
    config: EmailConfig = settings,
) -> EmailDeliveryResult:
    """Send one notification without ever exposing the SMTP authorization code."""
    if not config.ALERT_EMAIL_ENABLED:
        logger.info("邮件通知已禁用，跳过: %s", subject)
        return EmailDeliveryResult(False, skipped=True, error="email notifications disabled")

    recipients = _recipients(config)
    if not config.SMTP_SENDER or not config.SMTP_AUTH_CODE or not recipients:
        logger.warning("SMTP 配置不完整，跳过邮件: %s", subject)
        return EmailDeliveryResult(False, skipped=True, error="email configuration incomplete")

    message = _build_message(
        subject,
        body,
        is_html=is_html,
        config=config,
        recipients=recipients,
    )
    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.login(config.SMTP_SENDER, config.SMTP_AUTH_CODE)
            server.sendmail(config.SMTP_SENDER, recipients, message.as_string())
    except Exception as exc:
        logger.error("邮件发送失败 (%s): %s", type(exc).__name__, subject)
        return EmailDeliveryResult(False, error="email delivery failed")

    logger.info("邮件已发送: %s -> %s 个收件人", subject, len(recipients))
    return EmailDeliveryResult(True)


def send_test(*, config: EmailConfig = settings) -> EmailDeliveryResult:
    body = """<html><body>
<h2>EvoAlpha Watch Engine 邮件测试</h2>
<p>如果你收到这封邮件，说明 SMTP SSL 配置正确。</p>
</body></html>"""
    return send_notification("邮件通知测试", body, is_html=True, config=config)
