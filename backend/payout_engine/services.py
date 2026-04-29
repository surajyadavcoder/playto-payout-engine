"""
SERVICE LAYER - Where all the hard problems live.

This module handles:
1. Idempotency key lookup/creation
2. Concurrency-safe balance check + hold (SELECT FOR UPDATE)
3. State machine transitions with validation
4. Atomic fund returns on failure
"""

import logging
import random
import time
from datetime import timedelta

from django.db import transaction, IntegrityError
from django.db.models import Sum, Value, F
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Merchant, BankAccount, LedgerEntry, PayoutRequest
from .exceptions import (
    InsufficientFundsError,
    InvalidStateTransitionError,
    IdempotencyConflictError,
    PayoutNotFoundError,
)

logger = logging.getLogger(__name__)


def get_or_create_payout(merchant_id, amount_paise, bank_account_id, idempotency_key):
    """
    Main entry point for payout creation.
    Returns (payout, created) where created=False means it was a duplicate request.

    IDEMPOTENCY DESIGN:
    - We first check if (merchant, idempotency_key) already exists.
    - If yes and not expired → return existing payout (same response as first call).
    - If the first request is still in-flight (very unlikely due to DB transaction),
      the unique_together constraint causes an IntegrityError on the second insert,
      which we catch and re-fetch. This handles the "simultaneous first requests" edge case.
    - Keys expire after 24 hours (configurable).
    """
    from django.conf import settings

    expiry_hours = getattr(settings, 'IDEMPOTENCY_KEY_EXPIRY_HOURS', 24)

    # Check for existing non-expired idempotency key
    existing = PayoutRequest.objects.filter(
        merchant_id=merchant_id,
        idempotency_key=idempotency_key,
        idempotency_key_expires_at__gt=timezone.now(),
    ).first()

    if existing:
        logger.info(f"Idempotency hit: key={idempotency_key}, payout={existing.id}")
        return existing, False

    # New request — attempt to create
    try:
        payout = _create_payout_atomic(merchant_id, amount_paise, bank_account_id, idempotency_key, expiry_hours)
        return payout, True
    except IntegrityError:
        # Race condition: two simultaneous requests with same key both passed the
        # "existing" check above before either committed. The unique_together
        # constraint saves us — one wins, one gets IntegrityError. Re-fetch the winner.
        existing = PayoutRequest.objects.filter(
            merchant_id=merchant_id,
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            return existing, False
        raise


@transaction.atomic
def _create_payout_atomic(merchant_id, amount_paise, bank_account_id, idempotency_key, expiry_hours):
    """
    THE CRITICAL SECTION.

    We use SELECT FOR UPDATE to lock the merchant's ledger entries for the
    duration of this transaction. This prevents two concurrent payout requests
    from both reading the same available balance and both succeeding.

    Without SELECT FOR UPDATE:
        T1 reads balance = 10000p, sees 10000 >= 6000, proceeds
        T2 reads balance = 10000p, sees 10000 >= 6000, proceeds
        Both create payouts → overdraft

    With SELECT FOR UPDATE:
        T1 acquires lock, reads balance = 10000p, creates payout (holds 6000p)
        T2 blocks until T1 commits
        T2 acquires lock, reads balance = 4000p available, rejects 6000p request
        → Exactly one succeeds ✓

    The lock is on the Merchant row itself. We select it with select_for_update()
    so both transactions compete for the same row lock.
    """

    # Lock the merchant row for this transaction
    # Any other transaction trying to lock the same merchant will wait here
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)

    bank_account = BankAccount.objects.get(id=bank_account_id, merchant_id=merchant_id)

    # Calculate available balance INSIDE the lock
    # This is a pure DB aggregation — no Python arithmetic on fetched rows
    summary = merchant.get_balance_summary()
    available_paise = summary['available_paise']

    if amount_paise > available_paise:
        raise InsufficientFundsError(
            f"Insufficient funds: requested {amount_paise}p, available {available_paise}p"
        )

    if amount_paise <= 0:
        raise ValueError("Payout amount must be positive")

    payout = PayoutRequest.objects.create(
        merchant=merchant,
        bank_account=bank_account,
        amount_paise=amount_paise,
        status=PayoutRequest.PENDING,
        idempotency_key=idempotency_key,
        idempotency_key_expires_at=timezone.now() + timedelta(hours=expiry_hours),
    )

    logger.info(f"Payout created: {payout.id} for merchant {merchant_id}, amount={amount_paise}p")
    return payout


