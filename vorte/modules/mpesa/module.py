"""
Vorte M-Pesa Module — Module Registration
=========================================
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from vorte.core.module import Module, ModuleMeta, ModulePriority

if TYPE_CHECKING:
    from vorte.core.app import Vorte

logger = logging.getLogger("vorte.modules.mpesa")


class MpesaModule(Module):
    """
    M-Pesa payments module for Safaricom Daraja API.

    Features:
        - STK Push (Lipa Na M-Pesa Online)
        - C2B (Customer to Business)
        - B2C (Business to Customer)
        - B2B (Business to Business)
        - Account Balance
        - Transaction Status
        - Reversal
        - QR Code generation

    Configuration:
        - environment: ``sandbox`` | ``production``
        - consumer_key: Daraja app consumer key
        - consumer_secret: Daraja app consumer secret
        - shortcode: business shortcode
        - passkey: Lipa Na M-Pesa passkey
        - callback_url: base URL for callbacks
        - b2c_security_credential: B2C security credential
    """

    meta = ModuleMeta(
        name="mpesa",
        version="1.0.0",
        description="Safaricom M-Pesa payments (STK Push, C2B, B2C, B2B, etc.)",
        priority=ModulePriority.PAYMENTS,
        dependencies=[],
    )

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self._client: Any = None

    def register(self, app: "Vorte") -> None:
        from vorte.modules.mpesa.mpesa import MpesaClient

        self._client = MpesaClient(
            environment=self.get_config("environment", "sandbox"),
            consumer_key=self.get_config("consumer_key", ""),
            consumer_secret=self.get_config("consumer_secret", ""),
            shortcode=self.get_config("shortcode", ""),
            passkey=self.get_config("passkey", ""),
            callback_url=self.get_config("callback_url", ""),
            b2c_security_credential=self.get_config("b2c_security_credential", ""),
        )
        app.container.register_instance(MpesaClient, self._client)
        self._register_callback_routes(app)
        logger.debug("M-Pesa module registered (env=%s)", self.get_config("environment", "sandbox"))

    async def on_startup(self) -> None:
        try:
            token = await self._client.get_access_token()
            logger.debug("M-Pesa access token obtained: %s...", token[:12] if token else "None")
        except Exception as exc:
            logger.warning("M-Pesa startup: could not obtain access token: %s", exc)

    async def on_shutdown(self) -> None:
        pass

    async def health_check(self) -> Dict[str, Any]:
        try:
            token = await self._client.get_access_token()
            return {"module": self.meta.name, "status": "healthy" if token else "degraded"}
        except Exception as exc:
            return {"module": self.meta.name, "status": "unhealthy", "error": str(exc)}

    def _register_callback_routes(self, app: "Vorte") -> None:
        """Register M-Pesa callback routes."""

        @app.post("/api/mpesa/callback/stk", tags=["M-Pesa"], include_in_schema=False)
        async def stk_callback(body: Dict[str, Any]) -> Dict[str, Any]:
            from vorte.modules.mpesa.events import MpesaPaymentReceived, MpesaPaymentFailed
            body_copy = dict(body)
            result_code = body_copy.get("Body", {}).get("stkCallback", {}).get("ResultCode")
            if result_code == 0:
                event = MpesaPaymentReceived.from_callback(body_copy)
                await app.emit("mpesa.payment.received", event)
            else:
                event = MpesaPaymentFailed.from_callback(body_copy)
                await app.emit("mpesa.payment.failed", event)
            return {"status": "acknowledged"}

        @app.post("/api/mpesa/callback/c2b", tags=["M-Pesa"], include_in_schema=False)
        async def c2b_callback(body: Dict[str, Any]) -> Dict[str, Any]:
            await app.emit("mpesa.c2b.received", body)
            return {"status": "acknowledged"}

        @app.post("/api/mpesa/callback/b2c/result", tags=["M-Pesa"], include_in_schema=False)
        async def b2c_result_callback(body: Dict[str, Any]) -> Dict[str, Any]:
            await app.emit("mpesa.b2c.result", body)
            return {"status": "acknowledged"}

        @app.post("/api/mpesa/callback/b2c/timeout", tags=["M-Pesa"], include_in_schema=False)
        async def b2c_timeout_callback(body: Dict[str, Any]) -> Dict[str, Any]:
            from vorte.modules.mpesa.events import MpesaPaymentTimeout
            event = MpesaPaymentTimeout(transaction_id="", reason="b2c_timeout", raw=body)
            await app.emit("mpesa.b2c.timeout", event)
            return {"status": "acknowledged"}

    def get_client(self) -> Any:
        return self._client
