from setuptools import setup, find_packages

setup(
    name="wata",
    version="0.1",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'watasaxoauth=src.saxo_authen.cli:main',
            'watawebtoken=src.web_server.cli:main',
        ],
    },
    # The image installs requirements.txt alone. Each from the lowest version no known advisory applies to (OSV,
    # 2026-10-28): without a version, GitHub's Dependabot cannot tell whether the one installed is vulnerable.
    install_requires=[
        "requests>=2.33.0",
        "python-dotenv>=1.2.2",
    ],
) 