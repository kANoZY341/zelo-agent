"""Fixed synthetic USTAR isolation cases; no native operation on import."""

import hashlib
import json

from qualification import media_member_contract as contract

SOURCE_FILES = (
    (
        "qualification/media_member_contract.py",
        "/opt/zelo-ustar/qualification/media_member_contract.py",
        11519,
        "ff297c9c854255508289889ae6cc44e674d6b7dc0123ca0c2686e88f7c8acba0",
    ),
    (
        "qualification/media_tar_inventory.py",
        "/opt/zelo-ustar/qualification/media_tar_inventory.py",
        11642,
        "7a1afd0bbd6b7616ce9ee1ff286d8004d47926fddd36524fd690600bed897be3",
    ),
    (
        "runtime/media-tarfile-source-r2-20261004/source.reference",
        "/opt/zelo-ustar/tarfile.py",
        113907,
        "664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512",
    ),
)
MALFORMED = {
    "checksum": "tar_header_invalid",
    "extension": "tar_type",
    "unsafe_name": "member_name",
    "truncated": "tar_truncated",
    "payload_padding": "tar_payload_padding",
    "terminator": "tar_terminator",
    "trailing": "tar_trailing_data",
}
MATRIX = (
    "positive",
    *MALFORMED,
    "network",
    "readonly",
    "host_hidden",
    "imports",
    "resources",
    "stdout_limit",
    "stderr_limit",
    "timeout",
    "cancel",
    "cancel_repeated",
)
NATIVE_ERRORS = {
    "stdout_limit": "process_output_limit",
    "stderr_limit": "process_output_limit",
    "timeout": "process_timeout",
    "cancel": "operator_cancelled",
    "cancel_repeated": "operator_repeated_cancelled",
}
ENV = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/tmp"}
PAYLOAD = b"Zelo verified USTAR\n"
FIXTURE_MAX = 32768
READY_PATH = "/opt/zelo-ustar-ready"
LIMITS = {"as": 268435456, "cpu": 5, "nofile": 32, "fsize": 0, "core": 0, "nproc": 0}
_SCHEMA = "zelo-ustar-isolated-case-v1"
_SYNTHETIC_PROOF = hashlib.sha256(b"zelo-ustar-synthetic-only-not-package-provenance").hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _header(name, kind=b"0", size=0, link=""):
    """Construct a fixed test fixture, not an archive-reading implementation."""
    raw = bytearray(512)
    for offset, text in ((0, name), (157, link)):
        encoded = text.encode("ascii")
        if len(encoded) > 100:
            raise ValueError("synthetic_header")
        raw[offset : offset + len(encoded)] = encoded
    for start, end, value in (
        (100, 108, 0o644),
        (108, 116, 0),
        (116, 124, 0),
        (124, 136, size),
        (136, 148, 0),
        (329, 337, 0),
        (337, 345, 0),
    ):
        raw[start:end] = f"{value:0{end - start - 1}o}".encode() + b"\0"
    raw[156:157] = kind
    raw[257:265] = b"ustar\x0000"
    raw[148:156] = b" " * 8
    raw[148:156] = f"{sum(raw):06o}".encode() + b"\0 "
    return bytes(raw)


def fixture(case):
    if type(case) is not str or case not in MATRIX:
        raise ValueError("ustar_case")
    base = (
        _header("./", b"5")
        + _header("usr/", b"5")
        + _header("usr/tool", size=len(PAYLOAD))
        + PAYLOAD
        + b"\0" * (-len(PAYLOAD) % 512)
        + _header("alias", b"2", link="usr/tool")
        + _header("hard", b"1", link="usr/tool")
        + b"\0" * 1024
    )
    if case == "extension":
        return _header("extension", b"x", size=2**30) + b"\0" * 1024
    if case == "unsafe_name":
        return _header("../escape") + b"\0" * 1024
    if case == "truncated":
        return base[:-1]
    if case == "trailing":
        return base + _header("hidden")
    if case in ("checksum", "payload_padding", "terminator"):
        raw = bytearray(base)
        index = {
            "checksum": 0,
            "payload_padding": 3 * 512 + len(PAYLOAD),
            "terminator": len(base) - 512,
        }[case]
        raw[index] = 1
        return bytes(raw)
    return base


def expected_binding(case, run_id, policy_digest):
    raw = fixture(case)
    result = contract.ExpectedBinding(
        hashlib.sha256(raw).hexdigest(), len(raw), _SYNTHETIC_PROOF, policy_digest, run_id
    )
    return contract._binding(result)


