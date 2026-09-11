# Task 6.3 — Assessment API Tenancy

## Objective

Make assessment endpoints organization-scoped and remove tenant bypasses.

## Requirements

- Follow the repository's established organization-scoped URL convention.
- Scope list, detail, create, update, submit, grade, feedback, and report endpoints.
- Filter all querysets by the resolved organization.
- Scope serializer relation fields using request and organization context.
- Reject foreign keys referencing another academy.
- Return safe not-found or forbidden responses without revealing cross-tenant records.
- Retire or block legacy unscoped routes.
- Verify generated OpenAPI schemas and route parameters.

## Acceptance

A user from Academy A must not list, retrieve, create, update, submit, grade, or report assessment data belonging to Academy B.
