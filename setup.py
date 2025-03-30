from setuptools import setup, find_packages

setup(
    name="wata",
    version="0.1",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'watasaxoauth=src.saxo_authen.cli:main',
        ],
    },
    install_requires=[
        "requests",
        "python-dotenv",
    ],
) 