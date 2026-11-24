import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import content_review
import listing_image_roles


class ContentReviewTests(unittest.TestCase):
    def test_source_store_and_brand_names_are_reported(self):
        result = content_review.review(
            {'title': 'YIANNA Faja', 'source_shop_name': 'Seller House'},
            {'description': 'Official authenticity notice. YIANNA is authorized.', 'brand_names': ['YIANNA']},
        )
        self.assertTrue(result['requires_review'])
        self.assertEqual([x['name'] for x in result['title_matches']], ['YIANNA'])
        self.assertEqual([x['name'] for x in result['description_matches']], ['YIANNA'])
        self.assertIn('Official authentic', result['rights_markers'])

    def test_image_ocr_is_marked_without_changing_the_source(self):
        images = [{'key': 'image-1', 'ocr_text': 'YIANNA official store'}]
        marked = listing_image_roles.mark_matches(images, [{'name': 'YIANNA', 'kind': '品牌字段'}])
        self.assertEqual(marked[0]['ocr_text'], images[0]['ocr_text'])
        self.assertTrue(marked[0]['review_required'])
        self.assertEqual(marked[0]['text_matches'][0]['name'], 'YIANNA')

    def test_plain_text_does_not_require_review(self):
        result = content_review.review(
            {'title': 'Cotton shirt', 'source_shop_name': 'Seller House'},
            {'description': 'Cotton fabric and crew neck.', 'brand_names': []},
        )
        self.assertFalse(result['requires_review'])

    def test_stylized_store_name_is_flagged_with_corner_evidence(self):
        for region in ('左上角', '右上角', '左下角', '右下角'):
            with self.subTest(region=region):
                image = {'ocr_text': 'Dame.ace\nJESUS HAS MY BACK',
                         'ocr_regions': [{'region': '全图', 'text': 'Dame.ace'},
                                         {'region': region, 'text': 'Dame.ace'}]}
                matches = content_review.ocr_name_matches(image, [{'name': 'GAME.ace'}])
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]['match_type'], 'variant')
                self.assertEqual(matches[0]['region'], region)
                self.assertEqual(matches[0]['matched_text'], 'Dame.ace')

    def test_ocr_handles_letter_spacing_unicode_and_digit_substitutions(self):
        for text, kind in [('G A M E . A C E', 'exact'), ('ＧＡＭＥ.ａｃｅ', 'exact'),
                           ('𝔊𝔞𝔪𝔢.𝔞𝔠𝔢', 'exact'), ('G4M3.ace', 'variant'), ('Dane.ace', 'variant')]:
            with self.subTest(text=text):
                matches = content_review.ocr_name_matches({'ocr_text': text}, ['GAME.ace'])
                self.assertEqual(matches[0]['match_type'], kind)

    def test_ocr_exact_result_takes_priority_over_a_similar_candidate(self):
        image = {'ocr_regions': [{'region': '左上角', 'text': 'Dame.ace'},
                                 {'region': '左上角', 'text': 'Game.ace'}]}
        matches = content_review.ocr_name_matches(image, ['GAME.ace'])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['match_type'], 'exact')

    def test_fuzzy_ocr_requires_known_full_name_and_does_not_change_text_review(self):
        for text in ('JESUS\nHAS MY\nBACK', 'Winter Warm', 'ace', 'Dame\nace'):
            self.assertEqual(content_review.ocr_name_matches({'ocr_text': text}, ['GAME.ace']), [])
        self.assertEqual(content_review.ocr_name_matches({'ocr_text': 'GAME.ace'}, []), [])
        self.assertEqual(content_review.ocr_name_matches({'ocr_text': 'N1KE'}, ['NIKE']), [])
        self.assertEqual(content_review.find_matches('Dame.ace', ['GAME.ace']), [])
        self.assertEqual(content_review.sanitize_text('Dame.ace', ['GAME.ace']), 'Dame.ace')

    def test_sanitize_text_removes_the_entire_flagged_sentence(self):
        terms = [{'name': 'Seller House', 'kind': '店铺/品牌字段'}]
        text = 'Welcome to Seller House for purchase. Cotton fabric and crew neck.'
        self.assertEqual(
            content_review.sanitize_text(text, terms),
            'Cotton fabric and crew neck.',
        )

    def test_compact_title_keeps_product_words_after_store_name(self):
        self.assertEqual(
            content_review.sanitize_text(
                'SELLER-HOUSE | Printed Hoodie',
                [{'name': 'Seller House'}],
                drop_single=False,
            ),
            'Printed Hoodie',
        )

    def test_sanitize_source_keeps_raw_text_for_audit(self):
        source = {'description': 'Welcome to Seller House for purchase. Cotton fabric.'}
        cleaned = content_review.sanitize_source(source, {'shop_name': 'Seller House'})
        self.assertEqual(cleaned['description'], 'Cotton fabric.')
        self.assertEqual(cleaned['raw_description'], source['description'])


if __name__ == '__main__':
    unittest.main()
