"""Fixed scanner only; the copied package root never enters import search paths."""

IMAGE_ROOT = "/opt/zelo-media-image"
SOURCE_ROOT = "/opt/zelo-image-source"
READY_PATH = "/opt/zelo-image-ready"
GUEST_CASES = (
    "copy",
    "mutations",
    "network",
    "host_file",
    "imports",
    "memory",
    "output",
    "cpu",
    "cancel",
    "timeout",
    "setup_failure",
    "cancel_repeated",
)

GUEST_BOOTSTRAP = r"""
import dataclasses
import errno
import importlib.util
import json
import os
import resource
import socket
import sys
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
sys.path[:] = ['/opt/zelo-image-source', '/usr/lib/python312.zip',
               '/usr/lib/python3.12', '/usr/lib/python3.12/lib-dynload']
from qualification.artifacts import capture_manifest
from qualification.media_preparation import SCAN_POLICY, ROLES
root = '/opt/zelo-media-image'
case = sys.argv[1]
raw = sys.stdin.buffer.read(65537)
if len(raw) > 65536:
    raise SystemExit(20)
expected = json.loads(raw)
before = capture_manifest(root, ROLES, SCAN_POLICY)
if json.loads(json.dumps(dataclasses.asdict(before))) != expected:
    raise SystemExit(20)
if case == 'cpu':
    while True:
        pass
if case in ('cancel', 'cancel_repeated', 'timeout'):
    import time
    if case in ('cancel', 'cancel_repeated'):
        with open('/opt/zelo-image-ready', 'wb', buffering=0) as ready:
            ready.write(b'R')
    time.sleep(300)
if case == 'output':
    sys.stdout.write('x' * 65537)
    sys.stdout.flush()
    raise SystemExit(0)
probes = {}
if case == 'mutations':
    file = root + '/PIL/Image.py'
    directory = root + '/PIL'
    def write():
        with open(file, 'r+b') as output:
            output.write(b'x')
    actions = {
        'write': write,
        'truncate': lambda: os.truncate(file, 0),
        'chmod_file': lambda: os.chmod(file, 0o600),
        'create': lambda: os.open(root + '/new-file', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
        'unlink': lambda: os.unlink(file),
        'rename': lambda: os.rename(file, root + '/renamed-file'),
        'mkdir': lambda: os.mkdir(root + '/new-directory'),
        'rmdir': lambda: os.rmdir(directory),
        'chmod_directory': lambda: os.chmod(directory, 0o700),
    }
    for name, action in actions.items():
        try:
            value = action()
        except OSError as error:
            probes[name] = error.errno == errno.EROFS
        else:
            if name == 'create':
                os.close(value)
            probes[name] = False
elif case == 'network':
    namespace = os.readlink('/proc/self/ns/net')
    interfaces = [name for _, name in socket.if_nameindex()]
    with open('/proc/net/route', 'rb') as routes:
        data = routes.read(4097)
    rows = len(data.splitlines()) - 1
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    code = None
    try:
        connection.settimeout(2)
        try:
            connection.connect(('203.0.113.1', 443))
        except OSError as error:
            code = error.errno
    finally:
        connection.close()
    probes = {'status': 'bounded_probe', 'probe': 'network',
              'namespace': namespace, 'interfaces': interfaces,
              'route_rows': rows, 'connect_errno': code,
              'enforced': namespace != sys.argv[3] and interfaces == ['lo']
                          and rows == 0 and len(data) <= 4096 and code == errno.ENETUNREACH}
elif case == 'host_file':
    try:
        with open(sys.argv[2], 'rb') as sentinel:
            sentinel.read(1)
    except FileNotFoundError:
        probes['inaccessible'] = True
    else:
        probes['inaccessible'] = False
elif case == 'imports':
    # Finding a top-level spec never executes its package code.
    probes = {name: importlib.util.find_spec(name) is None for name in ('PIL', 'app')}
elif case == 'memory':
    try:
        allocation = bytearray(1024**3)
    except MemoryError:
        probes['refused'] = True
    else:
        probes['refused'] = False
elif case != 'copy':
    raise SystemExit(20)
after = capture_manifest(root, ROLES, SCAN_POLICY)
imports_absent = not any(name == 'PIL' or name.startswith('PIL.') for name in sys.modules)
environment_closed = dict(os.environ) == {'PATH': '/usr/bin', 'LANG': 'C.UTF-8',
                                          'LC_ALL': 'C.UTF-8', 'TMPDIR': '/tmp'}
print(json.dumps({'status': 'image_scanned', 'case': case,
                  'manifest': dataclasses.asdict(before), 'digest': before.digest,
                  'after_manifest': dataclasses.asdict(after), 'after_digest': after.digest,
                  'probes': probes, 'imports_absent': imports_absent,
                  'environment_closed': environment_closed},
                 sort_keys=True, separators=(',', ':')))
"""