def transition_payout_status(payout_id, new_status, failure_reason=''):
    """
    Validates and applies a state machine transition.
    For FAILED transitions, atomically returns held funds.
    For COMPLETED transitions, atomically creates debit ledger entry.
    """
    with transaction.atomic():
        # Lock the payout row during transition
        payout = PayoutRequest.objects.select_for_update().get(id=payout_id)

        if not payout.can_transition_to(new_status):
            raise InvalidStateTransitionError(
                f"Cannot transition from {payout.status} to {new_status} "
                f"for payout {payout_id}"
            )

        old_status = payout.status
        payout.status = new_status
        payout.updated_at = timezone.now()

        if new_status == PayoutRequest.PROCESSING:
            payout.processing_started_at = timezone.now()
            payout.attempt_count = F('attempt_count') + 1

        elif new_status == PayoutRequest.COMPLETED:
            payout.completed_at = timezone.now()
            # Create debit ledger entry — this is what reduces the merchant's net balance
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.DEBIT,
                amount_paise=payout.amount_paise,
                description=f"Payout to {payout.bank_account.account_number[-4:].zfill(4)}",
                reference_id=str(payout.id),
            )

        elif new_status == PayoutRequest.FAILED:
            payout.failure_reason = failure_reason
            # Funds are released simply by the payout moving out of PENDING/PROCESSING.
            # The balance formula counts held = sum of PENDING/PROCESSING payouts.
            # When status becomes FAILED, it's no longer counted → funds automatically return.
            # No separate transaction or entry needed. This is atomic with the status change.

        payout.save()
        logger.info(f"Payout {payout_id}: {old_status} → {new_status}")
        return payout


def process_payout(payout_id):
    """
    Called by the background worker (Django-Q).
    Simulates bank API call with realistic outcomes:
    - 70% success
    - 20% failure
    - 10% hang in processing (will be retried by the stuck-payout detector)
    """
    from django.conf import settings

    max_retries = getattr(settings, 'PAYOUT_MAX_RETRIES', 3)

    try:
        payout = PayoutRequest.objects.get(id=payout_id)
    except PayoutRequest.DoesNotExist:
        logger.error(f"Payout {payout_id} not found for processing")
        return

    if payout.status not in [PayoutRequest.PENDING, PayoutRequest.PROCESSING]:
        logger.warning(f"Payout {payout_id} in unexpected state {payout.status}, skipping")
        return

    if payout.attempt_count >= max_retries:
        logger.warning(f"Payout {payout_id} exceeded max retries, marking failed")
        transition_payout_status(payout_id, PayoutRequest.FAILED, "Max retries exceeded")
        return

    # Move to processing
    transition_payout_status(payout_id, PayoutRequest.PROCESSING)

    # Simulate bank API latency
    time.sleep(random.uniform(0.1, 0.5))

    # Simulate outcome: 70% success, 20% fail, 10% hang
    outcome = random.random()

    if outcome < 0.70:
        # Success
        transition_payout_status(payout_id, PayoutRequest.COMPLETED)
        logger.info(f"Payout {payout_id} completed successfully")

    elif outcome < 0.90:
        # Failure — funds return atomically with this transition
        transition_payout_status(
            payout_id,
            PayoutRequest.FAILED,
            "Bank rejected the transfer"
        )
        logger.info(f"Payout {payout_id} failed, funds returned")

    else:
        # Hang — payout stays in PROCESSING
        # The stuck-payout detector will retry it after PAYOUT_PROCESSING_TIMEOUT_SECONDS
        logger.warning(f"Payout {payout_id} hung in processing state")


def retry_stuck_payouts():
    """
    Scheduled job: finds payouts stuck in PROCESSING for too long and retries them.
    Exponential backoff is implemented by the caller scheduling this at increasing intervals.
    """
    from django.conf import settings

    timeout_seconds = getattr(settings, 'PAYOUT_PROCESSING_TIMEOUT_SECONDS', 30)
    max_retries = getattr(settings, 'PAYOUT_MAX_RETRIES', 3)

    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)

    stuck_payouts = PayoutRequest.objects.filter(
        status=PayoutRequest.PROCESSING,
        processing_started_at__lt=cutoff,
    )

    for payout in stuck_payouts:
        if payout.attempt_count >= max_retries:
            logger.warning(f"Payout {payout.id} stuck and exceeded retries, marking failed")
            transition_payout_status(str(payout.id), PayoutRequest.FAILED, "Processing timeout, max retries exceeded")
        else:
            logger.info(f"Retrying stuck payout {payout.id} (attempt {payout.attempt_count})")
            # Reset to pending so process_payout picks it up fresh
            with transaction.atomic():
                payout_locked = PayoutRequest.objects.select_for_update().get(id=payout.id)
                if payout_locked.status == PayoutRequest.PROCESSING:
                    payout_locked.status = PayoutRequest.PENDING
                    payout_locked.save()
            process_payout(str(payout.id))
