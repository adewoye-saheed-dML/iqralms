import re

with open('accounts/tests/test_tenancy.py', 'r') as f:
    content = f.read()

new_test_1 = """
    def test_inactive_organization_denies_teaching_membership(self):
        self.organization.is_active = False
        self.organization.save()
        self.assertIsNone(
            active_teaching_membership(user=self.lead, organization=self.organization)
        )
"""
content = content.replace("    def test_a_suspended_lead_is_refused(self):", new_test_1 + "\n    def test_a_suspended_lead_is_refused(self):")

new_test_2 = """
    def test_inactive_organization_denies_student_membership(self):
        self.organization.is_active = False
        self.organization.save()
        self.assertIsNone(
            active_student_membership(user=self.student, organization=self.organization)
        )
"""
content = content.replace("    def test_a_suspended_student_is_refused(self):", new_test_2 + "\n    def test_a_suspended_student_is_refused(self):")

with open('accounts/tests/test_tenancy.py', 'w') as f:
    f.write(content)
