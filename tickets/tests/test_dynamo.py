import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Conflict, DynamoStore, json_default


class DynamoAdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = DynamoStore.__new__(DynamoStore)
        self.store.table = Mock()

    def test_tasks_across_multiple_dynamodb_pages_are_not_lost(self):
        cursor = {'pk': 'USER#a', 'sk': 'TASK#1'}
        self.store.table.query.side_effect = [
            {'Items': [{'id': '1'}], 'LastEvaluatedKey': cursor},
            {'Items': [{'id': '2'}]},
        ]
        result = self.store.query('USER#a', 'TASK#')
        self.assertEqual([item['id'] for item in result], ['1', '2'])
        first, second = self.store.table.query.call_args_list
        self.assertNotIn('ExclusiveStartKey', first.kwargs)
        self.assertEqual(second.kwargs['ExclusiveStartKey'], cursor)
        self.assertTrue(second.kwargs['ConsistentRead'])

    def test_conditional_failure_is_reported_as_conflict(self):
        class ConditionalFailure(Exception):
            response = {'Error': {'Code': 'ConditionalCheckFailedException'}}

        self.store.table.put_item.side_effect = ConditionalFailure()
        with self.assertRaises(Conflict):
            self.store.put({'pk': 'USER#a', 'sk': 'TASK#1', 'version': 2}, version=1)
        with self.assertRaises(Conflict):
            self.store.put({'pk': 'USER#a', 'sk': 'TASK#1', 'version': 1}, create=True)

    def test_dynamodb_numbers_serialize_for_the_frontend(self):
        result = json.loads(json.dumps({'created_at': Decimal('1800000000000'), 'version': Decimal(2), 'active_ms': Decimal(60000)}, default=json_default))
        self.assertEqual(result, {'created_at': 1800000000000, 'version': 2, 'active_ms': 60000})


if __name__ == '__main__':
    unittest.main()
