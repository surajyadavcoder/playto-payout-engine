from django.apps import AppConfig


class PayoutEngineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payout_engine'
    verbose_name = 'Payout Engine'

    def ready(self):
        # Schedule the stuck payout retry job when app starts
        try:
            from django_q.models import Schedule
            from django_q.tasks import schedule
            Schedule.objects.get_or_create(
                name='retry_stuck_payouts',
                defaults={
                    'func': 'payout_engine.services.retry_stuck_payouts',
                    'schedule_type': Schedule.MINUTES,
                    'minutes': 1,
                }
            )
        except Exception:
            pass  # DB might not be ready yet (e.g., during migrations)
