import re

with open('organizations/models.py', 'r') as f:
    content = f.read()

fields = """
    track = models.ForeignKey(
        "curriculum.Track",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_enrollments",
        help_text="The academic track the student is enrolled in.",
    )
    level = models.ForeignKey(
        "curriculum.Level",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_enrollments",
        help_text="The student's current level in the track.",
    )
"""

content = content.replace("    status = models.CharField(", fields + "    status = models.CharField(")

validation = """
        if getattr(self, "track_id", None):
            if self.track.organization_id != self.organization_id:
                raise ValidationError(
                    {
                        "track": ValidationError(
                            "The track belongs to a different organization.",
                            code="track_organization_mismatch",
                        )
                    }
                )
        if getattr(self, "level_id", None):
            if not getattr(self, "track_id", None):
                raise ValidationError(
                    {
                        "level": ValidationError(
                            "Cannot set a level without a track.",
                            code="level_without_track",
                        )
                    }
                )
            if self.level.track_id != self.track_id:
                raise ValidationError(
                    {
                        "level": ValidationError(
                            "The level belongs to a different track.",
                            code="level_track_mismatch",
                        )
                    }
                )
"""

content = content.replace("        if self.user_id and self.user.role != Role.STUDENT:", validation + "        if self.user_id and self.user.role != Role.STUDENT:")

with open('organizations/models.py', 'w') as f:
    f.write(content)
