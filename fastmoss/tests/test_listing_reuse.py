import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import shop_listing as pub
import listing_image_roles as roles

class ReuseTests(unittest.TestCase):
    def test_collected_chart_is_not_automatically_used_as_company_chart(self):
        product={'variants':[],'images':[
            {'key':'photo','role':'product','group_name':'规格图'},
            {'key':'small','role':'size_chart'}, {'key':'valid','role':'size_chart'}]}
        source={'description':''}
        updated=pub.reuse_source_fields({'size_chart':'photo'},source,product,'/tmp')
        self.assertNotIn('size_chart',updated)
        self.assertNotIn('size_chart',pub.reuse_source_fields({},source,product))
        self.assertEqual(pub.reuse_source_fields({'size_chart':'valid'},source,product)['size_chart'],'valid')
        self.assertEqual(pub.reuse_source_fields({'size_chart':'photo','size_chart_template_id':'company'},source,product)['size_chart_template_id'],'company')

    def test_mixed_language_loungewear_category_maps_within_original_branch(self):
        nodes=[{'id':'root','parent_id':'0','local_name':'女装和内衣','english_name':'Womenswear'},
               {'id':'parent','parent_id':'root','local_name':'女士睡衣和家居服','english_name':'Pajamas & Loungewear','permission_statuses':['AVAILABLE']},
               {'id':'tops','parent_id':'parent','local_name':'家居服上装','english_name':'Loungewear Tops','is_leaf':True,'permission_statuses':['AVAILABLE']},
               {'id':'sets','parent_id':'parent','local_name':'家居服套装','english_name':'Loungewear Sets','is_leaf':True,'permission_statuses':['AVAILABLE']}]
        source={'category':'女装和内衣 > Pajamas & Loungewear'}
        ancestor=pub.source_category(source,nodes,leaf_only=False)
        self.assertEqual(ancestor,'parent')
        self.assertEqual(pub.refine_source_category('Women Y2K Leopard Hoodie',ancestor,nodes),'tops')
        self.assertEqual(pub.refine_source_category('Women Pajamas',ancestor,nodes),'')

    def test_shop_brands_paginates_and_includes_usable_custom_shop_brand(self):
        with tempfile.TemporaryDirectory() as data:
            responses=[{'brands':[{'id':'1','name':'Authorized','authorized_status':'AUTHORIZED'}],'next_page_token':'next'},
                       {'brands':[{'id':'2','name':'Other','authorized_status':'AUTHORIZED'}]},
                       {'brands':[{'id':'3','name':'Example Shop','authorized_status':'UNAUTHORIEZD','brand_status':'AVAILABLE','is_t1_brand':False},
                                  {'id':'4','name':'Example Shop extra','is_t1_brand':False}]}]
            with patch.object(pub,'identity',return_value=({}, {'name':'Example Shop'})),patch.object(pub,'call',side_effect=responses) as api:
                brands=pub.shop_brands(data,{},'tops')
                self.assertEqual([b['id'] for b in brands],['1','2','3'])
                self.assertEqual(api.call_args_list[1].kwargs['params']['page_token'],'next')
                self.assertEqual(api.call_args.kwargs['params']['brand_name'],'Example Shop')

    def test_property_recommendations_do_not_infer_declarations(self):
        rules=[{'id':'fit','name':'Fit','type':'PRODUCT_PROPERTY','is_required':True,'values':[{'id':'loose'}]},
               {'id':'legal','name':'CA Prop 65: Reprotoxic chemicals','type':'PRODUCT_PROPERTY','is_required':True,'values':[{'id':'no'}]}]
        with tempfile.TemporaryDirectory() as data,patch.object(pub,'call',return_value={'recommended_properties':[
            {'id':'fit','values':[{'id':'loose'}]},{'id':'legal','values':[{'id':'no'}]}]}) as api:
            matched=pub.recommend_properties(data,{}, {'category_id':'tops','title':'Loose shirt'}, {'description':'Loose fit'},rules,{})
            self.assertEqual(api.call_args.kwargs['payload']['target_property_ids'],['fit'])
            self.assertEqual(matched,{'fit':[{'id':'loose'}]})

    def test_chart_and_reminder_detection_preserves_product_photos(self):
        self.assertEqual(roles.role_from_text('BUST\nSHOULDER\nSLEEVE\n110 43.3 116 45.7 122 48 128 50.4'),'size_chart')
        self.assertEqual(roles.role_from_text('REMINDER\nREGARDING COLOR DIFFERENCE'),'reminder')
        self.assertEqual(roles.role_from_text('PARIS\nSTYLE'),'product')
        self.assertEqual(roles.role_from_text('Chest pocket, warm fabric'),'product')

    def test_two_column_size_chart_is_reclassified_from_cached_ocr(self):
        text='Size\nM\nL\nXL\nSize type: Regular\nBody measurement\nWaist(in)\n23.2-25.5\n24.8-27.1\n26.3-28.7\n27.9-30.3\nHips(in)\n33.8-36.2\n35.4-37.7\n37.0-39.3\n38.5-40.9'
        self.assertEqual(roles.role_from_text(text),'size_chart')
        self.assertEqual(roles.role_from_text('Waist and hips support, size M L XL, 1 2 3 4 5 6 7 8'),'product')
        with tempfile.TemporaryDirectory() as folder:
            data=pathlib.Path(folder);(data/'images').mkdir();(data/'image-analysis').mkdir()
            content=b'cached-image';(data/'images/chart.jpg').write_bytes(content)
            fingerprint=roles.VERSION+hashlib.sha256(content).hexdigest()
            (data/'image-analysis/roles.json').write_text(json.dumps({fingerprint:{'role':'product','text':text,'complete':True}}))
            with patch.object(roles.subprocess,'run') as ocr:
                result=roles.annotate(data,[{'key':'chart','local_path':'/images/chart.jpg'}])
            ocr.assert_not_called()
            self.assertEqual(result[0]['role'],'size_chart')
            self.assertEqual(result[0]['ocr_text'],text)

    def test_source_attribute_aliases_use_only_supported_values(self):
        rules=[{'id':'fit','name':'Fit type','type':'PRODUCT_PROPERTY','values':[{'id':'loose','name':'Loose Fitting'}]},
               {'id':'country','name':'Country of origin','type':'PRODUCT_PROPERTY','values':[{'id':'cn','name':'China'}]}]
        self.assertEqual(pub.match_source_attributes([{'name':'Fit','value':'Loose-fitting'},{'name':'Region of origin','value':'China'}],rules),{'fit':[{'id':'loose'}],'country':[{'id':'cn'}]})

if __name__=='__main__':unittest.main()
