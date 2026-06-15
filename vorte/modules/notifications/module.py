"""
Vorte Notifications Module — Module Registration
=================================================
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.notifications")


class NotificationsModule(Module):
    """
    Multi-channel notification module.

    Supports push, email, SMS, and Slack channels with a fluent API.

    Configuration:
        - channels: list of enabled channels (push, email, sms, slack)
        - fcm_server_key: Firebase Cloud Messaging key
        - twilio_account_sid / twilio_auth_token: Twilio credentials
        - slack_webhook_url: Slack incoming webhook
        - slack_bot_token: Slack bot token
    """

    meta = ModuleMeta(
        name="notifications",
        version="1.0.0",
        description="Multi-channel notifications (push, email, SMS, Slack)",
        priority=ModulePriority.DEFAULT,
        dependencies=["mailer"],
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._notifier: Any = None

    def register(self, app: "Vorte") -> None:
        from vorte.modules.notifications.notifier import Notifier

        self._notifier = Notifier(
            channels=self.get_config("channels", ["email"]),
            fcm_server_key=self.get_config("fcm_server_key", ""),
            twilio_account_sid=self.get_config("twilio_account_sid", ""),
            twilio_auth_token=self.get_config("twilio_auth_token", ""),
            twilio_from_number=self.get_config("twilio_from_number", ""),
            slack_webhook_url=self.get_config("slack_webhook_url", ""),
            slack_bot_token=self.get_config("slack_bot_token", ""),
        )
        app.container.register_instance(Notifier, self._notifier)
        logger.debug("Notifications module registered (channels=%s)", self.get_config("channels", ["email"]))

    async def on_startup(self) -> None:
        logger.debug("Notifications module ready")

    async def on_shutdown(self) -> None:
        pass

    async def health_check(self) -> Dict[str, Any]:
        return {"module": self.meta.name, "status": "healthy"}

    def get_notifier(self) -> Any:
        return self._notifier
