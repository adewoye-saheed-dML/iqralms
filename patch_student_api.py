import re

with open('organizations/tests/test_student_api.py', 'r') as f:
    content = f.read()

content = content.replace("self.add_member(student)", "from organizations.models import OrganizationMembership, OrganizationRole; OrganizationMembership.objects.create(organization=self.org, user=student, role=OrganizationRole.STAFF)")

with open('organizations/tests/test_student_api.py', 'w') as f:
    f.write(content)
