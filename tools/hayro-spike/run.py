# Compares hayro with pdfium on page 1 of every PDF in ./pdfs (not committed). Run it in a native x86_64
# container with `hayro-spike` (cargo build --release in this directory) beside it, pypdfium2, Pillow, numpy
# and GNU time installed. It prints the CSV committed as results.csv (the pip notice that shows up on the
# first line of a raw capture is stripped).
import subprocess, glob, os, sys, time, resource
import pypdfium2 as pdfium
from PIL import Image, ImageChops
import numpy as np
MAX = 2000
os.makedirs("out", exist_ok=True)
print("file,status,hayro_s,pdfium_s,hayro_rss_mb,mean_abs,pct_diff32,size")
for p in sorted(glob.glob("pdfs/*.pdf")):
    n = os.path.basename(p)[:-4]
    h = f"out/{n}.hayro.png"
    t = time.time()
    r = subprocess.run(["/usr/bin/time","-v","./hayro-spike",p,h,str(MAX)],capture_output=True,text=True,timeout=300)
    ht = time.time()-t
    rss = [l for l in r.stderr.splitlines() if "Maximum resident" in l]
    rss = int(rss[0].split()[-1])/1024 if rss else -1
    line = r.stdout.strip()
    if not line.startswith("OK"):
        print(f"{n},{line or 'CRASH rc=%d'%r.returncode},,,{rss:.0f},,,"); continue
    try:
        t = time.time()
        doc = pdfium.PdfDocument(p); pg = doc[0]
        w,hh = pg.get_size(); s = MAX/max(w,hh)
        im = pg.render(scale=s).to_pil().convert("L")
        pt = time.time()-t
        him = Image.open(h).convert("RGBA")
        bg = Image.new("RGBA",him.size,"white"); bg.alpha_composite(him); him = bg.convert("L")
        sz = f"{im.size}/{him.size}"
        if him.size != im.size: him = him.resize(im.size)
        a = np.asarray(im,dtype=np.int16); b = np.asarray(him,dtype=np.int16)
        d = np.abs(a-b)
        print(f"{n},OK,{ht:.2f},{pt:.2f},{rss:.0f},{d.mean():.3f},{(d>32).mean()*100:.3f},{sz}")
    except Exception as e:
        print(f"{n},PDFIUM_ERR {e},{ht:.2f},,{rss:.0f},,,")
