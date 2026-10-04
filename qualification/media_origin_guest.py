"""Fixed in-argv bootstrap; no project/module code loaded inside the guest."""

TOOL = (
    "/usr/bin/gpgv",
    310416,
    "f14d026b9eae172c432e015bce227483293b4966f2f3fdcfa582f71d3dbb2ae8",
    0o755,
)
KEYRING = (
    "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    3607,
    "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31",
    0o644,
)
INPUTS = (
    {
        "source": "/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_noble-updates_InRelease",
        "suite": "noble-updates",
        "bytes": 126125,
        "sha256": "73e9ce9b343db391c104da5f3574adaccd0cd672c450801afa5e6a53de0f2b2c",
        "mode": 420,
    },
    {
        "source": "/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_noble_InRelease",
        "suite": "noble",
        "bytes": 255850,
        "sha256": "cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0",
        "mode": 420,
    },
    {
        "source": "/var/lib/apt/lists/security.ubuntu.com_ubuntu_dists_noble-security_InRelease",
        "suite": "noble-security",
        "bytes": 126127,
        "sha256": "3b358f744efb789bab437ac77209ac20be2c8665197ce274f65c5f812bd4d519",
        "mode": 420,
    },
)
NEGATIVES = {
    "altered_text": {
        "bytes": 126125,
        "sha256": "a03389058ac6ba3b7b0507aca0a667ee76f2e168acbefa8437d3f4e4549aac45",
    }
}

