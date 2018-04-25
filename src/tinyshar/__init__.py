import binascii as _binascii
import collections as _collections
import contextlib as _contextlib
import hashlib as _hashlib
import io as _io
import os as _os
import posixpath as _posixpath
import shlex as _shlex
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys


try:
    __version__ = __import__('pkg_resources').get_distribution(__name__).version
except:  # noqa # pragma: no cover
    pass


def _to_bytes(s):
    assert isinstance(s, (str, bytes))

    if isinstance(s, str):
        return s.encode()
    else:
        return s


def _checked_which(what):
    result = _shutil.which(what)
    if result is None:
        raise FileNotFoundError(what)
    return result


def _make_reader(what):
    if callable(what):
        what = what()

    if isinstance(what, str):
        what = what.encode()

    if isinstance(what, bytes):
        ofs = 0

        def reader(n):
            nonlocal ofs
            n = min(n, len(what) - ofs)
            result = what[ofs: ofs + n]
            ofs += n
            return result

        return reader

    if isinstance(what, _io.IOBase):
        def reader(n):
            result = what.read(n)

            if not result:
                what.close()

            return result

        return reader

    raise TypeError(type(what))


class Base64Encoder:
    _MAXBINSIZE = 57

    def encode(self, reader, writer):
        writer(b"base64 -d << _END_\n")

        while True:
            chunk = reader(self._MAXBINSIZE)
            if not chunk:
                break

            writer(_binascii.b2a_base64(chunk))

        writer(b"_END_\n")


class ValidatorError(RuntimeError):
    pass


@_contextlib.contextmanager
def ShellcheckValidator():
    process = _subprocess.Popen(
        [_checked_which('shellcheck'), '-'],
        stdin=_subprocess.PIPE,
        stdout=_sys.stderr
    )

    yield process.stdin.write

    process.stdin.close()
    rc = process.wait()
    if rc != 0:
        raise ValidatorError("shellcheck failed")


class Md5Validator:
    def __init__(self):
        self.hashes = []

    def wrap_reader(self, reader, fname):
        md5 = _hashlib.md5()

        def reader_wrapper(n):
            chunk = reader(n)

            if chunk:
                md5.update(chunk)
            else:
                self.hashes.append((fname, md5.hexdigest().encode()))

            return chunk

        return reader_wrapper

    def render(self, writer):
        writer(b"md5sum -c << _END_\n")
        for fname, md5 in self.hashes:
            writer(b"%s  %s\n" % (md5, fname))
        writer(b"_END_\n")


