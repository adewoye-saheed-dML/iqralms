# Task 6.4 — Assessment Permissions and Privacy

## Objective

Define and enforce role-specific assessment access within an academy.

## Required access review

- Student: own permitted assessment records and submissions.
- Teacher: assigned or authorized assessment records within the academy.
- Lead: academy-wide assessment management within the academy.
- Parent: linked child's permitted assessment records within the academy.
- Unrelated member: no assessment access unless explicitly authorized.
- Inactive member: no current assessment access.
- Superuser/system access: preserve explicit administrative behavior without weakening ordinary tenant isolation.

## Privacy requirements

- Do not expose private grading notes to students or parents unless explicitly intended.
- Do not expose another student's submissions, grades, feedback, or reports.
- Ensure nested serializers do not bypass parent queryset restrictions.
- Test object-level access independently from list filtering.
