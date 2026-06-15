"""
Vorte Mailer Module — Module Registration
==========================================
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.mailer")


class MailerModule(Module):
    """
    Email module with SMTP, SendGrid, Resend, and AWS SES drivers.

    Configuration:
        - driver: ``smtp`` | ``sendgrid`` | ``resend`` | ``ses``
        - host / port / username / password: SMTP settings
        - from_address / from_name: default sender
        - api_key: API key for SendGrid/Resend/SES
        - templates_dir: path to email templates
    """

    meta = ModuleMeta(
        name="mailer",
        version="1.0.0",
        description="Multi-driver email with template rendering and fluent API",
        priority=ModulePriority.DEFAULT,
        dependencies=[],
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._mailer: Any = None

    def register(self, app: "Vorte") -> None:
        from vorte.modules.mailer.mailer import Mailer

        self._mailer = Mailer(
            driver=self.get_config("driver", "smtp"),
            host=self.get_config("host", "localhost"),
            port=self.get_config("port", 587),
            username=self.get_config("username", ""),
            password=self.get_config("password", ""),
            from_address=self.get_config("from_address", "noreply@vorte.dev"),
            from_name=self.get_config("from_name", "Vorte App"),
            api_key=self.get_config("api_key", ""),
            templates_dir=self.get_config("templates_dir", "templates/emails"),
        )
        app.container.register_instance(Mailer, self._mailer)
        logger.debug("Mailer module registered (driver=%s)", self.get_config("driver", "smtp"))

    async def on_startup(self) -> None:
        logger.debug("Mailer module ready")

    async def on_shutdown(self) -> None:
        pass

    async def health_check(self) -> Dict[str, Any]:
        return {"module": self.meta.name, "status": "healthy"}

    def get_mailer(self) -> Any:
        return self._mailer
