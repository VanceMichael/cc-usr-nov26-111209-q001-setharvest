import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path


def write_object(objects, body):
    digest = hashlib.sha256(body).hexdigest()
    destination = Path(objects) / digest
    if destination.exists():
        if destination.read_bytes() != body:
            raise ValueError('原文对象校验失败')
    else:
        descriptor, temporary = tempfile.mkstemp(prefix='.receive-', dir=objects)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return digest


def read_object(objects, digest):
    body = (Path(objects) / digest).read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError('原文摘要不匹配')
    return body


class Archive:
    def __init__(self, database, objects):
        self.objects = Path(objects)
        self.objects.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        self.connection.execute('pragma foreign_keys=on')
        self.connection.executescript("""
            create table if not exists captures (
                id integer primary key, url text not null, status integer not null,
                content_type text not null, digest text not null, size integer not null,
                received_at text not null default current_timestamp
            );
            create index if not exists capture_digest on captures(digest);
        """)

    def save(self, url, status, content_type, body):
        digest = write_object(self.objects, body)
        with self.connection:
            cursor = self.connection.execute(
                'insert into captures(url,status,content_type,digest,size) values(?,?,?,?,?)',
                (url, status, content_type, digest, len(body)),
            )
        return cursor.lastrowid

    def read(self, capture_id):
        row = self.connection.execute('select digest from captures where id=?', (capture_id,)).fetchone()
        if row is None:
            raise KeyError(capture_id)
        return read_object(self.objects, row[0])

    def rows(self):
        self.connection.row_factory = sqlite3.Row
        return [dict(row) for row in self.connection.execute('select * from captures order by id')]

    def close(self):
        self.connection.close()