GUEST_BOOTSTRAP = (
    "TOOL = "
    + repr(TOOL)
    + "\nKEYRING = "
    + repr(KEYRING)
    + "\nINPUTS = "
    + repr(INPUTS)
    + "\nNEGATIVES = "
    + repr(NEGATIVES)
    + "\n"
    + r"""
import resource
for kind,bound in ((resource.RLIMIT_AS,268435456),(resource.RLIMIT_CPU,10),
                   (resource.RLIMIT_NOFILE,64),(resource.RLIMIT_FSIZE,0),
                   (resource.RLIMIT_CORE,0),(resource.RLIMIT_NPROC,0)):
    resource.setrlimit(kind,(bound,bound))
import errno,hashlib,json,os,stat,sys
# The parent retains its own copies until settlement. Close any inherited child
# support descriptors before probes; only EBADF from the proc scanner is benign.
names=os.listdir('/proc/self/fd')
if len(names)>256 or any(not name.isdecimal() for name in names):
    raise SystemExit(31)
for name in names:
    fd=int(name)
    if fd<=2:
        continue
    try:
        os.fstat(fd)
    except OSError as error:
        if error.errno==errno.EBADF:
            continue
        raise
    os.close(fd)
if len(sys.argv)!=7:
    raise SystemExit(20)
case=sys.argv[1]
allowed=('input','altered_text','network','write','host_file',
         'imports','memory','output','cpu','timeout','cancel','cancel_repeated',
         'setup_failure','tool_pin','keyring_pin','input_pin')
if case not in allowed or sys.argv[4] not in ('0','1','2'):
    raise SystemExit(20)
index=int(sys.argv[4])
if case in ('cancel','cancel_repeated'):
    if any(not value.isdecimal() or len(value)>20 or str(int(value))!=value
           for value in sys.argv[5:7]):
        raise SystemExit(20)
    ready_identity=tuple(int(value) for value in sys.argv[5:7])
elif sys.argv[5:7]!=['-','-']:
    raise SystemExit(20)
expected_env={'PATH':'/usr/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
              'TMPDIR':'/tmp','HOME':'/tmp/zelo-gnupg','GNUPGHOME':'/tmp/zelo-gnupg',
              'PWD':'/workspace'}
if dict(os.environ)!=expected_env:
    raise SystemExit(21)
def identity_refused(check,observed):
    print(json.dumps({'status':'identity_refused','check':check,'observed':observed}))
    raise SystemExit(22)
if os.getresuid()!=(1000,1000,1000) or os.getresgid()!=(1000,1000,1000):
    identity_refused('ids',[list(os.getresuid()),list(os.getresgid())])
with open('/proc/self/status','rb') as source:
    status=source.read(16385)
if len(status)>16384:
    identity_refused('status-bound',len(status))
caps={row.split(b':',1)[0]:row.split(b':',1)[1].strip()
      for row in status.splitlines() if row.startswith(b'Cap')}
if (set(caps)!={b'CapInh',b'CapPrm',b'CapEff',b'CapBnd',b'CapAmb'}
    or any(int(v,16) for v in caps.values())):
    identity_refused('capabilities',{k.decode('ascii'):v.decode('ascii')
                                    for k,v in caps.items()})
for name in ('uid_map','gid_map'):
    with open('/proc/self/'+name,'rb') as source:
        mapping=source.read(257)
    if len(mapping)>256:
        identity_refused(name+'-bound',len(mapping))
    # --dev needs devpts: outer setup namespace maps host1000 to0, then
    # --disable-userns creates the final namespace mapping guest1000 to outer0.
    if mapping.split()!=[b'1000',b'0',b'1']:
        identity_refused(name,mapping.decode('ascii'))
def read(path,size,digest,mode):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=0
            or stat.S_IMODE(before.st_mode)!=mode or before.st_size!=size
            or not 0<size<=524288):
            raise ValueError('identity')
        raw=bytearray()
        while chunk:=os.read(fd,min(65536,size-len(raw)+1)):
            raw.extend(chunk)
            if len(raw)>size:
                raise ValueError('size')
        after=os.fstat(fd)
        if (len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest
            or (before.st_dev,before.st_ino,before.st_mode,before.st_nlink,before.st_size,
                before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,
                after.st_mode,after.st_nlink,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError('drift')
    finally:
        os.close(fd)
target=NEGATIVES[case] if case in NEGATIVES else INPUTS[index]
for role,path,size,digest,mode in (
    ('tool','/opt/zelo-origin-gpgv',TOOL[1],TOOL[2],TOOL[3]),
    ('keyring','/opt/zelo-origin-keyring.gpg',KEYRING[1],KEYRING[2],KEYRING[3]),
    ('input','/opt/zelo-origin.InRelease',target['bytes'],target['sha256'],0o644)):
    try:
        read(path,size,digest,mode)
    except (OSError,ValueError):
        print(json.dumps({'status':role+'_refused'}))
        raise SystemExit(0 if case==role+'_pin' else 23)
if case.endswith('_pin'):
    raise SystemExit(24)
if case in ('cpu','timeout'):
    if case=='cpu':
        while True:
            pass
    import time
    time.sleep(300)
if case in ('cancel','cancel_repeated'):
    import time
    ready=os.open('/opt/zelo-origin-ready',
                  os.O_WRONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        info=os.fstat(ready)
        if (not stat.S_ISFIFO(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600
            or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=ready_identity):
            raise SystemExit(32)
        if os.write(ready,b'R')!=1:
            raise SystemExit(32)
    finally:
        os.close(ready)
    time.sleep(300)
if case=='output':
    sys.stdout.write('x'*65537)
    sys.stdout.flush()
    raise SystemExit(0)
if case in ('network','write','host_file','imports','memory'):
    ok=False
    if case=='network':
        import errno,socket
        namespace=os.readlink('/proc/self/ns/net')
        interfaces=[name for _,name in socket.if_nameindex()]
        with open('/proc/net/route','rb') as route:
            route_data=route.read(4097)
        routes=len(route_data.splitlines())-1
        connection=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        code=None
        try:
            connection.settimeout(2)
            try:
                connection.connect(('203.0.113.1',443))
            except OSError as error:
                code=error.errno
        finally:
            connection.close()
        ok=(namespace!=sys.argv[3] and interfaces==['lo'] and len(route_data)<=4096
            and routes==0 and code==errno.ENETUNREACH)
        print(json.dumps({'status':'bounded_probe','probe':'network','enforced':ok,
                          'namespace':namespace,'interfaces':interfaces,
                          'route_rows':routes,'connect_errno':code}))
        raise SystemExit(0 if ok else 27)
    if case=='write':
        results=[]
        for path in ('/tmp/origin-probe','/workspace/origin-probe',
                     '/opt/origin-probe'):
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
            except OSError as error:
                results.append(error.errno==errno.EROFS)
            else:
                os.close(fd)
                # Creation itself succeeded: fail even when later writes would EFBIG.
                print(json.dumps({'status':'probe','case':case,'enforced':False}))
                raise SystemExit(28)
        ok=all(results)
    elif case=='host_file':
        try:
            fd=os.open(sys.argv[2],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        except OSError as error:
            ok=error.errno==errno.ENOENT
        else:
            os.close(fd)
            ok=False
    elif case=='imports':
        results=[]
        for name in ('PIL','app','tests','scripts'):
            try:
                __import__(name)
            except ImportError:
                results.append(True)
            else:
                results.append(False)
        ok=all(results)
    elif case=='memory':
        try:
            bytearray(1073741824)
        except MemoryError:
            ok=True
    print(json.dumps({'status':'probe','case':case,'enforced':ok}))
    raise SystemExit(0 if ok else 28)

# No caller code, optional library, shell, or additional process. The already
# authenticated private byte copy is mounted read-only at this fixed path.
# Preserve a final independent guard after explicit early support-FD closure.
names=os.listdir('/proc/self/fd')
if len(names)>8 or any(not name.isdecimal() for name in names):
    raise SystemExit(31)
for name in names:
    fd=int(name)
    if fd<=2:
        continue
    try:
        os.fstat(fd)
    except OSError as error:
        if error.errno!=errno.EBADF:
            raise
    else:
        raise SystemExit(31)
os.execve('/opt/zelo-origin-gpgv',
    ['/opt/zelo-origin-gpgv','--homedir','/tmp/zelo-gnupg',
     '--keyring','/opt/zelo-origin-keyring.gpg','--log-file','/dev/null',
     '--status-fd','2','--output','-','--','/opt/zelo-origin.InRelease'],
    expected_env)
"""
)
