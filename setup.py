import setuptools

with open('requirements.txt') as req_file:
    requirements = req_file.read().strip().split('\n')

with open('README.md') as f:
    readme = f.read()

setuptools.setup(
    name='viktor_dev_tools',
    version='1.1.1',
    description='A Command Line Interface with tools to help VIKTOR Developers with their daily work',
    long_description=readme,
    long_description_content_type='text/markdown',
    packages=setuptools.find_packages(),
    include_package_data=True,
    install_requires=[requirement for requirement in requirements if requirement and 'viktor' not in requirement],
    entry_points={'console_scripts': ['dev-cli = viktor_dev_tools.cli:cli']},
)