def source_hashes():
    return {dest: digest for _, dest, _, digest in SOURCE_FILES}


def expected_value(case, run_id, policy_digest):
    raw = fixture(case)
    result = {
        "schema": _SCHEMA,
        "case": case,
        "source_hashes": source_hashes(),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_authority": False,
    }
    if case in MALFORMED:
        return {**result, "status": "refused-input", "code": MALFORMED[case]}
    if case == "positive":
        rows = []
        for ordinal, (name, kind, data, target) in enumerate(
            (
                ("./", "directory", b"", None),
                ("usr/", "directory", b"", None),
                ("usr/tool", "regular", PAYLOAD, None),
                ("alias", "symlink", b"", "usr/tool"),
                ("hard", "hardlink", b"", "usr/tool"),
            )
        ):
            rows.append(
                {
                    "ordinal": ordinal,
                    "name": name,
                    "kind": kind,
                    "mode": 0o644,
                    "uid": 0,
                    "gid": 0,
                    "size": len(data),
                    "link_target": target,
                    "sha256": hashlib.sha256(data).hexdigest() if kind == "regular" else None,
                }
            )
        return {
            **result,
            "status": "parsed-synthetic",
            "manifest": {
                "schema": contract.SCHEMA,
                "binding": expected_binding(case, run_id, policy_digest),
                "expanded_bytes": len(raw),
                "members": rows,
            },
            "tar_sha256": hashlib.sha256(raw).hexdigest(),
            "member_count": 5,
        }
    details = {
        "network": {"namespace_distinct": True, "routes": 0, "connect_errno": 101},
        "readonly": {"write_errno": 30},
        "host_hidden": {"home_absent": True, "shell_absent": True},
        "imports": {
            "fixed_tarfile": True,
            "fixed_adapter": True,
            "site_absent": True,
            "app_absent": True,
            "pillow_absent": True,
        },
        "resources": {
            "limits": {key: [value, value] for key, value in LIMITS.items()},
            "allocation_refused": True,
        },
    }
    if case not in details:
        raise ValueError("ustar_no_json_outcome")
    return {**result, "status": "controlled", "details": details[case]}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ustar_duplicate_output")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("ustar_invalid_output")


