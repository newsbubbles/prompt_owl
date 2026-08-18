from setuptools import setup, find_packages

with open('README.md', 'r', encoding='utf-8') as fh:
    long_description = fh.read()

setup(
    name='prompt-owl',
    version='0.2.0',
    packages=find_packages(where='.'),
    install_requires=[
        'requests>=2.31.0',
        'aiohttp>=3.9.1',
    ],
    extras_require={
        'mcp': ['mcp>=1.2.0'],
        'studio': ['fastapi>=0.100.0', 'uvicorn>=0.23.0'],
        # Built-in tool integrations. Optional on purpose: a stack that never calls {@comfy}
        # should not need Pillow, and core.py skips any tool whose dependency is absent.
        'tools': ['pytz>=2023.3', 'pillow>=10.0.0', 'websockets>=11.0'],
        'rag': ['chromadb>=0.4.0'],
    },
    package_data={'prowl': ['studio/web/*', 'studio/web/*/*']},
    include_package_data=True,
    python_requires='>=3.9',
    entry_points={
        'console_scripts': [
            'prowl=prowl.cli:main',
            'prowl-mcp=prowl.mcp:main',
            'prowl-studio=prowl.studio.server:main',
        ],
    },
    # Additional metadata
    author='LoreKeeper Ltd',
    author_email='nathaniel@lorekeeper.co.uk',
    description='A Declarative Prompting Language for LLMs',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/lks-ai/prowl',
    license='MIT',
    classifiers=[
        # Classifiers help users find your project by categorizing it.
        # For a list of valid classifiers, see https://pypi.org/classifiers/
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
)
