"""esquema inicial del CRM

Tablas del núcleo (roles, users, contacts, consents, leads, appointments, audit_log, imported_citas),
roles del equipo, audit_log solo-inserción (trigger) y permisos mínimos para el rol de la aplicación.
Los campos 🔒 son bytea: se cifran en la aplicación (app/security/crypto.py).

Revision ID: 0001
Revises:
Create Date: 2026-10-24 17:00:17.882380
"""
import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

ROLES = [
    ('admin', 'Administra usuarios, auditoría y privacidad (ARCO)'),
    ('abogado', 'Atiende consultas, citas y casos'),
    ('asistente', 'Gestiona citas y contactos; no exporta datos'),
]
# Tablas que la aplicación lee y modifica. audit_log: solo SELECT e INSERT. roles: solo lectura.
# consents: SELECT e INSERT (cada decisión es una fila nueva; la supresión ARCO llega en la Fase 5).
APP_RW_TABLES = ['users', 'contacts', 'leads', 'appointments', 'imported_citas']
APP_INSERT_ONLY_TABLES = ['consents', 'audit_log']


def _app_role() -> str:
    """Rol de la aplicación (lo crea crm/docker/postgres-init/01-app-role.sh). Configurable para las pruebas."""
    role = os.environ.get('CRM_DB_APP_ROLE', 'jys_app')
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', role):
        raise ValueError('CRM_DB_APP_ROLE no es un nombre de rol válido.')
    return role


