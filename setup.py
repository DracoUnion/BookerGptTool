#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

import setuptools
import BookerGptTool

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    install_requires = fh.read().splitlines()

setuptools.setup(
    name="BookerGptTool",
    version=BookerGptTool.__version__,
    url="https://github.com/apachecn/BookerGptTool",
    author=BookerGptTool.__author__,
    author_email=BookerGptTool.__email__,
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "License :: Other/Proprietary License",
        "Natural Language :: Chinese (Simplified)",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Text Processing :: Markup :: HTML",
        "Topic :: Software Development :: Documentation",
        "Topic :: Software Development :: Localization",
        "Topic :: Utilities",
    ],
    description="iBooker/ApacheCN 知识库抓取工具",
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords=[
        "wiki",
        "知识库",
        "document",
        "文档",
        "crawler",
        "爬虫",
    ],
    install_requires=install_requires,
    python_requires=">=3.6",
    entry_points={
        'console_scripts': [
            "BookerGptTool=BookerGptTool.__main__:main",
            "pdf-tool=BookerGptTool.__main__:main",
        ],
    },
    packages=setuptools.find_packages(),
    package_data={'': ['*']},
)
