"""Add Unknown to clip subjects while preserving all existing rows and links."""
import re

def ensure_unknown_subjects(conn):
    row=conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='clip_detections'").fetchone()
    if row is None or "'Unknown'" in row[0]: return
    sql=row[0]
    if "('Person','Cat','Dog','Vehicle')" not in sql: raise RuntimeError('Unexpected subject schema; unknown migration was not applied.')
    if conn.in_transaction: raise RuntimeError('Unknown migration must precede catalog transactions.')
    definitions=[r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='clip_detections' AND type IN ('index','trigger') AND sql IS NOT NULL")]
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('BEGIN IMMEDIATE')
        current=conn.execute("SELECT sql FROM sqlite_master WHERE name='clip_detections'").fetchone()[0]
        if "'Unknown'" in current:
            conn.commit();return
        replacement=re.sub(r'CREATE TABLE\s+["\[]?clip_detections["\]]?', 'CREATE TABLE clip_detections_unknown_new',sql,count=1,flags=re.I)
        replacement=replacement.replace("('Person','Cat','Dog','Vehicle')","('Person','Cat','Dog','Vehicle','Unknown')")
        conn.execute(replacement)
        conn.execute('INSERT INTO clip_detections_unknown_new SELECT * FROM clip_detections')
        conn.execute('DROP TABLE clip_detections')
        conn.execute('ALTER TABLE clip_detections_unknown_new RENAME TO clip_detections')
        for definition in definitions: conn.execute(definition)
        if conn.execute('PRAGMA foreign_key_check').fetchone(): raise RuntimeError('Unknown migration failed link validation.')
        conn.commit()
    except Exception:
        conn.rollback();raise
    finally:
        conn.execute('PRAGMA foreign_keys=ON')
