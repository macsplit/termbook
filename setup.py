#!/usr/bin/env python3
from setuptools import setup
import re
import datetime

# Update build time in termbook.py
def update_build_time():
    build_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open("termbook.py", "r") as f:
        content = f.read()
    
    # Update the build time line
    pattern = r'__build_time__ = "[^"]*"'
    replacement = f'__build_time__ = "{build_time}"'
    new_content = re.sub(pattern, replacement, content)
    
    with open("termbook.py", "w") as f:
        f.write(new_content)
    
    print(f"Updated build time to: {build_time}")

# Update build time before building
update_build_time()

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="termbook",
    version="1.1.1",
    author="Lee Hanken",
    author_email="",
    description="A terminal-based EPUB reader optimized for programming books",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/macsplit/termbook",
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