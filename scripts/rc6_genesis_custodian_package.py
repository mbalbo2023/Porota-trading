"""Build the fixed R367 custodian from reviewed sources on the Actions runner."""

import hashlib
import io
import os
from pathlib import Path
import stat
from types import MappingProxyType
from zipfile import ZIP_STORED, ZipFile, ZipInfo


MAX_BYTES = 1024 * 1024
_SOURCE_PINS = MappingProxyType({
    'rc6_genesis_custodian.py': '76b6eca272780d47982dd52e6078412ef5f1b6a68c0825b22f4b706d00f1b452',
    'rc6_genesis_host_identity.py': '47abb1043d3180184bf369b0b4895a25ca57c73ba00cb085a068ab317f30bf58',
    'rc6_genesis_host_install.py': 'c925516b97a548fac4fc575be30307eb4b3baab72c3d7aff5e90f883d9641ec3',
    'rc6_genesis_host_transaction.py': 'ea48c40a16732a35d24b14ad2b83c05794a03b875986eb2c19c8f9cf99f5d420',
    'rc6_host_rootowned_installer_contract.py': '8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9',
    'rc6_oci_host_helper.py': 'aa68cc1def24096d32fc91c77c2076bee44ca8290000fdbd58b945cfc820ae1d',
    'rc6_r304_root_install_manifest_freeze.py': '6711ad67f4de122488787b5587ff947801afed19fe0df4d551db4e65f638fea9',
    'rc6_r308_root_launch_profile_freeze.py': 'f9e1b8dc4d32ebd662eebe382be5989b1f72bb36b17a0fe3f2f3072691712db9',
    'rc6_r326_root_bundle_provenance.py': '85f73daa141773ab74dc60a924a783d2d2fb606e9d8a142c1c8c0f2c671df7ca',
    'rc6_r334_root_env_genesis.py': 'c7060a643b0a72ee6b0416fd38967c5c741abe340667b8988e79dd3768f12fb8',
    'rc6_r337_root_env_seal.py': '5f0d41135c254b6d69bd1265a06ba1717053e027363c9e7c82d1bacdea610052',
})
_SHEBANG = b'#!/usr/bin/python3 -I\n'
_MAIN = (b'import sys\n'
         b'if not sys.flags.isolated:\n'
         b'    raise SystemExit(79)\n'
         b'from scripts.rc6_genesis_custodian import main\n'
         b'raise SystemExit(main())\n')


class PackageHold(ValueError):
    exit_code = 79


def _source(directory, name, digest):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=directory)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise PackageHold('CUSTODIAN_SOURCE_INVALID')
        raw = handle.read(MAX_BYTES + 1)
        if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != digest:
            raise PackageHold('CUSTODIAN_SOURCE_DIGEST_MISMATCH')
        return raw


def build_custodian(source_root=None):
    """Return executable bytes. Only the checkout root is selectable, never members or pins."""
    root = Path(__file__).resolve().parents[1] if source_root is None else Path(source_root)
    files = {'__main__.py': _MAIN, 'scripts/__init__.py': b''}
    flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root, flags)
        try:
            directory = os.open('scripts', flags, dir_fd=root_fd)
            try:
                for name, digest in _SOURCE_PINS.items():
                    files['scripts/' + name] = _source(directory, name, digest)
            finally:
                os.close(directory)
        finally:
            os.close(root_fd)
    except OSError as error:
        raise PackageHold('CUSTODIAN_SOURCE_UNAVAILABLE') from error
    if sum(map(len, files.values())) > MAX_BYTES:
        raise PackageHold('CUSTODIAN_PACKAGE_TOO_LARGE')

    output = io.BytesIO()
    output.write(_SHEBANG)
    with ZipFile(output, 'w', compression=ZIP_STORED, allowZip64=False) as archive:
        for name, raw in sorted(files.items()):
            member = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, raw)
    if output.tell() > MAX_BYTES:
        raise PackageHold('CUSTODIAN_PACKAGE_TOO_LARGE')
    return output.getvalue()
