import csv
import io
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User, ParentLink
from accounts.validators import validate_iana_timezone
from organizations.models import (
    MembershipStatus,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    InvitationStatus,
)
from .models import ImportJob, ImportKind, ImportStatus

import openpyxl

def parse_import_file(file_obj, file_name: str) -> list[dict]:
    rows = []
    if file_name.lower().endswith('.csv'):
        wrapper = io.TextIOWrapper(file_obj, encoding='utf-8-sig')
        reader = csv.DictReader(wrapper)
        for row in reader:
            rows.append(row)
        wrapper.detach() # Don't close the underlying file
    elif file_name.lower().endswith('.xlsx'):
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        sheet = wb.active
        headers = None
        for row in sheet.iter_rows(values_only=True):
            if headers is None:
                headers = [str(cell).strip() if cell else f"col_{i}" for i, cell in enumerate(row)]
                continue
            
            # Skip completely empty rows
            if not any(row):
                continue
                
            row_dict = {headers[i]: cell for i, cell in enumerate(row) if i < len(headers)}
            rows.append(row_dict)
    else:
        raise ValueError(f"Unsupported file format for {file_name}")
        
    return rows

def normalize_email(email: str) -> str:
    if not email:
        return ""
    return email.strip().lower()

def guess_column_mapping(headers: list[str], kind: str) -> dict:
    mapping = {}
    aliases = {
        "email": ["email", "e-mail", "email address", "email_address"],
        "first_name": ["first name", "given name", "fname", "first_name"],
        "last_name": ["last name", "surname", "family name", "lname", "last_name"],
        "timezone": ["timezone", "time zone", "tz"],
        "date_of_birth": ["date of birth", "dob", "birth date", "date_of_birth"],
        "parent_email": ["parent email", "parent e-mail", "parent", "parent_email"],
        "child_email": ["child email", "student email", "child", "child_email", "student_email"]
    }
    
    for h in headers:
        lower_h = str(h).lower().strip()
        for canonical, alias_list in aliases.items():
            if lower_h in alias_list:
                mapping[h] = canonical
                break
    return mapping

