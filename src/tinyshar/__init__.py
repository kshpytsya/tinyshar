import binascii as _binascii
import collections as _collections
import contextlib as _contextlib
import hashlib as _hashlib
import io as _io
import lzma as _lzma
import os as _os
import posixpath as _posixpath
import shlex as _shlex
import shutil as _shutil
import subprocess as _subprocess


try:
    __version__ = __import__('pkg_resources').get_distribution(__name__).version
except:  # noqa # pragma: no cover
    pass


def _call_if_callable(what):
    if callable(what):
        return what()
    else:
        return what


def _check_type(val, val_desc, types, types_desc):
    if not isinstance(val, types):
        raise TypeError("%s must be %s, but not %s" % (val_desc, types_desc, type(val)))


def _to_bytes(s, what):
    _check_type(s, what, (str, bytes), "'str' or 'bytes'")

    if isinstance(s, str):
        return s.encode()
    else:
        return s


def _checked_which(what):
    result = _shutil.which(what)
    if result is None:
        raise FileNotFoundError(what)
    return result


def _make_reader_stm(what):
    what = _call_if_callable(what)

    if isinstance(what, str):
        what = what.encode()

    if isinstance(what, bytes):
        return _io.BytesIO(what)

    if isinstance(what, _io.IOBase):
        return what

    raise TypeError("don't know how to read %s" % type(what))


class _NoCompressor:
    def wrap(self, reader):
        return b'', reader


NoCompressor = _NoCompressor
"""A no-op compressor class.

Note: the signature of this class is not a part of a public API, only the class itself and its constructor are.
"""


class _XzCompressor:
    def wrap(self, reader):
        READ_CHUNK = 1024 * 1024
        compressor = _lzma.LZMACompressor()

        compressed = []
        compressed_pos = 0
        eof = False

        def reader_wrapper(n):
            nonlocal compressed_pos
            nonlocal eof

            result = []

            while n:  # pragma: no branch
                while n and compressed:
                    avail = min(n, len(compressed[0]) - compressed_pos)
                    if avail == 0:
                        compressed.pop(0)
                        compressed_pos = 0
                        continue

                    result.append(compressed[0][compressed_pos:compressed_pos + avail])
                    n -= avail
                    compressed_pos += avail

                if eof:
                    break

                if n:  # pragma: no branch
                    uncompressed = reader(READ_CHUNK)
                    if uncompressed:
                        compressed.append(compressor.compress(uncompressed))
                    else:
                        eof = True
                        compressed.append(compressor.flush())

            return b''.join(result)

        return b'| unxz ', reader_wrapper


XzCompressor = _XzCompressor
"""Compressor class to compress data with xz_ compression. Compression is performed
using :class:`lzma.LZMACompressor` with default settings.
Decompression is performed with the help of `unxz` tool (which is also available as a part of BusyBox_).

Note: the signature of this class is not a part of a public API, only the class itself and its constructor are.

.. _xz: https://en.wikipedia.org/wiki/Xz
.. _BusyBox: https://en.wikipedia.org/wiki/BusyBox
"""


class _Base64Encoder:
    _MAXBINSIZE = 57

    def __init__(self, *, compressor=None):
        self.compressor = compressor or NoCompressor()

    def encode(self, dest, reader, writer):
        compressor_pipe_str, compressor_reader = self.compressor.wrap(reader)

        writer(b"base64 -d << '_END_' %s> '%s'\n" % (compressor_pipe_str, dest))

        while True:
            chunk = compressor_reader(self._MAXBINSIZE)
            if not chunk:
                break

            writer(_binascii.b2a_base64(chunk))

        writer(b"_END_\n")


Base64Encoder = _Base64Encoder
"""Data encoder class for base64_ encoding.

Note: the signature of this class is not a part of a public API, only the class itself and its constructor are.

Args:
    compressor (optional): Instance of a compressor class. Defaults to `None`
        which is a shortcut for :class:`NoCompressor`. See also :class:`XzCompressor`.

.. _base64: https://en.wikipedia.org/wiki/Base64
"""


