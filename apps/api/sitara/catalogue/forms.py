"""Admin form for inspiration assets (Phase 5B).

The upload is a NON-model field: the raw file is handed straight to the
ingestion service by the admin and is never assigned to a FileField, so
the original upload never touches storage. Status, storage keys, image
facts and approval fields are not on the form at all — they change only
through the services.
"""

from django import forms

from .models import InspirationAsset


class InspirationAssetAdminForm(forms.ModelForm):
    upload = forms.FileField(
        required=False,
        label="Image upload",
        help_text=(
            "JPEG, PNG or WebP only. The file is sanitised (metadata "
            "stripped, re-encoded as WebP) and the original is discarded. "
            "Available only while the asset is a draft without a processed "
            "image."
        ),
    )

    class Meta:
        model = InspirationAsset
        fields = ["title", "alt_text", "garment_type", "cultural_context", "usage_rights"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.pk is not None and (
            instance.status != InspirationAsset.Status.DRAFT or instance.image_storage_key
        ):
            self.fields["upload"].disabled = True
            self.fields["upload"].help_text = (
                "This asset already has a processed image (or is no longer "
                "a draft); retire it and create a new asset to replace the "
                "image."
            )

    def clean_upload(self):
        upload = self.cleaned_data.get("upload")
        if upload and self.instance.pk:
            if self.instance.status != InspirationAsset.Status.DRAFT:
                raise forms.ValidationError("Only a draft asset can receive an image.")
            if self.instance.image_storage_key:
                raise forms.ValidationError(
                    "This asset already has a processed image; retire it and "
                    "create a new asset instead."
                )
        return upload
