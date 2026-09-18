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

# Only replace within StudentEnrollment
# We can find 'class StudentEnrollment(models.Model):' and replace inside it.
parts = content.split('class StudentEnrollment(models.Model):')
if len(parts) == 2:
    parts[1] = parts[1].replace(
        '    status = models.CharField(',
        fields + '    status = models.CharField(',
        1
    )
    content = 'class StudentEnrollment(models.Model):'.join(parts)
else:
    print("Failed to split StudentEnrollment")

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

# Replace in clean() of StudentEnrollment
parts2 = content.split('class StudentEnrollment(models.Model):')
if len(parts2) == 2:
    parts2[1] = parts2[1].replace(
        '        if self.user_id and self.user.role != Role.STUDENT:',
        validation + '        if self.user_id and self.user.role != Role.STUDENT:',
        1
    )
    content = 'class StudentEnrollment(models.Model):'.join(parts2)

with open('organizations/models.py', 'w') as f:
    f.write(content)
