from rest_framework import serializers
from .models import Thumbnail


class ThumbnailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Thumbnail
        fields = "__all__"