class ImportValidator:
    def __init__(self, job: ImportJob, raw_rows: list[dict]):
        self.job = job
        self.raw_rows = raw_rows
        self.errors = []
        self.valid_rows = []
        self.seen_emails = set()
        
        self.allowed_fields = {"email", "first_name", "last_name", "timezone"}
        if self.job.kind == ImportKind.STUDENTS:
            self.allowed_fields.update({"date_of_birth", "parent_email"})
        elif self.job.kind == ImportKind.PARENTS:
            self.allowed_fields.update({"child_email"})
            
    def _add_error(self, row_idx: int, field: str, code: str, message: str):
        self.errors.append({
            "row": row_idx,
            "field": field,
            "code": code,
            "message": message
        })

    def validate(self):
        # We need a quick way to check if an email exists globally and in academy.
        # But for large files, doing individual DB queries is bad.
        # However, Phase 10 spec says "File size limit" and we can just query them in one go if needed.
        # But actually, doing single queries is fine if the row count is small.
        # Let's prefetch all existing users matching these emails.
        all_emails = set()
        for r in self.raw_rows:
            mapped = self._apply_mapping(r)
            email = normalize_email(mapped.get("email", ""))
            if email:
                all_emails.add(email)
            if self.job.kind == ImportKind.STUDENTS:
                p_email = normalize_email(mapped.get("parent_email", ""))
                if p_email: all_emails.add(p_email)
            elif self.job.kind == ImportKind.PARENTS:
                c_email = normalize_email(mapped.get("child_email", ""))
                if c_email: all_emails.add(c_email)

        # Pre-fetch existing users and their memberships in THIS academy
        users_qs = User.objects.filter(email__in=all_emails)
        existing_users = {}
        ambiguous_emails = set()
        for u in users_qs:
            norm_e = normalize_email(u.email)
            if norm_e in existing_users:
                ambiguous_emails.add(norm_e)
            else:
                existing_users[norm_e] = u
                
        existing_memberships = {
            m.user_id: m for m in 
            OrganizationMembership.objects.filter(
                organization_id=self.job.organization_id, 
                user__email__in=all_emails
            )
        }

        existing_pending_invitations = set(
            OrganizationInvitation.objects.filter(
                organization_id=self.job.organization_id,
                email__in=all_emails,
                status=InvitationStatus.PENDING,
            ).values_list("email", flat=True)
        )
        
        for idx, raw_row in enumerate(self.raw_rows, start=1):
            mapped = self._apply_mapping(raw_row)
            self._validate_row(
                idx,
                mapped,
                existing_users,
                existing_memberships,
                ambiguous_emails,
                existing_pending_invitations,
            )
            
        self.job.row_count = len(self.raw_rows)
        self.job.valid_row_count = len(self.valid_rows)
        self.job.invalid_row_count = self.job.row_count - self.job.valid_row_count
        self.job.error_report = self.errors
        self.job.valid_rows = self.valid_rows
        
        if self.job.valid_row_count > 0:
            self.job.status = ImportStatus.VALIDATED
        else:
            self.job.status = ImportStatus.FAILED

        self.job.save()

    def _apply_mapping(self, row: dict) -> dict:
        mapped = {}
        for h, val in row.items():
            canonical = self.job.column_mapping.get(h)
            if canonical and canonical in self.allowed_fields:
                mapped[canonical] = str(val).strip() if val is not None else ""
        return mapped
        
    def _validate_row(
        self,
        idx: int,
        row: dict,
        existing_users: dict,
        existing_memberships: dict,
        ambiguous_emails: set,
        existing_pending_invitations: set,
    ):
        # 1. Required fields
        email = normalize_email(row.get("email", ""))
        if not email:
            self._add_error(idx, "email", "missing_required", "Email is required.")
            return

        try:
            validate_email(email)
        except ValidationError:
            self._add_error(idx, "email", "invalid_email", "Email address is not valid.")
            return
            
        if email in self.seen_emails:
            self._add_error(idx, "email", "duplicate_row", "Duplicate email within the import file.")
            return
            
        if email in ambiguous_emails:
            self._add_error(idx, "email", "ambiguous_match", "Multiple existing users match this email.")
            return

        if email in existing_pending_invitations:
            self._add_error(idx, "email", "duplicate_pending_invitation", "A pending invitation already exists for this email.")
            return
        
        self.seen_emails.add(email)

        # 2. Timezone (required for students and parents, optional for teachers)
        tz = row.get("timezone", "")
        if not tz:
            if self.job.kind != ImportKind.TEACHERS:
                self._add_error(idx, "timezone", "missing_required", "Timezone is required.")
                return
        else:
            try:
                validate_iana_timezone(tz)
            except ValidationError:
                self._add_error(idx, "timezone", "invalid_timezone", "Timezone is not a valid IANA timezone.")
                return

        # 3. Existing User Logic
        user = existing_users.get(email)
        if user:
            # Check role conflicts globally
            expected_global_role = self._get_expected_global_role()
            if expected_global_role and user.role != expected_global_role:
                # If they are LEAD but we import as TEACHERS, it's fine. We treat TEACHERS as LEAD or SUB.
                if self.job.kind == ImportKind.TEACHERS and user.role in {Role.LEAD, Role.SUB}:
                    pass
                else:
                    self._add_error(idx, "role", "role_conflict", f"Existing user has conflicting role: {user.role}.")
                    return
            
            # Check academy membership
            membership = existing_memberships.get(user.id)
            if membership:
                if membership.status == MembershipStatus.SUSPENDED:
                    self._add_error(idx, "membership", "suspended_membership", "User has a suspended membership. Cannot silently reactivate.")
                    return
                if self.job.kind == ImportKind.TEACHERS:
                    self._add_error(idx, "membership", "existing_member", "User is already an active member of this academy.")
                    return
                # Role conflict in academy?
                expected_org_role = self._get_expected_org_role()
                if membership.role != expected_org_role:
                    self._add_error(idx, "membership", "org_role_conflict", f"Existing membership has conflicting role: {membership.role}.")
                    return

        # 4. Check Parent/Child Links
        if self.job.kind == ImportKind.STUDENTS:
            p_email = normalize_email(row.get("parent_email", ""))
            if p_email:
                if p_email == email:
                    self._add_error(idx, "parent_email", "invalid_reference", "Student cannot be their own parent.")
                    return
                if p_email in ambiguous_emails:
                    self._add_error(idx, "parent_email", "ambiguous_match", "Multiple existing users match this parent email.")
                    return
                # Check if parent exists and is in academy
                parent_user = existing_users.get(p_email)
                if not parent_user or parent_user.role != Role.PARENT:
                    self._add_error(idx, "parent_email", "invalid_reference", "Parent email does not resolve to an existing Parent account.")
                    return
                if not existing_memberships.get(parent_user.id):
                    self._add_error(idx, "parent_email", "missing_membership", "Parent does not belong to this academy.")
                    return
        elif self.job.kind == ImportKind.PARENTS:
            c_email = normalize_email(row.get("child_email", ""))
            if c_email:
                if c_email == email:
                    self._add_error(idx, "child_email", "invalid_reference", "Parent cannot be their own child.")
                    return
                if c_email in ambiguous_emails:
                    self._add_error(idx, "child_email", "ambiguous_match", "Multiple existing users match this child email.")
                    return
                child_user = existing_users.get(c_email)
                if not child_user or child_user.role != Role.STUDENT:
                    self._add_error(idx, "child_email", "invalid_reference", "Child email does not resolve to an existing Student account.")
                    return
                if not existing_memberships.get(child_user.id):
                    self._add_error(idx, "child_email", "missing_membership", "Child does not belong to this academy.")
                    return

        # Dates
        if self.job.kind == ImportKind.STUDENTS:
            dob = row.get("date_of_birth", "")
            if dob:
                # Basic ISO format check YYYY-MM-DD
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', dob):
                    self._add_error(idx, "date_of_birth", "invalid_date", "Date of birth must be YYYY-MM-DD.")
                    return

        self.valid_rows.append(row)

    def _get_expected_global_role(self) -> str:
        if self.job.kind == ImportKind.TEACHERS:
            return Role.LEAD
        if self.job.kind == ImportKind.STUDENTS:
            return Role.STUDENT
        if self.job.kind == ImportKind.PARENTS:
            return Role.PARENT
        return ""
        
    def _get_expected_org_role(self) -> str:
        if self.job.kind == ImportKind.TEACHERS:
            return OrganizationRole.TEACHER
        if self.job.kind == ImportKind.STUDENTS:
            return OrganizationRole.STUDENT
        if self.job.kind == ImportKind.PARENTS:
            return OrganizationRole.PARENT
        return OrganizationRole.STAFF


