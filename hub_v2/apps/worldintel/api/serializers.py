from rest_framework import serializers


class WorldDumpCitySerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    ally_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    ally_tag = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    level = serializers.IntegerField(required=False, allow_null=True)
    type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    state = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    in_fight = serializers.BooleanField(required=False, default=False)
    has_treaties = serializers.BooleanField(required=False, default=False)
    view_able = serializers.IntegerField(required=False, allow_null=True, default=0)
    infested_by_plague = serializers.BooleanField(required=False, default=False)
    actions = serializers.ListField(required=False, default=list)


class WorldDumpIslandSerializer(serializers.Serializer):
    island_id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    x = serializers.IntegerField(required=False, allow_null=True, default=0)
    y = serializers.IntegerField(required=False, allow_null=True, default=0)
    resource_type = serializers.IntegerField(required=False, allow_null=True, default=0)
    resource_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    resource_level = serializers.IntegerField(required=False, allow_null=True, default=0)
    wood_level = serializers.IntegerField(required=False, allow_null=True, default=0)
    miracle_type = serializers.IntegerField(required=False, allow_null=True, default=0)
    miracle_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    miracle_level = serializers.IntegerField(required=False, allow_null=True, default=0)
    city_count = serializers.IntegerField(required=False, allow_null=True, default=0)
    helios_built = serializers.BooleanField(required=False, default=False)
    cities = WorldDumpCitySerializer(many=True, required=False, default=list)
    avatar_scores = serializers.DictField(required=False, default=dict)


class WorldDumpCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    game_account_id = serializers.UUIDField(required=False, allow_null=True)
    source_job_id = serializers.UUIDField(required=False, allow_null=True)
    scope_mode = serializers.CharField(required=False, allow_blank=True, default="own_islands")
    title = serializers.CharField(required=False, allow_blank=True, default="")
    filters = serializers.JSONField(required=False, default=dict)
    dump_status = serializers.CharField(required=False, default="complete")
    islands = WorldDumpIslandSerializer(many=True, required=False, default=list)


class WorldDumpAppendSerializer(serializers.Serializer):
    islands = WorldDumpIslandSerializer(many=True, required=False, default=list)
    is_final = serializers.BooleanField(required=False, default=False)
