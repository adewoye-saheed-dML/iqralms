import re

with open('organizations/serializers.py', 'r') as f:
    content = f.read()

# Add track and level to StudentListSerializer
student_list_fields = """
            "enrollment_status",
            "track_id",
            "level_id",
"""
content = content.replace('            "enrollment_status",', student_list_fields)
content = content.replace('    enrollment_status = serializers.CharField(source="status", read_only=True)', '    enrollment_status = serializers.CharField(source="status", read_only=True)\n    track_id = serializers.IntegerField(source="track.id", read_only=True, allow_null=True)\n    level_id = serializers.IntegerField(source="level.id", read_only=True, allow_null=True)')

# Add track and level to StudentEnrollmentCreateSerializer
create_fields = """
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    from curriculum.models import Track, Level
    track_id = serializers.PrimaryKeyRelatedField(
        queryset=Track.objects.all(), source="track", required=False, allow_null=True
    )
    level_id = serializers.PrimaryKeyRelatedField(
        queryset=Level.objects.all(), source="level", required=False, allow_null=True
    )
"""
content = content.replace('    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())', create_fields)

create_logic = """
        enrollment = StudentEnrollment(
            organization=self.context["organization"],
            user=validated_data["user"],
            track=validated_data.get("track"),
            level=validated_data.get("level"),
            status=EnrollmentStatus.ACTIVE,
        )
"""
content = content.replace("""        enrollment = StudentEnrollment(
            organization=self.context["organization"],
            user=validated_data["user"],
            status=EnrollmentStatus.ACTIVE,
        )""", create_logic)

# Add track and level to StudentEnrollmentUpdateSerializer
update_fields = """
    from .models import EnrollmentStatus
    from curriculum.models import Track, Level
    status = serializers.ChoiceField(choices=EnrollmentStatus.choices, required=False)
    track_id = serializers.PrimaryKeyRelatedField(
        queryset=Track.objects.all(), source="track", required=False, allow_null=True
    )
    level_id = serializers.PrimaryKeyRelatedField(
        queryset=Level.objects.all(), source="level", required=False, allow_null=True
    )
"""
content = content.replace("""    from .models import EnrollmentStatus
    status = serializers.ChoiceField(choices=EnrollmentStatus.choices)""", update_fields)

update_logic = """
        if "status" in validated_data:
            enrollment.status = validated_data["status"]
        if "track" in validated_data:
            enrollment.track = validated_data["track"]
        if "level" in validated_data:
            enrollment.level = validated_data["level"]
"""
content = content.replace("""        if "status" in validated_data:
            enrollment.status = validated_data["status"]""", update_logic)

with open('organizations/serializers.py', 'w') as f:
    f.write(content)
