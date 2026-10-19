"""Writes assets/x-followers-{dark,light}.svg in the chain-explorer style.
Free sources: fxtwitter, then vxtwitter. If both fail, nothing is written (no fake number)."""
import json, urllib.request, os, sys
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'readme'}),timeout=20))
n=None
for u,k in [('https://api.fxtwitter.com/rahatirtaza',lambda j:j['user']['followers']),('https://api.vxtwitter.com/rahatirtaza',lambda j:j['followers_count'])]:
    try: n=k(get(u)); break
    except Exception: pass
if not isinstance(n,int): print('no data; keeping existing files'); sys.exit(0)
G=json.load(open(os.path.join(os.path.dirname(__file__),'glyphs.json')))
def txt(s,x,y):
    out='';cx=x
    for c in s:
        d,w=G.get(c,G[' ']);out+=f'<path transform="translate({cx:.1f} {y})" d="{d}"/>';cx+=w
    return out,cx-x
lab=(f'{n/1000:.1f}k' if n>=1000 else str(n))+' FOLLOWERS ON X'
os.makedirs('assets',exist_ok=True)
for m,(bg,bd,ink,c) in {'dark':('#0b0b0c','#e8e8e666','#e8e8e6','#e8e8e6'),'light':('#f6f6f3','#11111166','#111111','#111111')}.items():
    p,w=txt(lab,28,30);W=int(w+56)
    open(f'assets/x-followers-{m}.svg','w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="50" viewBox="0 0 {W} 50"><rect x="1" y="1" width="{W-2}" height="48" rx="24" fill="{bg}" stroke="{bd}" stroke-width="1.5"/><g fill="{ink}">{p}</g></svg>')
print('followers',n)
