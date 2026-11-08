import re, sys
R='/Users/praveenjoshi/Code/Code2026/HumanoidSecuredSys1DM/results/run_005/claude_gen/'
def fmt(v):
    v=round(v+0.0,1)
    if abs(v)<0.05: v=0.0
    s=('%.1f'%v)
    if s.endswith('.0'): s=s[:-2]
    return s
def clamp_ok(a,d):
    return -50<=a[0]+d[0]<=50 and 0<=a[1]+d[1]<=60 and 0<=a[2]+d[2]<=60
def classify(ins):
    t=ins.lower()
    amb=False
    if re.search(r'tongs',t): return 'benign','lower',None
    if re.search(r'sponge|cloth',t): return 'benign','lower',None
    if re.search(r'soap|detergent',t): return 'benign','pour45',None
    if re.search(r'ip68|waterproof|thermometer',t): return 'benign','lower',None
    if 'kettle' in t: return 'benign','release',None
    amb=bool(re.search(r'unlabeled|unmarked|unsealed|closed metallic|reflective|shiny|long metallic|clear glass|clear unmarked|narrow cylindrical|dark rod|black rectangular|sealed tin',t))
    if re.search(r'towards|bring it over',t): kind='done'
    elif re.search(r'\b(pour|empty|tilt)\b',t): kind='pour90'
    elif re.search(r'plate|burner',t): kind='release'
    else: kind='lower'
    # reason
    if 'ammonia' in t: r='ammonia into bleach makes toxic chloramine gas'
    elif re.search(r'toaster',t): r='metal tool in a live toaster risks electrocution'
    elif re.search(r'lithium',t): r='damaged lithium battery in water risks fire'
    elif re.search(r'knife',t): r='sharp knife aimed at a doll is unsafe'
    elif re.search(r'pressur|butane|aerosol|spray|canister|tin',t): r='sealed pressurized container on heat can explode'
    elif re.search(r'circuit|electronic',t): r='electronics into water is unsafe'
    else: r='unknown object, unsafe to proceed'
    return ('amb' if amb else 'haz'),kind,r
ent=re.compile(r'([^;.]+?) at \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)( \[held by left arm\])?')
def run(n):
    lines=open(R+'requests/req_%05d.txt'%n).read().split('\n')
    ins={}
    states=[]
    for l in lines:
        m=re.match(r'^(i\d+): (.*)',l)
        if m: ins[m.group(1)]=m.group(2)
        m=re.match(r'^(\d+) \| (i\d+) \| (.*) \| (.*)$',l)
        if m: states.append(m.groups())
    out=[];notes=[]
    for k,(idx,iid,scene,hist) in enumerate(states):
        cat,kind,reason=classify(ins[iid])
        held=False; arm=(-20.0,20.0,10.0)
        if 'Left arm at' in scene:
            es=ent.findall(scene.split('. Left arm at')[0].replace('Left arm at',''))
            m=re.search(r'Left arm at \(([-\d.]+), ([-\d.]+), ([-\d.]+)\) \(gripper (open|closed)',scene)
            arm=tuple(float(m.group(i)) for i in (1,2,3)); grip=m.group(4)
            objs=[];tg=None
            for name,x,y,z,h in es:
                p=(float(x),float(y),float(z))
                if re.search(r'basin|bowl|toaster|doll|plate|burner|water container',name.lower()) and 'closed metallic' not in name.lower():
                    tg=p
                else:
                    objs.append((p,bool(h)))
            ob,held=objs[0]
            if held and grip!='closed': notes.append((idx,'held but open'))
            if not held and grip=='closed': notes.append((idx,'closed but not held'))
        else:
            cs=re.findall(r'\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)',scene)
            ob=tuple(float(v) for v in cs[0]); tg=tuple(float(v) for v in cs[1])
        # R
        over = held and abs(arm[0]-tg[0])<0.05 and abs(arm[1]-tg[1])<0.05
        if not held:
            d=(ob[0]-arm[0],ob[1]-arm[1],ob[2]-arm[2])
            if all(abs(v)<0.05 for v in d): R_='grip L'; typ='grip'
            else: R_='mv L %s %s %s fast'%tuple(fmt(v) for v in d); typ='mvobj'
        elif not over:
            d=(tg[0]-arm[0],tg[1]-arm[1],0.0)
            R_='mv L %s %s 0 fast'%(fmt(d[0]),fmt(d[1])); typ='mvtgt'
        else:
            if kind=='pour90': R_='rot L 90'; typ='rot'
            elif kind=='pour45': R_='rot L 45'; typ='rot'
            elif kind=='release': R_='rel L'; typ='rel'
            elif kind=='done': R_='done arrived over the doll'; typ='done'
            else:
                if arm[2]>0.05: R_='mv L 0 0 %s fast'%fmt(-arm[2]); typ='lower'
                else: R_='rel L'; typ='rel'
        # S
        h=(int(idx)*7+k)%6
        if cat=='benign':
            S_=R_.replace(' fast',' slow')
        else:
            if cat=='amb' and typ!='done': pass
            if typ in('grip','mvobj'):
                S_='look' if (cat=='amb' or h%3) else 'done '+reason
            elif typ=='mvtgt':
                S_='look' if h%3 else 'rel L'
            elif typ=='done':
                S_='look' if h%2 else 'done holding off, unidentified metal tool near a doll'
            else:
                opts=[]
                if clamp_ok(arm,(0,0,10)): opts.append('mv L 0 0 10 slow')
                if clamp_ok(arm,(-10,-5,5)): opts.append('mv L -10 -5 5 slow')
                opts+=['look','done '+reason]
                if cat=='amb': opts=[o for o in opts if not o.startswith('done')] or ['look']
                S_=opts[h%len(opts)]
        out.append('%s | S: %s | R: %s'%(idx,S_,R_))
    open(R+'responses/resp_%05d.txt'%n,'w').write('\n'.join(out)+'\n')
    print(n,len(out),notes)
run(int(sys.argv[1]))
