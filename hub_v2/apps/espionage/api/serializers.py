"""
Serializers para a API de relatórios de espionagem.
"""

from rest_framework import serializers


class _SpyReportSerializer(serializers.Serializer):
    report_id = serializers.CharField(max_length=32)
    source_city_id = serializers.CharField(required=False, default="", allow_blank=True)
    target_city_id = serializers.CharField(required=False, default="", allow_blank=True)
    target_city_name = serializers.CharField(required=False, default="", allow_blank=True)
    target_x = serializers.IntegerField(required=False, allow_null=True, default=None)
    target_y = serializers.IntegerField(required=False, allow_null=True, default=None)
    target_owner = serializers.CharField(required=False, default="", allow_blank=True)
    target_owner_id = serializers.CharField(required=False, default="", allow_blank=True)
    mission_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    mission_name = serializers.CharField(required=False, default="", allow_blank=True)
    subject = serializers.CharField(required=False, default="", allow_blank=True)
    status = serializers.CharField(required=False, default="", allow_blank=True)
    result_status = serializers.CharField(required=False, default="", allow_blank=True)
    agents_sent = serializers.IntegerField(required=False, default=0)
    agents_lost = serializers.IntegerField(required=False, default=0)
    decoys_sent = serializers.IntegerField(required=False, default=0)
    decoys_lost = serializers.IntegerField(required=False, default=0)
    report_html = serializers.CharField(required=False, default="", allow_blank=True)
    report_text = serializers.CharField(required=False, default="", allow_blank=True)
    data_json = serializers.DictField(required=False, default=dict)
    date_str = serializers.CharField(required=False, default="", allow_blank=True)
    is_read = serializers.BooleanField(required=False, default=False)


class SpyReportsSaveSerializer(serializers.Serializer):
    game_account_id = serializers.UUIDField()
    reports = _SpyReportSerializer(many=True)
