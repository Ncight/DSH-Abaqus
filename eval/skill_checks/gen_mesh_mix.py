# -*- coding: utf-8 -*-
# 生成一份故意扰动的六面体 + 壳网格（含翻转单元），给 chk_mesh.py 对照 Abaqus 自带检查用
import numpy as np, sys
rng=np.random.default_rng(1)
n=12
out=['*Heading','*Part, name=HEXP','*Node']
P={}
k=0
for i in range(n+1):
  for j in range(n+1):
    for l in range(n+1):
      k+=1; P[(i,j,l)]=k
      x=np.array([i,j,l*3.0])+rng.normal(0,0.28,3)*(0<i<n and 0<j<n and 0<l<n)
      out.append('%d,%g,%g,%g'%(k,*x))
out.append('*Element, type=C3D8R, elset=E')
e=0
for i in range(n):
  for j in range(n):
    for l in range(n):
      e+=1
      c=[P[(i,j,l)],P[(i+1,j,l)],P[(i+1,j+1,l)],P[(i,j+1,l)],P[(i,j,l+1)],P[(i+1,j,l+1)],P[(i+1,j+1,l+1)],P[(i,j+1,l+1)]]
      out.append('%d,'%e+','.join(map(str,c)))
out+=['*Solid Section, elset=E, material=M','*End Part','*Part, name=SHP','*Node']
k=0;Q={}
for i in range(n+1):
  for j in range(n+1):
    k+=1;Q[(i,j)]=k
    x=np.array([i*1.0,j*1.0,0])+rng.normal(0,0.3,3)*(0<i<n and 0<j<n)
    out.append('%d,%g,%g,%g'%(k,*x))
out.append('*Element, type=S4R, elset=E')
e=0
for i in range(n):
  for j in range(n):
    e+=1;out.append('%d,%d,%d,%d,%d'%(e,Q[(i,j)],Q[(i+1,j)],Q[(i+1,j+1)],Q[(i,j+1)]))
out.append('*Element, type=S3, elset=E')
for i in range(n):
  for j in range(n):
    e+=1;out.append('%d,%d,%d,%d'%(e,Q[(i,j)],Q[(i+1,j+1)],Q[(i,j+1)]))
out+=['*Shell Section, elset=E, material=M','1.','*End Part','*Assembly, name=A','*Instance, name=H1, part=HEXP','*End Instance','*Instance, name=S1, part=SHP','100,0,0','*End Instance','*End Assembly','*Material, name=M','*Elastic','1000.,0.3']
open(sys.argv[1] if len(sys.argv) > 1 else 'mesh_mix.inp','w').write('\n'.join(out)+'\n')