def validate_result(case, result, error, cleanup_confirmed, run_id, policy_digest):
    if cleanup_confirmed is not True:
        raise ValueError("ustar_cleanup_unknown")
    if case in NATIVE_ERRORS:
        if result is not None or type(error) is not str or error != NATIVE_ERRORS[case]:
            raise ValueError("ustar_native_control")
        return {
            "case": case,
            "status": "passed",
            "expected_error": error,
            "cleanup_confirmed": True,
        }
    if (
        error is not None
        or result is None
        or type(result.exit_code) is not int
        or result.exit_code != 0
    ):
        raise ValueError("ustar_guest_failure")
    if type(result.stdout) is not bytes or len(result.stdout) > 65536 or result.stderr != b"":
        raise ValueError("ustar_output_bound")
    try:
        value = json.loads(
            result.stdout.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        if canonical(value) != canonical(expected_value(case, run_id, policy_digest)):
            raise ValueError("ustar_result_mismatch")
    except (UnicodeError, RecursionError, TypeError, ValueError):
        raise ValueError("ustar_result_mismatch") from None
    return {
        "case": case,
        "status": "passed",
        "output_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "cleanup_confirmed": True,
        "value": value,
    }


# Parent-provided source string is reviewed and hashed in the policy before launch.
# Limits precede optional module imports and all fixture parsing.
GUEST_BOOTSTRAP = r"""
import resource
import sys
for kind, bound in ((resource.RLIMIT_AS,268435456),(resource.RLIMIT_CPU,5),
                    (resource.RLIMIT_NOFILE,32),(resource.RLIMIT_FSIZE,0),
                    (resource.RLIMIT_CORE,0),(resource.RLIMIT_NPROC,0)):
    resource.setrlimit(kind,(bound,bound))
import hashlib
import io
import json
import os
import time
try:
    case, host_ns, binding_json = sys.argv[1:]
    root='/opt/zelo-ustar'
    if 'tarfile' in sys.modules or 'site' in sys.modules or os.getcwd()!='/workspace':
        raise ValueError('guest_start')
    pins=__SOURCE_HASHES__
    for path, digest in pins.items():
        with open(path,'rb') as source:
            raw=source.read(262145)
        if len(raw)>262144 or hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('guest_source_pin')
    sys.path.insert(0,root)
    from qualification import media_tar_inventory as scanner
    from qualification import media_member_contract as contract
    import tarfile
    if (tarfile.__file__!=root+'/tarfile.py' or
        scanner.__file__!=root+'/qualification/media_tar_inventory.py'):
        raise ValueError('guest_import')
    raw=sys.stdin.buffer.read(32769)
    if not 0<len(raw)<=32768:
        raise ValueError('guest_input_bound')
    binding=contract.ExpectedBinding(**json.loads(binding_json))
    contract._binding(binding)
    if len(raw)!=binding.archive_bytes or hashlib.sha256(raw).hexdigest()!=binding.archive_sha256:
        raise ValueError('guest_input_binding')
    result={'schema':'zelo-ustar-isolated-case-v1','case':case,'source_hashes':pins,
            'fixture_sha256':hashlib.sha256(raw).hexdigest(),'runtime_authority':False}
    if case in __PARSE_CASES__:
        try:
            parsed=scanner.scan_tar(io.BytesIO(raw),binding)
        except scanner.Refused as error:
            result.update(status='refused-input',code=str(error))
        else:
            result.update(status='parsed-synthetic',manifest=json.loads(parsed.manifest_raw),
                          tar_sha256=parsed.tar_sha256,member_count=parsed.member_count)
    elif case=='network':
        import socket
        with open('/proc/net/route','r') as source:
            routes=source.read(8193)
        if len(routes)>8192:raise ValueError('guest_routes_bound')
        rows=[row for row in routes.splitlines()[1:] if row.strip()]
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            sock.settimeout(1)
            try:sock.connect(('192.0.2.1',9)); code=0
            except OSError as error:code=error.errno
        finally:sock.close()
        result.update(status='controlled',details={'namespace_distinct':os.readlink('/proc/self/ns/net')!=host_ns,
                                                  'routes':len(rows),'connect_errno':code})
    elif case=='readonly':
        try:
            fd=os.open('/tmp/zelo-ustar-write-test',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except OSError as error:code=error.errno
        else:os.close(fd);code=0
        result.update(status='controlled',details={'write_errno':code})
    elif case=='host_hidden':
        result.update(status='controlled',details={'home_absent':not os.path.exists('/home'),
                                                  'shell_absent':not os.path.exists('/bin/sh')})
    elif case=='imports':
        import importlib.util
        result.update(status='controlled',details={'fixed_tarfile':tarfile.__file__==root+'/tarfile.py',
            'fixed_adapter':scanner.__file__==root+'/qualification/media_tar_inventory.py',
            'site_absent':'site' not in sys.modules,
            'app_absent':importlib.util.find_spec('app') is None,
            'pillow_absent':importlib.util.find_spec('PIL') is None})
    elif case=='resources':
        limits={name:list(resource.getrlimit(kind)) for name,kind in
            (('as',resource.RLIMIT_AS),('cpu',resource.RLIMIT_CPU),('nofile',resource.RLIMIT_NOFILE),
             ('fsize',resource.RLIMIT_FSIZE),('core',resource.RLIMIT_CORE),('nproc',resource.RLIMIT_NPROC))}
        try:unused=bytearray(300*1024*1024)
        except MemoryError:refused=True
        else:refused=False;del unused
        result.update(status='controlled',details={'limits':limits,'allocation_refused':refused})
    elif case in ('stdout_limit','stderr_limit'):
        fd=1 if case=='stdout_limit' else 2
        for _ in range(64):os.write(fd,b'X'*4096)
        raise SystemExit(0)
    elif case in ('timeout','cancel','cancel_repeated'):
        if case!='timeout':
            fd=os.open('/opt/zelo-ustar-ready',os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
            try:
                if os.write(fd,b'R')!=1:raise ValueError('guest_ready')
            finally:os.close(fd)
        time.sleep(30)
        raise ValueError('guest_control_not_interrupted')
    else:raise ValueError('guest_case')
    out=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(out)>65535:raise ValueError('guest_output_bound')
    sys.stdout.buffer.write(out+b'\n')
except Exception:
    sys.stderr.write('ustar_guest_refused\n')
    raise SystemExit(1)
""".replace("__SOURCE_HASHES__", repr(source_hashes())).replace(
    "__PARSE_CASES__", repr(("positive", *MALFORMED))
)
