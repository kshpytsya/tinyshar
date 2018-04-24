import pytest
from tinyshar.cli import main
import subprocess
import sys
import os


def test_empty():
    main([])


def test_validation_error():
    with pytest.raises(SystemExit):
        main(['-p', '"'])


@pytest.mark.skipif(sys.platform == 'win32', reason="does not run on windows")
def test_fail_due_symlink(tmpdir):
    arena_dir = tmpdir / "arena"
    arena_dir.mkdir()

    NAME = "6ac5f8bb-ac10-49af-b866-a1d21b42943c"

    (arena_dir / NAME).mksymlinkto(arena_dir)

    with pytest.raises(ValueError) as e:
        main(["-r", str(arena_dir)])

    assert NAME in str(e.value)


def test_all(tmpdir, run_wrapper):
    root_dir = tmpdir / "root"
    root_root_dir = root_dir / tmpdir / "root_out"
    arena_dir = tmpdir / "arena"
    (root_root_dir / "file1").write_binary(b"text1", ensure=True)
    (root_root_dir / "dir" / "file1").write_binary(b"text2", ensure=True)
    (arena_dir / "file1").write_binary(b"text3", ensure=True)
    (arena_dir / "dir" / "file1").write_binary(b"text4", ensure=True)

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
        "-r", str(arena_dir)
    ])

    script_path.chmod(0o500)

    env = dict(os.environ)
    env["TMPDIR"] = str(tmp_dir)
    subprocess.check_call(run_wrapper + [script_path], env=env)
