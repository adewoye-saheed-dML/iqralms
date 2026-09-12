from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('audit_logs', '0002_auditlog_actor_id_snapshot_auditlog_actor_type_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE OR REPLACE FUNCTION protect_audit_log_record()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'Audit logs are append-only and cannot be updated.';
                ELSIF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Audit logs cannot be deleted.';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER protect_audit_log_record_trigger
            BEFORE UPDATE OR DELETE ON audit_logs_auditlog
            FOR EACH ROW
            EXECUTE FUNCTION protect_audit_log_record();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS protect_audit_log_record_trigger ON audit_logs_auditlog;
            DROP FUNCTION IF EXISTS protect_audit_log_record();
            """
        )
    ]
