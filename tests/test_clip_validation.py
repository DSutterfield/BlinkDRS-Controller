import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from catalog_store import list_clips, set_clip_validation


class ClipValidationTests(unittest.TestCase):
    def test_visibility_counts_pagination_and_restoration(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'catalog.db'
            schema = Path(__file__).resolve().parent / 'catalog_schema_v1.sql'
            if not schema.exists():
                schema = Path(__file__).resolve().parents[1] / 'sql/catalog_schema_v1.sql'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript(schema.read_text())
                for i, duration in [(1, 0), (2, None), (3, 30000)]:
                    conn.execute('''INSERT INTO clips
                        (id, filename, video_path, captured_at, duration_ms, watched)
                        VALUES (?, ?, ?, ?, ?, 0)''',
                        (i, f'{i}.mp4', f'clips/{i}.mp4', f'2026-09-11T12:00:0{i}', duration))
            self.assertEqual(list_clips(db)['total'], 3)
            set_clip_validation(db, 1, 'damaged', 'Confirmed decode failure', 'a'*64)
            result = list_clips(db, limit=1, offset=1)
            self.assertEqual((result['total'], result['unreviewed_total'], result['count']), (2, 2, 1))
            self.assertEqual(result['clips'][0]['catalog_id'], 2)
            # Ordinary catalog metadata updates preserve validation.
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute('UPDATE clips SET duration_ms=0 WHERE id=1')
            research = list_clips(db, include_damaged=True)
            self.assertEqual(research['total'], 3)
            self.assertEqual(research['clips'][-1]['validation_status'], 'damaged')
            self.assertEqual(list_clips(db)['total'], 2)
            with self.assertRaises(ValueError):
                set_clip_validation(db, 999, 'damaged', 'Unknown clip', 'a'*64)
            set_clip_validation(db, 1, 'unchecked', 'Restore for research', 'a'*64)
            self.assertEqual(list_clips(db)['total'], 3)
            with closing(sqlite3.connect(db)) as conn, conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM clips WHERE local_present=1').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
