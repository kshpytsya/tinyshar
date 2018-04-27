import os
import pytest
import tinyshar
import subprocess
import filecmp


@pytest.fixture
def shar():
    return tinyshar.SharCreator()


@pytest.fixture
def empty_path(tmpdir):
    saved_path = os.environ['PATH']
    empty = tmpdir / "empty"
    empty.mkdir()
    os.environ['PATH'] = str(empty)
    yield
    os.environ['PATH'] = saved_path


@pytest.fixture
def run(tmpdir, shar, run_wrapper):
    class Run:
        def __init__(self):
            self.run_count = 0
            self.pre_cb = []
            self.post_cb = []

        def add_pre(self, cb):
            self.pre_cb.append(cb)

        def add_post(self, cb):
            self.post_cb.append(cb)

        def __call__(
            self,
            *,
            expect_returncode=[0],
            cb_params={},
            tee_to_file=True,
            encoder=None,
            patch_cb=None
        ):
            # __tracebackhide__ = True
            assert self.run_count == 0
            self.run_count += 1

            for cb in self.pre_cb:
                cb(**cb_params)

            tmp_dir = tmpdir / "tmp"
            tmp_dir.mkdir()

            script = tmpdir / "script.sh"
            script_fname = str(script)

            with open(script_fname, "wb") as out_stm:
                render_opts = dict(
                    tee_to_file=tee_to_file,
                    encoder=encoder,
                    _test_tmp_dir=str(tmp_dir),
                    header=[
                        'Generated by test_lib.py...',
                        '...and some restless silicon.'
                    ]
                )
                if patch_cb:
                    rendered = b''.join(shar.render(**render_opts))
                    rendered2 = b''.join(shar.render(**render_opts))
                    assert rendered == rendered2
                    rendered = patch_cb(rendered)
                    out_stm.write(rendered)
                else:
                    shar.render(
                        out_stm=out_stm,
                        **render_opts
                    )
                    rendered = None

            if rendered is None:
                rendered = script.read_binary()

            assert rendered.startswith(b'#!')
            assert rendered.endswith(b'#############\n')

            os.chmod(script_fname, 0o500)

            cp = subprocess.run(
                run_wrapper + [script_fname],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            assert cp.returncode in expect_returncode
            tmp_list = tmp_dir.listdir()
            assert len(tmp_list) == (1 if cp.returncode else 0)

            if tmp_list:
                self.arena_dir = tmp_list[0] / 'arena'
            else:
                self.arena_dir = None

            for cb in self.post_cb:
                cb(returncode=cp.returncode, **cb_params)

    r = Run()
    yield r
    assert r.run_count == 1


@pytest.fixture
def cmpdirs(tmpdir, shar, run):
    dir_n = 0

    def add(dest):
        nonlocal dir_n
        d = tmpdir / ("%04d" % dir_n)
        d.mkdir()
        dir_n += 1

        def pre_run_cb(tags_cb=None, **kw):
            shar.add_dir(str(d), dest, tags_cb=tags_cb)

        def post_run_cb(expect_files=True, **kw):
            if os.path.isabs(dest):
                real_dest = dest
            else:
                if run.arena_dir is None:
                    return

                real_dest = os.path.join(run.arena_dir, dest)

            if expect_files:
                assert os.path.isdir(real_dest)
                diff = filecmp.dircmp(str(d), real_dest)
                assert len(diff.left_only) == 0
                assert len(diff.right_only) == 0
            else:
                assert not os.path.exists(run.arena_dir)

        run.add_pre(pre_run_cb)
        run.add_post(post_run_cb)

        return d

    return add


@pytest.fixture
def somefiles(tmpdir, cmpdirs):
    root_dir = cmpdirs(str(tmpdir / "root1"))
    (root_dir / "1").write_binary(b"one", ensure=True)
    (root_dir / "d1" / "11").write_binary(b"one-one", ensure=True)
    (root_dir / "d1" / "12").write_binary(b"one-two", ensure=True)
    arena_dir = cmpdirs("")
    (arena_dir / "2").write_binary(b"two", ensure=True)
    (arena_dir / "d2" / "2").write_binary(b"two-two", ensure=True)
    (arena_dir / "d2" / "3").write_binary(b" " * (1024 * 1024), ensure=True)


def test_empty(shar):
    b''.join(shar.render())


def test_chaining(shar):
    (shar
        .add_pre("true")
        .add_post("true")
        .add_file("one", "")
        .render())


@pytest.mark.parametrize('extraction_validators', [[], None])
def test_files(shar, extraction_validators):
    shar.add_file("one", b"abc" * 10)
    shar.add_file("two/one", "")
    shar.add_file("/three", lambda: "")

    b''.join(shar.render(extraction_validators=extraction_validators))


def test_dup_file(shar):
    shar.add_file("one", '')
    with pytest.raises(FileExistsError):
        shar.add_file("one", '')


@pytest.mark.parametrize('name', ['/', '', '.'])
def test_is_a_directory_error1(shar, name):
    with pytest.raises(IsADirectoryError):
        shar.add_file(name, '')


def test_bad_contents_type(shar):
    shar.add_file("one", 1)

    with pytest.raises(TypeError):
        b''.join(shar.render())


def test_pre_post(shar):
    shar.add_pre('true')
    shar.add_pre(b'true')
    shar.add_post('false')
    shar.add_post(b'false')

    b''.join(shar.render())


def test_missing_shellcheck(shar, empty_path):
    with pytest.raises(FileNotFoundError):
        shar.render()


def test_shellcheck(shar):
    shar.add_pre('"')

    with pytest.raises(tinyshar.ValidatorError):
        b''.join(shar.render())


def test_no_shellcheck(shar):
    shar.add_pre('"')
    b''.join(shar.render(build_validators=[]))


@pytest.mark.parametrize('stage', ['add_pre', 'add_post'])
@pytest.mark.parametrize(
    'cmd,expect_returncode',
    [
        ('false', 1),
        ('true', 0),
        ('exit 42', 42),
    ]
)
@pytest.mark.parametrize('tee_to_file', [False, True])
def test_run_retcode(shar, run, stage, cmd, expect_returncode, tee_to_file):
    getattr(shar, stage)(cmd)
    run(expect_returncode=[expect_returncode], tee_to_file=tee_to_file)


@pytest.mark.parametrize('tee_to_file', [False, True])
def test_bad_md5(shar, run, tee_to_file):
    shar.add_file("one", "")
    run(
        expect_returncode=[1],
        patch_cb=lambda s: s.replace(b'd41d8cd98f00b204e9800998ecf8427e', b'deadbeaf'),
        tee_to_file=tee_to_file
    )


def test_dirs1(shar, run, somefiles):
    shar.add_post("false")
    run(expect_returncode=[1])


@pytest.mark.parametrize('encoder', [None, tinyshar.Base64Encoder()])
def test_dirs2(shar, run, somefiles, encoder):
    run(encoder=encoder)


def test_dirs3(shar, run, somefiles):
    shar.add_pre("false")
    run(expect_returncode=[1], cb_params=dict(expect_files=False))


def test_tags(shar):
    shar.add_file("one", "", tags=[])
    shar.add_file("two", "", tags=["x"])
    shar.add_file("\"three\"", "", tags=["x"])
    shar.add_file("four", "", tags=["y"])
    assert shar.files_by_tag_as_shell_str("z") == ""
    assert shar.files_by_tag_as_shell_str("x") == "'\"three\"' two"
    assert shar.files_by_tag_as_shell_str("y") == "four"


def test_dirs_tags_cb(shar, run, somefiles):
    def tags_cb(entry):
        return ['tag1'] if entry.name == '12' else []

    run(cb_params=dict(tags_cb=tags_cb))
    assert len(shar.files_by_tag("tag1")) == 1


def test_chunk_order(shar):
    shar.add_pre("f", order=3)
    shar.add_pre("e", order=3)
    shar.add_pre(b"d")
    shar.add_pre("c")
    shar.add_pre("b", order=-1)
    shar.add_pre(b"a", order=-1)
    shar.add_post("xxx")

    s = b''.join(shar.render())
    assert b'\nb\na\nd\nc\nf\ne\n' in s


def test_chunk_order_assert(shar):
    with pytest.raises(AssertionError):
        shar.add_pre("f", order="x")


def test_target_is_a_dir(tmpdir, shar, run):
    d = tmpdir / "target"
    d.mkdir()
    shar.add_file(str(d), "")
    run(expect_returncode=[1])
