# EXPLAINER.md — Playto Payout Engine

---

## 1. The Ledger

**Balance calculation query (from `models.py` → `get_balance_summary`):**

```python
entries = self.ledger_entries.aggregate(
    total_credits=Coalesce(
        Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.CREDIT)),
        Value(0)
    ),
    total_debits=Coalesce(
        Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.DEBIT)),
        Value(0)
    ),
)

held = self.payout_requests.filter(
    status__in=[PayoutRequest.PENDING, PayoutRequest.PROCESSING]
).aggregate(
    total=Coalesce(Sum('amount_paise'), Value(0))
)['total']

available = (total_credits - total_debits) - held
```

**Why this model?**

I never store balance as a column. A stored balance column has a consistency problem: if any bug or race condition updates the ledger without updating the balance column (or vice versa), you get silent drift. The ledger entries become your "truth" and the balance column becomes a lie.

Instead, balance is always derived: `credits - debits - held`. The invariant holds by construction — you cannot have a balance that doesn't match the ledger because the balance IS the ledger.

**Why separate "held" from "debits"?**

A debit entry only gets created when a payout is *completed* (money actually left). While a payout is pending or processing, the funds are "held" — committed but not yet gone. This lets the balance formula be:

```
available = credits - debits - held
```

If I debited immediately on payout creation, a failed payout would require a compensating credit entry — more complexity, more edge cases. The held approach is cleaner: failure just means the payout leaves the PENDING/PROCESSING set, and held automatically decreases.

**Why BigIntegerField and paise?**

Floating point cannot represent 0.1 exactly in binary. `0.1 + 0.2 = 0.30000000000000004`. For money, this is unacceptable. Integer paise (1 rupee = 100 paise) means all arithmetic is exact. There is no `DecimalField` either — while Decimal is safer than float, it adds complexity without benefit when integers work perfectly.

---

## 2. The Lock

**Exact code that prevents concurrent overdraft (`services.py`):**

```python
@transaction.atomic
def _create_payout_atomic(merchant_id, amount_paise, bank_account_id, idempotency_key, expiry_hours):
    # Lock the merchant row for this transaction
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)

    # Calculate available balance INSIDE the lock
    summary = merchant.get_balance_summary()
    available_paise = summary['available_paise']

    if amount_paise > available_paise:
        raise InsufficientFundsError(...)

    payout = PayoutRequest.objects.create(...)
    return payout
```

**The database primitive: `SELECT FOR UPDATE`**

`select_for_update()` translates to `SELECT ... FOR UPDATE` in PostgreSQL. This acquires a row-level exclusive lock on the Merchant row for the duration of the transaction.

**Why this works:**

Without the lock, two concurrent requests both read the balance before either writes:
```
T1: reads available=10000p → 10000 >= 6000 → proceed
T2: reads available=10000p → 10000 >= 6000 → proceed
T1: creates payout (holds 6000p)
T2: creates payout (holds 6000p) ← overdraft: 12000p held on 10000p balance
```

With `SELECT FOR UPDATE`:
```
T1: acquires lock on merchant row
T2: blocks, waiting for T1 to release
T1: reads available=10000p → creates payout (holds 6000p) → commits → releases lock
T2: acquires lock → reads available=4000p → 4000 < 6000 → InsufficientFundsError ✓
```

The lock is released automatically when the `@transaction.atomic` block exits (commit or rollback). The key insight is that the balance check and the payout creation happen **inside the same locked transaction** — you can't check the balance and then have someone else change it before you create the payout.

**Why not optimistic locking?**

Optimistic locking (version number / compare-and-swap) would work but requires a retry loop on the caller side. For payment systems, a clean immediate rejection is better UX than silent retries that might still fail. SELECT FOR UPDATE is the right tool here.

---

## 3. The Idempotency

**How the system knows it has seen a key before:**

The `PayoutRequest` model has a `unique_together = [('merchant', 'idempotency_key')]` constraint at the database level. Before creating a new payout, we query:

