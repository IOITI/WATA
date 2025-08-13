from setuptools import setup, find_packages

setup(
    name="wata",
    version="0.1",
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    entry_points={
        'console_scripts': [
            'watasaxoauth=src.saxo_authen.cli:main',
            'watawebtoken=src.web_server.cli:main',
        ],
    },
    install_requires=[
        "requests",
        "python-dotenv",
    ],
) 