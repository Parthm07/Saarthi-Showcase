"""Media publication and full-list sorting use disposable archives/mocked cloud."""
import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from test_desktop_archive import fixture
from desktop_archive import Archive
from desktop_workflows import Workflows
from product_media import push
import tally_shop as shop
import tally_store as store


class MediaAndSortTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'tally_raw.db';fixture(self.path)
        self.archive=Archive(self.path);self.work=Workflows(self.archive)
        self.local=shop.open_db(str(self.path));self.addCleanup(self.local.close)
        store.save_masters(self.local,'StockItem',[
            dict(GUID='charcoal',ALTERID='21',NAME='COLGATE CHARCOAL',PARENT='Toothpaste'),
            dict(GUID='harpic2',ALTERID='22',NAME='Harpic red',PARENT='Cleaner'),
            dict(GUID='nihar',ALTERID='23',NAME='NIHAR SHANTI',PARENT='Hair oil')])

    def test_search_rank_precedes_pagination_and_sort_covers_whole_list(self):
        results=self.archive.listing('products',q='har',size=1)
        self.assertEqual(results['total'],4)
        self.assertEqual(results['rows'][0]['id'],'p')
        self.assertEqual(self.archive.listing('products',q='har',size=1,page=2)['rows'][0]['id'],'harpic2')
        self.assertEqual(self.archive.listing('products',sort='name',direction='desc',size=1)['rows'][0]['id'],'nihar')
        with self.assertRaises(ValueError):self.archive.listing('products',sort='name; DROP TABLE master')
        with self.assertRaises(ValueError):self.archive.explorer('master',sort='bad_column')

    def test_saved_request_amount_sort_is_numeric_before_pagination(self):
        import json
        with self.work.db() as conn:
            for i,total in enumerate(('100','20','900')):
                conn.execute('INSERT INTO command(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                             (str(i),'sale',json.dumps({'party':{'name':'Demo'},'total':total}),'confirmed','now','now'))
        self.assertEqual([r['total'] for r in self.work.history_page(sort='total')['rows']],['20','100','900'])
        self.assertEqual([r['total'] for r in self.work.history_page(sort='total',direction='desc')['rows']],['900','100','20'])

    def test_shared_picture_publishes_one_blob_for_multiple_products_and_skips_unchanged(self):
        body=b'\x89PNG\r\n\x1a\nfixture';encoded=base64.b64encode(body).decode()
        self.work.save_image('p',encoded);self.work.save_image('harpic2',encoded)
        digest=hashlib.sha256(body).hexdigest();cur=Mock()
        cur.fetchone.return_value=('item_media',)
        cur.fetchall.side_effect=[[('p',),('harpic2',)],[],[]]
        self.assertEqual(push(self.local,cur),2)
        inserts=[c for c in cur.execute.call_args_list if c.args[0].startswith('INSERT INTO product_media')]
        self.assertEqual(len(inserts),1)
        self.assertEqual(inserts[0].args[1][0],digest)
        cur.reset_mock();cur.fetchall.side_effect=[[('p',),('harpic2',)],[('p',digest),('harpic2',digest)],[(digest,)]]
        self.assertEqual(push(self.local,cur),0)
        self.assertFalse(any(c.args[0].startswith('INSERT') for c in cur.execute.call_args_list))

    def test_missing_cloud_schema_retains_local_picture_and_reports_pending(self):
        self.work.save_image('p',base64.b64encode(b'\xff\xd8\xfffixture').decode())
        cur=Mock();cur.fetchone.return_value=(None,);log=Mock()
        self.assertEqual(push(self.local,cur,log),0)
        self.assertTrue(self.work.image('p'));self.assertIn('--media-schema',log.call_args.args[0])

    def test_image_api_requires_login_and_serves_content_hash_with_private_cache(self):
        import api
        from starlette.requests import Request
        route=next(r for r in api.app.routes if getattr(r,'path','')=='/api/product-image/{digest}')
        self.assertTrue(any(d.call is api.me for d in route.dependant.dependencies))
        digest='a'*64;cur=Mock();cur.fetchone.side_effect=[('item_media',),('image/png',b'picture')]
        response=api.product_image(digest,Request({'type':'http','headers':[]}),user={'email':'test'},cur=cur)
        self.assertEqual(response.body,b'picture');self.assertEqual(response.headers['etag'],'"'+digest+'"')
        self.assertIn('private',response.headers['cache-control'])


if __name__=='__main__':unittest.main()
