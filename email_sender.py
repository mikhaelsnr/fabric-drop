"""SMTP email delivery; credentials and settings are supplied by the caller."""
from email.mime.text import MIMEText
import logging
import smtplib

LOGGER = logging.getLogger(__name__)


def send_email(subject, body, sender_email, receiver_email, *, password, smtp_host, smtp_port):
    """Return delivery success and log failures without exposing SMTP credentials."""
    if not receiver_email:
        LOGGER.error('Email not sent: recipient list is empty (%s)', subject)
        return False
    try:
        if not password or password == '<email password>':
            LOGGER.error('Email credentials missing')
            return False
        message = MIMEText(body)
        message['Subject'], message['From'] = subject, sender_email
        message['To'] = ', '.join(receiver_email)
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(sender_email, password)
            refused = server.sendmail(sender_email, receiver_email, message.as_string())
        if refused:
            LOGGER.error('Email rejected for one or more recipients')
            return False
        LOGGER.info('Email sent: %s', subject)
        return True
    except Exception as exc:
        LOGGER.error('Email failed (%s)', type(exc).__name__)
        return False
