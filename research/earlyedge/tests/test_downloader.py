import json, unittest
from research.earlyedge.download_binance import download_klines

def kline(t):
    return [t,"1","2","0.5","1.5","10",t+299999,"15","20","5","7","0"]

class FakeResponse:
    def __init__(self,data): self.data=data
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.data).encode()

class DownloaderTests(unittest.TestCase):
    def test_downloads_pages_without_duplicate_candles(self):
        pages=[[kline(0),kline(300000)], [kline(600000)]]
        calls=[]
        def opener(req,timeout=20):
            calls.append(req.full_url)
            return FakeResponse(pages.pop(0))
        rows=download_klines("ETHUSDT","5m",0,900000,opener=opener,pause=0)
        self.assertEqual([r[0] for r in rows],[0,300000,600000])
        self.assertEqual(len(calls),2)

    def test_excludes_not_closed_candle(self):
        now=900000
        def opener(req,timeout=20):
            return FakeResponse([kline(0),kline(600000)])
        rows=download_klines("ETHUSDT","5m",0,now,opener=opener,pause=0)
        self.assertEqual([r[0] for r in rows],[0,600000])

    def test_rejects_invalid_interval(self):
        with self.assertRaises(ValueError):
            download_klines("ETHUSDT","2m",0,600000,pause=0)

if __name__=="__main__": unittest.main()
