import re

with open('organizations/tests/test_api.py', 'r') as f:
    content = f.read()

new_test = """
    def test_an_inactive_organization_denies_suspended_member(self):
        self.org_a.is_active = False
        self.org_a.save()
        response = self.as_user(self.suspended_a).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
"""

content = content.replace("    def test_an_inactive_organization_cannot_be_read(self):", new_test + "\n    def test_an_inactive_organization_cannot_be_read(self):")

with open('organizations/tests/test_api.py', 'w') as f:
    f.write(content)
