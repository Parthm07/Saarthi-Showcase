"""Generate the Neon SQL Editor audit from a clean, migrated PostgreSQL database."""
import os
from pathlib import Path

import psycopg


CATALOGUE = """\
SELECT c.relname::text AS table_name, 'table' AS kind, '' AS object_name, '' AS definition
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p')
UNION ALL
SELECT c.relname::text, 'column', a.attname::text,
       pg_catalog.format_type(a.atttypid,a.atttypmod)
       || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END
       || COALESCE(' DEFAULT ' || pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')
FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
UNION ALL
SELECT c.relname::text, 'constraint', k.conname::text, pg_catalog.pg_get_constraintdef(k.oid)
FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND k.contype IN ('p','u','f','c','x') AND k.convalidated
UNION ALL
SELECT c.relname::text, 'index', idx.relname::text, pg_catalog.pg_get_indexdef(idx.oid)
FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND i.indisvalid AND i.indisready
"""


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def main():
    # Explicit local reference keeps the generator separate from the live URL.
    port = os.environ['SAARTHI_SCHEMA_REFERENCE_PORT']
    with psycopg.connect(host='127.0.0.1', port=port, dbname='postgres', user='saarthi_audit',
                         options='-c search_path=public,pg_catalog -c default_transaction_read_only=on') as connection:
        expected = connection.execute(CATALOGUE + 'ORDER BY 1,2,3').fetchall()
    if ('cloud_order', 'column', 'invoice_date', 'date') not in expected:
        raise SystemExit('Apply the current cloud schema to the disposable reference first.')
    values = ',\n'.join('    (' + ', '.join(map(literal, row)) + ')' for row in expected)
    sql = f"""-- Generated from a clean database with cloud_db.create_schema().
-- Paste the entire query in Neon SQL Editor. An OK row means the listed schema matches.
WITH expected(table_name,kind,object_name,definition) AS (VALUES
{values}
), actual AS (
{CATALOGUE.rstrip()}
), problems AS (
    SELECT e.table_name,e.kind,e.object_name,
           CASE WHEN a.kind IS NULL THEN 'MISSING' ELSE 'MISMATCH' END AS result,
           e.definition AS expected_definition,a.definition AS actual_definition
    FROM expected e LEFT JOIN actual a USING (table_name,kind,object_name)
    WHERE a.definition IS DISTINCT FROM e.definition
)
SELECT current_database() AS database_name,p.* FROM problems p
UNION ALL
SELECT current_database(),NULL,NULL,NULL,'OK',NULL,NULL
WHERE NOT EXISTS (SELECT 1 FROM problems)
ORDER BY 2,3,4;
"""
    Path(__file__).with_name('verify_cloud_schema.sql').write_text(sql, encoding='utf-8')


if __name__ == '__main__':
    main()
