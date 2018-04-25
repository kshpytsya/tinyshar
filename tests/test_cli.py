import pytest
from tinyshar._cli import main
import subprocess
import os
from contextlib import contextmanager


def test_empty():
    main([])


def test_validation_error():
    with pytest.raises(SystemExit):
        main(['-p', '"'])


def test_fail_due_symlink(tmpdir, monkeypatch):
    arena_dir = tmpdir / "arena"
    arena_dir.mkdir()

    # note: since there are no proper symlinks on Windows, we just
    # mock them on all platforms for testing purposes

    NAME = "6ac5f8bb-ac10-49af-b866-a1d21b42943c"
    (arena_dir / NAME).write_binary(b"")

    class MockEntry:  # pragma: no coverage
        def __init__(self, real):
            self._real = real
            self.name = real.name

        def is_file(self, follow_symlinks=True):
            if self.name == NAME:
                return False

            return self._real.is_file(follow_symlinks)

        def is_dir(self, follow_symlinks=True):
            if self.name == NAME:
                return False

            return self._real.is_file(follow_symlinks)

        def __enter__(self):
            return self._real.__enter__()

        def __leave__(self, *a):
            return self._real.__leave__(*a)

    real_scandir = os.scandir

    @contextmanager
    def mock_scandir(path):
        yield (MockEntry(i) for i in real_scandir(path))  # pragma: no coverage

    monkeypatch.setattr(os, "scandir", mock_scandir)

    with pytest.raises(ValueError) as e:
        main(["-r", str(arena_dir)])

    assert NAME in str(e.value)


def test_all(tmpdir, run_wrapper):
    root_dir = tmpdir / "root"
    # note: we assume that tmpdir is on C: drive on Windows
    root_root_dir = root_dir / os.path.splitdrive(tmpdir)[1] / "root_out"
    arena_dir = tmpdir / "arena"
    (root_root_dir / "file1").write_binary(b"text1", ensure=True)
    (root_root_dir / "dir" / "file1").write_binary(b"text2", ensure=True)
    (arena_dir / "file1").write_binary(b"text3", ensure=True)
    (arena_dir / "dir" / "file1").write_binary(b"text4", ensure=True)

    file3_path = tmpdir / "file3"
    file4_path = tmpdir / "file4"
    file3_path.write_binary(b"text5")
    file4_path.write_binary(b"text6")

    tmp_dir = tmpdir / "tmp"
    tmp_dir.mkdir()

    script_path = tmpdir / "script.sh"

    main([
        "-o", str(script_path),
        "-p", "true",
        "-p", "true",
        "-c", "true",
        "-c", "true",
        "-a", str(root_dir),
        "-r", str(arena_dir),
        "-f", os.path.devnull + ":empty",
        "-f", os.path.devnull + ":empty file:600",
        "-f", str(file3_path) + ":",
        "-f", str(file4_path) + ":./",
    ])

    script_path.chmod(0o500)

    env = dict(os.environ)
    env["TMPDIR"] = str(tmp_dir)
    subprocess.check_call(run_wrapper + [str(script_path)], env=env)
