from setuptools import setup, find_packages

setup(
    name="tinyshar",
    description="simple library and utility for creation of shell archives",
    long_description=open("README.md").read(),  # no "with..." will do for setup.py
    long_description_content_type='text/markdown; charset=UTF-8; variant=GFM',
    license="MIT",
    author="Kyrylo Shpytsya",
    author_email="kshpitsa@gmail.com",
    url="https://github.com/kshpytsya/tinyshar",
    setup_requires=["setuptools_scm"],
    use_scm_version=True,
    python_requires=">=3.6, <=3.7",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={"console_scripts": ["tinyshar = tinyshar._cli:main"]},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Topic :: System :: Installation/Setup",
        "Topic :: System :: Software Distribution",
    ],
)
