"""Fixed authenticated metadata guest bootstrap; never caller-provided code."""

SOURCE_PINS = (
    (
        "qualification/__init__.py",
        82,
        "6e56244875c3f69a5985c579a973b1b5dade147aaffe2d190dcf0565f1be7404",
    ),
    (
        "qualification/artifacts.py",
        9301,
        "a6f4bd3cd162bb1176869d429cc5f089fd6a02411420f60f2b30f1a5092fc3ea",
    ),
    (
        "qualification/runtime_contracts.py",
        2997,
        "f52e3699d6a2048b7c587013b4c86004006b4884ae64507202c04f7c68a1edac",
    ),
    (
        "qualification/media_elf_metadata.py",
        14604,
        "fc5019fbd4597c5ee593ad51cc04e045f6da1829272b4a00b09517e468f84d3e",
    ),
    (
        "qualification/media_elf_version_metadata.py",
        24787,
        "44ae8ae9737bb8aff2f8a044525f4e3f8b83cc4cfcf68c4142e992f8118479ac",
    ),
)

CASES = (
    "fixture",
    "unversioned_fixture",
    "input",
    "malformed",
    "network",
    "write",
    "host_file",
    "imports",
    "memory",
    "output",
    "cpu",
    "cancel",
    "cancel_repeated",
    "timeout",
    "setup_failure",
    "source_pin",
    "tool_pin",
)
GUEST_BOOTSTRAP = (
    "SOURCE_PINS = "
    + repr(SOURCE_PINS)
    + "\n"
    + r"""
import resource
resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912))
resource.setrlimit(resource.RLIMIT_CPU,(10,10))
resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_NPROC,(0,0))
import hashlib,json,os,stat,sys
case = sys.argv[1]
allowed = ("fixture", "unversioned_fixture", "input", "malformed", "network", "write", "host_file",
           "imports", "memory", "output", "cpu", "cancel", "cancel_repeated",
           "timeout", "setup_failure", "source_pin", "tool_pin")
if case not in allowed:
    raise SystemExit(20)
expected_env = {"PATH":"/usr/bin","LANG":"C.UTF-8","LC_ALL":"C.UTF-8",
                "TMPDIR":"/tmp","PWD":"/workspace"}
if dict(os.environ) != expected_env:
    raise SystemExit(21)
source_ok = True
for name,size,digest in SOURCE_PINS:
    path = '/opt/zelo-elf-source/' + name
    try:
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o022 or before.st_size != size or size > 32768):
                raise ValueError('source')
            content = bytearray()
            while chunk := os.read(fd,min(65536,size-len(content)+1)):
                content.extend(chunk)
                if len(content)>size:
                    raise ValueError('source')
            after = os.fstat(fd)
            if (len(content)!=size or hashlib.sha256(content).hexdigest()!=digest
                or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,
                    before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,
                                         after.st_mtime_ns,after.st_ctime_ns)):
                raise ValueError('source')
        finally:
            os.close(fd)
    except (OSError,ValueError):
        source_ok = False
        break
if not source_ok:
    print(json.dumps({'status':'source_refused'}))
    raise SystemExit(0 if case=='source_pin' else 22)
if case=='source_pin':
    raise SystemExit(23)
sys.path[:] = ['/opt/zelo-elf-tools/lib','/opt/zelo-elf-source',
               '/usr/lib/python312.zip','/usr/lib/python3.12',
               '/usr/lib/python3.12/lib-dynload']
from qualification import media_elf_metadata as m
from qualification import media_elf_version_metadata as version
try:
    manifest = m.capture_manifest(m.TOOL_ROOT,m.TOOL_ROLES,m.TOOL_POLICY)
    if manifest.digest!=m.TOOL_MANIFEST_DIGEST:
        raise ValueError('tool')
except (OSError,ValueError):
    print(json.dumps({'status':'tool_refused'}))
    raise SystemExit(0 if case=='tool_pin' else 24)
if case=='tool_pin':
    raise SystemExit(25)
raw=sys.stdin.buffer.read(m.INPUT_BYTES+1)
if len(raw)>m.INPUT_BYTES:
    raise SystemExit(26)
if case in ('cpu','timeout'):
    if case=='cpu':
        while True:
            pass
    import time
    time.sleep(300)
if case in ('cancel','cancel_repeated'):
    import time
    with open('/opt/zelo-elf-ready','wb',buffering=0) as ready:
        ready.write(b'R')
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
        for path in ('/tmp/metadata-probe','/workspace/metadata-probe',
                     '/opt/zelo-elf-tools/metadata-probe'):
            try:
                with open(path,'xb') as output:
                    output.write(b'probe')
            except OSError:
                results.append(True)
            else:
                results.append(False)
        ok=all(results)
    elif case=='host_file':
        try:
            with open(sys.argv[2],'rb') as private:
                private.read(1)
        except OSError:
            ok=True
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
def audit(event,args):
    if event=='import' and args[0].split('.')[0] in ('PIL','app','ctypes','_ctypes'):
        raise RuntimeError('forbidden import')
    if (event.startswith(('subprocess.','ctypes.','socket.'))
        or event in ('os.system','os.posix_spawn','os.fork','os.exec')):
        raise RuntimeError('forbidden operation')
sys.addaudithook(audit)
try:
    output=version.inspect_elf_metadata(raw)
except m.ElfMetadataError as error:
    if case=='input':
        sys.stderr.write(str(error)+'\n')
    print(json.dumps({'status':'metadata_refused'}))
    raise SystemExit(0 if case=='malformed' else 29)
if case=='malformed':
    raise SystemExit(30)
sys.stdout.buffer.write(output+b'\n')
"""
)