class ValidatorError(RuntimeError):
    """Build-time validation failure."""


@_contextlib.contextmanager
def ShellcheckValidator():
    """Build-time validator of resulting shell script using shellcheck_.
    In case of validation failure, any stdout output from `shellcheck`
    will be available as ``args[1]`` of the raised :class:`ValidatorError` exception.

    Note: huge output from shellcheck may theoretically cause a deadlock.
    If this ever becomes a real issue, a solution suggested here_ should be used.

    Note: the signature of this class is not a part of a public API, only the class itself and its constructor are.

    Raises:
        FileNotFoundError: If `shellcheck` is not found on `PATH`.

    .. _shellcheck: https://www.shellcheck.net/
    .. _here: http://eyalarubas.com/python-subproc-nonblock.html
    """
    with _subprocess.Popen(
        [_checked_which('shellcheck'), '-'],
        stdin=_subprocess.PIPE,
        stdout=_subprocess.PIPE,
    ) as process:
        yield process.stdin.write

        outs, errs = process.communicate()

        if process.returncode != 0:
            raise ValidatorError("shellcheck failed", outs)


class _Md5Verifier:
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
        writer(b"md5sum -c << '_END_'\n")
        for fname, md5 in self.hashes:
            writer(b"%s  %s\n" % (md5, fname))
        writer(b"_END_\n")


Md5Verifier = _Md5Verifier
"""Class for MD5 extraction-time verification.

Note, according to wiki_ (the **bold** emphasis is ours):

    Although MD5 was initially designed to be used as a cryptographic hash
    function, it has been found to suffer from extensive vulnerabilities. It
    can still be used as a checksum to **verify data integrity**, but only against
    **unintentional corruption**.

.. _wiki: https://en.wikipedia.org/wiki/MD5
"""


