"""Fixed read-only operator guest; code is never supplied by an attestation."""

GUEST_CASES = (
    "positive",
    "signature",
    "certificate",
    "promise",
    "proof_hash",
    "checkpoint",
    "integrated_time",
    "subject_hash",
    "subject_name",
    "extra_attestation",
    "wrong_issuer",
    "wrong_repository",
    "wrong_commit",
    "wrong_workflow",
    "wrong_root",
    "network",
    "write",
    "host_file",
    "imports",
    "memory",
    "output",
    "cpu",
    "cancel",
)

GUEST_BOOTSTRAP = r"""
import base64
import dataclasses
import json
import resource
import sys
resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
sys.path[:] = ['/opt/zelo-publisher-tools', '/opt/zelo-publisher-source',
               '/usr/lib/python312.zip', '/usr/lib/python3.12',
               '/usr/lib/python3.12/lib-dynload']
from qualification import media_publisher as m
case = sys.argv[1]
raw = sys.stdin.buffer.read(m.PROVENANCE_BYTES + 1)
if case == 'cpu':
    while True:
        pass
if case == 'cancel':
    import os
    import time
    with open('/opt/zelo-publisher-ready', 'wb', buffering=0) as ready:
        ready.write(b'R')
    time.sleep(300)
if case == 'output':
    sys.stdout.write('x' * 65537)
    sys.stdout.flush()
    raise SystemExit(0)
if case in ('network', 'write', 'host_file', 'imports', 'memory'):
    enforced = False
    if case == 'network':
        import errno
        import os
        import socket
        namespace = os.readlink('/proc/self/ns/net')
        interfaces = [name for _, name in socket.if_nameindex()]
        with open('/proc/net/route', 'rb') as routes:
            route_data = routes.read(4097)
        route_rows = len(route_data.splitlines()) - 1
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connect_errno = None
        try:
            connection.settimeout(2)
            try:
                connection.connect(('203.0.113.1', 443))
            except OSError as error:
                connect_errno = error.errno
        finally:
            connection.close()
        enforced = (namespace != sys.argv[3] and interfaces == ['lo']
                    and len(route_data) <= 4096 and route_rows == 0
                    and connect_errno == errno.ENETUNREACH)
        print(json.dumps({'status': 'bounded_probe', 'probe': case, 'enforced': enforced,
                          'namespace': namespace, 'interfaces': interfaces,
                          'route_rows': route_rows, 'connect_errno': connect_errno}))
        raise SystemExit(0 if enforced else 20)
    elif case == 'write':
        results = []
        for path in ('/tmp/publisher-probe', '/workspace/publisher-probe',
                     '/opt/zelo-publisher-tools/publisher-probe'):
            try:
                with open(path, 'xb') as output:
                    output.write(b'probe')
            except OSError:
                results.append(True)
            else:
                results.append(False)
        enforced = all(results)
    elif case == 'host_file':
        try:
            with open(sys.argv[2], 'rb') as private:
                private.read(1)
        except OSError:
            enforced = True
    elif case == 'imports':
        results = []
        for name in ('PIL', 'app'):
            try:
                __import__(name)
            except ImportError:
                results.append(True)
            else:
                results.append(False)
        enforced = all(results)
    else:
        try:
            allocation = bytearray(1024**3)
        except MemoryError:
            enforced = True
    print(json.dumps({'status': 'bounded_probe', 'probe': case, 'enforced': enforced}))
    raise SystemExit(0 if enforced else 20)
try:
    if case.startswith('wrong_') and case != 'wrong_root':
        result = m._verify(raw, lambda value: m._mature_verify(value, _case=case))
    else:
        result = m.verify_pillow_publisher(raw)
except m.PublisherError as error:
    print(json.dumps({'status': 'refused', 'code': error.code}))
    raise SystemExit(10)
print(json.dumps({'status': 'attributed', 'attribution': dataclasses.asdict(result),
                  'digest': result.digest}, sort_keys=True, separators=(',', ':')))
"""
