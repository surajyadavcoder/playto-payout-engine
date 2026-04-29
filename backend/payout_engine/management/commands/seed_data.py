"""
Seed script: creates 3 merchants with realistic credit history.
Run with: python manage.py seed_data
"""
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction


class Command(BaseCommand):
    help = 'Seed the database with merchant test data'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete all data first')

    def handle(self, *args, **options):
        from payout_engine.models import Merchant, BankAccount, LedgerEntry

        if options['reset']:
            self.stdout.write('Resetting data...')
            LedgerEntry.objects.all().delete()
            BankAccount.objects.all().delete()
            Merchant.objects.all().delete()

        merchants_data = [
            {
                'name': 'Priya Sharma',
                'email': 'priya@designcraft.in',
                'business_name': 'DesignCraft Studio',
                'bank': {
                    'account_number': '1234567890123456',
                    'ifsc_code': 'HDFC0001234',
                    'account_holder_name': 'Priya Sharma',
                },
                'credits': [
                    (250000, 'Payment from Acme Corp - Logo Design'),
                    (180000, 'Payment from TechStart Inc - Brand Identity'),
                    (320000, 'Payment from GlobalMedia - UI Design'),
                    (95000, 'Payment from FinanceApp - Icon Set'),
                    (210000, 'Payment from EduPlatform - Website Design'),
                ],
            },
            {
                'name': 'Rahul Mehta',
                'email': 'rahul@codeforge.dev',
                'business_name': 'CodeForge Agency',
                'bank': {
                    'account_number': '9876543210987654',
                    'ifsc_code': 'ICIC0005678',
                    'account_holder_name': 'Rahul Mehta',
                },
                'credits': [
                    (500000, 'Payment from SaaS Co - Backend Development'),
                    (375000, 'Payment from HealthTech - API Integration'),
                    (420000, 'Payment from RetailChain - E-commerce Platform'),
                    (280000, 'Payment from InsureCo - Dashboard Development'),
                ],
            },
            {
                'name': 'Anjali Patel',
                'email': 'anjali@contentwave.io',
                'business_name': 'ContentWave',
                'bank': {
                    'account_number': '1122334455667788',
                    'ifsc_code': 'SBIN0001234',
                    'account_holder_name': 'Anjali Patel',
                },
                'credits': [
                    (150000, 'Payment from MediaCo - Content Strategy Q1'),
                    (120000, 'Payment from FinBlog - 20 Article Package'),
                    (200000, 'Payment from TechReview - Sponsored Content'),
                    (85000, 'Payment from StartupNews - Newsletter Copy'),
                    (175000, 'Payment from GlobalBrand - SEO Content Package'),
                    (95000, 'Payment from EdTech - Course Content Writing'),
                ],
            },
        ]

        for data in merchants_data:
            merchant, created = Merchant.objects.get_or_create(
                email=data['email'],
                defaults={
                    'name': data['name'],
                    'business_name': data['business_name'],
                }
            )

            if created:
                self.stdout.write(f'Created merchant: {merchant.business_name}')

                bank = BankAccount.objects.create(
                    merchant=merchant,
                    is_primary=True,
                    **data['bank'],
                )

                # Add credits spread over the past 30 days
                for i, (amount, description) in enumerate(data['credits']):
                    days_ago = random.randint(1, 30)
                    created_at = timezone.now() - timedelta(days=days_ago, hours=random.randint(0, 23))
                    entry = LedgerEntry(
                        merchant=merchant,
                        entry_type=LedgerEntry.CREDIT,
                        amount_paise=amount,
                        description=description,
                        reference_id=f'PAY-{random.randint(100000, 999999)}',
                    )
                    entry.save()
                    # Update created_at after save (auto_now_add bypasses assignment)
                    LedgerEntry.objects.filter(id=entry.id).update(created_at=created_at)

                self.stdout.write(f'  → Added {len(data["credits"])} credit entries')
            else:
                self.stdout.write(f'Merchant already exists: {merchant.business_name}')

        self.stdout.write(self.style.SUCCESS('\n✅ Seed data created successfully!'))
        self.stdout.write('\nMerchant IDs:')
        for m in Merchant.objects.all():
            balance = m.get_balance_summary()
            self.stdout.write(
                f'  {m.business_name}: {m.id} | '
                f'Available: ₹{balance["available_paise"] / 100:.2f}'
            )
