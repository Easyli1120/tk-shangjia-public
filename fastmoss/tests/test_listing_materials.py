import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import listing_materials as fabric


class FabricReviewTests(unittest.TestCase):
    def setUp(self):
        self.rules = [dict(id='m', name='Material', type='PRODUCT_PROPERTY', is_multiple_selection=True,
                           values=[dict(id='c', name='Cotton'), dict(id='p', name='Polyester'),
                                   dict(id='s', name='Spandex')])]
        self.source = {'specifications': [{'name': 'Material', 'value': 'Cotton'}]}
        self.title = 'Cotton-Polyester Hoodie Sweatshirt'
        self.description = 'T-shirt: 100% Cotton\nHoodie & Sweatshirt：50% Cotton 50% Polyester'

    def review(self, attributes=None, **kwargs):
        values = dict(source=self.source, title=self.title, description=self.description,
                      attributes=attributes or {}, rules=self.rules)
        values.update(kwargs)
        return fabric.review(**values)

    def test_actual_hoodie_omission_is_completed_with_both_fibers(self):
        result = self.review({'m': [{'id': 'c'}]})
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['suggestion'], '50% Cotton / 50% Polyester')
        self.assertEqual(result['automatic_attributes'], {'m': [{'id': 'c'}, {'id': 'p'}]})
        self.assertEqual(result['suggested_attributes']['m'], [{'id': 'c'}, {'id': 'p'}])
        ignored = [row['text'] for row in result['evidence'] if row['ignored']]
        self.assertEqual(ignored, ['T-shirt: 100% Cotton'])
        confirmed = self.review(result['suggested_attributes'])
        self.assertEqual(confirmed['status'], 'consistent')
        self.assertEqual(confirmed['issues'], [])
        self.assertIn('source_warning', confirmed)

    def test_tshirt_does_not_inherit_hoodie_composition(self):
        result = self.review(title='Cotton T-shirt')
        self.assertEqual(result['suggestion'], '100% Cotton')
        self.assertEqual(result['automatic_attributes'], {'m': [{'id': 'c'}]})

    def test_consistent_blend_can_fill_empty_field_without_dropping_second_value(self):
        self.source['specifications'][0]['value'] = 'Polyester,Cotton'
        result = self.review()
        self.assertEqual(result['automatic_attributes']['m'], [{'id': 'c'}, {'id': 'p'}])

    def test_manual_selection_is_not_overwritten(self):
        result = self.review({'m': [{'id': 's'}]})
        self.assertEqual(result['automatic_attributes'], {})
        self.assertEqual(result['status'], 'conflict')

    def test_explicit_composition_conflict_is_never_automatically_overwritten(self):
        for value in ('100% Cotton','Polyester 100%','Nylon','Proprietary blend'):
            with self.subTest(source=value):
                source={'specifications':[dict(name='Material',value=value)]}
                self.assertEqual(self.review({'m':[{'id':'c'}]},source=source)['automatic_attributes'],{})
        self.rules[0]['is_customizable']=True
        result=self.review({'m':[{'name':'100% Cotton'}]})
        self.assertEqual(result['automatic_attributes'],{})

    def test_incomplete_source_requires_both_current_title_and_details(self):
        result=self.review({'m':[{'id':'c'}]},title='Hoodie')
        self.assertEqual(result['automatic_attributes'],{})

    def test_two_official_material_fields_are_completed_together(self):
        second=copy.deepcopy(self.rules[0]);second.update(id='fabric',name='Fabric');self.rules.append(second)
        result=self.review({'m':[{'id':'c'}],'fabric':[{'id':'c'}]})
        self.assertEqual(result['automatic_attributes'],{'m':[{'id':'c'},{'id':'p'}],'fabric':[{'id':'c'},{'id':'p'}]})

    def test_single_select_cannot_silently_take_first_material(self):
        self.rules[0]['is_multiple_selection'] = False
        result = self.review()
        self.assertEqual(result['suggested_attributes'], {})
        self.assertTrue(any('第一种' in row['message'] for row in result['issues']))
        self.rules[0]['values'].append(dict(id='blend', name='Cotton / Polyester'))
        self.assertEqual(self.review()['suggested_attributes']['m'], [{'id': 'blend'}])

    def test_unavailable_option_never_drops_a_fiber(self):
        self.rules[0]['values'] = [dict(id='c', name='Cotton')]
        self.assertEqual(self.review()['suggested_attributes'], {})
        self.assertEqual(self.review()['automatic_attributes'], {})
        self.rules[0]['is_customizable'] = True
        self.assertEqual(self.review()['suggested_attributes'], {'m': [{'name': '50% Cotton, 50% Polyester'}]})

    def test_ratio_specific_option_cannot_override_actual_ratio(self):
        self.rules[0]['is_multiple_selection'] = False
        self.rules[0]['values'] = [dict(id='blend', name='80% Cotton 20% Polyester')]
        self.assertEqual(self.review()['suggested_attributes'], {})

    def test_ambiguous_styles_ratios_and_material_claims_block_suggestions(self):
        cases = [dict(title='T-shirt and Hoodie'),
                 dict(title='Cotton hoodie', description='100% Polyester'),
                 dict(description='80% Cotton 20% Polyester\n50% Cotton 50% Polyester'),
                 dict(description='80% Cotton 50% Polyester'),
                 dict(description='Cotton-like feel with polyester fabric'),
                 dict(description='Shell: Cotton\nLining: Polyester'),
                 dict(description='Cotton and other fibers'),
                 dict(description='Cotton blend'),
                 dict(description='Heather colors: 50% Cotton 50% Polyester')]
        for case in cases:
            with self.subTest(case=case):
                result = self.review(**case)
                self.assertEqual(result['status'], 'conflict')
                self.assertEqual(result['suggested_attributes'], {})

    def test_current_edited_copy_is_checked_instead_of_original_title(self):
        result = self.review({'m': [{'id': 'p'}]}, title='Polyester hoodie', description='100% Polyester')
        self.assertEqual(result['status'], 'consistent')
        self.assertEqual(result['suggestion'], '100% Polyester')

    def test_missing_materials_stay_unknown_and_do_not_invent_percentages(self):
        result = self.review(source={}, title='Hoodie', description='Comfortable clothing')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['suggested_attributes'], {})
        result = self.review(description='Cotton Polyester blend')
        self.assertEqual(result['suggestion'], 'Cotton / Polyester')

    def test_unrecognized_source_requires_confirmation_not_automatic_fill(self):
        self.source['specifications'][0]['value'] = 'Proprietary blend'
        self.assertEqual(self.review()['automatic_attributes'], {})

    def test_one_source_is_not_cross_checked_evidence(self):
        result=self.review(source={},title='Hoodie',description='Cotton Polyester')
        self.assertEqual(result['automatic_attributes'],{})

    def test_alias_and_percentage_order_are_preserved(self):
        result = self.review(source={}, title='Cotton spandex T-shirt', description='Cotton: 95%, Elastane: 5%')
        self.assertEqual(result['suggestion'], '95% Cotton / 5% Elastane')
        self.assertEqual(result['suggested_attributes']['m'], [{'id': 'c'}, {'id': 's'}])
        suffix = self.review(source={}, description='Cotton 80% Polyester 20%')
        self.assertEqual(suffix['suggestion'], '80% Cotton / 20% Polyester')

    def test_non_fabric_categories_are_unaffected(self):
        rules = copy.deepcopy(self.rules)
        rules[0]['name'] = 'Pattern'
        self.assertFalse(self.review(rules=rules)['applicable'])

    def test_latex_and_unknown_components_are_not_silently_discarded(self):
        parsed=fabric.parse('100%Latex,96%Cotton,4%Spandex')
        self.assertEqual(parsed['materials'],['Cotton','Elastane','Latex'])
        self.assertTrue(parsed['uncertain'])
        for text in ('95% Cotton, 5% Mysteryfiber','Cotton, Unknownfiber','Shell: Cotton; Lining: Polyester'):
            with self.subTest(text=text):
                result=self.review(source={'specifications':[dict(name='Material',value=text)]},title='Cotton T-shirt',description='100% Cotton')
                self.assertEqual(result['automatic_attributes'],{})
                self.assertTrue(result['issues'])

    def test_duplicate_material_conflicts_require_explicit_current_evidence_confirmation(self):
        source={'specifications':[dict(name='Material',value='Nylon,Elastane'),dict(name='Material',value='Polyester')]}
        args=dict(source=source,title='Polyester swimsuit',description='100% Polyester')
        result=self.review({'m':[{'id':'p'}]},**args)
        self.assertTrue(result['source_conflict'])
        self.assertEqual(len([r for r in result['evidence'] if r['label']=='采集属性']),2)
        confirmed=self.review({'m':[{'id':'p'}]},confirmation=result['verification_key'],**args)
        self.assertFalse(confirmed['issues'])
        args['description']='Polyester fabric'
        changed=self.review({'m':[{'id':'p'}]},confirmation=result['verification_key'],**args)
        self.assertTrue(changed['source_conflict'],'confirmation must expire when the evidence changes')


if __name__ == '__main__':
    unittest.main()