def upgrade() -> None:
    op.create_table('roles',
    sa.Column('name', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_roles'))
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('totp_secret', sa.LargeBinary(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('failed_logins', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('key_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lowercase')),
    sa.ForeignKeyConstraint(['role'], ['roles.name'], name=op.f('fk_users_role_roles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor', sa.String(length=10), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('entity', sa.String(length=40), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("actor IN ('user', 'bot', 'script')", name=op.f('ck_audit_log_actor')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_log_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_at'), 'audit_log', ['at'], unique=False)
    op.create_table('contacts',
    sa.Column('phone_hash', sa.String(length=64), nullable=False),
    sa.Column('phone', sa.LargeBinary(), nullable=False),
    sa.Column('full_name', sa.LargeBinary(), nullable=True),
    sa.Column('email', sa.LargeBinary(), nullable=True),
    sa.Column('wa_profile_name', sa.LargeBinary(), nullable=True),
    sa.Column('client_type', sa.String(length=20), server_default='persona_natural', nullable=False),
    sa.Column('source', sa.String(length=20), server_default='whatsapp', nullable=False),
    sa.Column('line', sa.String(length=20), server_default='juridico', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='prospecto', nullable=False),
    sa.Column('assigned_to', sa.UUID(), nullable=True),
    sa.Column('last_interaction_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consent_status', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('key_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("client_type IN ('persona_natural', 'empresa')", name=op.f('ck_contacts_client_type')),
    sa.CheckConstraint("consent_status IN ('pendiente', 'otorgado', 'rechazado', 'revocado')", name=op.f('ck_contacts_consent_status')),
    sa.CheckConstraint("line IN ('juridico', 'inmobiliario')", name=op.f('ck_contacts_line')),
    sa.CheckConstraint("source IN ('whatsapp', 'web', 'manual')", name=op.f('ck_contacts_source')),
    sa.CheckConstraint("status IN ('prospecto', 'cliente', 'inactivo')", name=op.f('ck_contacts_status')),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], name=op.f('fk_contacts_assigned_to_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts')),
    sa.UniqueConstraint('phone_hash', name=op.f('uq_contacts_phone_hash'))
    )
    op.create_table('consents',
    sa.Column('contact_id', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('policy_version', sa.String(length=20), nullable=False),
    sa.Column('policy_url', sa.String(length=300), nullable=False),
    sa.Column('text_shown', sa.Text(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('wa_message_id', sa.String(length=128), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.CheckConstraint("channel IN ('whatsapp_buttons', 'whatsapp_text', 'web_form', 'whatsapp_import')", name=op.f('ck_consents_channel')),
    sa.CheckConstraint("decision IN ('otorgado', 'rechazado', 'revocado')", name=op.f('ck_consents_decision')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_consents_contact_id_contacts'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consents')),
    sa.UniqueConstraint('contact_id', 'decision', 'policy_version', 'decided_at', name='uq_consents_decision')
    )
    op.create_index(op.f('ix_consents_contact_id'), 'consents', ['contact_id'], unique=False)
    op.create_table('leads',
    sa.Column('contact_id', sa.UUID(), nullable=False),
    sa.Column('area', sa.String(length=60), server_default='Otro', nullable=False),
    sa.Column('summary', sa.LargeBinary(), nullable=True),
    sa.Column('stage', sa.String(length=20), server_default='nueva', nullable=False),
    sa.Column('urgent', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('human_requested', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('closed_reason', sa.String(length=200), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('key_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("stage IN ('nueva', 'contactada', 'cita_solicitada', 'cita_confirmada', 'cliente', 'descartada')", name=op.f('ck_leads_stage')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_leads_contact_id_contacts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    op.create_index(op.f('ix_leads_contact_id'), 'leads', ['contact_id'], unique=False)
    op.create_table('appointments',
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('contact_id', sa.UUID(), nullable=False),
    sa.Column('modality', sa.String(length=20), server_default='por_definir', nullable=False),
    sa.Column('preferred_slot', sa.String(length=200), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='solicitada', nullable=False),
    sa.Column('lawyer_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.LargeBinary(), nullable=True),
    sa.Column('source', sa.String(length=20), server_default='valeria', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('key_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("modality IN ('presencial', 'virtual', 'por_definir')", name=op.f('ck_appointments_modality')),
    sa.CheckConstraint("source IN ('valeria', 'panel')", name=op.f('ck_appointments_source')),
    sa.CheckConstraint("status IN ('solicitada', 'confirmada', 'reprogramada', 'cancelada', 'atendida', 'no_asistio')", name=op.f('ck_appointments_status')),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_appointments_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lawyer_id'], ['users.id'], name=op.f('fk_appointments_lawyer_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_appointments_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_appointments'))
    )
    op.create_index('ix_appointments_contact_requested', 'appointments', ['contact_id', 'requested_at'], unique=False)
    op.create_index(op.f('ix_appointments_lead_id'), 'appointments', ['lead_id'], unique=False)
    op.create_table('imported_citas',
    sa.Column('line_hash', sa.String(length=64), nullable=False),
    sa.Column('appointment_id', sa.UUID(), nullable=True),
    sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id'], name=op.f('fk_imported_citas_appointment_id_appointments'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('line_hash', name=op.f('pk_imported_citas'))
    )

    op.bulk_insert(sa.table('roles', sa.column('name', sa.String), sa.column('description', sa.String)),
                   [{'name': n, 'description': d} for n, d in ROLES])

    # audit_log solo admite inserciones: el trigger rechaza UPDATE, DELETE y TRUNCATE para cualquier rol
    # (también para el dueño de la tabla); además, el rol de la aplicación no tiene esos permisos.
    op.execute("""
        CREATE FUNCTION audit_log_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log solo admite inserciones (% rechazado)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change()
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change()
    """)
    op.execute('REVOKE ALL ON audit_log FROM PUBLIC')

    # Permisos mínimos del rol de la aplicación (si existe; en Docker lo crea el script de inicio).
    role = _app_role()
    grants = [f'GRANT USAGE ON SCHEMA public TO {role}', f'GRANT SELECT ON roles TO {role}']
    grants += [f'GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role}' for t in APP_RW_TABLES]
    grants += [f'GRANT SELECT, INSERT ON {t} TO {role}' for t in APP_INSERT_ONLY_TABLES]
    body = ' '.join(f'EXECUTE {sql!r};' for sql in grants)
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                {body}
            ELSE
                RAISE WARNING 'No existe el rol {role}: no se otorgaron permisos a la aplicación.';
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.drop_table('imported_citas')
    op.drop_index(op.f('ix_appointments_lead_id'), table_name='appointments')
    op.drop_index('ix_appointments_contact_requested', table_name='appointments')
    op.drop_table('appointments')
    op.drop_index(op.f('ix_leads_contact_id'), table_name='leads')
    op.drop_table('leads')
    op.drop_index(op.f('ix_consents_contact_id'), table_name='consents')
    op.drop_table('consents')
    op.drop_table('contacts')
    # DROP TABLE no dispara el trigger de TRUNCATE: el downgrade (solo para desarrollo) sí puede borrar audit_log.
    op.drop_index(op.f('ix_audit_log_at'), table_name='audit_log')
    op.drop_table('audit_log')
    op.execute('DROP FUNCTION audit_log_reject_change()')
    op.drop_table('users')
    op.drop_table('roles')
