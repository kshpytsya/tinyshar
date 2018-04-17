from setuptools import setup, find_packages

setup(
    setup_requires=['pbr'],
    pbr=True,
    packages=find_packages(where="src"),
    package_dir={"": "src"},
)