```python
existing = PayoutRequest.objects.filter(
    merchant_id=merchant_id,
    idempotency_key=idempotency_key,
    idempotency_key_expires_at__gt=timezone.now(),  # Not expired
).first()

if existing:
    return existing, False  # Same response, no new payout
```

**What happens if the first request is in-flight when the second arrives?**

This is the race condition in idempotency itself. Both requests pass the `existing` check (key not found yet), and both attempt to insert. The `unique_together` database constraint causes the second INSERT to raise an `IntegrityError`. We catch this and re-fetch:

```python
try:
    payout = _create_payout_atomic(...)
    return payout, True
except IntegrityError:
    # The other concurrent request won. Fetch what it created.
    existing = PayoutRequest.objects.filter(
        merchant_id=merchant_id,
        idempotency_key=idempotency_key,
    ).first()
    if existing:
        return existing, False
    raise
```

This is a "optimistic idempotency" pattern: try to insert, handle the collision if it happens. The database constraint is the final enforcer.

**Key scoping and expiry:**
- Keys are scoped per merchant via `unique_together`. Merchant A's key `abc` does not conflict with Merchant B's key `abc`.
- Keys expire after 24 hours (`idempotency_key_expires_at`). Expired keys are excluded from the lookup, allowing re-use of the same key string for a genuinely new request.

---

## 4. The State Machine

**Where failed→completed is blocked (`models.py`):**

```python
VALID_TRANSITIONS = {
    PENDING: [PROCESSING],
    PROCESSING: [COMPLETED, FAILED],
    COMPLETED: [],   # Terminal — no outgoing transitions
    FAILED: [],      # Terminal — no outgoing transitions
}

def can_transition_to(self, new_status):
    return new_status in self.VALID_TRANSITIONS.get(self.status, [])
```

**And in `services.py`, every status change goes through:**

```python
def transition_payout_status(payout_id, new_status, failure_reason=''):
    with transaction.atomic():
        payout = PayoutRequest.objects.select_for_update().get(id=payout_id)

        if not payout.can_transition_to(new_status):
            raise InvalidStateTransitionError(
                f"Cannot transition from {payout.status} to {new_status}"
            )
        # ... apply transition
```

`FAILED: []` means `can_transition_to('completed')` returns `False` for a failed payout (empty list contains no valid transitions). The `InvalidStateTransitionError` is raised before any state change occurs.

The `select_for_update()` on the payout row during transition prevents two workers from transitioning the same payout simultaneously.

---

## 5. The AI Audit

**The bug: wrong aggregation without proper null handling**

When I asked an AI assistant to write the balance calculation, it produced:

```python
# What AI gave me:
def get_balance(self):
    credits = self.ledger_entries.filter(entry_type='credit').aggregate(
        total=Sum('amount_paise')
    )['total']
    debits = self.ledger_entries.filter(entry_type='debit').aggregate(
        total=Sum('amount_paise')
    )['total']
    return credits - debits  # BUG: crashes if no entries exist
```

**What's wrong:** `Sum()` on an empty queryset returns `None`, not `0`. If a merchant has no credits yet, `credits` is `None`, and `None - 0` raises `TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'`. This would crash on every new merchant's first balance check.

**What I replaced it with:**

```python
from django.db.models.functions import Coalesce

entries = self.ledger_entries.aggregate(
    total_credits=Coalesce(
        Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.CREDIT)),
        Value(0)
    ),
    total_debits=Coalesce(
        Sum('amount_paise', filter=models.Q(entry_type=LedgerEntry.DEBIT)),
        Value(0)
    ),
)
```

`Coalesce(Sum(...), Value(0))` evaluates at the database level — if `Sum` returns NULL, `Coalesce` returns 0 instead. This also collapses two queries into one (both aggregations in a single SQL query), which is more efficient.

Additionally, the original AI code did the arithmetic in Python on fetched values. My version keeps the aggregation entirely in the DB — the computation happens in a single SQL `SELECT SUM(...) FILTER WHERE ...` statement, and Python only receives the final integers. This is what the spec means by "balance calculations must use database-level operations."

---

*Built for Playto Founding Engineer Challenge 2026.*