class SharCreator:
    def __init__(self):
        self._files = {}
        self._dirs = set()
        self._pre_chunks = []
        self._post_chunks = []
        self._files_by_tag = _collections.defaultdict(lambda: [])

    def files_by_tag(self, tag):
        return sorted(self._files_by_tag[tag])

    def files_by_tag_as_shell_str(self, tag):
        return ' '.join(_shlex.quote(i) for i in self.files_by_tag(tag))

    def add_file(self, name, content, tags=[]):
        assert isinstance(name, str)
        name = _posixpath.normpath(name)

        if name in ['.', '/']:
            raise IsADirectoryError(name)

        if name in self._files:
            raise FileExistsError(name)

        components = tuple(name.split('/'))
        is_abs = components[0] == ''
        for depth in range(1 + int(is_abs), len(components)):
            self._dirs.add(components[:depth])

        self._files[name] = content

        for tag in tags:
            assert isinstance(tag, str)
            self._files_by_tag[tag].append(name)

        return self

    def add_dir(self, src, dest, follow_symlinks=False):
        with _os.scandir(src) as it:
            for entry in it:
                src_name = _os.path.join(src, entry.name)
                dest_name = _posixpath.join(dest, entry.name)
                if entry.is_file():
                    self.add_file(dest_name, lambda fname=src_name: open(fname, 'rb'))
                elif entry.is_dir(follow_symlinks=follow_symlinks):
                    self.add_dir(src_name, dest_name)
                else:
                    raise ValueError("do not know how to deal with " + src_name)

        return self

    def _add_chunk(self, dest, chunk, order):
        assert isinstance(order, int)
        dest.append((order, chunk))
        return self

    def add_pre(self, chunk, *, order=0):
        return self._add_chunk(self._pre_chunks, chunk, order)

    def add_post(self, chunk, *, order=0):
        return self._add_chunk(self._post_chunks, chunk, order)

    def render(
        self,
        *,
        shebang=b'/bin/sh',
        out_stm=None,
        encoder=None,
        build_validators=None,
        extraction_validators=None,
        tee_to_file=True,
        _test_tmp_dir=None,
    ):
        """ Produce a shell script

        Note: it is ok to invoke this multiple times.

        Args:
            out_stm (Optional[IO[bytes]]):
            encoder (Optional[Encoder]):
            build_validators (Optional[List[BuildValidator]]):
            extraction_validators (Optional[List[ExtractionValidator]]):
            tee_to_file (Optional[bool]): Defaults to True.
            _test_tmp_dir: used by unittests to avoid leftoever temp directories

        Returns:
            List[bytes]: list of chunks comprising rendered result. Use "b''.join(render_result)" to obtain ``bytes``.
            None: if `out_stm` was supplied
        """
        encoder = encoder or Base64Encoder()
        if build_validators is None:
            build_validators = [ShellcheckValidator()]
        if extraction_validators is None:
            extraction_validators = [Md5Validator()]

        with _contextlib.ExitStack() as exit_stack:
            writers = []

            for validator in build_validators:
                writers.append(exit_stack.enter_context(validator))

            if out_stm is None:
                result_chunks = []
                writers.append(lambda what: result_chunks.append(what))
            else:
                writers.append(out_stm.write)

            def put(what):
                for writer in writers:
                    writer(what)

            def putnl():
                put(b"\n")

            def putl(what):
                put(what)
                putnl()

            def put_break():
                put(b"################################################################################\n")

            def put_annotation(s):
                putnl()
                put_break()
                put(b"# %s\n" % s)

            def put_chunks(annotation, chunks):
                if chunks:
                    put_annotation(annotation)

                    # according to https://wiki.python.org/moin/HowTo/Sorting/#Sort_Stability_and_Complex_Sorts
                    # sorts are guaranteed to stable.
                    for _, i in sorted(chunks, key=lambda i: i[0]):
                        putl(_to_bytes(i))

            put(b'#!%s\n' % _to_bytes(shebang))
            put(
                b'set -euxo pipefail\n'
                b'DIR=$('
            )

            if _test_tmp_dir is not None:
                put(b"TMPDIR=%s " % _to_bytes(_shlex.quote(_test_tmp_dir)))

            put(
                b'mktemp -d)\n'
                b'cd "$DIR"\n'
            )

            if tee_to_file:
                putl(b'{')

            put_chunks(b'PRE:', self._pre_chunks)

            files_map = []
            for i, (name, content) in enumerate(sorted(self._files.items())):
                tmp_name = b'%06d' % i
                files_map.append((tmp_name, name))

                put_annotation(b'file: %s\n' % name.encode())
                put(b'>"%s" ' % tmp_name)

                reader = _make_reader(content)

                for validator in extraction_validators:
                    reader = validator.wrap_reader(reader, tmp_name)

                encoder.encode(reader, put)

            if files_map:
                put_annotation(b"validation:\n")

                for validator in extraction_validators:
                    validator.render(put)

            put_break()
            put(
                b'mkdir arena\n'
                b'cd arena\n'
            )

            for i in sorted(self._dirs):
                put(b'mkdir -p %s\n' % _shlex.quote('/'.join(i)).encode())

            for tmp_name, name in files_map:
                target = _shlex.quote(name).encode()
                put(b"test '!' -d %s\n" % target)
                put(b"mv -f ../%s %s\n" % (tmp_name, target))

            put_chunks(b'POST:', self._post_chunks)

            if tee_to_file:
                putl(b'} 2>&1 | tee log')

            put_break()

            put(
                b"cd /\n"
                b'rm -rf "$DIR"\n'
            )

            put_break()

            if out_stm is None:
                return result_chunks
