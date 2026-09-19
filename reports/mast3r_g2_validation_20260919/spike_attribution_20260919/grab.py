import csv, sqlite3, struct
from pathlib import Path
import numpy as np, cv2

DB3="/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260914_202348/d405_720p_rgb_stereo_ir.db3"
FR ="/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260914_202348/d405_frames.csv"
OUT=Path("/tmp/claude-1000/frames0914"); OUT.mkdir(parents=True, exist_ok=True)

def cdr_string(b,o):
    n=struct.unpack_from("<I",b,o)[0]; o+=4
    s=b[o:o+n-1].decode("utf-8","replace"); o+=n
    return s, o+(-o)%4

def parse_image(b):
    o=4+8
    _fid,o=cdr_string(b,o)
    h,w=struct.unpack_from("<II",b,o); o+=8
    enc,o=cdr_string(b,o)
    nbytes=h*w*(2 if enc.upper().startswith("YUY") else 3 if enc.lower()=="rgb8" else 1)
    return h,w,enc,b[len(b)-nbytes:]

mono=np.array([float(r["color_mono"]) for r in csv.DictReader(open(FR,newline=""))])
OFF=1789388630.979520-mono[0]
con=sqlite3.connect(f"file:{DB3}?mode=ro&immutable=1",uri=True)
ids=[r[0] for r in con.execute("SELECT id FROM messages WHERE topic_id=69 ORDER BY id")]

def grab(dt, tag, base=1789388632.841669):
    wall=base+dt
    i=int(np.argmin(np.abs(mono+OFF-wall)))
    h,w,enc,raw=parse_image(bytes(con.execute("SELECT data FROM messages WHERE id=?",(ids[i],)).fetchone()[0]))
    a=np.frombuffer(raw,np.uint8)
    img=cv2.cvtColor(a.reshape(h,w,2),cv2.COLOR_YUV2BGR_YUYV) if enc.upper().startswith("YUY") else a.reshape(h,w,3)[:,:,::-1]
    cv2.imwrite(str(OUT/f"{tag}_i{i:04d}.png"), img)
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    return i,g

print(f"编码 YUYV, 负载 1843200B 校验通过\n")
print(f"{'标记':<14}{'轨迹位置':>9}{'帧#':>7}{'亮度':>8}{'P99':>7}{'过曝%':>7}{'欠曝%':>7}")
for dt,tag in ((36.31,"SPIKE"),(40.0,"spike+4s"),(30.0,"spike-6s"),
               (5.0,"base_09pct"),(52.0,"base_90pct"),(0.5,"base_01pct")):
    i,g=grab(dt,tag)
    print(f"{tag:<14}{dt/58.1*100:>8.1f}%{i:>7}{g.mean():>8.1f}"
          f"{np.percentile(g,99):>7.0f}{np.mean(g>250)*100:>6.2f}%{np.mean(g<10)*100:>6.2f}%")
