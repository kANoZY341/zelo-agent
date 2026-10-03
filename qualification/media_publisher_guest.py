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
    import time
    time.sleep(300)
if case == 'output':
    sys.stdout.write('x' * 65537)
    sys.stdout.flush()
    raise SystemExit(0)
if case in ('network', 'write', 'host_file', 'imports', 'memory'):
    enforced = False
    if case == 'network':
        import socket
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            connection.settimeout(2)
            try:
                connection.connect(('203.0.113.1', 443))
            except OSError:
                enforced = True
        finally:
            connection.close()
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
