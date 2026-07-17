"""Questionnaire routes.

Slash-optional for the same reason as the auth and design routes: the
Next.js rewrite strips trailing slashes, and an APPEND_SLASH redirect
through the proxy would loop.
"""

from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        r"^questionnaire/active/?$",
        views.ActiveQuestionnaireView.as_view(),
        name="questionnaire-active",
    ),
]
