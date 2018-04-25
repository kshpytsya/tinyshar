from invoke import task


@task
def clean(ctx):
    ctx.run("rm -rf dist")
    ctx.run("rm -rf .tox")
    ctx.run("rm -rf reports")
    ctx.run("rm -rf docs/build")


@task
def bump(ctx, part='patch'):
    ctx.run("bumpversion " + part)


@task
def check(ctx):
    ctx.run("flake8 --max-line-length=120 setup.py tasks.py src tests")


@task
def build(ctx):
    ctx.run("python setup.py sdist bdist_wheel")


@task
def test(ctx):
    ctx.run("tox")


@task
def docs(ctx):
    ctx.run("sphinx-build docs/source docs/build")
