"""Append-only operator-selected WhatsApp account metadata, NOT provider trust.

This service has no HTTP route, webhook, Meta secret, account discovery,
Mind/Worker capability or transport. It does not authenticate a provider.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from app.auth import OperatorPrincipal
from app.models import (
    ChannelOperatorAccountRevocationRecord,
    ChannelOperatorAccountSelectionRecord,
)
from app.storage import SessionStore

_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ZERO = "0" * 64


def _key(value: str) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None or value == _ZERO:
        raise ValueError("channel_account_selection_identity_invalid")
    return value


def _operator(principal: OperatorPrincipal) -> None:
    if type(principal) is not OperatorPrincipal or principal.subject != "ahmad":
        raise PermissionError("channel_operator_required")


@dataclass(frozen=True)
class OperatorSelectedWhatsAppAccount:
    """Historical operator choice, not a verified provider account."""

    account_key: str
    app_secret_identity: str
    selected_at: datetime
    currently_selected: bool
    provider_authenticated: bool = False
    mind_admission_authority: bool = False
    delivery_authority: bool = False


class ChannelOperatorAccountStore:
    """Serialize one-time selection and irreversible revocation for opaque keys.

    No runtime admission/dispatch path is wired to this store. The app secret
    identity is an opaque, *Core-supplied* fingerprint; do not derive it from
    an attacker-supplied low-entropy value and assume it proves Meta identity.
    """

    def __init__(self, database: SessionStore) -> None:
        self.database = database

    @staticmethod
    def _snapshot(
        row: ChannelOperatorAccountSelectionRecord, *, revoked: bool
    ) -> OperatorSelectedWhatsAppAccount:
        if (
            _key(row.account_key) != row.account_key
            or _key(row.app_secret_identity) != row.app_secret_identity
            or row.operator_id != "ahmad"
            or row.provider_authenticated
            or row.mind_admission_authority
            or row.delivery_authority
        ):
            raise ValueError("channel_account_selection_record_invalid")
        return OperatorSelectedWhatsAppAccount(
            account_key=row.account_key,
            app_secret_identity=row.app_secret_identity,
            selected_at=row.selected_at,
            currently_selected=not revoked,
        )

    def select(
        self,
        *,
        account_key: str,
        app_secret_identity: str,
        principal: OperatorPrincipal,
    ) -> tuple[OperatorSelectedWhatsAppAccount, bool]:
        """Record a single operator choice; exact retries remain idempotent."""

        _operator(principal)
        account_key = _key(account_key)
        app_secret_identity = _key(app_secret_identity)
        with self.database.transaction() as session:
            row = session.get(ChannelOperatorAccountSelectionRecord, account_key)
            if row is not None:
                if session.get(ChannelOperatorAccountRevocationRecord, account_key) is not None:
                    raise ValueError("channel_account_selection_revoked")
                snapshot = self._snapshot(row, revoked=False)
                if snapshot.app_secret_identity != app_secret_identity:
                    raise ValueError("channel_account_selection_conflict")
                return snapshot, False

            row = ChannelOperatorAccountSelectionRecord(
                account_key=account_key,
                app_secret_identity=app_secret_identity,
                operator_id=principal.subject,
                provider_authenticated=False,
                mind_admission_authority=False,
                delivery_authority=False,
            )
            session.add(row)
            session.flush()
            return self._snapshot(row, revoked=False), True

    def revoke(self, *, account_key: str, principal: OperatorPrincipal) -> bool:
        """Permanently refuse an opaque account key; no implicit reactivation."""

        _operator(principal)
        account_key = _key(account_key)
        with self.database.transaction() as session:
            if session.get(ChannelOperatorAccountSelectionRecord, account_key) is None:
                raise LookupError("channel_account_not_selected")
            if session.get(ChannelOperatorAccountRevocationRecord, account_key) is not None:
                return False
            session.add(
                ChannelOperatorAccountRevocationRecord(
                    account_key=account_key, operator_id=principal.subject
                )
            )
            session.flush()
            return True

    def get(
        self, account_key: str, *, principal: OperatorPrincipal
    ) -> OperatorSelectedWhatsAppAccount | None:
        """Operator-only read; revoked identities remain historical but inactive."""

        _operator(principal)
        account_key = _key(account_key)
        with self.database.read() as session:
            row = session.get(ChannelOperatorAccountSelectionRecord, account_key)
            if row is None:
                return None
            revoked = session.get(ChannelOperatorAccountRevocationRecord, account_key)
            return self._snapshot(row, revoked=revoked is not None)