class SharCreator:
    """Class for creation of "active" self-extracting shell archives.
    """
    def __init__(self):
        self._files = {}
        self._dirs = set()
        self._pre_chunks = []
        self._post_chunks = []
        self._files_by_tag = _collections.defaultdict(lambda: [])

    def files_by_tag(self, tag):
        """Return a sorted list of names of files tagged with `tag`.
        """
        return sorted(self._files_by_tag[tag])

    def files_by_tag_as_shell_str(self, tag):
        """Return a string containing quoted space-separated sorted list of
        names of files tagged with `tag`. Useful for building command lines passed
        to :func:`add_post` method.
        """
        return ' '.join(_shlex.quote(i) for i in self.files_by_tag(tag))

    def add_file(self, name, content, *, tags=[]):
        """Add an archive file.

        Note: file names are encoded in the resulting archive as ``utf-8``.

        Note: it is acceptable to call `add_file` after :func:`render` call
        causing the next call to :func:`render` to produce a different
        resulting archive.

        Args:
            name (str): a posix path name for the file to be extracted to.
              Relative (i.e. no starting with slash) names are relative
              to ``arena`` directory.
            content (Union[AnyStr, IO[Bytes], Callable[[], Union[AnyStr, IO[Bytes]]]]):
              content of the file to be added. Unicode strings are encoded as ``utf-8``.
              Note: callables will be called once each time :func:`render` is invoked.
              If :func:`render` is expected to be called multiple times, streams must
              be supplied via callable returning either a freshly opened or a rewound
              stream. It is acceptable, if maybe somewhat tricky, to return different
              data for each call, causing resulting archives to differ.
            tags (:obj:`list` of :obj:`Str`, optional): list of tags to be assigned to the file.
              Defaults to no tags.

        Returns:
            `self` to allow method chaining.

        Raises:
            IsADirectoryError: If path `name` has the same name as one of the parent directories
              of already added files, or points to the root (``/``) or current directory.
            FileExistsError: If a file with such a path `name` has already been added, or if one of the parent
              directories leading to the path `name` has the same name as a file that has been already added.
        """
        _check_type(name, "name", str, "str")
        name = _posixpath.normpath(name)

        if name in ['.', '/']:
            raise IsADirectoryError(name)

        if name in self._files:
            raise FileExistsError(name)

        components = tuple(name.split('/'))
        if components in self._dirs:
            raise IsADirectoryError(name)

        is_abs = components[0] == ''
        for depth in range(1 + int(is_abs), len(components)):
            components_prefix = components[:depth]
            joined_components_prefix = '/'.join(components_prefix)
            if joined_components_prefix in self._files:
                raise FileExistsError(joined_components_prefix)

            self._dirs.add(components_prefix)

        self._files[name] = content

        for tag in tags:
            _check_type(tag, "tag", str, "str")
            self._files_by_tag[tag].append(name)

        return self

    def add_dir(
        self,
        src,
        dest,
        *,
        follow_symlinks=False,
        tags_cb=None
    ):
        """Recursively add files from `src` directory.

        Scanning is performed by recursive invocation of :func:`os.scandir`.

        Note: directory scanning is performed at the moment of :func:`add_dir` invocation,
        and file reading is done during :func:`render`.

        Note: empty directories will not be created during extraction.

        Args:
            src (str): a local directory path to be scanned for files. The format
              of the path name is dependent on the execution platform.
            dest (str): a posix path name for the file to be extracted to.
              Relative (i.e. no starting with slash) names are relative
              to ``arena`` directory.
            follow_symlinks (bool): specifies whether directory symlinks should
              be followed. Defaults to `False` causing directory symlinks to result in
              `ValueError` being raised. Note: file symlinks are always followed
              and are added as ordinary files with contents of the pointed file.
              If enabled, target directory is added as if it existed at the location
              of symlink.
            tags_cb (Callable[DirEntry, List[Str]], optional): callback to
              assign tags to files. Invoked during scanning. Defaults to no callback
              thus no tags. See :class:`os.DirEntry`.

        Returns:
            `self` to allow method chaining.

        Raises:
            ValueError: If some directory entry is encountered, which is not a file,
              a symlink to file, a directory, or (if enabled by `follow_symlinks`)
              a symlink to a directory.
        """
        if tags_cb is None:
            tags_cb = lambda e: []  # noqa: E731

        def recurse(src, dest):
            with _os.scandir(src) as it:
                for entry in it:
                    src_name = _os.path.join(src, entry.name)
                    dest_name = _posixpath.join(dest, entry.name)
                    if entry.is_file():
                        self.add_file(
                            dest_name,
                            lambda fname=src_name: open(fname, 'rb'),
                            tags=tags_cb(entry)
                        )
                    elif entry.is_dir(follow_symlinks=follow_symlinks):
                        recurse(src_name, dest_name)
                    else:
                        raise ValueError("do not know how to deal with " + src_name)

        recurse(src, dest)

        return self

    def _add_chunk(self, dest, chunk, order):
        _check_type(order, "order", int, "int")
        dest.append((order, chunk))
        return self

    def add_pre(self, chunk, *, order=0):
        """Add a chunk to the pre-extraction part of the archive.

        Chunk can contain any valid block of shell script (strictly speaking,
        it is the result of aggregation of all chunks that needs to be a valid
        block of shell script, so it is possible to construct things line by
        line).
        Chunks are emitted delimited by new lines, sorted by ascending `order`.
        Chunks with the same value of `order` are emitted in the order of addition.

        Execution of archive will be aborted at the first failure and the return code
        of the failing command will be returned as a return code of the script.

        Commands will be executed with current directory being a newly created
        temporary directory.

        Args:
            chunk (Union[AnyStr, Callable[[], AnyStr]]): if string is unicode, it will
              be ``utf-8`` encoded. If `chunk` is callable, it will be invoked during :func:`render`.
            order (int, optional): Defaults to `0`.

        Returns:
            `self` to allow method chaining.
        """
        return self._add_chunk(self._pre_chunks, chunk, order)

    def add_post(self, chunk, *, order=0):
        """Add a chunk to the post-extraction part of the archive.

        See :func:`add_pre` for description.

        Commands will be executed with `arena` as a current directory.
        """
        return self._add_chunk(self._post_chunks, chunk, order)

    def render(
        self,
        *,
        shebang=b'/bin/sh',
        header=[],
        out_stm=None,
        encoder=None,
        build_validators=None,
        extraction_verifiers=None,
        tee_to_file=True,
        _test_tmp_dir=None,
    ):
        """Produce a shell script.

        Note: it is ok to invoke this method multiple times.
        The state of the object is not changed.

        Args:
            shebang (AnyStr, optional): content of the shebang_ string of the resulting
              script. Defaults to `/bin/sh`.
            header (:obj:`list` of :obj:`AnyStr`, optional): strings
              to be added as comments at the top of the produced script.
            out_stm (IO[bytes], optional): stream to emit the script to. If not supplied,
              the script will be returned as a function result.
            encoder (Encoder, optional): Instance of a class used to encode content of each
              file. Defaults to `None` which is treated
              as ``Base64Encoder(compressor=XzCompressor())``.
              See :class:`Base64Encoder` and :class:`XzCompressor` for details.
            build_validators (:obj:`list` of :obj:`BuildValidator`, optional): list of build-time
              validator object instances.
              Defaults to `None` which is a shortcut for [:class:`ShellcheckValidator`].
            extraction_verifiers (:obj:`list` of `ExtractionVerifier`, optional): list of extraction-time
              verifier object instances.
              Defaults to `None` which is a shortcut for [:class:`Md5Verifier`].
            tee_to_file (bool, optional): specifies whether the produced script will be wrapped
              in a ``{...} 2>& | tee log`` construct. Defaults to `True`.
            _test_tmp_dir: for use by unit tests

        Returns:
            :obj:`list` of :obj:`bytes`: list of chunks comprising rendered result.
            Use ``b''.join(render_result)`` to obtain ``bytes``.

            None: if `out_stm` was supplied

        Raises:
            TypeError: If contents of some file is of wrong type (see :func:`add_file`), or
                if some of the passed parameters are of wrong type.

            ValidatorError: If resulting shell script does not pass build-time validation.

        .. _shebang: https://en.wikipedia.org/wiki/Shebang_(Unix)
        """
        encoder = encoder or Base64Encoder(compressor=XzCompressor())
        if build_validators is None:
            build_validators = [ShellcheckValidator()]
        if extraction_verifiers is None:
            extraction_verifiers = [Md5Verifier()]

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
                        putl(_to_bytes(_call_if_callable(i), "chunk"))

            put(b'#!%s\n' % _to_bytes(shebang, "shebang"))
            put_break()
            put(
                b'# AUTO-GENERATED FILE - DO NOT EDIT!!\n'
                b'# Produced with the help of tinyshar %s\n' % __version__.encode()
            )

            for i in header:
                for j in _to_bytes(i, "header item").split(b"\n"):
                    put(b'# %s\n' % j)

            put_break()
            put(
                b'set -euxo pipefail\n'
                b'DIR=$('
            )

            if _test_tmp_dir is not None:
                put(b"TMPDIR=%s " % _shlex.quote(_test_tmp_dir).encode())

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
                files_map.append((tmp_name, _shlex.quote(name).encode()))

                put_annotation(b'file: %s\n' % name.encode())

                with _make_reader_stm(content) as reader_stm:
                    reader = lambda n: reader_stm.read(n)  # noqa: E731

                    for verifier in extraction_verifiers:
                        reader = verifier.wrap_reader(reader, tmp_name)

                    encoder.encode(tmp_name, reader, put)

            if files_map:
                put_annotation(b"verification:\n")

                for verifier in extraction_verifiers:
                    verifier.render(put)

            put_break()
            put(
                b'mkdir arena\n'
                b'cd arena\n'
            )

            for i in sorted(self._dirs - set(j[:d] for j in self._dirs for d in range(1, len(j)))):
                put(b'mkdir -p %s\n' % _shlex.quote('/'.join(i)).encode())

            for tmp_name, quoted_target in files_map:
                put(b"test '!' -d %s\n" % quoted_target)

            for tmp_name, quoted_target in files_map:
                put(b"mv -f ../%s %s\n" % (tmp_name, quoted_target))

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
