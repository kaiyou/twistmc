"""
Setup configuration.
"""

import setuptools


setuptools.setup(
    name="twistmc",
    version="0.1.2",
    author="Pierre Jaury",
    author_email="pierre@jaury.eu",
    description="Twist My Components!",
    long_description=open("README.txt").read(),
    license="GPLv3",
    url="https://github.com/kaiyou/twistmc",
    py_modules=["twistmc"],
    install_requires=[
        "breadcrumbs >= 0.1.1",
        "zope.interface >= 3.6",
        "Twisted >= 12.0"
    ]
)
