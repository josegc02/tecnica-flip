"""DynamoDB in AWS; a small SQLite adapter for local development only."""
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from contextlib import contextmanager


class Conflict(Exception):
    pass


class SQLiteStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (pk TEXT, sk TEXT, data TEXT, PRIMARY KEY(pk, sk))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, pk, sk):
        with self.connect() as db:
            row = db.execute('SELECT data FROM records WHERE pk=? AND sk=?', (pk, sk)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, item, *, create=False, version=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM records WHERE pk=? AND sk=?', (item['pk'], item['sk'])).fetchone()
            if create and row or version is not None and (not row or json.loads(row[0]).get('version') != version):
                raise Conflict()
            db.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?)', (item['pk'], item['sk'], json.dumps(item)))

    def delete(self, pk, sk):
        with self.connect() as db:
            db.execute('DELETE FROM records WHERE pk=? AND sk=?', (pk, sk))

    def query(self, pk, prefix):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM records WHERE pk=? AND sk LIKE ? ORDER BY sk', (pk, prefix + '%')).fetchall()
            return [json.loads(row[0]) for row in rows]


class DynamoStore:
    def __init__(self, table_name):
        import boto3
        from botocore.config import Config
        self.table = boto3.resource('dynamodb', config=Config(retries={'max_attempts': 3, 'mode': 'standard'}, connect_timeout=2, read_timeout=3)).Table(table_name)

    def get(self, pk, sk):
        return self.table.get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item')

    def put(self, item, *, create=False, version=None):
        args = {'Item': item}
        if create:
            args['ConditionExpression'] = 'attribute_not_exists(pk)'
        elif version is not None:
            args.update(ConditionExpression='#v = :v', ExpressionAttributeNames={'#v': 'version'}, ExpressionAttributeValues={':v': version})
        try:
            self.table.put_item(**args)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                raise Conflict() from exc
            raise

    def delete(self, pk, sk):
        self.table.delete_item(Key={'pk': pk, 'sk': sk})

    def query(self, pk, prefix):
        args = dict(KeyConditionExpression='pk = :pk AND begins_with(sk, :prefix)', ExpressionAttributeValues={':pk': pk, ':prefix': prefix}, ConsistentRead=True)
        items = []
        while True:
            page = self.table.query(**args)
            items.extend(page['Items'])
            if 'LastEvaluatedKey' not in page:
                return items
            args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    raise TypeError(type(value).__name__)
