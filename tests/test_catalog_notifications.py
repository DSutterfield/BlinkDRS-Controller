import sqlite3
import tempfile
import unittest
from pathlib import Path
from catalog_store import open_catalog_notifications, set_clip_validation


class CatalogNotificationTests(unittest.TestCase):
    def test_committed_changes_and_quiet_metadata_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'catalog.db'
            root=Path(__file__).resolve().parent
            schema=root/'catalog_schema_v1.sql'
            if not schema.exists(): schema=root.parent/'sql/catalog_schema_v1.sql'
            writer=sqlite3.connect(db)
            writer.executescript(schema.read_text())
            reader=open_catalog_notifications(db)
            def revision(): return reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
            try:
                baseline=revision()
                writer.execute("INSERT INTO clips(id,filename,video_path,captured_at) VALUES(1,'one.mp4','clips/one.mp4','2026-09-12')")
                self.assertEqual(revision(),baseline)
                writer.commit(); self.assertEqual(revision(),baseline+1)
                writer.execute("UPDATE clips SET catalog_updated_at='new poll' WHERE id=1")
                writer.commit(); self.assertEqual(revision(),baseline+1)
                writer.execute('UPDATE clips SET watched=1 WHERE id=1')
                writer.rollback(); self.assertEqual(revision(),baseline+1)
                writer.execute('UPDATE clips SET watched=1 WHERE id=1')
                writer.commit(); self.assertEqual(revision(),baseline+2)
                set_clip_validation(db,1,'damaged','Test','a'*64)
                self.assertEqual(revision(),baseline+3)
                writer.execute('DELETE FROM clips WHERE id=1');writer.commit()
                self.assertGreater(revision(),baseline+3)
            finally:
                reader.close();writer.close()


if __name__=='__main__': unittest.main()
