import numpy as np, sys
def rd(p):
    v=[float(l.split(': ')[1]) for l in open(p) if l.startswith('FPS')]
    return np.array(v)
g=rd(sys.argv[1]); b=rd(sys.argv[2]); a=rd(sys.argv[3]); bb=rd(sys.argv[4])
print(f"样本数: 好={len(g)} 坏={len(b)} A={len(a)} B={len(bb)}")
for nm,v in (("好(09-16)",g),("坏(09-17)",b),("A(TF32开)",a),("B(TF32关)",bb)):
    print(f"  {nm:12s} 首={v[0]:.2f} 末={v[-1]:.2f} 中位={np.median(v):.2f} 峰={v.max():.2f}")
print(f"\n坏/好 逐点比值: 中位={np.median(b[:len(g)]/g):.3f} 均值={(b[:len(g)]/g).mean():.3f} 范围=[{(b[:len(g)]/g).min():.3f},{(b[:len(g)]/g).max():.3f}]")
print(f"A/B 逐点比值: 中位={np.median(a[:len(bb)]/bb):.3f}")
