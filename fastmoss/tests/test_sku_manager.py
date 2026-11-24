import copy
import re
import sqlite3
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sku_manager


class SkuManagerTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:', check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        sku_manager.migrate(self.c)

    def tearDown(self):
        self.c.close()

    def source(self, pid='p1'):
        return {'product_id': pid, 'title': 'Test shirt', 'skus': [
            {'sku_id': f'{pid}-1', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'Black'}, {'prop_name': 'Size', 'prop_value': 'M'}]},
            {'sku_id': f'{pid}-2', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'Black'}, {'prop_name': 'Size', 'prop_value': 'L'}]},
            {'sku_id': f'{pid}-3', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'White'}, {'prop_name': 'Size', 'prop_value': 'M'}]},
            {'sku_id': f'{pid}-4', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'White'}, {'prop_name': 'Size', 'prop_value': 'L'}]},
        ]}

    def test_four_variants_are_unique_and_repeat_capture_reuses_them(self):
        source = self.source()
        first = sku_manager.ensure_product(self.c, 'US', 'p1', source=source, title=source['title'])
        values = [r['seller_sku'] for r in first['rows']]
        self.assertEqual(len(values), 4)
        self.assertEqual(len(set(values)), 4)
        changed = copy.deepcopy(source)
        for index, variant in enumerate(changed['skus']):
            variant['sku_id'] = f'changed-{index}'
        second = sku_manager.ensure_product(self.c, 'US', 'p1', source=changed, title=source['title'])
        self.assertEqual(values, [r['seller_sku'] for r in second['rows']])

    def test_manual_change_is_logged_and_duplicate_is_blocked(self):
        source = self.source()
        mapping = sku_manager.ensure_product(self.c, 'US', 'p1', source=source)
        old = mapping['rows'][0]['seller_sku']
        sku_manager.apply_skus(self.c, 'US', 'p1', {'p1-1': 'MY-BLACK-M'}, source=source)
        self.assertEqual(self.c.execute('SELECT seller_sku FROM sku_variants WHERE source_variant_id=?', ('p1-1',)).fetchone()[0], 'MY-BLACK-M')
        self.assertEqual(tuple(self.c.execute('SELECT old_sku,new_sku,action FROM sku_change_log').fetchone()), (old, 'MY-BLACK-M', 'manual_edit'))
        with self.assertRaisesRegex(ValueError, '不能重复'):
            sku_manager.apply_skus(self.c, 'US', 'p1', {'p1-2': 'MY-BLACK-M'}, source=source)
        with self.assertRaisesRegex(ValueError, '6～50'):
            sku_manager.apply_skus(self.c, 'US', 'p1', {'p1-2': 'short'}, source=source)

    def test_new_skus_include_random_suffix_and_remain_stable(self):
        with patch.object(sku_manager.secrets,'token_hex',return_value='0123456789abcdef') as random:
            mapping=sku_manager.ensure_product(self.c,'US','p1',source=self.source())
        codes=[row['seller_sku'] for row in mapping['rows']]
        self.assertTrue(all(re.fullmatch(r'TK-\d{8}-\d{6}-[0-9A-F]{16}',code) for code in codes))
        self.assertTrue(all(len(code)==35 for code in codes))
        self.assertEqual(random.call_count,4)
        again=sku_manager.ensure_product(self.c,'US','p1',source=self.source())
        self.assertEqual(codes,[row['seller_sku'] for row in again['rows']])

    def test_existing_short_sku_and_manual_codes_are_preserved(self):
        sku_manager.ensure_product(self.c,'US','p1',source=self.source())
        self.c.execute("UPDATE sku_variants SET seller_sku='TK-20261119-000001' WHERE source_variant_id='p1-1'")
        self.c.execute("UPDATE sku_variants SET seller_sku='MY-EXISTING-SKU' WHERE source_variant_id='p1-2'")
        again=sku_manager.ensure_product(self.c,'US','p1',source=self.source())
        self.assertEqual([row['seller_sku'] for row in again['rows']][:2],['TK-20261119-000001','MY-EXISTING-SKU'])

    def test_same_counter_on_independent_databases_has_distinct_suffix(self):
        other=sqlite3.connect(':memory:');other.row_factory=sqlite3.Row;sku_manager.migrate(other)
        try:
            with patch.object(sku_manager.secrets,'token_hex',side_effect=['a'*16,'b'*16]):
                left=sku_manager._next_sku(self.c);right=sku_manager._next_sku(other)
            self.assertEqual(left.rsplit('-',1)[0],right.rsplit('-',1)[0])
            self.assertNotEqual(left,right)
        finally:other.close()

    def test_duplicate_source_combinations_still_get_distinct_rows(self):
        source = {'title': 'Duplicate test', 'skus': [
            {'sku_id': 'a', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'Black'}]},
            {'sku_id': 'b', 'sku_sale_props': [{'prop_name': 'Color', 'prop_value': 'Black'}]},
        ]}
        mapping = sku_manager.ensure_product(self.c, 'US', 'duplicate', source=source)
        self.assertEqual(len(mapping['rows']), 2)
        self.assertEqual(len({r['seller_sku'] for r in mapping['rows']}), 2)
        again = sku_manager.ensure_product(self.c, 'US', 'duplicate', source=copy.deepcopy(source))
        self.assertEqual([r['seller_sku'] for r in mapping['rows']], [r['seller_sku'] for r in again['rows']])

    def test_counter_rollover_is_safe(self):
        self.c.execute('INSERT INTO sku_counters(sku_date,next_number) VALUES(?,?)', (sku_manager._date(), 1000000))
        with self.assertRaisesRegex(ValueError, '999999'):
            sku_manager.ensure_product(self.c, 'US', 'rollover', source={'skus':[{'sku_id':'r','sku_sale_props':[]}]})
        self.c.rollback()

    def test_concurrent_products_get_distinct_skus(self):
        # SQLite's single writer lock plus the counter row makes allocation
        # atomic even when callers collect in parallel.
        results=[];errors=[];lock=threading.Lock();folder=tempfile.TemporaryDirectory();path=Path(folder.name)/'sku.sqlite3'
        base=sqlite3.connect(path);base.row_factory=sqlite3.Row;sku_manager.migrate(base);base.commit();base.close()
        def worker(index):
            try:
                connection=sqlite3.connect(path,timeout=30);connection.row_factory=sqlite3.Row
                sku_manager.ensure_product(connection, 'US', f'p{index}', source={'skus':[{'sku_id':str(index), 'sku_sale_props':[]}]})
                connection.commit()
                row=connection.execute('SELECT v.seller_sku FROM sku_variants v JOIN sku_products p ON p.product_id=v.product_id WHERE p.source_product_id=?',(f'p{index}',)).fetchone()
                connection.close()
                with lock: results.append(row[0])
            except Exception as exc:
                with lock: errors.append(exc)
        threads=[threading.Thread(target=worker,args=(i,)) for i in range(6)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(results), len(set(results)))
        self.assertTrue(all(value.startswith('TK-') for value in results))
        folder.cleanup()


if __name__ == '__main__':
    unittest.main()
