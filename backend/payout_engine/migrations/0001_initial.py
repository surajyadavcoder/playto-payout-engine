from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Merchant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('business_name', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='BankAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('account_number', models.CharField(max_length=20)),
                ('ifsc_code', models.CharField(max_length=11)),
                ('account_holder_name', models.CharField(max_length=255)),
                ('is_primary', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bank_accounts', to='payout_engine.merchant')),
            ],
        ),
        migrations.CreateModel(
            name='PayoutRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_paise', models.BigIntegerField()),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('attempt_count', models.IntegerField(default=0)),
                ('failure_reason', models.TextField(blank=True)),
                ('idempotency_key', models.CharField(db_index=True, max_length=255)),
                ('idempotency_key_expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('processing_started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payout_requests', to='payout_engine.merchant')),
                ('bank_account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payouts', to='payout_engine.bankaccount')),
            ],
        ),
        migrations.CreateModel(
            name='LedgerEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('entry_type', models.CharField(choices=[('credit', 'Credit'), ('debit', 'Debit')], max_length=10)),
                ('amount_paise', models.BigIntegerField()),
                ('description', models.CharField(max_length=500)),
                ('reference_id', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='payout_engine.merchant')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='payoutrequest',
            constraint=models.UniqueConstraint(fields=['merchant', 'idempotency_key'], name='unique_merchant_idempotency_key'),
        ),
        migrations.AlterUniqueTogether(
            name='payoutrequest',
            unique_together={('merchant', 'idempotency_key')},
        ),
        migrations.AddIndex(
            model_name='payoutrequest',
            index=models.Index(fields=['status', 'created_at'], name='payout_engi_status_5d7aeb_idx'),
        ),
        migrations.AddIndex(
            model_name='payoutrequest',
            index=models.Index(fields=['merchant', 'status'], name='payout_engi_merchan_7a2f3b_idx'),
        ),
        migrations.AddIndex(
            model_name='ledgerentry',
            index=models.Index(fields=['merchant', 'entry_type'], name='payout_engi_merchan_3c8d2a_idx'),
        ),
        migrations.AddIndex(
            model_name='ledgerentry',
            index=models.Index(fields=['merchant', 'created_at'], name='payout_engi_merchan_9f1e4b_idx'),
        ),
    ]
