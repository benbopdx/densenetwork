from setuptools import setup, find_packages

setup(
    name="densenet",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "anthropic>=0.40.0",
        "pyairtable>=2.3.0",
        "click>=8.1.0",
        "rich>=13.7.0",
        "python-dotenv>=1.0.0",
        "requests>=2.31.0",
        "pydantic>=2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "densenet=densenet.cli:cli",
        ],
    },
    python_requires=">=3.9",
)