def commit_import(job: ImportJob):
    if job.status != ImportStatus.VALIDATED:
        raise ValueError("Job is not in a valid state to commit.")

    job.status = ImportStatus.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])

    created_users = 0
    reused_users = 0
    created_memberships = 0
    reused_memberships = 0
    created_parent_links = 0
    skipped_rows = 0

    try:
        if job.kind == ImportKind.TEACHERS:
            import datetime
            from notifications.services import send_invitation_email

            invitations_to_send = []
            with transaction.atomic():
                for row in job.valid_rows:
                    email = normalize_email(row.get("email"))
                    token, digest = OrganizationInvitation.generate_token_and_digest()
                    invitation = OrganizationInvitation.objects.create(
                        organization_id=job.organization_id,
                        email=email,
                        role=OrganizationRole.TEACHER,
                        token_digest=digest,
                        expires_at=timezone.now() + datetime.timedelta(days=7),
                        status=InvitationStatus.PENDING,
                    )
                    invitations_to_send.append((invitation, token))

            emails_sent = 0
            emails_failed = 0
            for invitation, token in invitations_to_send:
                delivery = send_invitation_email(invitation, token)
                if delivery.status == "sent":
                    emails_sent += 1
                else:
                    emails_failed += 1

            job.invitations_created = len(invitations_to_send)
            job.created_count = len(invitations_to_send)
            job.emails_sent = emails_sent
            job.emails_failed = emails_failed
            if emails_failed == 0:
                job.status = ImportStatus.COMPLETED
            elif emails_sent > 0:
                job.status = ImportStatus.PARTIALLY_COMPLETED
            else:
                job.status = (
                    ImportStatus.PARTIALLY_COMPLETED
                    if len(invitations_to_send) > 0
                    else ImportStatus.COMPLETED
                )
            job.completed_at = timezone.now()
            job.save()
            return

        with transaction.atomic():
            for row in job.valid_rows:
                email = normalize_email(row.get("email"))

                # Global role
                if job.kind == ImportKind.STUDENTS:
                    g_role = Role.STUDENT
                    o_role = OrganizationRole.STUDENT
                else:
                    g_role = Role.PARENT
                    o_role = OrganizationRole.PARENT

                # Create or get user
                user, created = User.objects.get_or_create(
                    email=email,
                    defaults={
                        "username": email,
                        "first_name": row.get("first_name", ""),
                        "last_name": row.get("last_name", ""),
                        "timezone": row.get("timezone", "UTC"),
                        "role": g_role,
                        "date_of_birth": row.get("date_of_birth") if row.get("date_of_birth") else None,
                    }
                )

                if created:
                    created_users += 1
                else:
                    reused_users += 1

                # Create or get membership
                membership, m_created = OrganizationMembership.objects.get_or_create(
                    organization_id=job.organization_id,
                    user=user,
                    defaults={
                        "role": o_role,
                        "status": MembershipStatus.ACTIVE,
                    }
                )

                if m_created:
                    created_memberships += 1
                else:
                    reused_memberships += 1

                # Parent links
                if job.kind == ImportKind.STUDENTS and row.get("parent_email"):
                    parent_email = normalize_email(row.get("parent_email"))
                    parent = User.objects.get(email=parent_email)
                    _, link_created = ParentLink.objects.get_or_create(student=user, parent=parent)
                    if link_created:
                        created_parent_links += 1

                elif job.kind == ImportKind.PARENTS and row.get("child_email"):
                    child_email = normalize_email(row.get("child_email"))
                    child = User.objects.get(email=child_email)
                    _, link_created = ParentLink.objects.get_or_create(student=child, parent=user)
                    if link_created:
                        created_parent_links += 1

            job.status = ImportStatus.COMPLETED
            job.created_count = created_users
            job.updated_count = reused_users
            job.completed_at = timezone.now()
            job.save()

    except Exception as e:
        job.status = ImportStatus.FAILED
        job.error_report.append({"fatal": str(e)})
        job.save()
        raise e
