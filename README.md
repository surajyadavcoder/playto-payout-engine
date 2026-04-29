# Playto Payout Engine

Cross-border payout infrastructure for Indian merchants. Merchants accumulate balance from international payments and withdraw to their Indian bank account.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────┐
│   React Frontend │────▶│  Django + DRF API │────▶│  PostgreSQL  │
│   (Vite + TW)   │     │  (Gunicorn)       │     │  (Ledger DB) │
└─────────────────┘     └──────────────────┘     └──────────────┘
                                │
                         ┌──────▼──────┐
                         │  Django-Q   │
                         │  (Workers)  │
                         └─────────────┘
```

**Key design decisions:**
- Balance derived from ledger (never stored) — eliminates balance drift
- All amounts in paise (BigIntegerField) — exact integer arithmetic, no floats
- `SELECT FOR UPDATE` on merchant row — prevents concurrent overdraft
- `unique_together` DB constraint — idempotency backed by the database, not application logic
- Append-only ledger entries — complete audit trail

## Quick Start (Docker)

```bash
git clone <your-repo>
cd playto-payout
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/api/v1/
- Admin: http://localhost:8000/admin/

## Local Development

### Backend

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your PostgreSQL credentials

# Run migrations
python manage.py migrate

# Seed test data (3 merchants with credit history)
python manage.py seed_data

# Start API server
python manage.py runserver

# Start background worker (separate terminal)
python manage.py qcluster
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at http://localhost:5173, proxies `/api` to the Django backend.

## Environment Variables

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
DB_NAME=playto_payout
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
ALLOWED_HOSTS=localhost,127.0.0.1
IDEMPOTENCY_KEY_EXPIRY_HOURS=24
PAYOUT_PROCESSING_TIMEOUT_SECONDS=30
PAYOUT_MAX_RETRIES=3
```

## API Reference

### List merchants
```
GET /api/v1/merchants/
```

### Merchant dashboard (balance, ledger, payouts)
```
GET /api/v1/merchants/{merchant_id}/
```

### Request payout
```
POST /api/v1/merchants/{merchant_id}/payouts/
Headers:
  Content-Type: application/json
  Idempotency-Key: <uuid>

Body:
{
  "amount_paise": 50000,
  "bank_account_id": "<uuid>"
}

Response 201: Payout created
Response 200: Idempotent response (key already seen)
Response 422: Insufficient funds
Response 400: Validation error
```

### List payouts
```
GET /api/v1/merchants/{merchant_id}/payouts/list/
GET /api/v1/merchants/{merchant_id}/payouts/list/?status=pending
```

### Get specific payout
```
GET /api/v1/merchants/{merchant_id}/payouts/{payout_id}/
```

## Running Tests

```bash
cd backend
python manage.py test payout_engine.tests --verbosity=2
```

Tests cover:
- **Concurrency**: Two simultaneous 60-rupee requests against a 100-rupee balance → exactly one succeeds
- **Idempotency**: Same key twice → same payout returned, no duplicate created
- **State machine**: All valid transitions, all illegal transitions raise `InvalidStateTransitionError`
- **Balance integrity**: Credits, debits, and held amounts always sum correctly
- **Fund return**: Failed payouts release held funds atomically

## Deployment (Railway)

1. Push to GitHub
2. Create new Railway project
3. Add PostgreSQL service
4. Add backend service (root: `backend/`, start: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`)
5. Add worker service (same repo, start: `python manage.py qcluster`)
6. Add frontend service (root: `frontend/`, build: `npm run build`, output: `dist/`)
7. Set environment variables for each service

## Seed Data

Three merchants are seeded with realistic credit history:

| Merchant | Business | Available Balance |
|----------|----------|-------------------|
| Priya Sharma | DesignCraft Studio | ~₹10,550 |
| Rahul Mehta | CodeForge Agency | ~₹15,750 |
| Anjali Patel | ContentWave | ~₹8,250 |

Balances vary based on simulated payout outcomes.

## What I'm Most Proud Of

The fund-return mechanism on payout failure. Rather than creating a compensating credit entry (which adds ledger noise and complexity), the balance formula counts held funds as the sum of PENDING/PROCESSING payouts. When a payout transitions to FAILED, it simply leaves that set — no debit was ever recorded, no credit needs to be reversed. The state transition and fund release are atomic by construction. This is one of those designs that seems obvious in hindsight but requires understanding the full system to arrive at.
