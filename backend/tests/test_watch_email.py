from types import SimpleNamespace
from email import message_from_string
from email.header import decode_header, make_header

from app.notifications import email_notifier


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.mail_args = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, sender, auth_code):
        self.login_args = (sender, auth_code)

    def sendmail(self, sender, recipients, message):
        self.mail_args = (sender, recipients, message)


def make_settings(**overrides):
    values = {
        "ALERT_EMAIL_ENABLED": True,
        "SMTP_HOST": "smtp.qq.com",
        "SMTP_PORT": 465,
        "SMTP_SENDER": "sender@example.com",
        "SMTP_AUTH_CODE": "secret-auth-code",
        "SMTP_RECIPIENTS": "one@example.com, two@example.com",
        "SMTP_SUBJECT_PREFIX": "[EvoAlpha]",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_send_notification_uses_ssl_and_recipient_list(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(email_notifier.smtplib, "SMTP_SSL", FakeSMTP)

    result = email_notifier.send_notification(
        "低吸触发",
        "<b>测试</b>",
        is_html=True,
        config=make_settings(),
    )

    assert result.success
    instance = FakeSMTP.instances[-1]
    assert (instance.host, instance.port, instance.timeout) == ("smtp.qq.com", 465, 30)
    assert instance.login_args == ("sender@example.com", "secret-auth-code")
    assert instance.mail_args[1] == ["one@example.com", "two@example.com"]
    parsed = message_from_string(instance.mail_args[2])
    subject = str(make_header(decode_header(parsed["Subject"])))
    assert subject == "[EvoAlpha] 低吸触发"
    assert "secret-auth-code" not in instance.mail_args[2]


def test_send_notification_skips_when_disabled(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("disabled notifier must not connect")

    monkeypatch.setattr(email_notifier.smtplib, "SMTP_SSL", unexpected)

    result = email_notifier.send_notification(
        "低吸触发",
        "body",
        config=make_settings(ALERT_EMAIL_ENABLED=False),
    )

    assert not result.success
    assert result.skipped
    assert result.error == "email notifications disabled"


def test_send_notification_rejects_incomplete_configuration(monkeypatch):
    result = email_notifier.send_notification(
        "低吸触发",
        "body",
        config=make_settings(SMTP_AUTH_CODE=""),
    )

    assert not result.success
    assert result.skipped
    assert "incomplete" in (result.error or "")


def test_send_notification_returns_sanitized_failure(monkeypatch):
    class BrokenSMTP(FakeSMTP):
        def login(self, sender, auth_code):
            raise RuntimeError(f"authentication failed for {auth_code}")

    monkeypatch.setattr(email_notifier.smtplib, "SMTP_SSL", BrokenSMTP)

    result = email_notifier.send_notification(
        "低吸触发",
        "body",
        config=make_settings(),
    )

    assert not result.success
    assert "secret-auth-code" not in (result.error or "")
    assert result.error == "email delivery failed"
