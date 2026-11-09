import tempfile
import unittest
from archive import Archive


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/shadow.sqlite3'
        self.a = Archive(self.path)

    def tearDown(self):
        self.a.close()
        self.tmp.cleanup()

    def test_shared_budget_and_retries_and_restart(self):
        for slot in ('00','06','12','18'):
            for site in ('plane-1','plane-2'):
                self.a.reserve_request('2026-11-08T18:01:00Z',site,slot)
        for site in ('plane-1','plane-2'):
            self.a.reserve_request('2026-11-08T18:01:00Z',site,'18',retry=True)
        self.a.close();self.a=Archive(self.path)
        with self.assertRaises(ValueError):self.a.reserve_request('2026-11-08T18:02:00Z','plane-1','18',retry=True)
        self.a.reserve_request('2026-11-09T00:00:00Z','plane-1','00')

    def test_no_duplicate_or_unearned_retry(self):
        with self.assertRaises(ValueError):self.a.reserve_request('2026-11-08T01:00:00Z','plane-1','00',retry=True)
        self.a.reserve_request('2026-11-08T01:00:00Z','plane-1','00')
        with self.assertRaises(ValueError):self.a.reserve_request('2026-11-08T01:00:00Z','plane-1','00')

    def test_no_hindsight_archive(self):
        k=self.a.put('forecast',{'synthetic':True},'2026-11-08T01:00:00Z')
        with self.assertRaises(ValueError):self.a.available(k,'2026-11-08T00:00:00Z')
        self.assertIsNone(self.a.available(k,'2026-11-08T01:00:00Z')['issued'])

    def test_virtual_state_restart_and_no_reanchor(self):
        self.a.seed('2026-11-08T01:00:00Z','2','2',{'synthetic':True})
        k=self.a.put('plan',{'synthetic':True},'2026-11-08T01:00:00Z')
        self.a.advance(1,'2026-11-08T02:00:00Z','1.8','1.8',k,'matched-coverage',{'replayed':True})
        with self.assertRaises(ValueError):self.a.seed('2026-11-08T02:00:00Z','17','17',{})
        with self.assertRaises(ValueError):self.a.advance(1,'2026-11-08T03:00:00Z','3','3',k,'matched',{})
        self.a.close();self.a=Archive(self.path)
        self.assertEqual(self.a.latest()['lower'],'1.8')

if __name__ == '__main__':unittest.main()
