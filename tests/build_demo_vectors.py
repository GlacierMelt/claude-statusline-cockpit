#!/usr/bin/env python3
"""Generate content-free demo snapshots with the actual SQLite engine."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_history import BASE, event
from cache_history import BUCKET_MS, History
from cache_sources import parse_feed


def snapshots(steps):
    output=[]
    with tempfile.TemporaryDirectory(prefix="cockpit-demo-") as tmp:
        h=History(Path(tmp)/"history.sqlite3")
        try:
            for label, description, rows, now in steps:
                with h.transaction():
                    for i,row in enumerate(rows):
                        h.put(parse_feed(row,i+1,now))
                    view=h.view()
                output.append({"label":label,"description":description,"now_ms":now,"view":view})
        finally:
            h.close()
    return output


def main():
    now=BASE+100
    first=event("first",BASE-11*BUCKET_MS,read=0,uncached=100)
    mid=event("mid",BASE-3*BUCKET_MS,read=900,uncached=100)
    last=event("last",BASE,read=1000,uncached=0)
    frozen=[first,mid,last]
    suites={
      "live":snapshots([
        ("Recovered requests","Three genuine completed requests establish the activity window.",frozen,now),
        ("Idle +5 minutes","Wall time passes; no new request, no movement.",[],now+BUCKET_MS),
        ("Idle +1 hour / TTL expired","No cache operation occurred. TTL is not usage.",[],now+12*BUCKET_MS),
        ("Idle +1 day","The complete label, ratio and twelve cells remain frozen.",[],now+86400000),
        ("Repeat the same records","Duplicate identities are ignored, regardless of later observation time.",frozen,now+86400000),
        ("New request after idle","One new occupied bucket advances ONE slot after a day idle; no 288-slot catch-up. This request has true 0% cache hit.",[event("after-idle",BASE+86400000,read=0,write=990,uncached=10)],now+86400000),
        ("Same bucket new request","Token-weighted total changes without shifting a single cell.",[event("same-bucket",BASE+86400001,read=9000,uncached=1000)],now+86400002),
      ]),
      "boundaries":snapshots([
        ("Before boundary","A request at 1 millisecond before the clock boundary belongs to the old bucket.",[event("before",BASE-1,read=0,uncached=100)],now),
        ("Exactly on boundary","A genuine new request belongs to the new bucket; shift by one, not a rounded-up interval.",[event("on",BASE,read=100,uncached=0)],now),
        ("After boundary","Another request in the same bucket changes weighted usage, not the position.",[event("after",BASE+1,read=95,uncached=5)],now),
        ("80–100% ramp","Twelve request buckets demonstrate all eight original height/color steps.",[event("ramp-"+str(i),BASE+(i+1)*BUCKET_MS,read=round(pct*100),uncached=10000-round(pct*100)) for i,pct in enumerate((80,82.5,85,87.5,90,92.5,95,97.5,100,0,90,100))],now+12*BUCKET_MS),
      ]),
      "sessions":snapshots([
        ("Session A, cold small call","A small request contributes 100 fresh tokens.",[event("same-id",BASE,read=0,uncached=100,session_id="A")],now),
        ("Session B, same second","A large call with a separate session contributes 9,900 cached and 100 fresh tokens.",[event("same-id",BASE,read=9900,uncached=100,session_id="B")],now),
        ("Interleaved duplicate A/B","Shared token weighting is about 98%, not a 50% average of requests. Duplicates do not accumulate.",[event("same-id",BASE,read=0,uncached=100,session_id="A"),event("same-id",BASE,read=9900,uncached=100,session_id="B")],now+BUCKET_MS),
        ("Next bucket session A","A new request advances all shared history once across the clock boundary.",[event("next",BASE+BUCKET_MS,read=900,uncached=100,session_id="A")],now+BUCKET_MS),
        ("Late old request session C","Older evidence changes a past cell but cannot rewind the shared anchor.",[event("late",BASE-2*BUCKET_MS,read=100,uncached=0,session_id="C")],now+2*BUCKET_MS),
      ])
    }
    dest=Path(__file__).resolve().parents[1]/"demos/history-vectors.json"
    dest.write_text(json.dumps({"schema":"cockpit-demo/v1","fixture_only":True,"suites":suites},indent=2)+"\n")
    print(dest)


if __name__=="__main__":main()
