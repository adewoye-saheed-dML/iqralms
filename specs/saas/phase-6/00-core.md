# Phase 6 Core — Assessment Tenancy

## Goal

Make all assessment-domain data organization-aware without weakening existing assessment behavior.

## Required sequence

1. Audit ownership and data flows.
2. Decide whether ownership is derived or explicitly stored.
3. Define valid student, teacher, parent, curriculum, and assessment relationships.
4. Implement model-level invariants.
5. Scope APIs and serializers.
6. Enforce permissions and privacy.
7. Add adversarial tenant-isolation tests.
8. Remediate legacy data safely.
9. Complete PostgreSQL, API, OpenAPI, and manual acceptance.
10. Update documentation and close the phase.

## Non-negotiable boundary

No assessment object may be readable or writable merely because its numeric ID is known. Every access path must resolve and validate the organization boundary.
