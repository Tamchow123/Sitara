"""Case-insensitive canonical email identity.

1. Detects existing case-insensitive email collisions and FAILS clearly —
   accounts are never silently merged or deleted.
2. Normalises existing emails (trim + lower-case) to the canonical form.
3. Adds a PostgreSQL functional unique constraint on Lower(email) so no
   ORM path (including bulk_create) can create case-only duplicates.
"""

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def normalize_existing_emails(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    collisions = (
        User.objects.annotate(email_ci=Lower("email"))
        .values("email_ci")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    if collisions.exists():
        duplicates = sorted(row["email_ci"] for row in collisions)
        raise RuntimeError(
            "cannot enforce case-insensitive email uniqueness: "
            f"{len(duplicates)} case-insensitive collision(s) already exist "
            f"({', '.join(duplicates)}). Resolve these accounts manually "
            "before migrating; accounts are never merged or deleted "
            "automatically."
        )

    for user in User.objects.all().iterator():
        canonical = (user.email or "").strip().lower()
        if canonical != user.email:
            user.email = canonical
            user.save(update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            normalize_existing_emails, migrations.RunPython.noop
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"), name="accounts_user_email_ci_unique"
            ),
        ),
    ]
