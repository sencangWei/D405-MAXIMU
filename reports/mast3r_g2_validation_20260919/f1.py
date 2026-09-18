import sys, hashlib, numpy as np
def rep(p):
    try:
        d = np.loadtxt(p)
    except Exception as e:
        print(f"{p}: 读取失败 {e}"); return
    sha = hashlib.sha256(open(p,'rb').read()).hexdigest()[:16]
    n = len(d)
    t = d[:,0]
    f1 = np.linalg.norm(d[1,1:4]-d[0,1:4])*1000.0
    k = min(300, n)
    net = np.linalg.norm(d[k-1,1:4]-d[0,1:4])*1000.0
    # 旋转总变化
    q = d[:,3:7] if d.shape[1]>=8 else None
    print(f"{p}")
    print(f"   sha={sha}  帧数={n}  f1={f1:.4f}mm  前300帧净位移={net:.4f}mm")
for p in sys.argv[1:]:
    rep(p)
