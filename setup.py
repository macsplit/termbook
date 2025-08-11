#!/usr/bin/env python3
from setuptools import setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="termbook",
    version="1.0.0",
    author="Lee Hanken",
    author_email="",
    description="A terminal-based EPUB reader optimized for programming books",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/leehanken/termbook",
    py_modules=["termbook"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Environment :: Console :: Curses",
        "Topic :: Education",
        "Topic :: Utilities",
        "Development Status :: 4 - Beta",
    ],
    python_requires=">=3.8",
    install_requires=[
        "Pillow>=9.0.0",
        "pygments>=2.10.0",
    ],
    entry_points={
        "console_scripts": [
            "termbook=termbook:main",
        ],
    },
)