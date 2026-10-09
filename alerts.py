"""
alerts.py — Real-time notification and alerting module for the Egyptian Legal Data Pipeline.
Supports Telegram and Slack webhooks with graceful fallbacks and silent no-op when unconfigured.
"""

import logging
import os
from typing import Optional
import requests

logger = logging.getLogger("alerts")


class AlertManager:
    """
    Handles pipeline event notifications (Telegram / Slack).
    If tokens are not set, alerts log cleanly to the local logger without failing the pipeline.
    """

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        slack_webhook_url: Optional[str] = None,
    ):
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.slack_webhook_url = slack_webhook_url or os.getenv("SLACK_WEBHOOK_URL")

    def send_telegram(self, message: str) -> bool:
        if not self.telegram_token or not self.telegram_chat_id:
            logger.debug("Telegram credentials not configured. Skipping alert.")
            return False

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("Telegram notification sent successfully.")
                return True
            else:
                logger.warning("Telegram alert failed (%d): %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.warning("Telegram notification exception: %s", e)
            return False

    def send_slack(self, message: str) -> bool:
        if not self.slack_webhook_url:
            return False
        try:
            resp = requests.post(self.slack_webhook_url, json={"text": message}, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Slack notification exception: %s", e)
            return False

    def notify_success(self, processed_count: int, synced_count: int, runtime_sec: float) -> None:
        msg = (
            f"✅ <b>Egyptian Legal Pipeline Success</b>\n"
            f"• <b>Laws Processed:</b> {processed_count}\n"
            f"• <b>Files Synced to Drive:</b> {synced_count}\n"
            f"• <b>Execution Time:</b> {runtime_sec:.1f}s\n"
            f"• <b>Status:</b> All data quality assertions passed."
        )
        logger.info(msg.replace("<b>", "").replace("</b>", ""))
        self.send_telegram(msg)
        self.send_slack(msg)

    def notify_failure(self, error_message: str, stage: str = "Pipeline Execution") -> None:
        msg = (
            f"🚨 <b>Egyptian Legal Pipeline Alert — Failure</b>\n"
            f"• <b>Stage:</b> {stage}\n"
            f"• <b>Error:</b> <code>{error_message}</code>\n"
            f"• <b>Action:</b> Review logs immediately."
        )
        logger.error(msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
        self.send_telegram(msg)
        self.send_slack(msg)


# Module-level helper
_global_alerter = AlertManager()


def send_alert(message: str):
    _global_alerter.send_telegram(message)
    _global_alerter.send_slack(message)
