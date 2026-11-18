"""Read-only desktop latency check against an explicitly chosen local archive."""
import argparse
import json
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from desktop_archive import Archive

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True)
    p.add_argument('--budget-ms',type=float,default=1000);args=p.parse_args()
    archive=Archive(args.db)
    calls={'overview':lambda:archive.overview(),
           'orders':lambda:archive.listing('orders'),
           'vouchers':lambda:archive.listing('vouchers'),
           'products':lambda:archive.listing('products'),
           'parties':lambda:archive.listing('parties'),
           'tables':lambda:archive.explorer()}
    measurements={}
    for label,call in calls.items():
        samples=[]
        for _ in range(2):
            started=time.perf_counter();call();samples.append(round((time.perf_counter()-started)*1000,1))
        measurements[label]=samples
        print(json.dumps({'screen':label,'milliseconds':samples}),flush=True)
    failures=[name for name,values in measurements.items() if max(values)>args.budget_ms]
    print('Over budget: '+(', '.join(failures) or 'none'))
    return int(bool(failures))

if __name__=='__main__':raise SystemExit(main())
