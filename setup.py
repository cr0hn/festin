from setuptools import setup

requirements = open("requirements.txt", "r").read().splitlines()

setup(install_requires=requirements)
