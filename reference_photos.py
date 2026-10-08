"""User-managed reference photos, processed by the existing sequential AI worker."""
import base64
import io
import json
from PIL import Image, ImageOps, UnidentifiedImageError
from identity_store import connect
from identity_analysis import normalized


def ensure_photos(conn):
    conn.executescript('''CREATE TABLE IF NOT EXISTS identity_photos (
        photo_id INTEGER PRIMARY KEY, identity_id INTEGER NOT NULL REFERENCES identities(identity_id) ON DELETE CASCADE,
        filename TEXT NOT NULL, jpeg BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        message TEXT NOT NULL DEFAULT 'Waiting for bAI', sample_id INTEGER REFERENCES identity_samples(sample_id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')


def list_photos(db, identity_id):
    with connect(db) as conn:
        return [dict(r) for r in conn.execute('SELECT photo_id,identity_id,filename,state,message,created_at FROM identity_photos WHERE identity_id=? ORDER BY photo_id DESC',(identity_id,))]


def add_photo(db, identity_id, filename, encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw)>8*1024*1024: raise ValueError('Photo must be under 8 MB.')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('JPEG','PNG','WEBP','BMP'): raise ValueError('Use a JPEG, PNG, WebP or BMP photo.')
            if image.width*image.height>20_000_000: raise ValueError('Photo must be under 20 megapixels.')
            if min(image.size)<48: raise ValueError('Photo is too small; use at least 48 pixels in each dimension.')
            image=ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1600,1600))
            output=io.BytesIO();image.save(output,format='JPEG',quality=92)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('The photo could not be read. Choose a valid image.') from exc
    except (ValueError, __import__('binascii').Error) as exc:
        raise ValueError(str(exc)) from exc
    filename=filename.replace('\\','/').rsplit('/',1)[-1][:150] or 'Reference photo'
    with connect(db) as conn:
        if not conn.execute('SELECT 1 FROM identities WHERE identity_id=? AND active=1',(identity_id,)).fetchone(): raise LookupError('Identity not found.')
        if conn.execute('SELECT COUNT(*) FROM identity_photos WHERE identity_id=?',(identity_id,)).fetchone()[0]>=100: raise ValueError('An identity can have up to 100 uploaded photos. Remove an unused photo first.')
        cur=conn.execute('INSERT INTO identity_photos(identity_id,filename,jpeg) VALUES(?,?,?)',(identity_id,filename,output.getvalue()))
        return {'photo_id':cur.lastrowid,'state':'pending'}


def photo_image(db, identity_id, photo_id):
    with connect(db) as conn:
        row=conn.execute('SELECT jpeg FROM identity_photos WHERE identity_id=? AND photo_id=?',(identity_id,photo_id)).fetchone()
        if not row: raise LookupError('Photo not found.')
        return row['jpeg']


def remove_photo(db, identity_id, photo_id):
    with connect(db) as conn:
        row=conn.execute('SELECT sample_id FROM identity_photos WHERE identity_id=? AND photo_id=?',(identity_id,photo_id)).fetchone()
        if not row: raise LookupError('Photo not found.')
        conn.execute('DELETE FROM identity_photos WHERE photo_id=?',(photo_id,))
        if row['sample_id']: conn.execute('DELETE FROM identity_samples WHERE sample_id=?',(row['sample_id'],))
        return {'deleted':True}


def retry_photo(db, identity_id, photo_id):
    with connect(db) as conn:
        cur=conn.execute("UPDATE identity_photos SET state='pending',message='Waiting for bAI' WHERE identity_id=? AND photo_id=? AND state='rejected'",(identity_id,photo_id))
        if not cur.rowcount: raise ValueError('Only a rejected photo can be retried.')
        return {'state':'pending'}


def process_photos(db, models, checkpoint=lambda:None):
    import numpy as np
    with connect(db) as conn:
        rows=[dict(r) for r in conn.execute("SELECT p.*,i.subject_type FROM identity_photos p JOIN identities i USING(identity_id) WHERE p.state='pending' AND i.active=1 ORDER BY photo_id LIMIT 4")]
    for row in rows:
        checkpoint()
        try:
            crop=models.cv2.imdecode(np.frombuffer(row['jpeg'],dtype=np.uint8),models.cv2.IMREAD_COLOR)
            if crop is None: raise ValueError('Image could not be decoded.')
            vector,version,quality=models.embedding(crop,row['subject_type'])
            if vector is None:
                message=('No usable single face found. Use a sharper, larger face looking toward the camera.' if row['subject_type']=='Person' else 'Not enough clear detail. Use a sharp photo of one animal.')
                state='rejected'
            else:
                vector=normalized(vector);state='ready';message='Ready for identity matching'
        except Exception as exc:
            vector=None;state='rejected';message='Could not process this photo: '+str(exc)[:200]
        with connect(db) as conn:
            # A photo removed during inference must never recreate a reference.
            if not conn.execute("SELECT 1 FROM identity_photos p JOIN identities i USING(identity_id) WHERE p.photo_id=? AND p.identity_id=? AND p.jpeg=? AND p.state='pending' AND i.subject_type=?",(row['photo_id'],row['identity_id'],row['jpeg'],row['subject_type'])).fetchone(): continue
            sample=None
            if vector is not None:
                sample=conn.execute('INSERT INTO identity_samples(identity_id,embedding,model_version,quality_score) VALUES(?,?,?,?)',(row['identity_id'],json.dumps(vector),version,quality)).lastrowid
            conn.execute('UPDATE identity_photos SET state=?,message=?,sample_id=? WHERE photo_id=?',(state,message,sample,row['photo_id']))
